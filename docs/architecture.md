# TerraformAgent — Technical Architecture

> Status: implementation baseline. This document is the design of record for the
> application described in [docs/readme.md](./readme.md). It is intentionally
> concrete about security boundaries, because the product can create and destroy
> cloud infrastructure.

## 1. System context

TerraformAgent turns natural-language requirements and uploaded architecture
documents into a reviewed Azure architecture, a modular Terraform project, a
validated plan, and an audited deployment.

```
┌────────────────────────────────────────────────────────────────────┐
│ Browser (React + TypeScript + Vite)                                │
│  Dashboard · Project workspace · Chat · Diagram · Code · Logs      │
└───────────────▲───────────────────────────────┬────────────────────┘
                │ HTTPS JSON / SSE              │ multipart upload
┌───────────────┴───────────────────────────────▼────────────────────┐
│ FastAPI control plane                                              │
│  Authn/Authz · Projects · Conversations · Documents · Architecture  │
│  Generation · Review · Approvals · Audit · Subscriptions            │
│                                                                    │
│  Orchestrator (state machine + checkpoints)                        │
│   ├── Requirement Analyst     (LLM)                                │
│   ├── Azure Solution Architect(LLM)                                │
│   ├── Architecture Reviewer   (LLM)                                │
│   ├── Terraform Engineer      (LLM + deterministic writer)         │
│   ├── Terraform Validator     (deterministic CLI)                   │
│   ├── Plan Analyst            (deterministic parser + LLM summary)  │
│   └── Verification Agent      (Azure read-only)                     │
└───────┬───────────────────────┬──────────────────────┬─────────────┘
        │ SQL                   │ artifact blobs       │ jobs
┌───────▼────────┐   ┌──────────▼──────────┐   ┌───────▼─────────────┐
│ PostgreSQL     │   │ Artifact store      │   │ Isolated executor   │
│ (metadata)     │   │ (local fs / Azure   │   │ containers /        │
│                │   │  Blob)              │   │ sandboxed processes │
└────────────────┘   └─────────────────────┘   └───────┬─────────────┘
                                                       │
                                    ┌──────────────────▼──────────────┐
                                    │ terraform CLI + tflint + policy │
                                    │ AzureRM / AzAPI / AzureAD       │
                                    └──────────────────┬──────────────┘
                                                       │ OIDC / MI / SP
                                                  ┌────▼────┐
                                                  │ Microsoft│
                                                  │  Azure   │
                                                  └──────────┘
```

## 2. Architectural principles (enforced in code)

| # | Principle | Enforcement |
|---|-----------|-------------|
| 1 | Terraform is the only mutation path to Azure | Only `TerraformExecutor` may run `terraform apply`; no Azure SDK write calls exist in the codebase |
| 2 | LLM never executes commands | Agent layer returns validated JSON only; `llm/` has no subprocess access |
| 3 | LLM never sees credentials/state | Prompt builders receive redacted architecture + requirement context only; secrets live in `secrets/` and worker env |
| 4 | Architecture and deployment approvals are separate | `Approval` rows with distinct `approval_type`; state machine forbids skipping |
| 5 | Apply uses the exact approved binary plan | `Deployment.plan_hash` must equal `Approval.artifact_hash`; apply runs `terraform apply <saved.tfplan>` |
| 6 | Any material change invalidates approvals | `invalidate_approvals()` called on code/architecture/subscription/plan changes |
| 7 | Security checks cannot be disabled by the model | Policy engine is deterministic and runs after every generation; critical findings fail validation |
| 8 | Every action is audited | Append-only `audit_events` written by service layer |

## 3. Component responsibilities

### 3.1 Control plane (`backend/app`)

- **API layer** (`api/routes/*`): thin HTTP adapters. Validates input with
  Pydantic, calls services, never shells out.
- **Domain services** (`services/*`): transactional business logic. Owns state
  transitions, approval invalidation, audit writes.
- **Agents** (`agents/*`): prompt construction + structured-output validation.
  Pure functions over a `ProjectContext`; no DB writes, no shell access.
- **Orchestrator** (`agents/orchestrator.py`): single writer of project status.
  Chooses the next agent, enforces checkpoints, records agent runs.
- **LLM layer** (`llm/*`): provider-agnostic interface with strict JSON schema
  validation and bounded retries. Providers: Azure OpenAI, OpenAI-compatible,
  DeepSeek, and a deterministic offline reasoner for air-gapped/dev use.
  A platform administrator can configure the provider at runtime from the
  **AI provider** screen; the key is encrypted at rest in `llm_configurations`
  (authenticated HMAC-SHA256 envelope derived from `SECRET_KEY`) and is never
  returned by the API, written to logs, or included in prompts. Environment
  variables remain the fallback when no runtime row exists.
- **Terraform layer** (`terraform/*`): HCL writer, project materializer, CLI
  executor, plan parser, policy engine.
