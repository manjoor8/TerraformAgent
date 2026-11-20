# API Specification (v1)

Base URL: `/api/v1` · JSON · `Authorization: Bearer <jwt>` · OpenAPI at `/api/docs`.

Conventions:
- IDs are UUID strings. Timestamps ISO-8601 UTC.
- Errors: `{"detail": "message", "code": "optional_machine_code"}` with 400/401/403/404/409/422/500.
- List endpoints return `{"items": [...], "total": n}`.
- Mutating endpoints append audit events.

## Auth

| Method | Path | Body | Notes |
|---|---|---|---|
| POST | `/auth/register` | `{name,email,password,organization_name}` | First user of org is `owner`; returns token |
| POST | `/auth/login` | `{email,password}` | Returns `{access_token, token_type, user}` |
| GET | `/auth/me` | — | `{user, organizations:[{id,name,slug,role}]}` |
| POST | `/auth/logout` | — | Client discards token (stateless JWT) |

## Organizations, subscriptions

| Method | Path | Notes |
|---|---|---|
| GET | `/organizations` | memberships of caller |
| GET | `/organizations/{id}` | includes settings/counts |
| PATCH | `/organizations/{id}` | name/settings (admin+) |
| GET | `/subscriptions` | non-secret metadata only |
| POST | `/subscriptions` | `{tenant_id,subscription_id,subscription_name,auth_method,default_region}` |
| POST | `/subscriptions/{id}/verify` | live ARM check; updates `connection_status`,`permissions` |
| DELETE | `/subscriptions/{id}` | 409 if a project references it |

## Dashboard

`GET /dashboard/summary` →
```json
{"projects":4,"active_deployments":1,"successful_deployments":2,"failed_deployments":0,
 "pending_approvals":2,"subscriptions":1,"recent_activity":[{...}],
 "status_counts":{"gathering_requirements":2,"completed":1}}
```

## Projects

| Method | Path | Notes |
|---|---|---|
| GET | `/projects?status=&q=&limit=&offset=` | org-scoped |
| POST | `/projects` | `{name,description,environment,region,azure_subscription_id?}` |
| GET | `/projects/{id}` | project + counts + current versions |
| PATCH | `/projects/{id}` | name/description/environment/region/subscription/settings |
| DELETE | `/projects/{id}` | owner/admin; cascades |
| GET | `/projects/{id}/overview` | next actions, checklist, artifacts summary |
| GET | `/projects/{id}/export` | ZIP: terraform, architecture.json, diagram, README, audit |

Project object:
```json
{"id":"...","name":"Flight Prod","description":"...","status":"awaiting_architecture_approval",
 "environment":"production","region":"uaenorth","created_at":"...","updated_at":"...",
 "azure_subscription_id":null,"counts":{"documents":2,"requirements":9,"open_questions":3}}
```

## Conversation & requirements

| Method | Path | Notes |
|---|---|---|
| GET | `/projects/{id}/messages?limit=&before=` | chronological |
| POST | `/projects/{id}/messages` | `{content}` → runs orchestrator turn; returns `{message(user), reply(assistant), questions, requirements_added, status}` |
| POST | `/projects/{id}/chat/stream` | SSE variant of the above (events: `message`, `status`, `done`) |
| GET | `/projects/{id}/requirements` | filter `?status=` |
| POST | `/projects/{id}/requirements` | `{requirement_type,description,priority,source}` |
| PATCH | `/requirements/{id}` | `{description?,status?,priority?}`; edits require architecture re-approval |
| DELETE | `/requirements/{id}` | |
| GET | `/projects/{id}/questions` | open clarification questions |
| POST | `/projects/{id}/questions/{qid}/answer` | `{answer}` → recorded as confirmed requirement, question closed |

Question object:
```json
{"id":"q-1","category":"networking","question":"Should resources be private?",
 "why_it_matters":"...","options":["Private (recommended)","Public"],"recommended":"Private",
 "blocking":true,"status":"open"}
```

## Documents

