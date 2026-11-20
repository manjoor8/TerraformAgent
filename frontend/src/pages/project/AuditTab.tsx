import { useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ScrollText, Search } from "lucide-react";
import { api } from "../../lib/api";
import { formatDate, timeAgo } from "../../lib/format";
import { EmptyState, LoadingBlock, PageHeader } from "../../components/UI";

export function AuditTab() {
  const { projectId = "" } = useParams();
  const [filter, setFilter] = useState("");
  const audit = useQuery({ queryKey: ["audit", projectId], queryFn: () => api.audit.project(projectId, 300) });

  const items = useMemo(() => {
    const all = audit.data?.items ?? [];
    if (!filter.trim()) return all;
    const needle = filter.toLowerCase();
    return all.filter(
      (event) =>
        event.action.toLowerCase().includes(needle) ||
        JSON.stringify(event.metadata ?? {}).toLowerCase().includes(needle),
    );
  }, [audit.data, filter]);

  if (audit.isLoading) return <LoadingBlock />;

  return (
    <div className="space-y-4">
      <PageHeader
        eyebrow="Traceability"
        title="Project audit trail"
        description="Append-only events for AI artifacts, approvals, code changes, validation, plans and deployments."
      />

      <div className="relative max-w-md">
        <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-faint" />
        <input className="input pl-9" placeholder="Filter events…" value={filter} onChange={(event) => setFilter(event.target.value)} />
      </div>

      {items.length === 0 ? (
        <EmptyState icon={<ScrollText size={26} />} title="No events" description="Project activity will appear here." />
      ) : (
        <div className="card overflow-hidden">
          <div className="overflow-x-auto">
            <table className="table">
              <thead>
                <tr>
                  <th>Time</th>
                  <th>Action</th>
                  <th>Actor</th>
                  <th>Metadata</th>
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
                    <td className="font-mono text-[10px] text-faint">{event.user_id?.slice(0, 8) ?? "system"}</td>
                    <td>
                      <details>
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
    </div>
  );
}
