import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  CheckCircle2,
  ChevronRight,
  FileCode2,
  Loader2,
  Play,
  ShieldCheck,
  Wand2,
  XCircle,
} from "lucide-react";
import clsx from "clsx";
import { api, apiErrorMessage, waitForJob } from "../../lib/api";
import { formatDate, statusLabel, timeAgo } from "../../lib/format";
import { StatusBadge } from "../../components/StatusBadge";
import { EmptyState, LoadingBlock, SectionTitle } from "../../components/UI";
import { useToast } from "../../context/ToastContext";
import type { PolicyFinding, ValidationRun } from "../../lib/types";

export function ValidationTab() {
  const { projectId = "" } = useParams();
  const queryClient = useQueryClient();
  const toast = useToast();
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);

  const terraform = useQuery({
    queryKey: ["terraform", projectId],
    queryFn: () => api.terraform.current(projectId),
    retry: false,
  });
  const project = terraform.data;
  const system = useQuery({ queryKey: ["system"], queryFn: api.system.info, staleTime: 60_000 });

  const runs = useQuery({
    queryKey: ["validation", projectId, project?.id],
    queryFn: () => api.terraform.validations(project!.id),
    enabled: Boolean(project?.id),
    refetchInterval: (query) => {
      const items = query.state.data?.items ?? [];
      return items.some((run) => ["queued", "running"].includes(run.status)) ? 2500 : false;
    },
  });

  const runQuery = useQuery({
    queryKey: ["validation-run", selectedRunId],
    queryFn: () => api.validation.get(selectedRunId!),
    enabled: Boolean(selectedRunId),
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status && ["queued", "running"].includes(status) ? 1500 : false;
    },
  });

  useEffect(() => {
    const items = runs.data?.items ?? [];
    if (!selectedRunId && items.length > 0) setSelectedRunId(items[0].id);
  }, [runs.data, selectedRunId]);

  const start = useMutation({
    mutationFn: () => api.terraform.validate(project!.id),
    onSuccess: async (result) => {
      setSelectedRunId(result.validation_run_id);
      toast.info("Validation started");
      queryClient.invalidateQueries({ queryKey: ["validation", projectId] });
    },
    onError: (error) => toast.error(apiErrorMessage(error)),
  });

  const repair = useMutation({
    mutationFn: () => api.terraform.repair(project!.id),
    onSuccess: async (result) => {
      toast.info(`AI repair started (max ${result.max_attempts} attempts)`);
      try {
        const job = await waitForJob(result.job_id);
        queryClient.invalidateQueries({ queryKey: ["terraform", projectId] });
        queryClient.invalidateQueries({ queryKey: ["validation", projectId] });
        queryClient.invalidateQueries({ queryKey: ["validation-run"] });
        queryClient.invalidateQueries({ queryKey: ["overview", projectId] });
        queryClient.invalidateQueries({ queryKey: ["project", projectId] });
        setSelectedRunId(null);
        if (job.status === "succeeded") toast.success("Repair workflow finished");
        else toast.error(job.error ?? "Repair workflow failed — inspect the latest validation run");
      } catch (error) {
        toast.error(apiErrorMessage(error));
      }
    },
    onError: (error) => toast.error(apiErrorMessage(error)),
  });

  const selectedRun: ValidationRun | undefined =
    runQuery.data ?? runs.data?.items.find((run) => run.id === selectedRunId);
  const report = selectedRun?.results?.report;
  const findings = useMemo<PolicyFinding[]>(
    () => (report?.checks ?? []).flatMap((check) => check.findings ?? []),
    [report],
  );

  if (terraform.isLoading) return <LoadingBlock />;

  if (!project) {
    return (
      <EmptyState
        icon={<FileCode2 size={28} />}
        title="No Terraform to validate"
        description="Generate the Terraform project from the approved architecture first."
        action={
          <Link className="btn btn-primary" to={`/projects/${projectId}/terraform`}>
            Terraform <ChevronRight size={14} />
          </Link>
        }
      />
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold text-ink">Validation</h2>
          <p className="mt-0.5 text-sm text-muted">
            terraform fmt, init, validate, deterministic policy, secret scan and provider checks.
          </p>
        </div>
        <div className="flex flex-wrap justify-end gap-2">
          {selectedRun?.status === "failed" && (
            <button
              className="btn"
              onClick={() => repair.mutate()}
              disabled={repair.isPending || !system.data?.llm.available}
              title={
                system.data?.llm.available
                  ? "Let the Terraform Engineer agent propose fixes (max 3 attempts)"
                  : "Configure an LLM provider to enable automatic repair"
              }
            >
              {repair.isPending ? <Loader2 size={15} className="animate-spin" /> : <Wand2 size={15} />}
              {repair.isPending ? "Repairing…" : "AI repair"}
            </button>
          )}
          <button className="btn btn-primary" onClick={() => start.mutate()} disabled={start.isPending}>
            {start.isPending ? <Loader2 size={15} className="animate-spin" /> : <Play size={15} />}
            Run validation
          </button>
        </div>
      </div>

      <div className="grid gap-4 xl:grid-cols-[1fr_320px]">
        <div className="space-y-4">
          {selectedRun ? (
            <>
              <div className="card p-4">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <StatusBadge status={selectedRun.status} pulse={["running", "queued"].includes(selectedRun.status)} />
                    <span className="text-xs text-muted">
                      attempt {selectedRun.attempt} · {formatDate(selectedRun.created_at)}
                    </span>
                    {selectedRun.results?.source_digest && (
                      <span className="font-mono text-[10px] text-faint">{selectedRun.results.source_digest.slice(0, 18)}…</span>
                    )}
                  </div>
                  {["queued", "running"].includes(selectedRun.status) && (
                    <span className="flex items-center gap-1.5 text-xs text-amber-300">
                      <Loader2 size={13} className="animate-spin" /> running checks…
                    </span>
                  )}
                </div>

                {report ? (
                  <div className="mt-4 space-y-2">
                    {(report.checks ?? []).map((check) => (
                      <div key={check.name} className="rounded-xl border border-line bg-surface/30 p-3">
                        <div className="flex flex-wrap items-center gap-2">
                          {check.status === "passed" ? (
                            <CheckCircle2 size={15} className="text-emerald-400" />
                          ) : check.status === "failed" || check.status === "error" ? (
                            <XCircle size={15} className="text-rose-400" />
                          ) : (
                            <AlertTriangle size={15} className="text-amber-400" />
                          )}
                          <span className="text-xs font-medium text-ink">{statusLabel(check.name)}</span>
                          <StatusBadge status={check.status} />
                          <span className="text-[10px] text-faint">{check.duration_ms} ms</span>
                          {check.errors > 0 && <span className="badge border-rose-500/30 bg-rose-500/10 text-[9px] text-rose-300">{check.errors} errors</span>}
                          {check.warnings > 0 && <span className="badge border-amber-500/30 bg-amber-500/10 text-[9px] text-amber-300">{check.warnings} warnings</span>}
                        </div>
                        {check.details && (
                          <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap rounded-lg bg-black/30 p-2 text-[10px] leading-relaxed text-muted">
                            {check.details}
                          </pre>
                        )}
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="mt-3 text-xs text-muted">
                    {selectedRun.status === "error" ? selectedRun.results?.error : "Waiting for results…"}
                  </p>
                )}
              </div>

              {findings.length > 0 && (
                <div className="card overflow-hidden">
                  <div className="border-b border-line px-4 py-3">
                    <h3 className="text-sm font-semibold text-ink">Findings ({findings.length})</h3>
                    <p className="text-xs text-muted">Critical and high findings block planning.</p>
                  </div>
                  <div className="overflow-x-auto">
                    <table className="table">
                      <thead>
                        <tr>
                          <th>Rule</th>
                          <th>Severity</th>
                          <th>Location</th>
                          <th>Finding</th>
                          <th>Remediation</th>
                        </tr>
                      </thead>
                      <tbody>
                        {findings.map((finding, index) => (
                          <tr key={`${finding.rule_id}-${index}`}>
                            <td className="font-mono text-[10px] text-cyan-200">{finding.rule_id}</td>
                            <td>
                              <StatusBadge status={finding.severity} label={finding.severity} />
                            </td>
                            <td className="font-mono text-[10px] text-faint">
                              {finding.file ?? "—"}
                              {finding.line ? `:${finding.line}` : ""}
                            </td>
                            <td className="text-xs text-slate-300">{finding.message}</td>
                            <td className="max-w-[280px] text-[11px] text-muted">{finding.remediation}</td>
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
              icon={<ShieldCheck size={26} />}
              title="No validation runs"
              description="Run the validation pipeline to execute the real Terraform CLI and deterministic policy checks."
              action={
                <button className="btn btn-primary" onClick={() => start.mutate()} disabled={start.isPending}>
                  <Play size={15} /> Run validation
                </button>
              }
            />
          )}
        </div>

        <div className="space-y-2">
          <SectionTitle title="History" description={`${runs.data?.items.length ?? 0} run(s)`} />
          <div className="space-y-2">
            {(runs.data?.items ?? []).map((run) => (
              <button
                key={run.id}
                onClick={() => setSelectedRunId(run.id)}
                className={clsx(
                  "w-full rounded-xl border p-2.5 text-left transition",
                  selectedRunId === run.id
                    ? "border-cyan-500/40 bg-cyan-500/5"
                    : "border-line bg-surface/30 hover:border-line-bright",
                )}
              >
                <div className="flex items-center justify-between">
                  <StatusBadge status={run.status} />
                  <span className="text-[10px] text-faint">{timeAgo(run.created_at)}</span>
                </div>
                <p className="mt-1.5 text-[11px] text-muted">
                  Attempt {run.attempt} · {run.results?.report?.summary?.errors ?? 0} errors ·{" "}
                  {run.results?.report?.summary?.modules ?? 0} modules
                </p>
              </button>
            ))}
            {runs.data?.items.length === 0 && <p className="text-xs text-faint">No runs yet.</p>}
          </div>
        </div>
      </div>
    </div>
  );
}