| Method | Path | Body |
|---|---|---|
| POST | `/projects/{id}/documents` | multipart `file`; ≤25 MB; pdf/docx/doc/txt/md/xlsx/csv/json/yaml/yml/tf/hcl |
| GET | `/projects/{id}/documents` | |
| GET | `/documents/{id}` | metadata + extraction summary |
| GET | `/documents/{id}/download` | original bytes |
| POST | `/documents/{id}/extract` | re-run extraction |
| DELETE | `/documents/{id}` | |

Document response includes `extraction`:
```json
{"components":["App Service","Azure SQL"],"requirements":["..."],
 "constraints":["..."],"dependencies":["..."],"missing_information":["..."],
 "conflicts":["..."],"untrusted_instruction_detected":false,"pages":12,"word_count":840}
```

## Architecture

| Method | Path | Notes |
|---|---|---|
| POST | `/projects/{id}/architecture/generate` | `{instructions?, base_version_id?}` → new version |
| GET | `/projects/{id}/architecture` | current version (latest) |
| GET | `/projects/{id}/architecture/versions` | history + approval status |
| GET | `/architecture/{version_id}` | full spec + diagram |
| POST | `/architecture/{version_id}/review` | run reviewer; returns recommendations |
| PATCH | `/architecture/{version_id}/recommendations/{rec_id}` | `{status:"accepted"\\|"rejected", note?}` |
| POST | `/architecture/{version_id}/submit` | → `awaiting_architecture_approval` |
| POST | `/architecture/{version_id}/approve` | `{note?}` → active architecture approval |
| POST | `/architecture/{version_id}/reject` | `{reason}` |
| POST | `/architecture/{version_id}/request-changes` | `{feedback}` → regenerates v+1 |
| GET | `/architecture/{version_id}/diagram` | `{mermaid, drawio_xml?}` |
| GET | `/architecture/{version_id}/cost` | estimate: `{currency:"USD",monthly_total,lines:[{service,sku,quantity,unit,hourly,monthly,source}]}` |

ArchitectureSpec (JSON, versioned):
```json
{"summary":"...","region":"uaenorth","environments":["production"],
 "resources":[{"id":"appgw","type":"azurerm_application_gateway","name":"Application Gateway",
   "purpose":"HTTPS ingress","sku":"WAF_v2","private":false,"zone_redundant":true,
   "module":"networking","depends_on":["vnet"],"config":{}}],
 "networks":[{"id":"vnet","name":"vnet-flight-prod","address_space":["10.0.0.0/16"],
   "subnets":[{"name":"snet-app","cidr":"10.0.1.0/24","delegation":null,"purpose":"application"}]}],
 "security":{"managed_identity":true,"key_vault":true,"private_endpoints":true,
   "diagnostics":true,"rbac":[{"principal":"app","role":"Key Vault Secrets User","scope":"kv"}]},
 "availability":{"zones":true,"multi_region":false,"backup":"...","rpo":"1h","rto":"4h"},
 "cost":{"currency":"USD","monthly_total":0,"lines":[]},
 "recommendations":[{"id":"rec-1","category":"security","recommendation":"...",
   "current_limitation":"...","proposed_improvement":"...","technical_impact":"...",
   "security_impact":"...","availability_impact":"...","cost_impact":"...",
   "mandatory":true,"status":"proposed"}],
 "assumptions":["..."],"limitations":["..."],
 "decisions":[{"decision":"...","rationale":"...","source_requirement":"..."}]
}
```

## Terraform

| Method | Path | Notes |
|---|---|---|
| POST | `/projects/{id}/terraform/generate` | requires approved architecture; creates project vN |
| GET | `/projects/{id}/terraform` | current project, files, notes and source digest |
| GET | `/projects/{id}/terraform/versions` | |
| GET | `/terraform-projects/{id}` | metadata + files |
| GET | `/terraform-projects/{id}/files` | `{path,content,hash,version}` list |
| PUT | `/terraform-projects/{id}/files` | `{path, content}` → creates a new project version |
| POST | `/terraform-projects/{id}/files` | `{path, content}` → new file in a new version |
| DELETE | `/terraform-projects/{id}/files?path=` | creates a new version |
| GET | `/terraform-projects/{id}/download` | ZIP |
| GET | `/terraform-projects/{id}/module-map` | resource→module table |
| POST | `/terraform-projects/{id}/validate` | `{}` → `{validation_run_id, job_id}` (async) |
| POST | `/terraform-projects/{id}/repair` | `{}` → `{job_id, max_attempts:3}`; bounded AI repair (requires LLM) |
| GET | `/terraform-projects/{id}/validations` | history |
| GET | `/validation-runs/{id}` | status + results |
| GET | `/jobs/{id}` | background job status/error/result |

