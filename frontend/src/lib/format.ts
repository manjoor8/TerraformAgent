export function formatDate(value?: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function timeAgo(value?: string | null): string {
  if (!value) return "—";
  const date = new Date(value).getTime();
  if (Number.isNaN(date)) return value;
  const seconds = Math.max(1, Math.floor((Date.now() - date) / 1000));
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days}d ago`;
  return formatDate(value);
}

export function formatMoney(value?: number | null, currency = "USD"): string {
  if (value === undefined || value === null) return "—";
  return new Intl.NumberFormat(undefined, {
    style: "currency",
    currency,
    maximumFractionDigits: 0,
  }).format(value);
}

export function formatBytes(bytes: number): string {
  if (!bytes) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const index = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)));
  return `${(bytes / 1024 ** index).toFixed(index === 0 ? 0 : 1)} ${units[index]}`;
}

export function shortHash(hash?: string | null, length = 12): string {
  if (!hash) return "—";
  const value = hash.replace(/^sha256:/, "");
  return value.slice(0, length);
}

export const STATUS_LABELS: Record<string, string> = {
  draft: "Draft",
  gathering_requirements: "Gathering Requirements",
  architecture_ready: "Architecture Ready",
  awaiting_architecture_approval: "Awaiting Architecture Approval",
  architecture_approved: "Architecture Approved",
  generating_terraform: "Generating Terraform",
  validating: "Validating",
  validated: "Validated",
  validation_failed: "Validation Failed",
  planning: "Planning",
  planning_failed: "Planning Failed",
  awaiting_deployment_approval: "Awaiting Deployment Approval",
  deploying: "Deploying",
  deployment_failed: "Deployment Failed",
  deployment_successful: "Deployment Successful",
  verification_failed: "Verification Failed",
  completed: "Completed",
  passed: "Passed",
  failed: "Failed",
  skipped: "Skipped",
  error: "Error",
  running: "Running",
  queued: "Queued",
  succeeded: "Succeeded",
  cancelled: "Cancelled",
  generated: "Generated",
  approved: "Approved",
  rejected: "Rejected",
  stale: "Stale",
  applied: "Applied",
  draft_approval: "Draft",
  awaiting_approval: "Awaiting Approval",
  superseded: "Superseded",
  pending: "Pending",
  extracting: "Extracting",
  extracted: "Extracted",
  verified: "Verified",
  incomplete: "Incomplete",
  unsupported: "Unsupported",
  read_only: "Read-only",
  unverified: "Unverified",
  active: "Active",
};

export function statusLabel(status?: string | null): string {
  if (!status) return "Unknown";
  return STATUS_LABELS[status] ?? status.replaceAll("_", " ");
}

export type Tone = "neutral" | "good" | "warn" | "bad" | "info" | "violet";

export function statusTone(status?: string | null): Tone {
  if (!status) return "neutral";
  if (["completed", "passed", "succeeded", "verified", "active", "approved", "applied", "validated", "deployment_successful"].includes(status))
    return "good";
  if (["failed", "validation_failed", "deployment_failed", "verification_failed", "planning_failed", "error", "rejected", "stale"].includes(status))
    return "bad";
  if (["awaiting_architecture_approval", "awaiting_deployment_approval", "awaiting_approval", "planning", "deploying", "validating", "extracting", "running"].includes(status))
    return "warn";
  if (["architecture_approved", "generated", "validated"].includes(status)) return "info";
  if (["draft", "gathering_requirements"].includes(status)) return "neutral";
  return "neutral";
}

export const SEVERITY_TONE: Record<string, Tone> = {
  critical: "bad",
  high: "bad",
  medium: "warn",
  low: "info",
  info: "neutral",
};

export function environmentLabel(environment: string): string {
  return { dev: "Development", staging: "Staging", production: "Production" }[environment] ?? environment;
}

export function titleCase(value: string): string {
  return value
    .replaceAll("_", " ")
    .replaceAll("-", " ")
    .replace(/\b\w/g, (char) => char.toUpperCase());
}
