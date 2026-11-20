import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  ArrowRight,
  CheckCircle2,
  Cloud,
  Cpu,
  FolderKanban,
  Plus,
  Rocket,
  ShieldCheck,
  Sparkles,
  TriangleAlert,
  XCircle,
} from "lucide-react";
import clsx from "clsx";
import { api } from "../lib/api";
import { formatDate, statusLabel, statusTone, timeAgo } from "../lib/format";
import { StatusBadge } from "../components/StatusBadge";
import { CreateProjectModal } from "../components/CreateProjectModal";
import { EmptyState, LoadingBlock, PageHeader, SectionTitle } from "../components/UI";

const STATUS_BAR: Record<string, string> = {
  good: "bg-emerald-400",
  warn: "bg-amber-400",
  bad: "bg-rose-400",
  info: "bg-cyan-400",
  neutral: "bg-slate-500",
  violet: "bg-violet-400",
};

function StatCard({
  label,
  value,
  icon: Icon,
  tone,
  hint,
}: {
  label: string;
  value: number | string;
  icon: typeof FolderKanban;
  tone: string;
  hint?: string;
}) {
  return (
    <div className="card relative overflow-hidden p-4">
      <div className="flex items-start justify-between">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-[0.1em] text-faint">{label}</p>
          <p className="mt-2 text-3xl font-semibold tabular-nums tracking-tight text-ink">{value}</p>
          {hint && <p className="mt-1 text-xs text-muted">{hint}</p>}
        </div>
        <div className={clsx("grid h-9 w-9 place-items-center rounded-lg border", tone)}>
          <Icon size={17} />
        </div>
      </div>
    </div>
  );
}

const WORKFLOW = ["Requirements", "Architecture", "Review", "Terraform", "Validate", "Plan", "Deploy"];