- **Artifact store** (`services/artifacts.py`): content-addressed blob storage
  with SHA-256, path-traversal-safe keys, optional Azure Blob backend.

### 3.2 Execution plane (`backend/app/terraform/executor.py`)

Every Terraform invocation:

1. Creates a per-run workspace under `data/workspaces/<project>/<run>`.
2. Materializes files from the DB **at the approved version** (never from an
   unsaved editor buffer).
3. Writes `.terraformrc`/backend config and a minimal environment: `TF_IN_AUTOMATION=1`,
   `TF_INPUT=0`, `TF_CLI_ARGS_*` cleared, no proxy credentials, no application secrets.
4. Runs the CLI with a hard timeout, capturing stdout/stderr to a log artifact.
5. Computes SHA-256 of `plan.tfplan`; that hash is the approval binding.
6. Records the workspace digest (all file hashes) alongside the run.

Production deployments run the same code inside the container image defined in
`deploy/Dockerfile`: non-root user, read-only root filesystem, dropped capabilities,
`no-new-privileges`, tmpfs for `/tmp` and a writable artifact/workspace volume. In this
release the control plane and executor run in one process/container; the executor is
already isolated behind `TerraformExecutor` and a per-run workspace, so extracting it into
a dedicated worker service or separate container is a deployment change, not a rewrite.
Network egress allow-listing and per-project container isolation are deployment controls
documented in the roadmap.

### 3.3 Data plane

- PostgreSQL for all metadata (see `docs/database.md`); SQLite for local development.
- Artifact store for uploads, generated projects, plans, logs, exports.
- In-process event bus and bounded job runner (thread pool) for validation, planning and
  apply. The interfaces are deliberately small so Redis/Celery can replace them for
  multi-node deployments.
- Terraform state never enters the application database or prompts. It lives in
  the configured Azure Storage backend, referenced by storage coordinates only.

## 4. Agent orchestration

```
User message / document
        │
        ▼
 RequirementAnalyst ──► requirements[] (typed, sourced, status)
        │                    │
        │ missing_info?      │
        ├──► Clarification questions (grouped, max 4 per turn)
        ▼
 AzureSolutionArchitect ──► ArchitectureSpec (resources, network, security,
        │                    HA/DR, cost, assumptions, decisions)
        ▼
 ArchitectureReviewer ──► recommendations[] (mandatory/optional, impacts)
        │
        ▼  user approves (explicit)  ◄── approval checkpoint 1
 TerraformEngineer ──► TerraformProject (dynamic modules, envs, docs)
        │
        ▼
 TerraformValidator ──► fmt/init/validate/policy findings
        │   fail ──► bounded self-correction (≤3) ──► regenerate
        ▼
 PlanAnalyst ──► plan summary + destructive-operation flags
        │
        ▼  user approves exact plan hash  ◄── approval checkpoint 2
 TerraformExecutor ──► apply saved.tfplan ──► logs/artifacts
        │
        ▼
 VerificationAgent ──► resource verification report ──► Completed
```

Each agent boundary is a versioned JSON schema (`backend/app/agents/schemas.py`).
The orchestrator validates every response; invalid output is retried, then the
step fails closed (no silent fallback to "success").

Model output is advisory. Deterministic code decides:

- whether requirements are sufficient to proceed,
- whether an architecture is valid (schema + referential + CIDR checks),
- whether Terraform passes static policy,
- whether an approval is valid,
- whether a deployment may execute.

## 5. Security architecture

### 5.1 Identity and authorization

- Mailbox-style users with JWT access tokens (HS256, rotating secret).
- Organization memberships with roles `owner`, `admin`, `member`, `viewer`.
- Every project query is scoped by organization membership; cross-org IDs
  return 404 (not 403) to avoid existence leaks.
- Approvals record `approved_by`; the AI service identity cannot create
  approvals (`Approval` has no agent code path).

### 5.2 Credential isolation

| Secret | Stored in | Never sent to |
|---|---|---|
| JWT signing key | env / Key Vault | frontend, logs |
| LLM API keys | env / Key Vault | frontend, agents, prompts |
| Azure client secret | env / Key Vault / worker secret mount | DB, API responses, logs |
| Terraform state | Azure Blob backend | DB, prompts, logs |
| Uploaded file contents | artifact store | no restriction (user data) |

Terraform subprocess env is built from an allow-list (`TF_*`, `ARM_*` required
for backend access). Values are redacted in logs by a regex scrubber that covers
bearer tokens, client secrets, connection strings, and SAS tokens before any log
line is persisted or streamed.

### 5.3 Untrusted inputs

- Uploads: extension allow-list, MIME sniffing, 25 MB limit, text extraction
  only (no macros, no active content). Extracted text is fenced and labelled as
  untrusted data in prompts; the system prompt states that document content can
  never change tools, policies, approvals, or scope.
