import { useQuery } from "@tanstack/react-query";
import { Cpu, Database, Lock, Server, ShieldAlert, Sparkles, TerminalSquare } from "lucide-react";
import { api } from "../lib/api";
import { LoadingBlock, PageHeader } from "../components/UI";
import { StatusBadge } from "../components/StatusBadge";

export function SystemPage() {
  const system = useQuery({ queryKey: ["system"], queryFn: api.system.info });
  const policies = useQuery({ queryKey: ["policies"], queryFn: api.system.policies });

  if (system.isLoading) return <LoadingBlock />;
  const data = system.data;
  if (!data) return null;

  const infoCards = [
    {
      label: "AI reasoning",
      icon: Sparkles,
      value: data.llm.available ? data.llm.model : "Deterministic reasoner",
      detail: data.llm.available
        ? `${data.llm.active_provider} · model-backed`
        : "No API key configured. Rule-based, schema-valid output.",
      tone: data.llm.available ? "violet" : "info",
    },
    {
      label: "Terraform CLI",
      icon: TerminalSquare,
      value: data.terraform.terraform_version ?? "Not installed",
      detail: data.terraform.binary ?? "Set TERRAFORM_BINARY",
      tone: data.terraform.available ? "good" : "bad",
    },
    {
      label: "Metadata database",
      icon: Database,
      value: data.database === "postgresql" ? "PostgreSQL" : "SQLite (dev)",
      detail: data.database === "postgresql" ? "Production-ready metadata store" : "Local development mode",
      tone: data.database === "postgresql" ? "good" : "warn",
    },
    {
      label: "Artifact store",
      icon: Server,
      value: "Filesystem",
      detail: "Uploads, plans and logs are stored outside the database.",
      tone: "info",
    },
  ];

  return (
    <div className="space-y-5">
      <PageHeader
        eyebrow="Platform"
        title="System"
        description="Provider configuration, execution tooling and the deterministic security policy catalog."
      />

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {infoCards.map((card) => (
          <div key={card.label} className="card p-4">
            <div className="flex items-start justify-between">
              <div>
                <p className="text-[11px] font-semibold uppercase tracking-[0.1em] text-faint">{card.label}</p>
                <p className="mt-2 text-sm font-medium text-ink">{card.value}</p>
                <p className="mt-1 line-clamp-2 text-xs text-muted">{card.detail}</p>
              </div>
              <card.icon size={17} className="text-accent" />
            </div>
            <StatusBadge className="mt-3" status={card.tone === "good" ? "active" : card.tone === "bad" ? "failed" : "pending"} label={card.tone === "good" ? "healthy" : card.tone === "bad" ? "unavailable" : "attention"} />
          </div>
        ))}
      </div>

      {!data.llm.available && (
        <div className="card border-cyan-500/20 bg-cyan-500/[0.03] p-4 text-sm">
          <p className="flex items-center gap-2 font-medium text-ink">
            <Sparkles size={16} className="text-cyan-300" /> Deterministic mode is active
          </p>
          <p className="mt-1 text-xs leading-relaxed text-muted">{data.llm.hint}</p>
          <p className="mt-2 text-xs text-muted">
            The deterministic architect produces real, schema-validated Azure designs, Terraform projects and
            cost estimates. Add <span className="font-mono text-cyan-200">AZURE_OPENAI_*</span> or{" "}
            <span className="font-mono text-cyan-200">LLM_API_KEY</span> to enable model-backed reasoning.
          </p>
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-[1.4fr_1fr]">
        <section className="card overflow-hidden">
          <div className="flex items-center justify-between border-b border-line px-4 py-3">
            <div>
              <h3 className="text-sm font-semibold text-ink">Security policy catalog</h3>
              <p className="text-xs text-muted">Deterministic rules run on every generation and edit.</p>
            </div>
            <span className="badge">{policies.data?.items.length ?? 0} rules</span>
          </div>
          <div className="max-h-[430px] overflow-auto">
            <table className="table">
              <thead>
                <tr>
                  <th>Rule</th>
                  <th>Severity</th>
                  <th>Enforcement</th>
                </tr>
              </thead>
              <tbody>
                {(policies.data?.items as { id: string; title: string; severity: string; can_disable: boolean }[] | undefined)?.map((rule) => (
                  <tr key={rule.id}>
                    <td>
                      <p className="font-mono text-[11px] text-cyan-200">{rule.id}</p>
                      <p className="text-xs text-slate-300">{rule.title}</p>
                    </td>
                    <td>
                      <StatusBadge status={rule.severity} label={rule.severity} />
                    </td>
                    <td className="text-xs text-muted">
                      {rule.can_disable ? (
                        "Configurable per project"
                      ) : (
                        <span className="flex items-center gap-1 text-violet-300">
                          <Lock size={12} /> Not disableable
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        <div className="space-y-4">
          <section className="card p-4">
            <h3 className="flex items-center gap-2 text-sm font-semibold text-ink">
              <ShieldAlert size={16} className="text-rose-300" /> Execution guardrails
            </h3>
            <ul className="mt-3 space-y-2.5 text-xs text-muted">
              {[
                "Apply uses the exact saved binary plan and verifies its SHA-256 before execution.",
                "A staleness preflight re-plans with -refresh=false and refuses to apply if the change set differs.",
                "Architecture and deployment approvals are separate, human-only records.",
                "Any code, architecture or target change invalidates approvals.",
                "Terraform runs in an allow-listed environment; credentials never enter prompts.",
                "No automatic terraform destroy or rollback is ever executed.",
              ].map((item) => (
                <li key={item} className="flex gap-2">
                  <span className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-cyan-400" />
                  {item}
                </li>
              ))}
            </ul>
          </section>

          <section className="card p-4">
            <h3 className="flex items-center gap-2 text-sm font-semibold text-ink">
              <Cpu size={16} className="text-accent" /> Features
            </h3>
            <div className="mt-3 grid grid-cols-2 gap-2">
              {Object.entries(data.features).map(([feature, enabled]) => (
                <div key={feature} className="flex items-center gap-2 rounded-lg border border-line/70 bg-surface/40 px-2.5 py-2 text-[11px] text-muted">
                  <span className={`h-1.5 w-1.5 rounded-full ${enabled ? "bg-emerald-400" : "bg-slate-600"}`} />
                  {feature.replaceAll("_", " ")}
                </div>
              ))}
            </div>
          </section>
        </div>
      </div>
    </div>
  );
}