export function DashboardPage() {
  const [createOpen, setCreateOpen] = useState(false);
  const summary = useQuery({ queryKey: ["dashboard"], queryFn: api.dashboard });
  const projects = useQuery({ queryKey: ["projects", "recent"], queryFn: () => api.projects.list() });
  const system = useQuery({ queryKey: ["system"], queryFn: api.system.info, staleTime: 60_000 });

  if (summary.isLoading || projects.isLoading) return <LoadingBlock label="Loading control plane…" />;

  const data = summary.data;
  const items = projects.data?.items ?? [];
  const statusEntries = Object.entries(data?.status_counts ?? {}).sort((a, b) => b[1] - a[1]);
  const totalStatuses = statusEntries.reduce((sum, [, count]) => sum + count, 0) || 1;
  const warnings: { tone: "warn" | "bad" | "info"; text: string; action?: { label: string; to: string } }[] = [];

  if (system.data && !system.data.terraform.available) {
    warnings.push({
      tone: "bad",
      text: "Terraform CLI is not available on the worker. Validation and plans cannot run.",
      action: { label: "System", to: "/system" },
    });
  }
  if (data && data.subscriptions === 0) {
    warnings.push({
      tone: "warn",
      text: "No Azure subscription is connected. Plans and deployments are blocked until you add one.",
      action: { label: "Connect", to: "/subscriptions" },
    });
  }
  if (system.data && !system.data.llm.available) {
    warnings.push({
      tone: "info",
      text: "No LLM provider configured. The deterministic architect is active — output is schema-valid but rule-based.",
      action: { label: "Details", to: "/system" },
    });
  }

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Control plane"
        title="Infrastructure dashboard"
        description="Projects, approvals and deployment activity across the organization."
        actions={
          <button className="btn btn-primary" onClick={() => setCreateOpen(true)}>
            <Plus size={15} /> New project
          </button>
        }
      />

      {warnings.length > 0 && (
        <div className="space-y-2">
          {warnings.map((warning) => (
            <div
              key={warning.text}
              className={clsx(
                "flex flex-wrap items-center gap-2 rounded-xl border px-3.5 py-2.5 text-sm",
                warning.tone === "bad"
                  ? "border-rose-500/30 bg-rose-500/5 text-rose-200"
                  : warning.tone === "warn"
                    ? "border-amber-500/30 bg-amber-500/5 text-amber-100"
                    : "border-cyan-500/25 bg-cyan-500/5 text-cyan-100",
              )}
            >
              {warning.tone === "bad" ? <XCircle size={15} /> : <TriangleAlert size={15} />}
              <span className="flex-1">{warning.text}</span>
              {warning.action && (
                <Link className="btn btn-ghost btn-sm" to={warning.action.to}>
                  {warning.action.label} <ArrowRight size={13} />
                </Link>
              )}
            </div>
          ))}
        </div>
      )}

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard
          label="Projects"
          value={data?.projects ?? 0}
          icon={FolderKanban}
          tone="border-cyan-500/30 bg-cyan-500/10 text-cyan-300"
          hint={`${statusEntries.filter(([status]) => status === "completed").map(([, count]) => count)[0] ?? 0} completed`}
        />
        <StatCard
          label="Active deployments"
          value={data?.active_deployments ?? 0}
          icon={Activity}
          tone="border-amber-500/30 bg-amber-500/10 text-amber-300"
          hint={`${data?.successful_deployments ?? 0} succeeded · ${data?.failed_deployments ?? 0} failed`}
        />
        <StatCard
          label="Pending approvals"
          value={data?.pending_approvals ?? 0}
          icon={ShieldCheck}
          tone="border-violet-500/30 bg-violet-500/10 text-violet-300"
          hint={`${data?.pending_architecture_approvals ?? 0} architecture · ${data?.pending_deployment_approvals ?? 0} plan`}
        />
        <StatCard
          label="Azure subscriptions"
          value={data?.subscriptions ?? 0}
          icon={Cloud}
          tone="border-emerald-500/30 bg-emerald-500/10 text-emerald-300"
          hint="Credentials stay outside the model"
        />
      </div>

      <div className="grid gap-4 lg:grid-cols-[1.55fr_1fr]">
        <section className="card p-4">
          <SectionTitle
            title="Projects"
            description="Open a workspace to continue discovery, design or deployment."
            actions={
              <Link className="btn btn-ghost btn-sm" to="/projects">
                View all <ArrowRight size={13} />
              </Link>
            }
          />
          {items.length === 0 ? (
            <EmptyState
              icon={<Sparkles size={26} />}
              title="No infrastructure projects yet"
              description="Describe an Azure workload in English or upload the architecture document. The agent will ask the questions that change the design."
              action={
                <button className="btn btn-primary" onClick={() => setCreateOpen(true)}>
                  <Plus size={15} /> Create the first project
                </button>
              }
            />
          ) : (
            <div className="grid gap-2.5 sm:grid-cols-2">
              {items.slice(0, 6).map((project) => (
                <Link
                  key={project.id}
                  to={`/projects/${project.id}`}
                  className="group rounded-xl border border-line bg-surface-2/40 p-3.5 transition hover:border-line-bright hover:bg-surface-2/80"
                >
                  <div className="flex items-start justify-between gap-2">
                    <p className="truncate text-sm font-semibold text-ink group-hover:text-accent">
                      {project.name}
                    </p>
                    <StatusBadge status={project.status} />
                  </div>
                  <p className="mt-1.5 line-clamp-2 text-xs leading-relaxed text-muted">
                    {project.description || "No description yet."}
                  </p>
                  <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-faint">
                    <span className="capitalize">{project.environment}</span>
                    <span>·</span>
                    <span>{project.region}</span>
                    <span>·</span>
                    <span>{timeAgo(project.updated_at)}</span>
                  </div>
                  <div className="mt-2 flex gap-3 text-[11px] text-muted">
                    <span>{project.counts.requirements} requirements</span>
                    <span>{project.counts.documents} docs</span>
                    <span>{project.counts.open_questions} questions</span>
                  </div>
                </Link>
              ))}
            </div>
          )}
        </section>

        <div className="space-y-4">
          <section className="card p-4">
            <SectionTitle title="Pipeline" description="Two human checkpoints guard Apply." />
            <ol className="space-y-2.5">
              {WORKFLOW.map((step, index) => (
                <li key={step} className="flex items-center gap-3">
                  <span className="grid h-6 w-6 shrink-0 place-items-center rounded-full border border-line bg-surface-2 text-[10px] font-semibold text-muted">
                    {index + 1}
                  </span>
                  <span className="text-xs text-slate-300">{step}</span>
                  {(step === "Architecture" || step === "Plan") && (
                    <span className="badge border-violet-500/30 bg-violet-500/10 text-[10px] text-violet-300">
                      approval gate
                    </span>
                  )}
                  {index < WORKFLOW.length - 1 && <span className="ml-auto h-px flex-1 bg-line/70" />}
                  {index === WORKFLOW.length - 1 && <CheckCircle2 size={14} className="ml-auto text-emerald-400" />}
                </li>
              ))}
            </ol>
          </section>

          <section className="card p-4">
            <SectionTitle title="Recent activity" />
            {(data?.recent_activity ?? []).length === 0 ? (
              <p className="py-6 text-center text-xs text-faint">No audit events yet.</p>
            ) : (
              <ul className="space-y-2.5">
                {(data?.recent_activity ?? []).slice(0, 7).map((event) => (
                  <li key={event.id} className="flex items-start gap-2.5">
                    <span
                      className={clsx(
                        "mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full",
                        event.action.includes("failed")
                          ? "bg-rose-400"
                          : event.action.includes("approved")
                            ? "bg-violet-400"
                            : "bg-cyan-400",
                      )}
                    />
                    <div className="min-w-0">
                      <p className="truncate text-xs text-slate-300">
                        {statusLabel(event.action.split(".").pop() ?? event.action)}
                        <span className="text-faint"> · {event.action}</span>
                      </p>
                      <p className="text-[11px] text-faint" title={formatDate(event.timestamp)}>
                        {timeAgo(event.timestamp)}
                      </p>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </div>
      </div>

      {statusEntries.length > 0 && (
        <section className="card p-4">
          <SectionTitle title="Portfolio status" description="Projects by lifecycle stage." />
          <div className="flex h-2.5 w-full overflow-hidden rounded-full bg-surface-3">
            {statusEntries.map(([status, count]) => (
              <div
                key={status}
                className={STATUS_BAR[statusTone(status)]}
                style={{ width: `${(count / totalStatuses) * 100}%` }}
                title={`${statusLabel(status)}: ${count}`}
              />
            ))}
          </div>
          <div className="mt-3 flex flex-wrap gap-2">
            {statusEntries.map(([status, count]) => (
              <span key={status} className="flex items-center gap-1.5 text-[11px] text-muted">
                <span className={clsx("h-2 w-2 rounded-full", STATUS_BAR[statusTone(status)])} />
                {statusLabel(status)} <span className="text-faint">{count}</span>
              </span>
            ))}
          </div>
        </section>
      )}

      <CreateProjectModal
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={() => undefined}
      />
    </div>
  );
}
