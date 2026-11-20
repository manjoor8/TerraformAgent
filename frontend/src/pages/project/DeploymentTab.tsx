import { useEffect, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  CheckCircle2,
  Clock,
  Loader2,
  RefreshCw,
  Rocket,
  ShieldCheck,
  Square,
  XCircle,
} from "lucide-react";
import clsx from "clsx";
import { api, apiErrorMessage } from "../../lib/api";
import { formatDate, shortHash, statusLabel, timeAgo } from "../../lib/format";
import { StatusBadge } from "../../components/StatusBadge";
import { LogConsole } from "../../components/LogConsole";
import { EmptyState, LoadingBlock, SectionTitle } from "../../components/UI";
import { useToast } from "../../context/ToastContext";

export function DeploymentTab() {
  const { projectId = "" } = useParams();
  const [searchParams] = useSearchParams();
  const queryClient = useQueryClient();
  const toast = useToast();
  const [selectedId, setSelectedId] = useState<string | null>(searchParams.get("deployment"));

  const deployments = useQuery({
    queryKey: ["deployments", projectId],
    queryFn: () => api.deployments.list(projectId),
    refetchInterval: (query) =>
      (query.state.data?.items ?? []).some((item) => ["queued", "running"].includes(item.status)) ? 2500 : false,
  });

  useEffect(() => {
    const items = deployments.data?.items ?? [];
    if (!selectedId && items.length > 0) setSelectedId(items[0].id);
  }, [deployments.data, selectedId]);

  const deployment = useQuery({
    queryKey: ["deployment", selectedId],
    queryFn: () => api.deployments.get(selectedId!),
    enabled: Boolean(selectedId),
    refetchInterval: (query) => (query.state.data && ["queued", "running"].includes(query.state.data.status) ? 2000 : false),
  });

  const logs = useQuery({
    queryKey: ["deployment-logs", selectedId],
    queryFn: () => api.deployments.logs(selectedId!),
    enabled: Boolean(selectedId),
    refetchInterval: (query) => {
      const status = deployment.data?.status;
      return status && ["queued", "running"].includes(status) ? 2500 : false;
    },
  });

  const verify = useMutation({
    mutationFn: () => api.deployments.verify(selectedId!),
    onSuccess: () => {
      toast.success("Verification refreshed");
      queryClient.invalidateQueries({ queryKey: ["deployment", selectedId] });
    },
    onError: (error) => toast.error(apiErrorMessage(error)),
  });

  const cancel = useMutation({
    mutationFn: () => api.deployments.cancel(selectedId!),
    onSuccess: () => {
      toast.info("Cancellation requested");
      queryClient.invalidateQueries({ queryKey: ["deployment", selectedId] });
    },
    onError: (error) => toast.error(apiErrorMessage(error)),
  });

  const current = deployment.data;
  const isRunning = current && ["queued", "running"].includes(current.status);
  const logLines = (logs.data ?? "").split("\n").filter(Boolean);
  const summary = (current?.summary ?? {}) as Record<string, unknown>;
  const resources = (summary.resources as { address: string; type: string; name: string; id?: string }[] | undefined) ?? [];
  const verification = current?.verification ?? {};

  if (deployments.isLoading) return <LoadingBlock label="Loading deployments…" />;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold text-ink">Deployment</h2>
          <p className="mt-0.5 text-sm text-muted">
            Apply executes the approved binary plan, streams logs, then verifies the deployed resources.
          </p>
        </div>
        <div className="flex gap-2">
          {current && isRunning && (
            <button className="btn btn-danger btn-sm" onClick={() => cancel.mutate()} disabled={cancel.isPending}>
              {cancel.isPending ? <Loader2 size={13} className="animate-spin" /> : <Square size={13} />}
              Cancel
            </button>
          )}
          {current && !isRunning && (
            <button className="btn btn-sm" onClick={() => verify.mutate()} disabled={verify.isPending}>
              {verify.isPending ? <Loader2 size={13} className="animate-spin" /> : <RefreshCw size={13} />}
              Verify resources
            </button>
          )}
          <Link className="btn btn-primary btn-sm" to={`/projects/${projectId}/plan`}>
            <Rocket size={13} /> Plan &amp; approve
          </Link>
        </div>
      </div>

      <div className="grid gap-4 xl:grid-cols-[1fr_300px]">
        <div className="space-y-4">
          {current ? (
            <>
              <div className="card p-4">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <StatusBadge status={current.status} pulse={isRunning} />
                    <span className="text-xs text-muted">{current.environment}</span>
                    <span className="font-mono text-[10px] text-faint">{current.subscription_id}</span>
                  </div>
                  <div className="flex flex-wrap items-center gap-3 text-[11px] text-faint">
                    <span className="inline-flex items-center gap-1">
                      <Clock size={11} /> {timeAgo(current.started_at ?? current.created_at)}
                    </span>
                    {summary.duration_seconds !== undefined && <span>{String(summary.duration_seconds)}s</span>}
                    <span className="font-mono">plan {shortHash(String(summary.plan_hash ?? ""), 14)}</span>
                  </div>
                </div>

                {Boolean(summary.error) && (
                  <div className="mt-3 rounded-lg border border-rose-500/25 bg-rose-950/20 p-3 text-xs text-rose-200">
                    <p className="flex items-center gap-1.5 font-medium">
                      <AlertTriangle size={13} /> Deployment failed
                    </p>
                    <pre className="mt-1.5 max-h-40 overflow-auto whitespace-pre-wrap text-[11px] leading-relaxed">
                      {String(summary.error)}
                    </pre>
                  </div>
                )}

                <div className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-4">
                  {[
                    { label: "Added", value: Number(summary.add ?? 0), color: "text-emerald-300" },
                    { label: "Changed", value: Number(summary.change ?? 0), color: "text-amber-300" },
                    { label: "Destroyed", value: Number(summary.destroy ?? 0), color: "text-rose-300" },
                    { label: "Replaced", value: Number(summary.replace ?? 0), color: "text-violet-300" },
                  ].map((metric) => (
                    <div key={metric.label} className="rounded-lg border border-line bg-surface/40 p-2.5 text-center">
                      <p className={clsx("text-2xl font-semibold tabular-nums", metric.color)}>{metric.value}</p>
                      <p className="text-[10px] uppercase tracking-wider text-faint">{metric.label}</p>
                    </div>
                  ))}
                </div>

                {verification.status && (
                  <div
                    className={clsx(
                      "mt-4 rounded-xl border p-3",
                      verification.status === "verified"
                        ? "border-emerald-500/25 bg-emerald-500/5"
                        : verification.status === "failed"
                          ? "border-rose-500/25 bg-rose-500/5"
                          : "border-amber-500/25 bg-amber-500/5",
                    )}
                  >
                    <div className="flex flex-wrap items-center gap-2">
                      <StatusBadge status={verification.status} />
                      <span className="text-xs text-muted">
                        {verification.matched ?? 0}/{verification.checked ?? 0} resource types matched
                      </span>
                      {verification.checked_at && (
                        <span className="ml-auto text-[10px] text-faint">{formatDate(verification.checked_at)}</span>
                      )}
                    </div>
                    {(verification.notes ?? []).map((note) => (
                      <p key={note} className="mt-1.5 text-[11px] leading-relaxed text-muted">
                        {note}
                      </p>
                    ))}
                    {(verification.missing ?? []).length > 0 && (
                      <div className="mt-2">
                        <p className="text-[10px] font-semibold uppercase tracking-wider text-faint">Missing resource types</p>
                        <ul className="mt-1 space-y-0.5">
                          {(verification.missing ?? []).map((item) => (
                            <li key={item.type} className="font-mono text-[10px] text-rose-300">
                              {item.type}
                            </li>
                          ))}
                        </ul>
                      </div>
                    )}
                  </div>
                )}
              </div>

              <div className="card overflow-hidden">
                <div className="flex items-center justify-between border-b border-line px-4 py-2.5">
                  <h3 className="text-sm font-semibold text-ink">Execution log</h3>
                  <StatusBadge status={current.status} pulse={isRunning} />
                </div>
                <div className="p-3">
                  <LogConsole
                    lines={logLines}
                    live={Boolean(isRunning)}
                    empty={isRunning ? "Waiting for Terraform output…" : "No log output recorded."}
                    maxHeight={430}
                  />
                </div>
              </div>

              {resources.length > 0 && (
                <div className="card overflow-hidden">
                  <div className="border-b border-line px-4 py-3">
                    <h3 className="text-sm font-semibold text-ink">Deployed resources ({resources.length})</h3>
                    <p className="text-xs text-muted">Recorded from Terraform state after apply.</p>
                  </div>
                  <div className="max-h-[360px] overflow-auto">
                    <table className="table">
                      <thead>
                        <tr>
                          <th>Address</th>
                          <th>Type</th>
                          <th>Name</th>
                          <th>Azure ID</th>
                        </tr>
                      </thead>
                      <tbody>
                        {resources.map((resource) => (
                          <tr key={resource.address}>
                            <td className="font-mono text-[10px] text-slate-300">{resource.address}</td>
                            <td className="font-mono text-[10px] text-cyan-200">{resource.type}</td>
                            <td className="text-[11px] text-muted">{resource.name}</td>
                            <td className="max-w-[280px] truncate font-mono text-[9px] text-faint">{resource.id || "—"}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}

              {Object.keys(current.outputs ?? {}).length > 0 && (
                <div className="card p-4">
                  <SectionTitle title="Terraform outputs" description="Sensitive outputs are masked." />
                  <div className="space-y-1.5">
                    {Object.entries(current.outputs).map(([key, value]) => (
                      <div key={key} className="flex items-start gap-3 rounded-lg border border-line/60 bg-surface/30 px-2.5 py-1.5">
                        <span className="w-40 shrink-0 font-mono text-[10px] text-cyan-200">{key}</span>
                        <span className="break-all font-mono text-[10px] text-muted">
                          {typeof value === "string" ? value : JSON.stringify(value)}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </>
          ) : (
            <EmptyState
              icon={<Rocket size={28} />}
              title="No deployments yet"
              description="Approve a Terraform plan, then deploy the exact saved plan artifact. Deployment is never automatic."
              action={
                <Link className="btn btn-primary" to={`/projects/${projectId}/plan`}>
                  <ShieldCheck size={15} /> Review plan
                </Link>
              }
            />
          )}
        </div>

        <div className="space-y-2">
          <SectionTitle title="Deployment history" description={`${deployments.data?.items.length ?? 0} run(s)`} />
          <div className="space-y-2">
            {(deployments.data?.items ?? []).map((item) => (
              <button
                key={item.id}
                onClick={() => setSelectedId(item.id)}
                className={clsx(
                  "w-full rounded-xl border p-2.5 text-left transition",
                  selectedId === item.id
                    ? "border-cyan-500/40 bg-cyan-500/5"
                    : "border-line bg-surface/30 hover:border-line-bright",
                )}
              >
                <div className="flex items-center justify-between">
                  <StatusBadge status={item.status} />
                  <span className="text-[10px] text-faint">{timeAgo(item.started_at ?? item.created_at)}</span>
                </div>
                <p className="mt-1 text-[11px] text-muted">{item.environment} · {item.subscription_id}</p>
                <p className="font-mono text-[9px] text-faint">{item.id.slice(0, 18)}</p>
              </button>
            ))}
            {deployments.data?.items.length === 0 && <p className="text-xs text-faint">No deployments yet.</p>}
          </div>
          <div className="card-flat p-3 text-[11px] leading-relaxed text-muted">
            <p className="font-medium text-ink">Verification</p>
            <p className="mt-1">
              After apply the platform reads Azure Resource Manager to confirm the expected resource types and
              tag ownership. The model cannot mark a deployment successful.
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}
