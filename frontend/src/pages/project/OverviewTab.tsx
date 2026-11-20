import { Link, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowRight,
  Boxes,
  CheckCircle2,
  Circle,
  Cloud,
  FileCode2,
  GitBranch,
  Loader2,
  MessageSquare,
  ShieldCheck,
  Sparkles,
  TriangleAlert,
} from "lucide-react";
import clsx from "clsx";
import { api, apiErrorMessage } from "../../lib/api";
import { formatMoney, shortHash, statusLabel, timeAgo } from "../../lib/format";
import { StatusBadge } from "../../components/StatusBadge";
import { EmptyState, LoadingBlock, SectionTitle } from "../../components/UI";
import { useToast } from "../../context/ToastContext";

export function OverviewTab() {
  const { projectId = "" } = useParams();
  const queryClient = useQueryClient();
  const toast = useToast();

  const overview = useQuery({
    queryKey: ["overview", projectId],
    queryFn: () => api.projects.overview(projectId),
  });

  const generate = useMutation({
    mutationFn: () => api.architecture.generate(projectId, {}),
    onSuccess: (version) => {
      toast.success(`Architecture v${version.version} generated`);
      queryClient.invalidateQueries({ queryKey: ["overview", projectId] });
      queryClient.invalidateQueries({ queryKey: ["project", projectId] });
    },
    onError: (error) => toast.error(apiErrorMessage(error)),
  });

  if (overview.isLoading) return <LoadingBlock />;
  if (!overview.data) return null;
  const data = overview.data;
  const done = data.checklist.filter((item) => item.done).length;
  const progress = Math.round((done / data.checklist.length) * 100);
  const architecture = data.latest_architecture;
  const plan = data.latest_plan;

  return (
    <div className="grid gap-4 xl:grid-cols-[1.5fr_1fr]">
      <div className="space-y-4">
        <section className="card p-4">
          <SectionTitle
            title="Delivery readiness"
            description={`${done} of ${data.checklist.length} milestones complete`}
            actions={<span className="text-xs font-semibold text-accent">{progress}%</span>}
          />
          <div className="mb-4 h-1.5 w-full overflow-hidden rounded-full bg-surface-3">
            <div
              className="h-full rounded-full bg-gradient-to-r from-cyan-400 to-emerald-400 transition-all"
              style={{ width: `${progress}%` }}
            />
          </div>
          <ul className="grid gap-2 sm:grid-cols-2">
            {data.checklist.map((item) => (
              <li
                key={item.key}
                className={clsx(
                  "flex items-center gap-2 rounded-lg border px-2.5 py-2 text-xs",
                  item.done
                    ? "border-emerald-500/20 bg-emerald-500/5 text-emerald-200"
                    : "border-line/70 bg-surface/30 text-muted",
                )}
              >
                {item.done ? (
                  <CheckCircle2 size={14} className="shrink-0 text-emerald-400" />
                ) : (
                  <Circle size={14} className="shrink-0 text-faint" />
                )}
                {item.label}
              </li>
            ))}
          </ul>
        </section>

        <section className="card p-4">
          <SectionTitle
            title="Architecture"
            description="Versioned Azure design with review and cost estimate."
            actions={
              architecture ? (
                <Link className="btn btn-ghost btn-sm" to={`/projects/${projectId}/architecture`}>
                  Open <ArrowRight size={13} />
                </Link>
              ) : null
            }
          />
          {architecture ? (
            <div className="space-y-3">
              <div className="flex flex-wrap items-center gap-2">
                <span className="badge border-cyan-500/30 bg-cyan-500/10 text-cyan-300">
                  v{architecture.version}
                </span>
                <StatusBadge status={architecture.approval_status} />
                <span className="text-xs text-muted">
                  {architecture.resource_count} resources · {architecture.module_count} modules · review{" "}
                  {architecture.review?.score ?? "—"}/100
                </span>
              </div>
              <p className="line-clamp-3 text-sm leading-relaxed text-slate-300">{architecture.summary}</p>
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                <div className="rounded-lg border border-line bg-surface/40 p-2.5">
                  <p className="text-[10px] uppercase tracking-wider text-faint">Monthly</p>
                  <p className="mt-0.5 text-sm font-semibold text-ink">
                    {formatMoney(architecture.cost?.monthly_total, architecture.cost?.currency)}
                  </p>
                </div>
                <div className="rounded-lg border border-line bg-surface/40 p-2.5">
                  <p className="text-[10px] uppercase tracking-wider text-faint">Private endpoints</p>
                  <p className="mt-0.5 text-sm font-semibold text-ink">
                    {(architecture.inventory ?? []).filter((row) => row.azure_resource === "Private Endpoint").length}
                  </p>
                </div>
                <div className="rounded-lg border border-line bg-surface/40 p-2.5">
                  <p className="text-[10px] uppercase tracking-wider text-faint">Recommendations</p>
                  <p className="mt-0.5 text-sm font-semibold text-ink">
                    {architecture.review?.recommendations?.length ?? 0}
                  </p>
                </div>
                <div className="rounded-lg border border-line bg-surface/40 p-2.5">
                  <p className="text-[10px] uppercase tracking-wider text-faint">Region</p>
                  <p className="mt-0.5 text-sm font-semibold text-ink">{architecture.region}</p>
                </div>
              </div>
              {architecture.approval_status !== "approved" && (
                <Link className="btn btn-sm" to={`/projects/${projectId}/review`}>
                  <ShieldCheck size={14} /> Resolve review &amp; submit
                </Link>
              )}
            </div>
          ) : (
            <EmptyState
              icon={<Boxes size={24} />}
              title="No architecture yet"
              description="Once the requirements are clear, generate the Azure design. The agent reviews it before you can approve."
              action={
                <button className="btn btn-primary" onClick={() => generate.mutate()} disabled={generate.isPending}>
                  {generate.isPending ? <Loader2 size={15} className="animate-spin" /> : <Sparkles size={15} />}
                  Design architecture
                </button>
              }
            />
          )}
        </section>

        <section className="card p-4">
          <SectionTitle
            title="Latest Terraform plan"
            description="Plans are hash-bound to the deployment approval."
            actions={
              <Link className="btn btn-ghost btn-sm" to={`/projects/${projectId}/plan`}>
                Plan tab <ArrowRight size={13} />
              </Link>
            }
          />
          {plan ? (
            <div className="space-y-3">
              <div className="flex flex-wrap items-center gap-2">
                <StatusBadge status={plan.status} />
                {plan.destructive && (
                  <span className="badge border-rose-500/40 bg-rose-500/10 text-rose-300">
                    <TriangleAlert size={11} /> destructive
                  </span>
                )}
                <span className="font-mono text-[11px] text-faint">{shortHash(plan.plan_hash)}</span>
              </div>
              <div className="grid grid-cols-4 gap-2 text-center">
                {[
                  ["add", plan.summary?.add ?? 0, "text-emerald-300"],
                  ["change", plan.summary?.change ?? 0, "text-amber-300"],
                  ["destroy", plan.summary?.destroy ?? 0, "text-rose-300"],
                  ["replace", plan.summary?.replace ?? 0, "text-violet-300"],
                ].map(([label, value, color]) => (
                  <div key={String(label)} className="rounded-lg border border-line bg-surface/40 p-2">
                    <p className={clsx("text-lg font-semibold tabular-nums", color)}>{value}</p>
                    <p className="text-[10px] uppercase tracking-wider text-faint">{label}</p>
                  </div>
                ))}
              </div>
              {plan.summary?.narrative && (
                <p className="line-clamp-2 text-xs leading-relaxed text-muted">{plan.summary.narrative}</p>
              )}
            </div>
          ) : (
            <p className="py-3 text-xs text-muted">
              No plan generated. Validate Terraform, then generate a plan against the target subscription.
            </p>
          )}
        </section>
      </div>

      <div className="space-y-4">
        <section className="card p-4">
          <SectionTitle
            title="Open questions"
            description="Answers change the architecture."
            actions={
              <Link className="btn btn-ghost btn-sm" to={`/projects/${projectId}/chat`}>
                Chat <ArrowRight size={13} />
              </Link>
            }
          />
          {data.open_questions.length === 0 ? (
            <p className="rounded-lg border border-emerald-500/20 bg-emerald-500/5 px-3 py-2 text-xs text-emerald-200">
              No blocking questions. The design can proceed.
            </p>
          ) : (
            <ul className="space-y-2.5">
              {data.open_questions.slice(0, 4).map((question) => (
                <li key={question.id} className="rounded-lg border border-line/80 bg-surface/30 p-2.5">
                  <p className="text-xs font-medium text-slate-200">{question.question}</p>
                  <p className="mt-1 text-[11px] leading-relaxed text-muted">{question.why_it_matters}</p>
                  {question.recommended && (
                    <p className="mt-1.5 text-[11px] text-cyan-300">Recommended: {question.recommended}</p>
                  )}
                </li>
              ))}
            </ul>
          )}
          <Link className="btn btn-sm mt-3 w-full" to={`/projects/${projectId}/chat`}>
            <MessageSquare size={14} /> Continue conversation
          </Link>
        </section>

        <section className="card p-4">
          <SectionTitle title="Artifacts" />
          <div className="space-y-2 text-xs">
            <Link
              to={`/projects/${projectId}/terraform`}
              className="flex items-center justify-between rounded-lg border border-line/80 bg-surface/30 px-2.5 py-2 hover:border-line-bright"
            >
              <span className="flex items-center gap-2 text-slate-300">
                <FileCode2 size={14} className="text-cyan-300" /> Terraform project
              </span>
              <span className="text-faint">
                {data.latest_terraform ? `v${data.latest_terraform.version}` : "not generated"}
              </span>
            </Link>
            <Link
              to={`/projects/${projectId}/validation`}
              className="flex items-center justify-between rounded-lg border border-line/80 bg-surface/30 px-2.5 py-2 hover:border-line-bright"
            >
              <span className="flex items-center gap-2 text-slate-300">
                <CheckCircle2 size={14} className="text-emerald-300" /> Validation
              </span>
              <span className={data.validation_passed ? "text-emerald-300" : "text-faint"}>
                {data.validation_passed ? "passed" : "pending"}
              </span>
            </Link>
            <Link
              to={`/projects/${projectId}/deployment`}
              className="flex items-center justify-between rounded-lg border border-line/80 bg-surface/30 px-2.5 py-2 hover:border-line-bright"
            >
              <span className="flex items-center gap-2 text-slate-300">
                <Cloud size={14} className="text-violet-300" /> Deployment
              </span>
              <span className="text-faint">{statusLabel(data.project.status)}</span>
            </Link>
          </div>
        </section>

        <section className="card p-4">
          <SectionTitle title="Guardrails" />
          <ul className="space-y-2 text-[11px] leading-relaxed text-muted">
            {[
              "Architecture approval is separate from deployment approval.",
              "Apply executes the exact approved binary plan.",
              "Any code or target change invalidates approvals.",
              "The model cannot approve, deploy or access credentials.",
            ].map((item) => (
              <li key={item} className="flex gap-2">
                <ShieldCheck size={12} className="mt-0.5 shrink-0 text-cyan-400" />
                {item}
              </li>
            ))}
          </ul>
          <p className="mt-3 border-t border-line pt-2.5 text-[10px] text-faint">
            Last update {timeAgo(data.project.updated_at)} · <GitBranch size={10} className="inline" />{" "}
            {data.project.environment}
          </p>
        </section>
      </div>
    </div>
  );
}