- Generated HCL: treated as untrusted; static policy + `terraform validate` gate
  it. Providers pinned; external module sources restricted to an allow-list
  (registry modules for Azure only) and recorded.
- Plan artifacts: hash-bound to approval; staleness detected by re-planning only
  on explicit user action, never during apply.

### 5.4 Approval enforcement

`ApprovalService` is the only way to create approvals. It recomputes the
artifact hash server-side from stored content before recording. `DeploymentService`
re-verifies all of:

1. approval row `status == active` and `approval_type == deployment`,
2. `plan_hash == frozen plan artifact hash`,
3. plan belongs to the project's current Terraform source digest,
4. architecture version is still the approved/current one,
5. subscription and environment match the approval,
6. no newer plan or code digest exists.

If any check fails, the deployment is rejected with `409` and an audit event.

## 6. State machine

```
draft
 └─► gathering_requirements ─► architecture_ready ─► awaiting_architecture_approval
        │                                                  │ approved
        │                                                  ▼
        │                                     generating_terraform
        │                                                  │
        │                                                  ▼
        │                                             validating
        │                                        ┌─────────┴─────────┐
        │                                        ▼                   ▼
        │                               validation_failed      planning
        │                                        │ (retry ≤3)        │
        │                                        └───────────────────►│
        │                                                             ▼
        │                                              awaiting_deployment_approval
        │                                                             │ approved
        │                                                             ▼
        │                                                        deploying
        │                                       ┌──────────────┬──────┴──────┐
        │                                       ▼              ▼             ▼
        │                              deployment_failed  verification_failed  verifying
        │                                       │              │             │
        │                                       └── manual ────┴─────────────┘
        │                                                             ▼
        └──────────────────────────────────────────────────────── completed
```

Transitions are declared in `core/state_machine.py`; unknown transitions raise
`InvalidTransition` and are audited.

## 7. Data model summary

20 primary tables: `users`, `organizations`, `memberships`, `projects`,
`project_conversations`, `requirements`, `documents`, `architecture_versions`,
`architecture_decisions`, `terraform_projects`, `terraform_files`,
`validation_runs`, `terraform_plans`, `approvals`, `deployments`, `audit_events`,
`azure_subscriptions`, `agent_runs`, `project_locks`, `llm_configurations`. Full DDL in `docs/database.md`.

## 8. API surface

REST under `/api/v1`, JSON, bearer auth, cursor-free pagination (`limit/offset`).
Full endpoint list in `docs/api.md`, live OpenAPI at `/api/docs`.

Streaming: `GET /projects/{id}/events` (SSE) delivers agent progress, validation
progress, and deployment logs. Each event has `type`, `project_id`, `payload`,
`ts`.

## 9. Terraform code generation strategy

Deterministic writer (`terraform/writer.py`) + architecture-driven module
composition:

- One module per architectural capability present (never empty modules).
- `environments/{dev,staging,production}` each with own state key/backend.
- Root module wires modules, locals for naming/tags, validated variables.
- Docs: root README, per-module README, resource-to-module map, tfvars examples.
- The LLM may propose additional modules/files, but every file passes the same
  deterministic validator and policy gate before being shown as deployable.

Naming: `<prefix>-<env>-<purpose>` via locals, tags `project`, `environment`,
`managed_by=terraform`, `owner`, plus user-supplied tags.

## 10. Failure handling

| Failure | Behavior |
|---|---|
| LLM error/timeout | Retry ≤3 with backoff, then fail step; project status unchanged |
| Invalid structured output | Schema repair attempt, then fail closed |
| Terraform init/validate error | Capture logs, feed redacted stderr ≤3 correction rounds, then `validation_failed` with manual intervention |
| Plan error | `planning_failed`-equivalent status via deployment record; logs retained |
| Apply failure | `deployment_failed`, partial resources recorded from state/logs; never auto-destroy |
| Stale plan | 409 + audit; require new plan and approval |
| Concurrent apply | Per-project advisory lock (`project_locks`) + DB check |

## 11. Non-functional targets

- API p95 < 300 ms excluding LLM/terraform.
- Generated Terraform: `fmt -check` clean, `validate` clean, zero critical policy
  findings.
- All state transitions and approvals auditable and tamper-evident (append-only).
- Frontend: no secret ever enters browser storage; only server-redacted data.

## 12. Phased delivery (see `docs/roadmap.md`)

1. Foundation (auth, orgs, projects, conversations, LLM abstraction)
2. Requirement analysis + documents
3. Azure architecture agent + review + versioning + approval
4. Terraform generation + editor + ZIP export
5. Validation engine + policy + auto-correction
6. Azure integration: subscriptions, backend, plan
7. Deployment engine + verification + audit
8. Lifecycle: drift, modification, import
