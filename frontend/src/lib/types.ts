export interface Organization {
  id: string;
  name: string;
  slug: string;
  role: string;
}

export interface User {
  id: string;
  name: string;
  email: string;
  authentication_provider: string;
  is_platform_admin: boolean;
  created_at: string;
  organizations: Organization[];
}

export interface ProjectCounts {
  documents: number;
  requirements: number;
  open_questions: number;
  architecture_version: number | null;
  architecture_status: string | null;
  terraform_version: number | null;
  architecture_approvals: number;
}

export interface Project {
  id: string;
  organization_id: string;
  name: string;
  description: string;
  status: string;
  environment: string;
  region: string;
  azure_subscription_id: string | null;
  created_at: string;
  updated_at: string;
  counts: ProjectCounts;
  settings?: { backend?: BackendSettings; policies?: { disabled_rules?: string[] }; terraform_notes?: string[] };
  subscription?: {
    id: string;
    subscription_id: string;
    subscription_name: string;
    tenant_id: string;
    connection_status: string;
  };
}

export interface BackendSettings {
  mode: "local" | "azurerm";
  resource_group?: string;
  storage_account?: string;
  container?: string;
  key_prefix?: string;
}

export interface Message {
  id: string;
  role: "user" | "assistant" | "system" | "tool";
  message: string;
  metadata: Record<string, unknown>;
  created_at: string;
}

export interface Question {
  id: string;
  category: string;
  question: string;
  why_it_matters: string;
  options: string[];
  recommended?: string;
  blocking: boolean;
  status: "open" | "answered";
  answer?: string;
}

