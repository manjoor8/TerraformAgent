import type {
  ArchitectureVersion,
  AuditEvent,
  ChatResponse,
  DashboardSummary,
  Deployment,
  JobStatus,
  LLMConfig,
  LLMTestResult,
  Message,
  PlanSummary,
  Project,
  ProjectDocument,
  ProjectOverview,
  Question,
  Requirement,
  Subscription,
  SystemInfo,
  TerraformFile,
  TerraformPlan,
  TerraformProject,
  User,
  ValidationRun,
} from "./types";

const API_BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? "/api/v1";

let token: string | null = localStorage.getItem("ta_token");
let organizationId: string | null = localStorage.getItem("ta_org");

export function setToken(value: string | null) {
  token = value;
  if (value) localStorage.setItem("ta_token", value);
  else localStorage.removeItem("ta_token");
}

export function getToken() {
  return token;
}

export function setOrganizationId(value: string | null) {
  organizationId = value;
  if (value) localStorage.setItem("ta_org", value);
  else localStorage.removeItem("ta_org");
}

export function getOrganizationId() {
  return organizationId;
}

export class ApiError extends Error {
  status: number;
  code: string;

  constructor(message: string, status: number, code = "request_failed") {
    super(message);
    this.status = status;
    this.code = code;
  }
}

function headers(extra?: HeadersInit): Headers {
  const result = new Headers(extra);
  if (token) result.set("Authorization", `Bearer ${token}`);
  if (organizationId) result.set("X-Organization-Id", organizationId);
  return result;
}

/** Headers for streaming requests (SSE via fetch). */
export function authHeaders(): Headers {
  return headers();
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const method = init.method ?? "GET";
  const reqHeaders = headers(init.headers);
  if (init.body && !(init.body instanceof FormData)) {
    reqHeaders.set("Content-Type", "application/json");
  }
  const response = await fetch(`${API_BASE}${path}`, { ...init, method, headers: reqHeaders });
  if (response.status === 401) {
    window.dispatchEvent(new Event("auth:expired"));
  }
  if (!response.ok) {
    let detail = `Request failed (${response.status})`;
    let code = "request_failed";
    try {
      const payload = await response.json();
      detail = payload.detail ?? detail;
      code = payload.code ?? code;
    } catch {
      /* non-JSON error */
    }
    throw new ApiError(detail, response.status, code);
  }
  if (response.status === 204) return undefined as T;
  const contentType = response.headers.get("content-type") ?? "";
  if (!contentType.includes("application/json")) {
    return (await response.text()) as unknown as T;
  }
  return (await response.json()) as T;
}

const json = (body: unknown) => JSON.stringify(body);

