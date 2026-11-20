import { useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  ArrowRight,
  Boxes,
  CheckCircle2,
  ChevronDown,
  CircleDollarSign,
  Download,
  Globe2,
  Loader2,
  Lock,
  Network,
  RefreshCw,
  Send,
  ShieldCheck,
  Sparkles,
  Table2,
  XCircle,
} from "lucide-react";
import clsx from "clsx";
import { api, apiErrorMessage } from "../../lib/api";
import { formatMoney, statusLabel, shortHash, statusTone } from "../../lib/format";
import { StatusBadge } from "../../components/StatusBadge";
import { EmptyState, LoadingBlock, SectionTitle } from "../../components/UI";
import { MermaidDiagram } from "../../components/MermaidDiagram";
import { Modal } from "../../components/Modal";
import { useToast } from "../../context/ToastContext";
import type { ArchitectureVersion } from "../../lib/types";

const VIEWS = [
  { key: "overview", label: "Overview", icon: Boxes },
  { key: "network", label: "Network", icon: Network },
  { key: "security", label: "Security", icon: ShieldCheck },
  { key: "cost", label: "Cost", icon: CircleDollarSign },
  { key: "inventory", label: "Inventory", icon: Table2 },
  { key: "decisions", label: "Decisions", icon: Sparkles },
] as const;

