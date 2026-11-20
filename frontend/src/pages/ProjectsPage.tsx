import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ArrowUpRight, FolderKanban, Plus, Search } from "lucide-react";
import { api } from "../lib/api";
import { STATUS_LABELS, timeAgo } from "../lib/format";
import { StatusBadge } from "../components/StatusBadge";
import { CreateProjectModal } from "../components/CreateProjectModal";
import { EmptyState, LoadingBlock, PageHeader } from "../components/UI";

export function ProjectsPage() {
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("");
  const [createOpen, setCreateOpen] = useState(false);

  const projects = useQuery({
    queryKey: ["projects", "list", status, query],
    queryFn: () => api.projects.list({ status: status || undefined, q: query || undefined }),
  });

  const items = projects.data?.items ?? [];

  return (
    <div className="space-y-5">
      <PageHeader
        eyebrow="Portfolio"
        title="Projects"
        description="Every infrastructure project with its lifecycle state, requirements and deployment artifacts."
        actions={
          <button className="btn btn-primary" onClick={() => setCreateOpen(true)}>
            <Plus size={15} /> New project
          </button>
        }
      />

      <div className="flex flex-wrap gap-2">
        <div className="relative min-w-[220px] flex-1">
          <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-faint" />
          <input
            className="input pl-9"
            placeholder="Search projects…"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        </div>
        <select className="select w-auto min-w-[200px]" value={status} onChange={(event) => setStatus(event.target.value)}>
          <option value="">All statuses</option>
          {Object.entries(STATUS_LABELS)
            .filter(([key]) => !["passed", "failed", "skipped", "error", "running", "queued", "succeeded", "cancelled", "generated", "approved", "rejected", "stale", "applied", "awaiting_approval", "superseded", "pending", "extracting", "extracted", "verified", "incomplete", "unsupported", "read_only", "unverified", "active"].includes(key))
            .map(([key, label]) => (
              <option key={key} value={key}>
                {label}
              </option>
            ))}
        </select>
      </div>

      {projects.isLoading ? (
        <LoadingBlock />
      ) : items.length === 0 ? (
        <EmptyState
          icon={<FolderKanban size={28} />}
          title="No projects match"
          description="Create a project to start collecting requirements and designing Azure infrastructure."
          action={
            <button className="btn btn-primary" onClick={() => setCreateOpen(true)}>
              <Plus size={15} /> Create project
            </button>
          }
        />
      ) : (
        <div className="card overflow-hidden">
          <div className="overflow-x-auto">
            <table className="table">
              <thead>
                <tr>
                  <th>Project</th>
                  <th>Status</th>
                  <th>Environment</th>
                  <th>Region</th>
                  <th className="text-right">Requirements</th>
                  <th className="text-right">Docs</th>
                  <th>Updated</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {items.map((project) => (
                  <tr key={project.id}>
                    <td>
                      <Link to={`/projects/${project.id}`} className="group block">
                        <p className="font-medium text-ink group-hover:text-accent">{project.name}</p>
                        <p className="mt-0.5 line-clamp-1 max-w-[380px] text-xs text-muted">
                          {project.description || "No description"}
                        </p>
                      </Link>
                    </td>
                    <td>
                      <StatusBadge status={project.status} />
                    </td>
                    <td className="capitalize text-muted">{project.environment}</td>
                    <td className="text-muted">{project.region}</td>
                    <td className="text-right tabular-nums text-muted">{project.counts.requirements}</td>
                    <td className="text-right tabular-nums text-muted">{project.counts.documents}</td>
                    <td className="whitespace-nowrap text-xs text-faint">{timeAgo(project.updated_at)}</td>
                    <td className="text-right">
                      <Link className="btn btn-ghost btn-sm" to={`/projects/${project.id}`}>
                        Open <ArrowUpRight size={13} />
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <CreateProjectModal
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={(project) => navigate(`/projects/${project.id}/chat`)}
      />
    </div>
  );
}
