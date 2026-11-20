# Implementation roadmap

The application is delivered in vertical slices; each phase leaves the system runnable and the
end-to-end workflow testable. Status reflects this repository.

## Phase 1 — Application foundation — **done**

- FastAPI control plane, SQLAlchemy models, Alembic baseline, SQLite/PostgreSQL parity.
- JWT auth, organizations, memberships, roles, project isolation.
- React + TypeScript + Vite + Tailwind application shell, dashboard, authentication.
- Provider-agnostic LLM layer with strict Pydantic structured outputs and bounded retries.
- Persistent conversations, agent-run records and append-only audit events.
- In-process event bus and SSE project stream.

## Phase 2 — Requirement analysis — **done**

- Dynamic clarification questions (max 4 per turn) with recommendations and rationale.
- Typed requirements, sources, priorities and statuses; deduplication.
- Document upload/validation/extraction: PDF, DOCX, TXT/MD, XLSX, CSV, JSON, YAML, TF/HCL.
- Component, requirement, constraint and dependency extraction.
- Conflict detection, missing-information tracking, prompt-injection detection.
- Explicit assumptions; no Terraform before sufficient understanding.

## Phase 3 — Azure architecture — **done**

- Deterministic Azure Solution Architect plus optional LLM path, validated by the same schema.
- Compute (VM / App Service / AKS), networking, NSGs, private endpoints/DNS, NAT.
- Azure SQL / PostgreSQL / MySQL, storage, Key Vault, Log Analytics, App Insights.
- Availability zones, backup/RPO/RTO, DR strategy, cost estimate (catalog + optional retail API).
- Versioned architecture, Mermaid diagram, inventory table, decisions and rationale.
- Reviewer with mandatory/optional recommendations and accept/reject decisions.
- Formal architecture approval bound to the canonical architecture hash.

## Phase 4 — Terraform generation — **done**

- Architecture-driven dynamic module composition; no empty modules.
- AzureRM 4.x pinning, environment directory, backend configuration, tfvars and scripts.
- Per-module README, root README, resource→module map, tfvars examples.
- Monaco file explorer/editor; edits create immutable versions and invalidate approvals.
- ZIP export; project-level export (architecture + Terraform + audit).
- `terraform fmt` normalization before persistence.

## Phase 5 — Validation engine — **done**

- Real CLI pipeline: `terraform fmt -check -recursive`, `init -backend=false`, `validate -json`.
- Built-in deterministic policy engine with severity classification and non-disableable critical rules.
- Secret scanning; provider version/lock check.
- Optional TFLint and Checkov execution when installed.
- Validation history, findings, raw logs persisted as artifacts.
- Bounded AI repair workflow (≤3 attempts): model proposes patches to existing `.tf` files only;
  deterministic validation/policy gate every attempt; repairs create new immutable versions.
- Fail-closed behavior: critical/high findings block planning.

## Phase 6 — Azure integration & planning — **done**

- Subscription metadata connections; ARM verification and permission summary.
- Service principal, OIDC, managed identity and Azure CLI token acquisition (read-only uses).
- Remote backend configuration (Azure Blob) with per-project/per-environment keys.
- Real `terraform plan` execution in a constrained worker process.
- JSON plan parsing, destructive-operation classification and hash capture.
- Plan explanation through the Plan Analyst agent.

## Phase 7 — Approval-gated deployment — **done**

- Deployment approval bound to plan hash, source digest, architecture version, subscription
  and environment; destructive plans require explicit confirmation.
- Apply executes the exact saved binary plan; SHA-256 verified before execution.
- Staleness preflight re-plans (`-refresh=false`) and refuses a changed change-set.
- Per-project execution lock, cancellation, streamed logs, artifact storage.
- Post-apply state parsing, outputs (sensitive masked), ARM verification report.
- Deployment states: queued, running, succeeded, failed, cancelled.

## Phase 8 — Lifecycle management — **partial**

Delivered:

- Natural-language architecture changes generate new versions and invalidate approvals.
- Manual Terraform edits, version history and re-validation.
- Multi-subscription storage per organization; project target subscription switching.
- Audit trail, project export and deployment history.

Designed but not exposed yet:

- Scheduled drift detection (`terraform plan` on a schedule with a diff report).
- Terraform import block generation for existing resources.
- State bootstrap workflow that creates the state storage account after explicit approval.
- Multi-environment generation in one project (dev/staging/prod directories with per-env SKUs).
- AWS/GCP providers (the architecture agent, module registry and executor are provider-scoped).

## Extension points

| Extension | Where |
|---|---|
| New LLM provider | `backend/app/llm/providers.py` + factory |
| New Azure module | `backend/app/terraform/templates_*.py` + generator mapping |
| New policy | `backend/app/terraform/policy.py` |
| New document format | `backend/app/services/documents.py` |
| Distributed jobs/Redis | Replace `backend/app/services/jobs.py` implementation; interface stays |
| Object storage (Azure Blob) | Replace `backend/app/services/artifacts.py` implementation |
| Drift/import | Add services using `TerraformExecutor` and the plan parser |

## Deliberate non-goals for v1

- No direct Azure write SDK path; Terraform remains the only mutation mechanism.
- No automatic `terraform destroy` rollback.
- No AI-created approvals or AI-triggered applies.
- No raw Terraform state in the database, logs or prompts.