async function download(path: string, filename: string) {
  const response = await fetch(`${API_BASE}${path}`, { headers: headers() });
  if (!response.ok) throw new ApiError("Download failed", response.status);
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

export const api = {
  auth: {
    login: (email: string, password: string) =>
      request<{ access_token: string; user: User }>("/auth/login", {
        method: "POST",
        body: json({ email, password }),
      }),
    register: (payload: { name: string; email: string; password: string; organization_name: string }) =>
      request<{ access_token: string; user: User }>("/auth/register", {
        method: "POST",
        body: json(payload),
      }),
    me: () => request<User>("/auth/me"),
  },
  dashboard: () => request<DashboardSummary>("/dashboard/summary"),
  system: {
    info: () => request<SystemInfo>("/system/info"),
    policies: () => request<{ items: unknown[]; non_disableable: string[] }>("/system/policies"),
    llm: () => request<LLMConfig>("/system/llm"),
    updateLlm: (payload: Record<string, unknown>) =>
      request<LLMConfig>("/system/llm", { method: "PUT", body: json(payload) }),
    testLlm: (payload: Record<string, unknown>) =>
      request<LLMTestResult>("/system/llm/test", { method: "POST", body: json(payload) }),
    resetLlm: () => request<LLMConfig>("/system/llm", { method: "DELETE" }),
  },
  projects: {
    list: (params: { status?: string; q?: string } = {}) => {
      const search = new URLSearchParams();
      if (params.status) search.set("status", params.status);
      if (params.q) search.set("q", params.q);
      return request<{ items: Project[]; total: number; status_labels: Record<string, string> }>(
        `/projects?${search.toString()}`,
      );
    },
    create: (payload: Partial<Project>) =>
      request<Project>("/projects", { method: "POST", body: json(payload) }),
    get: (id: string) => request<Project>(`/projects/${id}`),
    update: (id: string, payload: Record<string, unknown>) =>
      request<Project>(`/projects/${id}`, { method: "PATCH", body: json(payload) }),
    remove: (id: string) => request<{ deleted: boolean }>(`/projects/${id}`, { method: "DELETE" }),
    overview: (id: string) => request<ProjectOverview>(`/projects/${id}/overview`),
    export: (id: string, filename: string) => download(`/projects/${id}/export`, filename),
  },
  messages: {
    list: (projectId: string) => request<{ items: Message[] }>(`/projects/${projectId}/messages`),
    send: (projectId: string, content: string) =>
      request<ChatResponse>(`/projects/${projectId}/messages`, {
        method: "POST",
        body: json({ content }),
      }),
  },
  questions: {
    list: (projectId: string) => request<{ items: Question[] }>(`/projects/${projectId}/questions`),
    answer: (projectId: string, questionId: string, answer: string) =>
      request<{ requirement: Requirement; open_questions: Question[] }>(
        `/projects/${projectId}/questions/${questionId}/answer`,
        { method: "POST", body: json({ answer }) },
      ),
  },
  requirements: {
    list: (projectId: string) =>
      request<{ items: Requirement[] }>(`/projects/${projectId}/requirements`),
    create: (projectId: string, payload: Record<string, unknown>) =>
      request<Requirement>(`/projects/${projectId}/requirements`, {
        method: "POST",
        body: json(payload),
      }),
    update: (id: string, payload: Record<string, unknown>) =>
      request<Requirement>(`/requirements/${id}`, { method: "PATCH", body: json(payload) }),
    remove: (id: string) =>
      request<{ deleted: boolean }>(`/requirements/${id}`, { method: "DELETE" }),
  },
  documents: {
    list: (projectId: string) =>
      request<{ items: ProjectDocument[] }>(`/projects/${projectId}/documents`),
    upload: (projectId: string, file: File) => {
      const form = new FormData();
      form.append("file", file);
      return request<ProjectDocument>(`/projects/${projectId}/documents`, {
        method: "POST",
        body: form,
      });
    },
    get: (id: string) => request<ProjectDocument>(`/documents/${id}`),
    reextract: (id: string) =>
      request<ProjectDocument>(`/documents/${id}/extract`, { method: "POST" }),
    remove: (id: string) =>
      request<{ deleted: boolean }>(`/documents/${id}`, { method: "DELETE" }),
    download: (id: string, filename: string) =>
      download(`/documents/${id}/download`, filename),
  },
  architecture: {
    current: (projectId: string) =>
      request<ArchitectureVersion>(`/projects/${projectId}/architecture`),
    versions: (projectId: string) =>
      request<{ items: ArchitectureVersion[] }>(`/projects/${projectId}/architecture/versions`),
    get: (versionId: string) => request<ArchitectureVersion>(`/architecture/${versionId}`),
    generate: (projectId: string, payload: { instructions?: string; base_version_id?: string }) =>
      request<ArchitectureVersion>(`/projects/${projectId}/architecture/generate`, {
        method: "POST",
        body: json(payload),
      }),
    review: (versionId: string) =>
      request<{ review: ArchitectureVersion["review"]; version: ArchitectureVersion }>(
        `/architecture/${versionId}/review`,
        { method: "POST" },
      ),
    updateRecommendation: (
      versionId: string,
      recommendationId: string,
      status: string,
      note?: string,
    ) =>
      request<{ recommendation: unknown; version: ArchitectureVersion }>(
        `/architecture/${versionId}/recommendations/${recommendationId}`,
        { method: "PATCH", body: json({ status, note }) },
      ),
    submit: (versionId: string) =>
      request<ArchitectureVersion>(`/architecture/${versionId}/submit`, { method: "POST" }),
    approve: (versionId: string, note = "") =>
      request<{ approval: { id: string; artifact_hash: string }; project_status: string }>(
        `/architecture/${versionId}/approve`,
        { method: "POST", body: json({ note }) },
      ),
    reject: (versionId: string, reason: string) =>
      request<{ version: ArchitectureVersion }>(`/architecture/${versionId}/reject`, {
        method: "POST",
        body: json({ reason }),
      }),
    requestChanges: (versionId: string, feedback: string) =>
      request<ArchitectureVersion>(`/architecture/${versionId}/request-changes`, {
        method: "POST",
        body: json({ feedback }),
      }),
    diagram: (versionId: string) =>
      request<{ format: string; mermaid: string }>(`/architecture/${versionId}/diagram`),
  },
  terraform: {
    current: (projectId: string) => request<TerraformProject>(`/projects/${projectId}/terraform`),
    versions: (projectId: string) =>
      request<{ items: TerraformProject[] }>(`/projects/${projectId}/terraform/versions`),
    generate: (projectId: string) =>
      request<TerraformProject>(`/projects/${projectId}/terraform/generate`, { method: "POST" }),
    get: (id: string) => request<TerraformProject>(`/terraform-projects/${id}`),
    updateFile: (id: string, path: string, content: string) =>
      request<TerraformProject>(`/terraform-projects/${id}/files`, {
        method: "PUT",
        body: json({ path, content }),
      }),
    createFile: (id: string, path: string, content: string) =>
      request<TerraformProject>(`/terraform-projects/${id}/files`, {
        method: "POST",
        body: json({ path, content }),
      }),
    deleteFile: (id: string, path: string) =>
      request<TerraformProject>(
        `/terraform-projects/${id}/files?path=${encodeURIComponent(path)}`,
        { method: "DELETE" },
      ),
    download: (id: string, filename: string) =>
      download(`/terraform-projects/${id}/download`, filename),
    validate: (id: string) =>
      request<{ validation_run_id: string; job_id: string }>(
        `/terraform-projects/${id}/validate`,
        { method: "POST" },
      ),
    repair: (id: string) =>
      request<{ job_id: string; max_attempts: number }>(
        `/terraform-projects/${id}/repair`,
        { method: "POST" },
      ),
    validations: (id: string) =>
      request<{ items: ValidationRun[] }>(`/terraform-projects/${id}/validations`),
    moduleMap: (id: string) =>
      request<{ items: { resource: string; terraform_type: string; module: string; file: string }[] }>(
        `/terraform-projects/${id}/module-map`,
      ),
  },
  validation: {
    get: (id: string) => request<ValidationRun>(`/validation-runs/${id}`),
  },
  plans: {
    list: (projectId: string) => request<{ items: TerraformPlan[] }>(`/projects/${projectId}/plans`),
    start: (projectId: string, environment?: string) =>
      request<{ plan_id: string; job_id: string }>(`/projects/${projectId}/plan`, {
        method: "POST",
        body: json({ environment }),
      }),
    get: (id: string) => request<TerraformPlan>(`/plans/${id}`),
    approve: (id: string, confirmDestructive = false, note = "") =>
      request<{ plan: TerraformPlan; project_status: string }>(`/plans/${id}/approve`, {
        method: "POST",
        body: json({ note, confirm_destructive: confirmDestructive }),
      }),
    reject: (id: string, reason: string) =>
      request<{ plan: TerraformPlan }>(`/plans/${id}/reject`, {
        method: "POST",
        body: json({ reason }),
      }),
    deploy: (id: string) =>
      request<{ deployment_id: string; job_id: string }>(`/plans/${id}/deploy`, {
        method: "POST",
        body: json({}),
      }),
    download: (id: string) => download(`/plans/${id}/download`, `${id}.tfplan`),
  },
  deployments: {
    list: (projectId: string) =>
      request<{ items: Deployment[] }>(`/projects/${projectId}/deployments`),
    get: (id: string) => request<Deployment>(`/deployments/${id}`),
    logs: async (id: string) => {
      const response = await fetch(`${API_BASE}/deployments/${id}/logs`, { headers: headers() });
      if (!response.ok) throw new ApiError("Could not load logs", response.status);
      return response.text();
    },
    cancel: (id: string) =>
      request<{ cancellation_requested: boolean }>(`/deployments/${id}/cancel`, {
        method: "POST",
      }),
    verify: (id: string) =>
      request<{ verification: Deployment["verification"] }>(`/deployments/${id}/verify`, {
        method: "POST",
      }),
  },
  audit: {
    project: (projectId: string, limit = 200) =>
      request<{ items: AuditEvent[] }>(`/projects/${projectId}/audit?limit=${limit}`),
    organization: (limit = 100) => request<{ items: AuditEvent[] }>(`/audit?limit=${limit}`),
  },
  subscriptions: {
    list: () => request<{ items: Subscription[] }>("/subscriptions"),
    create: (payload: Record<string, unknown>) =>
      request<Subscription>("/subscriptions", { method: "POST", body: json(payload) }),
    verify: (id: string) =>
      request<Subscription>(`/subscriptions/${id}/verify`, { method: "POST" }),
    remove: (id: string) =>
      request<{ deleted: boolean }>(`/subscriptions/${id}`, { method: "DELETE" }),
  },
  jobs: {
    get: (id: string) => request<JobStatus>(`/jobs/${id}`),
  },
};

export function apiErrorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error) return error.message;
  return String(error);
}

export async function waitForJob(
  jobId: string,
  onTick?: (status: JobStatus) => void,
  intervalMs = 1500,
  timeoutMs = 15 * 60 * 1000,
): Promise<JobStatus> {
  const started = Date.now();
  for (;;) {
    const status = await api.jobs.get(jobId);
    onTick?.(status);
    if (["succeeded", "failed", "cancelled"].includes(status.status)) return status;
    if (Date.now() - started > timeoutMs) {
      throw new ApiError("Timed out waiting for the job", 408, "job_timeout");
    }
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
}

export { API_BASE };