export interface Requirement {
  id: string;
  project_id: string;
  requirement_type: string;
  description: string;
  source: string;
  status: string;
  priority: string;
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface DocumentExtraction {
  components?: string[];
  requirements?: string[];
  constraints?: string[];
  dependencies?: string[];
  missing_information?: string[];
  conflicts?: string[];
  untrusted_instruction_detected?: boolean;
  word_count?: number;
  pages?: number | null;
  sheets?: string[];
  error?: string;
}

export interface ProjectDocument {
  id: string;
  project_id: string;
  filename: string;
  content_type: string;
  size_bytes: number;
  document_type: string;
  extraction_status: "pending" | "extracting" | "extracted" | "failed";
  extraction_error: string | null;
  content_hash: string;
  extraction: DocumentExtraction;
  created_at: string;
  has_text: boolean;
}

export interface ArchitectureResource {
  id: string;
  type: string;
  name: string;
  purpose: string;
  sku?: string | null;
  module: string;
  private: boolean;
  zone_redundant: boolean;
  depends_on: string[];
  config: Record<string, unknown>;
}

export interface SubnetSpec {
  name: string;
  cidr: string;
  purpose: string;
  delegation?: string | null;
  service_endpoints: string[];
  nsg?: string | null;
}

export interface NetworkSpec {
  id: string;
  name: string;
  address_space: string[];
  subnets: SubnetSpec[];
  nsg_rules: Record<string, unknown[]>;
  nat_gateway: boolean;
  private_dns_zones: string[];
  route_tables: unknown[];
}

export interface Recommendation {
  id: string;
  category: string;
  recommendation: string;
  current_limitation: string;
  proposed_improvement: string;
  technical_impact: string;
  security_impact: string;
  availability_impact: string;
  cost_impact: string;
  mandatory: boolean;
  status: "proposed" | "accepted" | "rejected";
  note?: string;
}

export interface CostLine {
  service: string;
  sku: string;
  quantity: number;
  unit: string;
  hourly: number;
  monthly: number;
  source: string;
}

export interface CostEstimate {
  currency: string;
  monthly_total: number;
  lines: CostLine[];
  disclaimer: string;
}

export interface ArchitectureSpec {
  summary: string;
  region: string;
  environments: string[];
  resources: ArchitectureResource[];
  networks: NetworkSpec[];
  security: {
    managed_identity: boolean;
    key_vault: boolean;
    private_endpoints: boolean;
    diagnostics: boolean;
    encryption: string;
    rbac: { principal: string; role: string; scope: string }[];
    notes: string[];
  };
  availability: {
    zones: boolean;
    multi_region: boolean;
    backup: string;
    rpo: string;
    rto: string;
    dr_strategy: string;
  };
  cost: CostEstimate;
  recommendations: Recommendation[];
  assumptions: string[];
  limitations: string[];
  decisions: {
    decision: string;
    rationale: string;
    source_requirement?: string | null;
    alternates_considered: string[];
  }[];
}

export interface ArchitectureReview {
  overall_assessment: string;
  score: number;
  recommendations: Recommendation[];
  risks: string[];
  reviewed_at?: string;
  inventory?: InventoryRow[];
}

export interface InventoryRow {
  component: string;
  azure_resource: string;
  terraform_type: string;
  name: string;
  configuration: string;
  module: string;
}

export interface ArchitectureVersion {
  id: string;
  project_id: string;
  version: number;
  summary: string;
  region: string;
  approval_status: string;
  created_at: string;
  approved_at: string | null;
  created_by_agent: string;
  resource_count: number;
  module_count: number;
  cost: CostEstimate;
  assumptions: string[];
  inventory: InventoryRow[];
  review: ArchitectureReview;
  architecture?: ArchitectureSpec;
  diagram?: string;
  decisions?: {
    id: string;
    decision: string;
    rationale: string;
    source_requirement?: string | null;
    alternates_considered: string[];
    status: string;
  }[];
  active_approval?: {
    id: string;
    artifact_hash: string;
    approved_by: string;
    approved_at: string;
  } | null;
  project_status?: string;
}

export interface TerraformFile {
  path: string;
  content: string;
  hash: string;
  version: number;
  updated_at: string;
}

export interface TerraformProject {
  id: string;
  project_id: string;
  architecture_version_id: string;
  version: number;
  source_digest: string;
  status: string;
  kind: string;
  module_map: { resource: string; terraform_type: string; module: string; file: string }[];
  created_at: string;
  file_count: number;
  files?: TerraformFile[];
  notes?: string[];
}

export interface PolicyFinding {
  rule_id: string;
  title: string;
  severity: string;
  message: string;
  file?: string | null;
  line?: number | null;
  resource?: string | null;
  remediation: string;
}

export interface ValidationCheck {
  name: string;
  status: string;
  duration_ms: number;
  errors: number;
  warnings: number;
  details: string;
  findings: PolicyFinding[];
}

export interface ValidationReport {
  status: string;
  checks: ValidationCheck[];
  summary: {
    files?: number;
    modules?: number;
    errors?: number;
    warnings?: number;
    critical?: number;
    failed_checks?: string[];
  };
  correction_rounds: number;
}

export interface ValidationRun {
  id: string;
  terraform_project_id: string;
  status: string;
  attempt: number;
  results: {
    report?: ValidationReport;
    source_digest?: string;
    files?: number;
    modules?: number;
    error?: string;
  };
  log_location?: string | null;
  created_at: string;
  completed_at?: string | null;
}

export interface PlanResourceChange {
  address: string;
  type: string;
  action: string;
  module: string;
  replacing: boolean;
  reason: string;
}

export interface DestructiveOperation {
  address: string;
  type: string;
  action: string;
  severity: string;
  reason: string;
}

export interface PlanSummary {
  add: number;
  change: number;
  destroy: number;
  replace: number;
  total: number;
  resources: PlanResourceChange[];
  destructive_operations: DestructiveOperation[];
  outputs: Record<string, unknown>;
  narrative: string;
  plan_hash: string;
  source_digest: string;
  terraform_version: string;
  stale: boolean;
}

export interface TerraformPlan {
  id: string;
  terraform_project_id: string;
  environment: string;
  subscription_id: string | null;
  plan_hash: string;
  source_digest: string;
  status: string;
  destructive: boolean;
  summary: PlanSummary;
  error: string | null;
  created_at: string;
  source_digest_current?: string;
  source_stale?: boolean;
  project_name?: string;
  terraform_version?: number;
  approval?: {
    id: string;
    artifact_hash: string;
    approved_at: string;
    approved_by: string;
  } | null;
}

export interface Deployment {
  id: string;
  project_id: string;
  terraform_plan_id: string;
  approval_id: string;
  subscription_id: string;
  environment: string;
  status: string;
  summary: Record<string, unknown>;
  outputs: Record<string, unknown>;
  verification: {
    status?: string;
    checked?: number;
    matched?: number;
    missing?: { type: string; state: string }[];
    notes?: string[];
    resources?: { type: string; state: string; count?: number }[];
    checked_at?: string;
  };
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
}

export interface AuditEvent {
  id: string;
  organization_id: string;
  project_id: string | null;
  user_id: string | null;
  action: string;
  metadata: Record<string, unknown>;
  timestamp: string;
}

export interface Subscription {
  id: string;
  organization_id: string;
  tenant_id: string;
  subscription_id: string;
  subscription_name: string;
  auth_method: string;
  connection_status: string;
  default_region: string;
  permissions: Record<string, unknown>;
  created_at: string;
}

export interface DashboardSummary {
  projects: number;
  active_deployments: number;
  successful_deployments: number;
  failed_deployments: number;
  pending_architecture_approvals: number;
  pending_deployment_approvals: number;
  pending_approvals: number;
  subscriptions: number;
  status_counts: Record<string, number>;
  recent_activity: AuditEvent[];
}

export interface ChatResponse {
  message: Message;
  reply: Message;
  questions: Question[];
  requirements_added: number;
  status: string;
  status_label?: string;
  architecture_version: number | null;
  terraform_version: number | null;
  actions: string[];
  readiness_score: number;
  conflicts: string[];
}

export interface ProjectOverview {
  project: Project;
  status_label: string;
  checklist: { key: string; label: string; done: boolean }[];
  next_action: { key: string; label: string; done: boolean } | null;
  open_questions: Question[];
  latest_architecture: ArchitectureVersion | null;
  latest_terraform: TerraformProject | null;
  latest_plan: TerraformPlan | null;
  validation_passed: boolean;
}

export interface SystemInfo {
  app: string;
  version: string;
  environment: string;
  llm: {
    configured_provider: string;
    active_provider: string;
    model: string;
    available: boolean;
    mode: string;
    source: string;
    hint: string;
  };
  terraform: { terraform_version: string | null; available: boolean; binary: string | null };
  database: string;
  features: Record<string, boolean>;
  policy_rules: { id: string; title: string; severity: string; can_disable: boolean }[];
}

export interface LLMPreset {
  label: string;
  model: string;
  base_url: string;
  key_hint: string;
}

export interface LLMConfig {
  configured: boolean;
  provider: string;
  model: string;
  base_url: string;
  has_api_key: boolean;
  api_key_last4: string | null;
  enabled: boolean;
  source: string;
  updated_at: string | null;
  effective: SystemInfo["llm"];
  presets: Record<string, LLMPreset>;
  env_provider: string;
}

export interface LLMTestResult {
  ok: boolean;
  provider: string;
  model?: string;
  latency_ms?: number;
  message?: string;
  preview?: string;
  error?: string;
}

export interface JobStatus {
  id: string;
  project_id: string;
  kind: string;
  status: string;
  error: string | null;
  result: Record<string, unknown> | null;
  created_at: string;
}
