# Database Design (PostgreSQL)

All metadata lives in PostgreSQL. Artifacts (uploads, generated projects, binary
plans, logs) live in the artifact store; the database only stores coordinates and
hashes. Terraform state never enters these tables.

Conventions: UUID primary keys (`gen_random_uuid()`), `timestamptz` timestamps,
`jsonb` for structured payloads, foreign keys with `ON DELETE CASCADE` except
audit rows which are append-only (`ON DELETE RESTRICT`).

## Entity-relationship overview

```
organizations 1─* memberships *─1 users
organizations 1─* projects 1─* conversations
                          1─* requirements
                          1─* documents
                          1─* architecture_versions 1─* architecture_decisions
                          1─* terraform_projects   1─* terraform_files
                                                   1─* validation_runs
                                                   1─* terraform_plans
                          1─* approvals
                          1─* deployments
                          1─* audit_events
                          1─* agent_runs
organizations 1─* azure_subscriptions
```

## DDL

```sql
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE users (
  id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name                  text NOT NULL,
  email                 citext UNIQUE NOT NULL,
  password_hash         text,                     -- null for OIDC users
  authentication_provider text NOT NULL DEFAULT 'local',
  created_at            timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE organizations (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name       text NOT NULL,
  slug       text UNIQUE NOT NULL,
  settings   jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE memberships (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  user_id         uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  role            text NOT NULL CHECK (role IN ('owner','admin','member','viewer')),
  created_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (organization_id, user_id)
);

CREATE TABLE azure_subscriptions (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id   uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  tenant_id         text NOT NULL,
  subscription_id   text NOT NULL,
  subscription_name text NOT NULL,
  auth_method       text NOT NULL CHECK (auth_method IN ('managed_identity','oidc','service_principal','azure_cli')),
  connection_status text NOT NULL DEFAULT 'unverified',
  default_region    text NOT NULL DEFAULT 'eastus',
  permissions       jsonb NOT NULL DEFAULT '{}'::jsonb,
  -- secret material is NEVER stored here; only a Key Vault reference
  credential_ref    text,
  created_at        timestamptz NOT NULL DEFAULT now(),
  UNIQUE (organization_id, subscription_id)
);

CREATE TABLE projects (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  name            text NOT NULL,
  description     text NOT NULL DEFAULT '',
  status          text NOT NULL DEFAULT 'draft',
  environment     text NOT NULL DEFAULT 'dev',
  region          text NOT NULL DEFAULT 'eastus',
  azure_subscription_id uuid REFERENCES azure_subscriptions(id) ON DELETE SET NULL,
  created_by      uuid REFERENCES users(id) ON DELETE SET NULL,
  settings        jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX projects_org_status_idx ON projects (organization_id, status);
CREATE INDEX projects_updated_idx ON projects (updated_at DESC);

CREATE TABLE project_conversations (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  role       text NOT NULL CHECK (role IN ('user','assistant','system','tool')),
  message    text NOT NULL,
  metadata   jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX conversations_project_created_idx ON project_conversations (project_id, created_at);

CREATE TABLE requirements (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id       uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  requirement_type text NOT NULL,          -- compute|network|database|storage|security|availability|cost|governance|other
  description      text NOT NULL,
  source           text NOT NULL DEFAULT 'conversation', -- conversation|document|assumption|user
  source_document_id uuid REFERENCES documents(id) ON DELETE SET NULL,
  status           text NOT NULL DEFAULT 'proposed', -- proposed|confirmed|assumed|rejected
  priority         text NOT NULL DEFAULT 'mandatory', -- mandatory|optional|assumption
  metadata         jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at       timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX requirements_project_idx ON requirements (project_id);

CREATE TABLE documents (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id        uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  filename          text NOT NULL,
  content_type      text NOT NULL,
  size_bytes        bigint NOT NULL,
  storage_location  text NOT NULL,          -- artifact-store key
  content_hash      text NOT NULL,
  document_type     text NOT NULL DEFAULT 'other',
  extraction_status text NOT NULL DEFAULT 'pending', -- pending|extracting|extracted|failed
  extraction_error  text,
  extracted_text    text,
  extraction        jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at        timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE architecture_versions (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id        uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  version           integer NOT NULL,
  architecture_json jsonb NOT NULL,
  diagram_source    text NOT NULL DEFAULT '',
  cost_estimate     jsonb NOT NULL DEFAULT '{}'::jsonb,
  summary           text NOT NULL DEFAULT '',
  assumptions       jsonb NOT NULL DEFAULT '[]'::jsonb,
  review            jsonb NOT NULL DEFAULT '{}'::jsonb, -- assessment, score, recommendations, inventory
  approval_status   text NOT NULL DEFAULT 'draft', -- draft|awaiting_approval|approved|rejected|superseded
  created_by_agent  text NOT NULL DEFAULT 'azure_solution_architect',
  created_at        timestamptz NOT NULL DEFAULT now(),
  approved_at       timestamptz,
  UNIQUE (project_id, version)
);

CREATE TABLE architecture_decisions (
  id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  architecture_version_id uuid NOT NULL REFERENCES architecture_versions(id) ON DELETE CASCADE,
  decision               text NOT NULL,
  rationale              text NOT NULL,
  source_requirement     text,
  alternates_considered  jsonb NOT NULL DEFAULT '[]'::jsonb,
  status                 text NOT NULL DEFAULT 'accepted',
  created_at             timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE terraform_projects (
  id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id             uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  architecture_version_id uuid NOT NULL REFERENCES architecture_versions(id) ON DELETE RESTRICT,
  version                integer NOT NULL,
  source_digest          text NOT NULL,       -- sha256 over sorted (path, content)
  repository_location    text NOT NULL DEFAULT '', -- artifact key of zip
  status                 text NOT NULL DEFAULT 'generated',
  kind                   text NOT NULL DEFAULT 'main',
  module_map             jsonb NOT NULL DEFAULT '[]'::jsonb,
  created_at             timestamptz NOT NULL DEFAULT now(),
  UNIQUE (project_id, version)
);

CREATE TABLE terraform_files (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  terraform_project_id uuid NOT NULL REFERENCES terraform_projects(id) ON DELETE CASCADE,
  file_path            text NOT NULL,
  file_content         text NOT NULL,
  content_hash         text NOT NULL,
  version              integer NOT NULL DEFAULT 1,
  updated_at           timestamptz NOT NULL DEFAULT now(),
  UNIQUE (terraform_project_id, file_path)
);

CREATE TABLE validation_runs (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  terraform_project_id uuid NOT NULL REFERENCES terraform_projects(id) ON DELETE CASCADE,
  status               text NOT NULL,        -- queued|running|passed|failed|error
  results              jsonb NOT NULL DEFAULT '{}'::jsonb,
  log_location         text,
  attempt              integer NOT NULL DEFAULT 1,
  created_at           timestamptz NOT NULL DEFAULT now(),
  completed_at         timestamptz
);

CREATE TABLE terraform_plans (
  id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  terraform_project_id   uuid NOT NULL REFERENCES terraform_projects(id) ON DELETE CASCADE,
  environment            text NOT NULL,
  subscription_id        text,
  plan_artifact_location text NOT NULL,
  plan_json_location     text NOT NULL,
  plan_hash              text NOT NULL,
  source_digest          text NOT NULL,
  plan_summary           jsonb NOT NULL DEFAULT '{}'::jsonb,
  destructive            boolean NOT NULL DEFAULT false,
  status                 text NOT NULL DEFAULT 'generated', -- generating|generated|approved|rejected|stale|applied|failed
  error                  text,
  created_at             timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE approvals (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id      uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  approval_type   text NOT NULL CHECK (approval_type IN ('architecture','deployment')),
  artifact_hash   text NOT NULL,
  architecture_version_id uuid REFERENCES architecture_versions(id) ON DELETE SET NULL,
  terraform_plan_id uuid REFERENCES terraform_plans(id) ON DELETE SET NULL,
  approved_by     uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
  approved_at     timestamptz NOT NULL DEFAULT now(),
  status          text NOT NULL DEFAULT 'active', -- active|invalidated|revoked
  invalidated_reason text
);

CREATE TABLE deployments (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id         uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  terraform_plan_id  uuid NOT NULL REFERENCES terraform_plans(id) ON DELETE RESTRICT,
  approval_id        uuid NOT NULL REFERENCES approvals(id) ON DELETE RESTRICT,
  subscription_id    text NOT NULL,
  environment        text NOT NULL,
  status             text NOT NULL,          -- queued|running|succeeded|failed|cancelled
  execution_logs     text NOT NULL DEFAULT '',
  log_location       text,
  outputs            jsonb NOT NULL DEFAULT '{}'::jsonb,
  summary            jsonb NOT NULL DEFAULT '{}'::jsonb,
  verification       jsonb NOT NULL DEFAULT '{}'::jsonb,
  started_at         timestamptz,
  completed_at       timestamptz,
  created_at         timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE audit_events (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
  project_id      uuid REFERENCES projects(id) ON DELETE SET NULL,
  user_id         uuid REFERENCES users(id) ON DELETE RESTRICT,
  action          text NOT NULL,
  metadata        jsonb NOT NULL DEFAULT '{}'::jsonb,
  timestamp       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX audit_project_ts_idx ON audit_events (project_id, timestamp DESC);
-- Append-only: revoke UPDATE/DELETE from the application role.
REVOKE UPDATE, DELETE ON audit_events FROM terraformagent_app;

CREATE TABLE agent_runs (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  agent      text NOT NULL,
  status     text NOT NULL DEFAULT 'running',
  input_summary text NOT NULL DEFAULT '',
  output     jsonb NOT NULL DEFAULT '{}'::jsonb,
  error      text,
  provider   text,
  model      text,
  latency_ms integer,
  created_at timestamptz NOT NULL DEFAULT now(),
  completed_at timestamptz
);

CREATE TABLE project_locks (
  project_id uuid PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
  reason     text NOT NULL,
  locked_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE llm_configurations (
  id                integer PRIMARY KEY,            -- single row (id = 1)
  provider          text NOT NULL DEFAULT 'deepseek', -- deepseek|openai_compatible|azure_openai|offline
  model             text NOT NULL DEFAULT 'deepseek-chat',
  base_url          text NOT NULL DEFAULT 'https://api.deepseek.com/v1',
  api_key_encrypted text,                           -- authenticated HMAC-SHA256 envelope
  api_key_last4     text,
  enabled           boolean NOT NULL DEFAULT true,
  updated_at        timestamptz NOT NULL DEFAULT now(),
  updated_by        uuid REFERENCES users(id) ON DELETE SET NULL
);
```

## SQLite development parity

The app supports `DATABASE_URL=sqlite:///./data/terraformagent.db` for local
development. SQLite has no `citext`/`jsonb`; models use portable `String` and
`JSON` types, and enums are stored as strings with CHECK-like validation in
Pydantic. Production and tests-with-Postgres use the DDL above (Alembic revision
`0001`).

## Important invariants

- `terraform_files` are immutable per Terraform project version; edits create a
  new `terraform_projects` row with a new `source_digest`.
- `approvals.artifact_hash` for architecture = SHA-256 of canonical
  `architecture_json`; for deployment = `terraform_plans.plan_hash`.
- An active approval is invalidated when its artifact hash, architecture version,
  plan, subscription, environment, or source digest changes.
- `audit_events` has no update/delete path in the application.