Validation result:
```json
{"status":"passed","attempt":1,
 "checks":[{"name":"terraform_fmt","status":"passed","duration_ms":900,"errors":0,"warnings":0,"details":"..."},
           {"name":"terraform_validate","status":"passed",...},
           {"name":"policy","status":"passed","findings":[]},
           {"name":"secret_scan","status":"passed"}],
 "summary":{"files":28,"modules":6,"errors":0,"warnings":0,"critical":0},
 "correction_rounds":0}
```

## Plans

| Method | Path | Notes |
|---|---|---|
| POST | `/projects/{id}/plan` | `{environment?}` async → `{plan_id, job_id}` |
| GET | `/projects/{id}/plans` | |
| GET | `/plans/{id}` | summary + destructive ops + analysis |
| GET | `/plans/{id}/download` | binary `plan.tfplan` |
| POST | `/plans/{id}/approve` | `{note?, confirm_destructive?}` → deployment approval bound to hash |
| POST | `/plans/{id}/reject` | `{reason}` |
| POST | `/plans/{id}/deploy` | `{}` → `{deployment_id, job_id}`; 409 if stale |

Plan summary:
```json
{"add":8,"change":0,"destroy":0,"replace":0,"total":8,
 "resources":[{"address":"azurerm_linux_virtual_machine.app[0]","type":"azurerm_linux_virtual_machine",
   "action":"create","module":".module.compute","reason":"New resource."}],
 "destructive_operations":[{"address":"...","action":"replace","reason":"...","severity":"high"}],
 "outputs":{"application_url":"..."},"narrative":"...",
 "plan_hash":"sha256:...","source_digest":"sha256:..."}
```

## Deployments

| Method | Path | Notes |
|---|---|---|
| GET | `/projects/{id}/deployments` | |
| GET | `/deployments/{id}` | status, summary, outputs (sensitive redacted) |
| GET | `/deployments/{id}/logs` | text/plain |
| POST | `/deployments/{id}/cancel` | safe cancellation |
| POST | `/deployments/{id}/verify` | Azure read-only verification |
| GET | `/deployments/{id}/verify` | latest verification report |

## Audit, events, system

| Method | Path | Notes |
|---|---|---|
| GET | `/projects/{id}/audit?limit=` | append-only events |
| GET | `/audit?action=&limit=` | organization audit |
| GET | `/projects/{id}/events?token=&since=` | SSE: `status`, `agent`, `log`, `validation`, `plan`, `deployment`, `job` |
| GET | `/dashboard/summary` | portfolio metrics and recent activity |
| GET | `/system/health` | liveness/readiness |
| GET | `/system/info` | provider, model, terraform version, features |
| GET | `/system/policies` | deterministic policy catalog and non-disableable rules |
| GET | `/system/llm` | platform admin only: LLM config, masked key metadata, effective provider |
| PUT | `/system/llm` | platform admin only: `{provider,model,base_url,api_key?}`; applied at runtime |
| POST | `/system/llm/test` | platform admin only: `{provider?,model?,base_url?,api_key?}` → live connectivity test |
| DELETE | `/system/llm` | platform admin only: remove runtime provider, fall back to environment |

## Approval rules (server-enforced)

Architecture approval requires: latest version, status `awaiting_architecture_approval`,
schema-valid architecture, review executed, no unresolved mandatory recommendation,
user role `member+`.

Deployment approval requires: plan status `generated`, plan not stale, source digest
matches current terraform project, architecture version approved, target subscription
configured, role `member+`, explicit `confirm: true` for destructive plans.

`POST /plans/{id}/deploy` re-verifies approval hash before executing; a mismatch is
`409 approval_invalidated` and no Terraform process is started.
