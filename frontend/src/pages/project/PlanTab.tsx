import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  Download,
  GitBranch,
  Loader2,
  Play,
  Rocket,
  ShieldCheck,
  XCircle,
} from "lucide-react";
import clsx from "clsx";
import { api, apiErrorMessage, waitForJob } from "../../lib/api";
import { formatDate, formatMoney, shortHash, statusLabel, timeAgo } from "../../lib/format";
import { StatusBadge } from "../../components/StatusBadge";
import { EmptyState, LoadingBlock, SectionTitle } from "../../components/UI";
import { Modal } from "../../components/Modal";
import { useToast } from "../../context/ToastContext";
import type { TerraformPlan } from "../../lib/types";

const ACTION_COLORS: Record<string, string> = {
  create: "text-emerald-300",
  update: "text-amber-300",
  delete: "text-rose-300",
  replace: "text-violet-300",
  noop: "text-muted",
};

export function PlanTab() {
  const { projectId = "" } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const toast = useToast();

  const [selectedPlanId, setSelectedPlanId] = useState<string | null>(null);
  const [approveOpen, setApproveOpen] = useState(false);
  const [note, setNote] = useState("");
  const [confirmDestructive, setConfirmDestructive] = useState(false);
  const [jobState, setJobState] = useState<string | null>(null);

  const project = useQuery({ queryKey: ["project", projectId], queryFn: () => api.projects.get(projectId) });
  const overview = useQuery({ queryKey: ["overview", projectId], queryFn: () => api.projects.overview(projectId) });
  const plans = useQuery({ queryKey: ["plans", projectId], queryFn: () => api.plans.list(projectId) });

  const planQuery = useQuery({
    queryKey: ["plan", selectedPlanId],
    queryFn: () => api.plans.get(selectedPlanId!),
    enabled: Boolean(selectedPlanId),
    refetchInterval: (query) => (query.state.data?.status === "generating" ? 2000 : false),
  });

  useEffect(() => {
    const items = plans.data?.items ?? [];
    if (!selectedPlanId && items.length > 0) setSelectedPlanId(items[0].id);
  }, [plans.data, selectedPlanId]);

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["plans", projectId] });
    queryClient.invalidateQueries({ queryKey: ["overview", projectId] });
    queryClient.invalidateQueries({ queryKey: ["project", projectId] });
  };

  const start = useMutation({
    mutationFn: () => api.plans.start(projectId, project.data?.environment),
    onSuccess: async (result) => {
      setSelectedPlanId(result.plan_id);
      setJobState("planning");
      try {
        const job = await waitForJob(result.job_id, (status) => setJobState(status.status));
        if (job.status === "succeeded") {
          toast.success("Terraform plan generated");
        } else {
          toast.error(job.error ?? "Plan failed — inspect the plan diagnostics");
        }
      } catch (error) {
        toast.error(apiErrorMessage(error));
      } finally {
        setJobState(null);
        invalidate();
      }
    },
    onError: (error) => toast.error(apiErrorMessage(error)),
  });

  const approve = useMutation({
    mutationFn: () => api.plans.approve(selectedPlanId!, confirmDestructive, note),
    onSuccess: () => {
      toast.success("Deployment approval recorded against the plan hash");
      setApproveOpen(false);
      setNote("");
      setConfirmDestructive(false);
      invalidate();
      queryClient.invalidateQueries({ queryKey: ["plan", selectedPlanId] });
    },
    onError: (error) => toast.error(apiErrorMessage(error)),
  });

  const reject = useMutation({
    mutationFn: (reason: string) => api.plans.reject(selectedPlanId!, reason),
    onSuccess: () => {
      toast.info("Plan rejected");
      invalidate();
      queryClient.invalidateQueries({ queryKey: ["plan", selectedPlanId] });
    },
    onError: (error) => toast.error(apiErrorMessage(error)),
  });

  const deploy = useMutation({
    mutationFn: () => api.plans.deploy(selectedPlanId!),
    onSuccess: (result) => {
      toast.success("Deployment started with the approved plan artifact");
      queryClient.invalidateQueries({ queryKey: ["deployments", projectId] });
      queryClient.invalidateQueries({ queryKey: ["project", projectId] });
      navigate(`/projects/${projectId}/deployment?deployment=${result.deployment_id}`);
    },
    onError: (error) => toast.error(apiErrorMessage(error)),
  });

  const plan: TerraformPlan | undefined = planQuery.data ?? plans.data?.items.find((item) => item.id === selectedPlanId);
  const summary = plan?.summary;
  const destructiveOps = summary?.destructive_operations ?? [];
  const resourceChanges = summary?.resources ?? [];
  const subscription = project.data?.subscription;

  if (plans.isLoading) return <LoadingBlock label="Loading plans…" />;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold text-ink">Terraform plan</h2>
          <p className="mt-0.5 text-sm text-muted">
            A real plan against the target subscription. The binary artifact and its hash are the approval unit.
          </p>
        </div>
        <button
          className="btn btn-primary"
          onClick={() => start.mutate()}
          disabled={start.isPending || jobState !== null || !overview.data?.validation_passed}
          title={overview.data?.validation_passed ? "Generate plan" : "Validation must pass first"}
        >
          {start.isPending || jobState ? <Loader2 size={15} className="animate-spin" /> : <Play size={15} />}
          {jobState ? "Planning…" : "Generate plan"}
        </button>
      </div>

      <div className="flex flex-wrap items-center gap-2 rounded-xl border border-line bg-surface/30 px-3.5 py-2.5 text-xs">
        <ShieldCheck size={14} className="text-cyan-300" />
        <span className="text-muted">Target:</span>
        <span className="font-medium text-ink">
          {subscription?.subscription_name ?? "No subscription configured"}
        </span>
        <span className="font-mono text-[10px] text-faint">{subscription?.subscription_id ?? "—"}</span>
        <span className="text-faint">·</span>
        <span className="text-muted">Environment:</span>
        <span className="text-slate-200">{project.data?.environment}</span>
        <span className="text-faint">·</span>
        <span className="text-muted">Region:</span>
        <span className="text-slate-200">{project.data?.region}</span>
        {!subscription && (
          <Link className="ml-auto text-accent hover:underline" to="/subscriptions">
            Connect a subscription →
          </Link>
        )}
      </div>

      {!overview.data?.validation_passed && (
        <div className="flex flex-wrap items-center gap-2 rounded-xl border border-amber-500/30 bg-amber-500/5 px-3.5 py-2.5 text-xs text-amber-100">
          <AlertTriangle size={14} />
          Validation has not passed for the current Terraform source. Planning is blocked.
          <Link className="underline" to={`/projects/${projectId}/validation`}>
            Run validation
          </Link>
        </div>
      )}

      <div className="grid gap-4 xl:grid-cols-[1fr_320px]">
        <div className="space-y-4">
          {plan ? (
            <>
              <div className="card p-4">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <StatusBadge status={plan.status} pulse={plan.status === "generating"} />
                    {plan.destructive && (
                      <span className="badge border-rose-500/40 bg-rose-500/10 text-rose-300">
                        <AlertTriangle size={11} /> destructive
                      </span>
                    )}
                    <span className="font-mono text-[10px] text-faint">hash {shortHash(plan.plan_hash, 20)}</span>
                  </div>
                  <span className="text-[11px] text-faint">{formatDate(plan.created_at)}</span>
                </div>

                {plan.error && (
                  <pre className="mt-3 max-h-48 overflow-auto whitespace-pre-wrap rounded-lg border border-rose-500/25 bg-rose-950/20 p-3 text-[11px] leading-relaxed text-rose-200">
                    {plan.error}
                  </pre>
                )}

                {summary && (
                  <>
                    <div className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-4">
                      {[
                        ["Add", summary.add, "text-emerald-300"],
                        ["Change", summary.change, "text-amber-300"],
                        ["Destroy", summary.destroy, "text-rose-300"],
                        ["Replace", summary.replace, "text-violet-300"],
                      ].map(([label, value, color]) => (
                        <div key={String(label)} className="rounded-lg border border-line bg-surface/40 p-2.5 text-center">
                          <p className={clsx("text-2xl font-semibold tabular-nums", color)}>{value}</p>
                          <p className="text-[10px] uppercase tracking-wider text-faint">{label}</p>
                        </div>
                      ))}
                    </div>
                    <p className="mt-3 text-xs leading-relaxed text-muted">{summary.narrative}</p>
                    <div className="mt-3 flex flex-wrap items-center gap-2 text-[10px] text-faint">
                      <span>source {shortHash(plan.source_digest, 16)}</span>
                      <span>·</span>
                      <span>terraform {summary.terraform_version || "—"}</span>
                      {plan.source_stale && <span className="text-rose-300">· source changed after planning</span>}
                    </div>
                  </>
                )}

                <div className="mt-4 flex flex-wrap gap-2 border-t border-line pt-3">
                  {plan.status === "generated" && (
                    <button className="btn btn-primary btn-sm" onClick={() => setApproveOpen(true)}>
                      <ShieldCheck size={13} /> Approve plan
                    </button>
                  )}
                  {plan.status === "generated" && (
                    <button
                      className="btn btn-sm"
                      onClick={() => {
                        const reason = window.prompt("Reason for rejecting this plan?", "Plan changes not acceptable");
                        if (reason) reject.mutate(reason);
                      }}
                    >
                      <XCircle size={13} /> Reject
                    </button>
                  )}
                  {plan.status === "approved" && (
                    <button className="btn btn-primary btn-sm" onClick={() => deploy.mutate()} disabled={deploy.isPending}>
                      {deploy.isPending ? <Loader2 size={13} className="animate-spin" /> : <Rocket size={13} />}
                      Deploy approved plan
                    </button>
                  )}
                  <button
                    className="btn btn-sm"
                    onClick={() => api.plans.download(plan.id).catch((error) => toast.error(apiErrorMessage(error)))}
                    disabled={!plan.plan_hash}
                  >
                    <Download size={13} /> Download .tfplan
                  </button>
                  {plan.approval && (
                    <span className="badge border-emerald-500/30 bg-emerald-500/10 text-emerald-300">
                      <CheckCircle2 size={11} /> approved
                    </span>
                  )}
                </div>
              </div>

              {destructiveOps.length > 0 && (
                <div className="card border-rose-500/30 bg-rose-500/[0.03] p-4">
                  <h3 className="flex items-center gap-2 text-sm font-semibold text-rose-200">
                    <AlertTriangle size={15} /> Destructive operations
                  </h3>
                  <div className="mt-2 space-y-2">
                    {destructiveOps.map((operation) => (
                      <div key={`${operation.address}-${operation.action}`} className="rounded-lg border border-rose-500/20 bg-rose-950/20 p-2.5">
                        <div className="flex flex-wrap items-center gap-2">
                          <StatusBadge status={operation.severity} label={operation.severity} />
                          <span className="font-mono text-[10px] text-rose-200">{operation.address}</span>
                          <span className="text-[10px] uppercase text-rose-300">{operation.action}</span>
                        </div>
                        <p className="mt-1 text-[11px] leading-relaxed text-rose-100/80">{operation.reason}</p>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {resourceChanges.length > 0 && (
                <div className="card overflow-hidden">
                  <div className="border-b border-line px-4 py-3">
                    <h3 className="text-sm font-semibold text-ink">Resource changes ({resourceChanges.length})</h3>
                  </div>
                  <div className="max-h-[420px] overflow-auto">
                    <table className="table">
                      <thead>
                        <tr>
                          <th>Action</th>
                          <th>Address</th>
                          <th>Type</th>
                          <th>Reason</th>
                        </tr>
                      </thead>
                      <tbody>
                        {resourceChanges.map((resource) => (
                          <tr key={resource.address}>
                            <td>
                              <span className={clsx("font-mono text-[10px] uppercase", ACTION_COLORS[resource.action])}>
                                {resource.action}
                              </span>
                            </td>
                            <td className="font-mono text-[10px] text-slate-300">{resource.address}</td>
                            <td className="font-mono text-[10px] text-faint">{resource.type}</td>
                            <td className="text-[11px] text-muted">{resource.reason}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}
            </>
          ) : (
            <EmptyState
              icon={<GitBranch size={26} />}
              title="No plan selected"
              description="Validate the Terraform project, then generate a plan against the configured Azure subscription."
              action={
                <button
                  className="btn btn-primary"
                  onClick={() => start.mutate()}
                  disabled={start.isPending || !overview.data?.validation_passed}
                >
                  <Play size={15} /> Generate plan
                </button>
              }
            />
          )}
        </div>

        <div className="space-y-2">
          <SectionTitle title="Plan history" description={`${plans.data?.items.length ?? 0} plan(s)`} />
          <div className="space-y-2">
            {(plans.data?.items ?? []).map((item) => (
              <button
                key={item.id}
                onClick={() => setSelectedPlanId(item.id)}
                className={clsx(
                  "w-full rounded-xl border p-2.5 text-left transition",
                  selectedPlanId === item.id
                    ? "border-cyan-500/40 bg-cyan-500/5"
                    : "border-line bg-surface/30 hover:border-line-bright",
                )}
              >
                <div className="flex items-center justify-between">
                  <StatusBadge status={item.status} />
                  <span className="text-[10px] text-faint">{timeAgo(item.created_at)}</span>
                </div>
                <p className="mt-1.5 text-[11px] text-muted">
                  +{item.summary?.add ?? 0} ~{item.summary?.change ?? 0} −{item.summary?.destroy ?? 0} ↻
                  {item.summary?.replace ?? 0}
                  {item.destructive && <span className="ml-1 text-rose-300">destructive</span>}
                </p>
                <p className="mt-0.5 font-mono text-[9px] text-faint">{shortHash(item.plan_hash, 16)}</p>
              </button>
            ))}
            {plans.data?.items.length === 0 && <p className="text-xs text-faint">No plans yet.</p>}
          </div>
          {project.data && (
            <div className="card-flat p-3 text-[11px] leading-relaxed text-muted">
              <p className="font-medium text-ink">Approval binding</p>
              <p className="mt-1">
                Plan hash, source digest, architecture version, subscription and environment are all recorded.
                If any change, the approval is invalidated.
              </p>
            </div>
          )}
        </div>
      </div>

      <Modal
        open={approveOpen}
        onClose={() => setApproveOpen(false)}
        title="Approve deployment plan"
        description="This records a human approval bound to the exact binary plan hash. Apply will execute this artifact only."
        footer={
          <>
            <button className="btn" onClick={() => setApproveOpen(false)}>
              Cancel
            </button>
            <button
              className="btn btn-primary"
              disabled={approve.isPending || (plan?.destructive && !confirmDestructive)}
              onClick={() => approve.mutate()}
            >
              {approve.isPending ? <Loader2 size={15} className="animate-spin" /> : <ShieldCheck size={15} />}
              Approve deployment
            </button>
          </>
        }
      >
        {plan && (
          <div className="rounded-lg border border-line bg-surface/40 p-3 text-xs">
            <p className="font-mono text-[11px] text-cyan-200">{plan.plan_hash}</p>
            <p className="mt-1 text-muted">
              +{summary?.add ?? 0} add · ~{summary?.change ?? 0} change · −{summary?.destroy ?? 0} destroy ·{" "}
              {summary?.replace ?? 0} replace
            </p>
            {summary && <p className="mt-1 text-muted">Indicative workflow: review → approve → apply exact plan.</p>}
          </div>
        )}
        <textarea
          className="textarea min-h-20"
          placeholder="Approval note (recorded in audit)…"
          value={note}
          onChange={(event) => setNote(event.target.value)}
        />
        {plan?.destructive && (
          <label className="flex items-start gap-2 rounded-lg border border-rose-500/30 bg-rose-500/5 p-3 text-xs text-rose-100">
            <input
              type="checkbox"
              className="mt-0.5 accent-rose-500"
              checked={confirmDestructive}
              onChange={(event) => setConfirmDestructive(event.target.checked)}
            />
            I reviewed the destructive operations and accept the data-loss and downtime risk.
          </label>
        )}
      </Modal>

      <div className="flex items-center justify-between text-[10px] text-faint">
        <span className="inline-flex items-center gap-1">
          <CheckCircle2 size={11} /> Apply requires an active approval matching the plan hash
        </span>
        <Link to={`/projects/${projectId}/deployment`} className="inline-flex items-center gap-1 hover:text-ink">
          Deployment results <ArrowRight size={11} />
        </Link>
      </div>
    </div>
  );
}
