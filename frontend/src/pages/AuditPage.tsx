import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ScrollText, Search } from "lucide-react";
import { api } from "../lib/api";
import { formatDate, statusLabel, timeAgo } from "../lib/format";
import { EmptyState, LoadingBlock, PageHeader } from "../components/UI";

export function AuditPage() {
  const [query, setQuery] = useState("");
  const audit = useQuery({ queryKey: ["audit", "org"], queryFn: () => api.audit.organization(250) });

  const items = useMemo(() => {
    const all = audit.data?.items ?? [];
    if (!query.trim()) return all;
    const needle = query.toLowerCase();
    return all.filter(
      (event) =>
        event.action.toLowerCase().includes(needle) ||
        JSON.stringify(event.metadata ?? {}).toLowerCase().includes(needle),
    );
  }, [audit.data, query]);

  return (
    <div className="space-y-5">
      <PageHeader
        eyebrow="Governance"
        title="Audit log"
        description="Append-only events for every user action, AI artifact, approval and Terraform execution."
      />

      <div className="relative max-w-md">
        <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-faint" />
        <input
          className="input pl-9"
          placeholder="Filter by action or metadata…"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
      </div>

      {audit.isLoading ? (
        <LoadingBlock />
      ) : items.length === 0 ? (
        <EmptyState icon={<ScrollText size={28} />} title="No audit events" description="Actions will appear here as the project progresses." />
      ) : (
        <div className="card overflow-hidden">
          <div className="overflow-x-auto">
            <table className="table">
              <thead>
                <tr>
                  <th>Time</th>
                  <th>Action</th>
                  <th>Project</th>
                  <th>Actor</th>
                  <th>Details</th>
                </tr>
              </thead>
              <tbody>
                {items.map((event) => (
                  <tr key={event.id}>
                    <td className="whitespace-nowrap text-xs text-muted" title={formatDate(event.timestamp)}>
                      {timeAgo(event.timestamp)}
                    </td>
                    <td>
                      <span className="font-mono text-[11px] text-cyan-200">{event.action}</span>
                    </td>
                    <td className="font-mono text-[10px] text-faint">{event.project_id?.slice(0, 8) ?? "—"}</td>
                    <td className="font-mono text-[10px] text-faint">{event.user_id?.slice(0, 8) ?? "system"}</td>
                    <td>
                      <details className="group">
                        <summary className="cursor-pointer list-none text-[11px] text-muted hover:text-ink">
                          {Object.keys(event.metadata ?? {}).length} field(s)
                        </summary>
                        <pre className="mt-1.5 max-w-xl overflow-auto rounded-lg border border-line bg-black/40 p-2 text-[10px] leading-relaxed text-slate-300">
                          {JSON.stringify(event.metadata, null, 2)}
                        </pre>
                      </details>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
      <p className="text-xs text-faint">
        Showing {items.length} event(s). Actions labeled {statusLabel("approved")} identify explicit human approvals.
      </p>
    </div>
  );
}