export function ArchitectureTab() {
  const { projectId = "" } = useParams();
  const queryClient = useQueryClient();
  const toast = useToast();
  const [view, setView] = useState<(typeof VIEWS)[number]["key"]>("overview");
  const [selectedVersion, setSelectedVersion] = useState<string | null>(null);
  const [approveOpen, setApproveOpen] = useState(false);
  const [note, setNote] = useState("");
  const [changesOpen, setChangesOpen] = useState(false);
  const [feedback, setFeedback] = useState("");
  const [generateOpen, setGenerateOpen] = useState(false);
  const [instructions, setInstructions] = useState("");

  const versions = useQuery({
    queryKey: ["architectures", projectId],
    queryFn: () => api.architecture.versions(projectId),
  });

  const architecture = useQuery({
    queryKey: ["architecture", projectId, selectedVersion ?? "current"],
    queryFn: () =>
      selectedVersion ? api.architecture.get(selectedVersion) : api.architecture.current(projectId),
    retry: false,
  });

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["architecture", projectId] });
    queryClient.invalidateQueries({ queryKey: ["architectures", projectId] });
    queryClient.invalidateQueries({ queryKey: ["overview", projectId] });
    queryClient.invalidateQueries({ queryKey: ["project", projectId] });
  };

  const submit = useMutation({
    mutationFn: (id: string) => api.architecture.submit(id),
    onSuccess: () => {
      toast.success("Submitted for approval");
      invalidate();
    },
    onError: (error) => toast.error(apiErrorMessage(error)),
  });

  const approve = useMutation({
    mutationFn: () => api.architecture.approve(architecture.data!.id, note),
    onSuccess: () => {
      toast.success("Architecture approved");
      setApproveOpen(false);
      setNote("");
      invalidate();
    },
    onError: (error) => toast.error(apiErrorMessage(error)),
  });

  const requestChanges = useMutation({
    mutationFn: () => api.architecture.requestChanges(architecture.data!.id, feedback),
    onSuccess: (version) => {
      toast.success(`Architecture v${version.version} generated`);
      setChangesOpen(false);
      setFeedback("");
      setSelectedVersion(null);
      invalidate();
    },
    onError: (error) => toast.error(apiErrorMessage(error)),
  });

  const generate = useMutation({
    mutationFn: () => api.architecture.generate(projectId, { instructions }),
    onSuccess: (version) => {
      toast.success(`Architecture v${version.version} generated`);
      setGenerateOpen(false);
      setInstructions("");
      setSelectedVersion(null);
      invalidate();
    },
    onError: (error) => toast.error(apiErrorMessage(error)),
  });

  const unresolvedMandatory = useMemo(
    () =>
      (architecture.data?.review?.recommendations ?? []).filter(
        (recommendation) => recommendation.mandatory && recommendation.status === "proposed",
      ),
    [architecture.data],
  );

  if (architecture.isLoading) return <LoadingBlock label="Loading architecture…" />;

  if (!architecture.data) {
    return (
      <div className="space-y-4">
        <EmptyState
          icon={<Boxes size={28} />}
          title="No architecture generated"
          description="The agent designs compute, networking, data, security, observability and DR from the captured requirements. Review questions first for a better design."
          action={
            <div className="flex flex-wrap justify-center gap-2">
              <button className="btn btn-primary" onClick={() => generate.mutate()} disabled={generate.isPending}>
                {generate.isPending ? <Loader2 size={15} className="animate-spin" /> : <Sparkles size={15} />}
                Generate architecture
              </button>
              <Link className="btn" to={`/projects/${projectId}/chat`}>
                Review requirements
              </Link>
            </div>
          }
        />
      </div>
    );
  }

  const version = architecture.data;
  const spec = version.architecture;
  const review = version.review;
  const network = spec?.networks?.[0];
  const isLatest = !selectedVersion || versions.data?.items?.[0]?.id === version.id;
  const canApprove = version.approval_status === "awaiting_approval" && unresolvedMandatory.length === 0;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-lg font-semibold text-ink">Architecture v{version.version}</h2>
            <StatusBadge status={version.approval_status} />
            {isLatest && <span className="badge border-cyan-500/30 bg-cyan-500/10 text-[10px] text-cyan-300">current</span>}
          </div>
          <p className="mt-1 max-w-3xl text-sm leading-relaxed text-muted">{version.summary}</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <div className="relative">
            <select
              className="select min-w-[150px] py-1.5 text-xs"
              value={selectedVersion ?? version.id}
              onChange={(event) => setSelectedVersion(event.target.value)}
            >
              {(versions.data?.items ?? []).map((item) => (
                <option key={item.id} value={item.id}>
                  v{item.version} · {statusLabel(item.approval_status)}
                </option>
              ))}
            </select>
            <ChevronDown size={12} className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-faint" />
          </div>
          <button className="btn btn-sm" onClick={() => setGenerateOpen(true)}>
            <RefreshCw size={13} /> Regenerate
          </button>
          <button className="btn btn-sm" onClick={() => setChangesOpen(true)}>
            Request changes
          </button>
          {version.approval_status === "draft" && isLatest && (
            <button className="btn btn-primary btn-sm" onClick={() => submit.mutate(version.id)} disabled={submit.isPending}>
              {submit.isPending ? <Loader2 size={13} className="animate-spin" /> : <Send size={13} />}
              Submit for approval
            </button>
          )}
          {version.approval_status === "awaiting_approval" && isLatest && (
            <button
              className="btn btn-primary btn-sm"
              onClick={() => setApproveOpen(true)}
              disabled={!canApprove}
              title={canApprove ? "Approve architecture" : "Resolve mandatory recommendations first"}
            >
              <ShieldCheck size={13} /> Approve
            </button>
          )}
        </div>
      </div>

      {unresolvedMandatory.length > 0 && version.approval_status === "awaiting_approval" && (
        <div className="flex flex-wrap items-center gap-2 rounded-xl border border-amber-500/30 bg-amber-500/5 px-3.5 py-2.5 text-xs text-amber-100">
          <AlertTriangle size={14} />
          {unresolvedMandatory.length} mandatory recommendation(s) still need an accept/reject decision.
          <Link className="underline" to={`/projects/${projectId}/review`}>
            Open review
          </Link>
        </div>
      )}

      {version.active_approval && (
        <div className="flex flex-wrap items-center gap-2 rounded-xl border border-emerald-500/25 bg-emerald-500/5 px-3.5 py-2.5 text-xs text-emerald-100">
          <CheckCircle2 size={14} />
          Approved · hash <span className="font-mono text-[10px]">{shortHash(version.active_approval.artifact_hash, 20)}</span>
          <span className="text-emerald-200/60">Immutable snapshot recorded in audit.</span>
        </div>
      )}

      <div className="flex gap-0.5 overflow-x-auto border-b border-line">
        {VIEWS.map((item) => (
          <button
            key={item.key}
            className={clsx("tab shrink-0", view === item.key && "tab-active")}
            onClick={() => setView(item.key)}
          >
            <item.icon size={14} /> {item.label}
          </button>
        ))}
      </div>

      {view === "overview" && spec && (
        <div className="space-y-4">
          <MermaidDiagram code={version.diagram ?? ""} height={540} />
          <div className="grid gap-4 xl:grid-cols-[1.5fr_1fr]">
            <div className="space-y-4">
              <section className="card p-4">
              <SectionTitle title="Architecture overview" />
              <p className="text-sm leading-relaxed text-slate-300">{spec.summary}</p>
              <div className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-4">
                {[
                  ["Resources", spec.resources.length],
                  ["Modules", new Set(spec.resources.map((resource) => resource.module)).size],
                  ["Private endpoints", spec.resources.filter((resource) => resource.type === "azurerm_private_endpoint").length],
                  ["Review score", review?.score ?? "—"],
                ].map(([label, value]) => (
                  <div key={String(label)} className="rounded-lg border border-line bg-surface/40 p-2.5">
                    <p className="text-[10px] uppercase tracking-wider text-faint">{label}</p>
                    <p className="mt-0.5 text-lg font-semibold text-ink">{value}</p>
                  </div>
                ))}
              </div>
            </section>
            <section className="card p-4">
              <SectionTitle title="Assumptions & limitations" />
              <div className="grid gap-3 sm:grid-cols-2">
                <div>
                  <p className="text-[10px] font-semibold uppercase tracking-wider text-faint">Assumptions</p>
                  <ul className="mt-1.5 space-y-1.5">
                    {(spec.assumptions ?? []).map((assumption) => (
                      <li key={assumption} className="flex gap-2 text-[11px] leading-relaxed text-slate-300">
                        <span className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-cyan-400" />
                        {assumption}
                      </li>
                    ))}
                  </ul>
                </div>
                <div>
                  <p className="text-[10px] font-semibold uppercase tracking-wider text-faint">Limitations</p>
                  <ul className="mt-1.5 space-y-1.5">
                    {(spec.limitations ?? []).map((limitation) => (
                      <li key={limitation} className="flex gap-2 text-[11px] leading-relaxed text-muted">
                        <span className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-amber-400" />
                        {limitation}
                      </li>
                    ))}
                  </ul>
                </div>
              </div>
            </section>
          </div>

          <div className="space-y-4">
            <section className="card p-4">
              <SectionTitle title="Cost estimate" description={version.cost?.disclaimer} />
              <div className="flex items-end justify-between">
                <div>
                  <p className="text-3xl font-semibold tracking-tight text-ink">
                    {formatMoney(version.cost?.monthly_total, version.cost?.currency)}
                  </p>
                  <p className="text-xs text-muted">indicative monthly run rate</p>
                </div>
                <button className="btn btn-ghost btn-sm" onClick={() => setView("cost")}>
                  Breakdown <ArrowRight size={13} />
                </button>
              </div>
              <div className="mt-3 space-y-1.5">
                {(version.cost?.lines ?? []).slice(0, 5).map((line, index) => (
                  <div key={`${line.service}-${index}`} className="flex items-center justify-between text-[11px]">
                    <span className="truncate text-muted">{line.sku}</span>
                    <span className="tabular-nums text-slate-300">{formatMoney(line.monthly)}</span>
                  </div>
                ))}
              </div>
              <p className="mt-3 text-[10px] text-faint">Not an Azure quotation. Prices vary by agreement and usage.</p>
            </section>

            <section className="card p-4">
              <SectionTitle
                title="Review"
                actions={
                  <Link className="btn btn-ghost btn-sm" to={`/projects/${projectId}/review`}>
                    Details <ArrowRight size={13} />
                  </Link>
                }
              />
              <div className="flex items-center gap-3">
                <div className="relative grid h-16 w-16 place-items-center">
                  <svg viewBox="0 0 36 36" className="absolute h-16 w-16 -rotate-90">
                    <circle cx="18" cy="18" r="15.5" fill="none" stroke="#1e3048" strokeWidth="3" />
                    <circle
                      cx="18"
                      cy="18"
                      r="15.5"
                      fill="none"
                      stroke="#22d3ee"
                      strokeWidth="3"
                      strokeDasharray={`${(review?.score ?? 0) * 0.974} 100`}
                      strokeLinecap="round"
                    />
                  </svg>
                  <span className="text-sm font-semibold text-ink">{review?.score ?? "—"}</span>
                </div>
                <div className="min-w-0">
                  <p className="text-xs text-slate-300">{review?.overall_assessment || "Not reviewed yet."}</p>
                  <p className="mt-1 text-[11px] text-faint">
                    {(review?.recommendations ?? []).filter((item) => item.status === "accepted").length} accepted ·{" "}
                    {(review?.recommendations ?? []).filter((item) => item.status === "rejected").length} rejected
                  </p>
                </div>
              </div>
              {(review?.risks ?? []).length > 0 && (
                <ul className="mt-3 space-y-1.5 border-t border-line pt-2.5">
                  {review!.risks.map((risk) => (
                    <li key={risk} className="flex gap-2 text-[11px] text-amber-200/80">
                      <AlertTriangle size={11} className="mt-0.5 shrink-0" /> {risk}
                    </li>
                  ))}
                </ul>
              )}
            </section>

            <section className="card p-4">
              <SectionTitle title="Availability & DR" />
              <dl className="space-y-2 text-xs">
                {[
                  ["Availability zones", spec.availability.zones ? "Enabled" : "Single zone"],
                  ["Multi-region", spec.availability.multi_region ? "Yes" : "No"],
                  ["RPO", spec.availability.rpo],
                  ["RTO", spec.availability.rto],
                  ["Backup", spec.availability.backup],
                ].map(([label, value]) => (
                  <div key={label} className="flex gap-3">
                    <dt className="w-28 shrink-0 text-faint">{label}</dt>
                    <dd className="text-slate-300">{value}</dd>
                  </div>
                ))}
              </dl>
            </section>
          </div>
          </div>
        </div>
      )}

      {view === "network" && network && spec && (
        <div className="space-y-4">
          <section className="card p-4">
            <SectionTitle title={`Virtual network · ${network.name}`} description={network.address_space.join(", ")} />
            <div className="overflow-x-auto">
              <table className="table">
                <thead>
                  <tr>
                    <th>Subnet</th>
                    <th>CIDR</th>
                    <th>Purpose</th>
                    <th>NSG</th>
                    <th>Delegation</th>
                  </tr>
                </thead>
                <tbody>
                  {network.subnets.map((subnet) => (
                    <tr key={subnet.name}>
                      <td className="font-mono text-[11px] text-cyan-200">{subnet.name}</td>
                      <td className="font-mono text-[11px] text-slate-300">{subnet.cidr}</td>
                      <td className="text-muted">{subnet.purpose}</td>
                      <td className="text-muted">{subnet.nsg ?? "—"}</td>
                      <td className="text-muted">{subnet.delegation ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          <div className="grid gap-4 lg:grid-cols-2">
            <section className="card p-4">
              <SectionTitle title="NSG intent" description="Applied by the networking module." />
              <div className="space-y-3">
                {Object.entries(network.nsg_rules ?? {}).map(([nsg, rules]) => (
                  <div key={nsg}>
                    <p className="text-xs font-medium text-ink">{nsg}</p>
                    <div className="mt-1 space-y-1">
                      {(rules as { name: string; direction: string; access: string; destination_port_ranges: string[]; source_address_prefixes: string[] }[]).map((rule) => (
                        <div key={rule.name} className="flex flex-wrap items-center gap-2 rounded-lg border border-line/60 bg-surface/30 px-2 py-1.5 text-[10px]">
                          <StatusBadge status={rule.access === "Allow" ? "active" : "failed"} label={rule.access} />
                          <span className="font-mono text-slate-300">{rule.direction}</span>
                          <span className="font-mono text-slate-300">ports {rule.destination_port_ranges.join(",")}</span>
                          <span className="text-muted">from {rule.source_address_prefixes.join(",")}</span>
                          <span className="ml-auto text-faint">{rule.name}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            </section>
            <section className="card p-4">
              <SectionTitle title="DNS, egress & public IPs" />
              <dl className="space-y-2 text-xs">
                <div className="flex gap-3">
                  <dt className="w-32 shrink-0 text-faint">NAT gateway</dt>
                  <dd className="text-slate-300">{network.nat_gateway ? "Enabled for controlled egress" : "Not required"}</dd>
                </div>
                <div className="flex gap-3">
                  <dt className="w-32 shrink-0 text-faint">Private DNS</dt>
                  <dd className="space-y-1">
                    {(network.private_dns_zones ?? []).length === 0 ? "None" : network.private_dns_zones.map((zone) => (
                      <p key={zone} className="font-mono text-[11px] text-cyan-200">{zone}</p>
                    ))}
                  </dd>
                </div>
                <div className="flex gap-3">
                  <dt className="w-32 shrink-0 text-faint">Public IPs</dt>
                  <dd className="text-slate-300">
                    {spec.resources.filter((resource) => resource.type === "azurerm_public_ip").length} configured
                  </dd>
                </div>
              </dl>
              <p className="mt-3 rounded-lg border border-line bg-surface/30 px-2.5 py-2 text-[11px] leading-relaxed text-muted">
                Subnet overlap and CIDR validity are checked before the architecture can be approved.
              </p>
            </section>
          </div>
        </div>
      )}

      {view === "security" && spec && (
        <div className="grid gap-4 lg:grid-cols-[1fr_1fr]">
          <section className="card p-4">
            <SectionTitle title="Controls" />
            <div className="grid gap-2 sm:grid-cols-2">
              {[
                ["Managed identities", spec.security.managed_identity],
                ["Key Vault", spec.security.key_vault],
                ["Private endpoints", spec.security.private_endpoints],
                ["Diagnostic settings", spec.security.diagnostics],
              ].map(([label, enabled]) => (
                <div
                  key={String(label)}
                  className={clsx(
                    "flex items-center gap-2 rounded-lg border px-2.5 py-2 text-xs",
                    enabled ? "border-emerald-500/25 bg-emerald-500/5 text-emerald-200" : "border-line bg-surface/30 text-muted",
                  )}
                >
                  {enabled ? <CheckCircle2 size={13} /> : <XCircle size={13} />}
                  {label}
                </div>
              ))}
            </div>
            <div className="mt-4">
              <p className="text-[10px] font-semibold uppercase tracking-wider text-faint">Encryption</p>
              <p className="mt-1 text-xs text-slate-300">{spec.security.encryption}</p>
            </div>
            <div className="mt-4">
              <p className="text-[10px] font-semibold uppercase tracking-wider text-faint">Notes</p>
              <ul className="mt-1 space-y-1.5">
                {spec.security.notes.map((noteText) => (
                  <li key={noteText} className="flex gap-2 text-[11px] leading-relaxed text-muted">
                    <Lock size={11} className="mt-0.5 shrink-0 text-cyan-400" /> {noteText}
                  </li>
                ))}
              </ul>
            </div>
          </section>
          <section className="card p-4">
            <SectionTitle title="RBAC assignments" description="Least-privilege, identity-based access." />
            <table className="table">
              <thead>
                <tr>
                  <th>Principal</th>
                  <th>Role</th>
                  <th>Scope</th>
                </tr>
              </thead>
              <tbody>
                {spec.security.rbac.map((assignment, index) => (
                  <tr key={`${assignment.role}-${index}`}>
                    <td className="text-slate-300">{assignment.principal}</td>
                    <td className="text-slate-300">{assignment.role}</td>
                    <td className="font-mono text-[11px] text-muted">{assignment.scope}</td>
                  </tr>
                ))}
                {spec.security.rbac.length === 0 && (
                  <tr>
                    <td colSpan={3} className="text-center text-xs text-faint">
                      No explicit RBAC assignments.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </section>
        </div>
      )}

      {view === "cost" && version.cost && (
        <div className="space-y-4">
          <section className="card p-4">
            <div className="flex flex-wrap items-end justify-between gap-3">
              <div>
                <SectionTitle title="Indicative monthly cost" />
                <p className="text-4xl font-semibold tracking-tight text-ink">
                  {formatMoney(version.cost.monthly_total, version.cost.currency)}
                </p>
                <p className="mt-1 text-xs text-muted">{version.cost.disclaimer}</p>
              </div>
              <div className="text-right text-[11px] text-faint">
                <p>Deterministic SKU-based estimate</p>
                <p>Live retail API when enabled</p>
              </div>
            </div>
          </section>
          <section className="card overflow-hidden">
            <div className="overflow-x-auto">
              <table className="table">
                <thead>
                  <tr>
                    <th>Service</th>
                    <th>SKU</th>
                    <th className="text-right">Quantity</th>
                    <th className="text-right">Hourly</th>
                    <th className="text-right">Monthly</th>
                    <th>Source</th>
                  </tr>
                </thead>
                <tbody>
                  {version.cost.lines.map((line, index) => (
                    <tr key={`${line.service}-${index}`}>
                      <td className="capitalize text-slate-300">{line.service.replaceAll("_", " ")}</td>
                      <td className="text-muted">{line.sku}</td>
                      <td className="text-right tabular-nums text-muted">{line.quantity}</td>
                      <td className="text-right tabular-nums text-muted">${line.hourly.toFixed(4)}</td>
                      <td className="text-right tabular-nums text-slate-200">{formatMoney(line.monthly)}</td>
                      <td>
                        <span className={clsx("badge text-[9px]", line.source === "azure-retail-api" ? "border-cyan-500/30 text-cyan-300" : "text-faint")}>
                          {line.source}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        </div>
      )}

      {view === "inventory" && (
        <section className="card overflow-hidden">
          <div className="border-b border-line px-4 py-3">
            <h3 className="text-sm font-semibold text-ink">Infrastructure inventory</h3>
            <p className="text-xs text-muted">{version.inventory.length} resources derived from the architecture.</p>
          </div>
          <div className="overflow-x-auto">
            <table className="table">
              <thead>
                <tr>
                  <th>Component</th>
                  <th>Azure resource</th>
                  <th>Terraform type</th>
                  <th>Name</th>
                  <th>Configuration</th>
                  <th>Module</th>
                </tr>
              </thead>
              <tbody>
                {version.inventory.map((row, index) => (
                  <tr key={`${row.terraform_type}-${index}`}>
                    <td className="text-slate-300">{row.component}</td>
                    <td className="text-muted">{row.azure_resource}</td>
                    <td className="font-mono text-[10px] text-cyan-200">{row.terraform_type}</td>
                    <td className="font-mono text-[10px] text-slate-300">{row.name}</td>
                    <td className="text-[11px] text-muted">{row.configuration}</td>
                    <td className="font-mono text-[10px] text-faint">{row.module}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {view === "decisions" && (
        <div className="space-y-3">
          {(version.decisions ?? []).map((decision) => (
            <section key={decision.id} className="card p-4">
              <div className="flex items-start justify-between gap-3">
                <h3 className="text-sm font-semibold text-ink">{decision.decision}</h3>
                <span className="badge border-emerald-500/25 bg-emerald-500/10 text-[10px] text-emerald-300">
                  {decision.status}
                </span>
              </div>
              <p className="mt-1.5 text-sm leading-relaxed text-slate-300">{decision.rationale}</p>
              {decision.source_requirement && (
                <p className="mt-2 rounded-lg border border-line bg-surface/30 px-2.5 py-1.5 text-[11px] text-muted">
                  Source requirement: {decision.source_requirement}
                </p>
              )}
              {decision.alternates_considered.length > 0 && (
                <p className="mt-2 text-[11px] text-faint">
                  Alternatives considered: {decision.alternates_considered.join("; ")}
                </p>
              )}
            </section>
          ))}
          {(version.decisions ?? []).length === 0 && (
            <EmptyState title="No decisions recorded" description="Generate the architecture again to capture decisions and rationale." />
          )}
        </div>
      )}

      <Modal
        open={changesOpen}
        onClose={() => setChangesOpen(false)}
        title="Request architecture changes"
        description="Describe what should change. A new version is generated and prior approvals are invalidated."
        footer={
          <>
            <button className="btn" onClick={() => setChangesOpen(false)}>
              Cancel
            </button>
            <button
              className="btn btn-primary"
              disabled={!feedback.trim() || requestChanges.isPending}
              onClick={() => requestChanges.mutate()}
            >
              {requestChanges.isPending ? <Loader2 size={15} className="animate-spin" /> : <Sparkles size={15} />}
              Generate new version
            </button>
          </>
        }
      >
        <textarea
          className="textarea min-h-28"
          placeholder="e.g. Add an Azure Front Door with WAF, switch SQL to Business Critical, and enable multi-region backups."
          value={feedback}
          onChange={(event) => setFeedback(event.target.value)}
        />
      </Modal>

      <Modal
        open={generateOpen}
        onClose={() => setGenerateOpen(false)}
        title="Regenerate architecture"
        description="Generate a new version from the current requirements. Existing approvals are invalidated."
        footer={
          <>
            <button className="btn" onClick={() => setGenerateOpen(false)}>
              Cancel
            </button>
            <button className="btn btn-primary" disabled={generate.isPending} onClick={() => generate.mutate()}>
              {generate.isPending ? <Loader2 size={15} className="animate-spin" /> : <RefreshCw size={15} />}
              Regenerate
            </button>
          </>
        }
      >
        <textarea
          className="textarea min-h-24"
          placeholder="Optional instructions or extra constraints for the new version…"
          value={instructions}
          onChange={(event) => setInstructions(event.target.value)}
        />
      </Modal>

      <Modal
        open={approveOpen}
        onClose={() => setApproveOpen(false)}
        title="Approve architecture"
        description="This records an immutable approval bound to the architecture hash. Code generation unlocks after approval."
        footer={
          <>
            <button className="btn" onClick={() => setApproveOpen(false)}>
              Cancel
            </button>
            <button className="btn btn-primary" disabled={approve.isPending} onClick={() => approve.mutate()}>
              {approve.isPending ? <Loader2 size={15} className="animate-spin" /> : <ShieldCheck size={15} />}
              Approve architecture
            </button>
          </>
        }
      >
        <div className="rounded-lg border border-line bg-surface/40 p-3 text-xs text-muted">
          Version v{version.version} · {version.resource_count} resources ·{" "}
          {formatMoney(version.cost?.monthly_total, version.cost?.currency)}/month indicative
        </div>
        <textarea
          className="textarea min-h-20"
          placeholder="Optional approval note (recorded in the audit log)…"
          value={note}
          onChange={(event) => setNote(event.target.value)}
        />
      </Modal>
    </div>
  );
}
