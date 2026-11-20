import { Link, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  ClipboardCheck,
  Loader2,
  RefreshCw,
  ShieldAlert,
  XCircle,
} from "lucide-react";
import clsx from "clsx";
import { api, apiErrorMessage } from "../../lib/api";
import { statusTone } from "../../lib/format";
import { StatusBadge } from "../../components/StatusBadge";
import { EmptyState, LoadingBlock, SectionTitle } from "../../components/UI";
import { useToast } from "../../context/ToastContext";

const IMPACT_LABELS: Record<string, string> = {
  technical_impact: "Technical",
  security_impact: "Security",
  availability_impact: "Availability",
  cost_impact: "Cost",
};

export function ReviewTab() {
  const { projectId = "" } = useParams();
  const queryClient = useQueryClient();
  const toast = useToast();

  const architecture = useQuery({
    queryKey: ["architecture", projectId, "current"],
    queryFn: () => api.architecture.current(projectId),
    retry: false,
  });

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["architecture", projectId] });
    queryClient.invalidateQueries({ queryKey: ["overview", projectId] });
    queryClient.invalidateQueries({ queryKey: ["project", projectId] });
  };

  const decide = useMutation({
    mutationFn: ({ id, status }: { id: string; status: string }) =>
      api.architecture.updateRecommendation(architecture.data!.id, id, status),
    onSuccess: () => {
      invalidate();
    },
    onError: (error) => toast.error(apiErrorMessage(error)),
  });

  const rerun = useMutation({
    mutationFn: () => api.architecture.review(architecture.data!.id),
    onSuccess: () => {
      toast.success("Architecture review updated");
      invalidate();
    },
    onError: (error) => toast.error(apiErrorMessage(error)),
  });

  const submit = useMutation({
    mutationFn: () => api.architecture.submit(architecture.data!.id),
    onSuccess: () => {
      toast.success("Submitted for approval");
      invalidate();
    },
    onError: (error) => toast.error(apiErrorMessage(error)),
  });

  if (architecture.isLoading) return <LoadingBlock />;

  if (!architecture.data) {
    return (
      <EmptyState
        icon={<ClipboardCheck size={28} />}
        title="No architecture to review"
        description="Generate an architecture first. The reviewer checks security, availability, performance and cost."
        action={
          <Link className="btn btn-primary" to={`/projects/${projectId}/architecture`}>
            Architecture <ArrowRight size={14} />
          </Link>
        }
      />
    );
  }

  const version = architecture.data;
  const review = version.review;
  const recommendations = review?.recommendations ?? [];
  const unresolved = recommendations.filter((item) => item.mandatory && item.status === "proposed");
  const accepted = recommendations.filter((item) => item.status === "accepted");
  const rejected = recommendations.filter((item) => item.status === "rejected");
  const score = review?.score ?? 0;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold text-ink">Architecture review</h2>
          <p className="mt-0.5 text-sm text-muted">
            v{version.version} · recommendations are advisory. Accepting one does not silently change the design.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button className="btn btn-sm" onClick={() => rerun.mutate()} disabled={rerun.isPending}>
            {rerun.isPending ? <Loader2 size={13} className="animate-spin" /> : <RefreshCw size={13} />}
            Re-run review
          </button>
          {version.approval_status === "draft" && (
            <button className="btn btn-primary btn-sm" onClick={() => submit.mutate()} disabled={submit.isPending}>
              {submit.isPending ? <Loader2 size={13} className="animate-spin" /> : null}
              Submit for approval
            </button>
          )}
          {version.approval_status === "awaiting_approval" && (
            <Link className="btn btn-primary btn-sm" to={`/projects/${projectId}/architecture`}>
              Approve <ShieldAlert size={13} />
            </Link>
          )}
        </div>
      </div>

      <div className="grid gap-3 sm:grid-cols-[repeat(4,minmax(0,1fr))]">
        {[
          ["Score", `${score}/100`, score >= 80 ? "text-emerald-300" : score >= 60 ? "text-amber-300" : "text-rose-300"],
          ["Recommendations", recommendations.length, "text-ink"],
          ["Mandatory open", unresolved.length, unresolved.length ? "text-rose-300" : "text-emerald-300"],
          ["Decisions", `${accepted.length + rejected.length}/${recommendations.length}`, "text-ink"],
        ].map(([label, value, color]) => (
          <div key={String(label)} className="card p-3.5">
            <p className="text-[10px] font-semibold uppercase tracking-wider text-faint">{label}</p>
            <p className={clsx("mt-1 text-2xl font-semibold tabular-nums", color)}>{value}</p>
          </div>
        ))}
      </div>

      {unresolved.length > 0 && (
        <div className="flex items-start gap-2 rounded-xl border border-rose-500/30 bg-rose-500/5 px-3.5 py-2.5 text-xs text-rose-100">
          <AlertTriangle size={15} className="mt-0.5 shrink-0" />
          <div>
            <p className="font-medium">Approval is blocked by {unresolved.length} mandatory recommendation(s).</p>
            <p className="mt-0.5 text-rose-200/70">
              Accept or reject each mandatory item with a clear decision. Rejecting records the accepted risk.
            </p>
          </div>
        </div>
      )}

      <div className="grid gap-4 xl:grid-cols-[1.5fr_1fr]">
        <div className="space-y-3">
          {recommendations.length === 0 && (
            <EmptyState title="No recommendations" description="Run the review to generate security, availability, cost and governance findings." />
          )}
          {recommendations.map((recommendation) => {
            const tone = recommendation.mandatory ? "bad" : "warn";
            return (
              <section key={recommendation.id} className="card p-4">
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <StatusBadge
                        tone={recommendation.mandatory ? "bad" : "warn"}
                        label={recommendation.mandatory ? "mandatory" : "optional"}
                      />
                      <span className="badge text-[9px]">{recommendation.category}</span>
                      <StatusBadge status={recommendation.status} />
                    </div>
                    <h3 className="mt-2 text-sm font-semibold text-ink">{recommendation.recommendation}</h3>
                    <p className="mt-1 text-xs leading-relaxed text-muted">{recommendation.current_limitation}</p>
                  </div>
                  <div className="flex shrink-0 gap-1">
                    <button
                      className={clsx(
                        "btn btn-sm",
                        recommendation.status === "accepted" && "border-emerald-500/40 bg-emerald-500/10 text-emerald-200",
                      )}
                      onClick={() => decide.mutate({ id: recommendation.id, status: "accepted" })}
                      disabled={decide.isPending}
                    >
                      <CheckCircle2 size={13} /> Accept
                    </button>
                    <button
                      className={clsx(
                        "btn btn-sm",
                        recommendation.status === "rejected" && "border-rose-500/40 bg-rose-500/10 text-rose-200",
                      )}
                      onClick={() => decide.mutate({ id: recommendation.id, status: "rejected" })}
                      disabled={decide.isPending}
                    >
                      <XCircle size={13} /> Reject
                    </button>
                  </div>
                </div>

                <div className="mt-3 rounded-lg border border-line/70 bg-surface/30 p-2.5">
                  <p className="text-[10px] font-semibold uppercase tracking-wider text-faint">Proposed improvement</p>
                  <p className="mt-1 text-xs leading-relaxed text-slate-300">{recommendation.proposed_improvement}</p>
                </div>

                <div className="mt-2.5 grid gap-2 sm:grid-cols-2">
                  {Object.entries(IMPACT_LABELS).map(([key, label]) => (
                    <div key={key} className="rounded-lg border border-line/60 bg-canvas/30 px-2.5 py-2">
                      <p className="text-[9px] font-semibold uppercase tracking-wider text-faint">{label} impact</p>
                      <p className="mt-0.5 text-[11px] leading-relaxed text-muted">
                        {String(recommendation[key as keyof typeof recommendation] || "—")}
                      </p>
                    </div>
                  ))}
                </div>

                <p className="mt-2.5 text-[10px] text-faint">Rule {recommendation.id} · {statusTone(tone) === "bad" ? "blocking approval until decided" : "advisory"}</p>
              </section>
            );
          })}
        </div>

        <div className="space-y-4">
          <section className="card p-4">
            <SectionTitle title="Reviewer assessment" />
            <p className="text-sm leading-relaxed text-slate-300">
              {review?.overall_assessment || "The review has not run yet."}
            </p>
            {review?.reviewed_at && (
              <p className="mt-2 text-[10px] text-faint">Reviewed {new Date(review.reviewed_at).toLocaleString()}</p>
            )}
            {(review?.risks ?? []).length > 0 && (
              <div className="mt-3 border-t border-line pt-2.5">
                <p className="text-[10px] font-semibold uppercase tracking-wider text-faint">Open risks</p>
                <ul className="mt-1.5 space-y-1.5">
                  {review!.risks.map((risk) => (
                    <li key={risk} className="flex gap-2 text-[11px] leading-relaxed text-amber-200/80">
                      <AlertTriangle size={11} className="mt-0.5 shrink-0" /> {risk}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </section>

          <section className="card p-4">
            <SectionTitle title="Decision log" description="Accept/reject decisions are audited." />
            {decide.isPending ? (
              <div className="flex items-center gap-2 py-4 text-xs text-muted">
                <Loader2 size={14} className="animate-spin" /> Recording decision…
              </div>
            ) : (
              <div className="space-y-2 text-xs">
                <div className="flex items-center justify-between rounded-lg border border-emerald-500/20 bg-emerald-500/5 px-2.5 py-2">
                  <span className="text-emerald-200">Accepted</span>
                  <span className="font-semibold text-emerald-300">{accepted.length}</span>
                </div>
                <div className="flex items-center justify-between rounded-lg border border-rose-500/20 bg-rose-500/5 px-2.5 py-2">
                  <span className="text-rose-200">Rejected</span>
                  <span className="font-semibold text-rose-300">{rejected.length}</span>
                </div>
                <div className="flex items-center justify-between rounded-lg border border-line bg-surface/30 px-2.5 py-2">
                  <span className="text-muted">Pending</span>
                  <span className="font-semibold text-slate-300">
                    {recommendations.filter((item) => item.status === "proposed").length}
                  </span>
                </div>
              </div>
            )}
          </section>
        </div>
      </div>
    </div>
  );
}
