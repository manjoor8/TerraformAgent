import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  CheckCircle2,
  Download,
  FileCode2,
  FilePlus2,
  FolderTree,
  Loader2,
  Play,
  RefreshCw,
  Save,
  Search,
  ShieldCheck,
  Trash2,
} from "lucide-react";
import clsx from "clsx";
import { api, apiErrorMessage } from "../../lib/api";
import { shortHash, timeAgo } from "../../lib/format";
import { StatusBadge } from "../../components/StatusBadge";
import { CodeEditor } from "../../components/CodeEditor";
import { Modal } from "../../components/Modal";
import { EmptyState, LoadingBlock, SectionTitle } from "../../components/UI";
import { useToast } from "../../context/ToastContext";

function fileIcon(path: string) {
  if (path.endsWith(".tf")) return <FileCode2 size={13} className="text-cyan-300" />;
  if (path.endsWith(".md")) return <FileCode2 size={13} className="text-violet-300" />;
  if (path.endsWith(".sh") || path.endsWith(".ps1")) return <FileCode2 size={13} className="text-amber-300" />;
  return <FileCode2 size={13} className="text-faint" />;
}

export function TerraformTab() {
  const { projectId = "" } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const toast = useToast();

  const terraform = useQuery({
    queryKey: ["terraform", projectId],
    queryFn: () => api.terraform.current(projectId),
    retry: false,
  });

  const [filter, setFilter] = useState("");
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [original, setOriginal] = useState("");
  const [newFileOpen, setNewFileOpen] = useState(false);
  const [newPath, setNewPath] = useState("");
  const [mapOpen, setMapOpen] = useState(false);

  const project = terraform.data;
  const files = project?.files ?? [];

  useEffect(() => {
    if (!files.length) {
      setSelectedPath(null);
      return;
    }
    if (!selectedPath || !files.some((file) => file.path === selectedPath)) {
      const preferred =
        files.find((file) => file.path.startsWith("environments/") && file.path.endsWith("/main.tf"))?.path ??
        files.find((file) => file.path.endsWith("/main.tf"))?.path ??
        files.find((file) => file.path.endsWith(".tf"))?.path ??
        files[0].path;
      setSelectedPath(preferred);
    }
  }, [files, selectedPath]);

  useEffect(() => {
    const file = files.find((item) => item.path === selectedPath);
    if (file) {
      setDraft(file.content);
      setOriginal(file.content);
    }
  }, [selectedPath, project?.id]);

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["terraform", projectId] });
    queryClient.invalidateQueries({ queryKey: ["overview", projectId] });
    queryClient.invalidateQueries({ queryKey: ["project", projectId] });
    queryClient.invalidateQueries({ queryKey: ["validation", projectId] });
  };

  const generate = useMutation({
    mutationFn: () => api.terraform.generate(projectId),
    onSuccess: (result) => {
      toast.success(`Terraform v${result.version} generated`);
      invalidate();
    },
    onError: (error) => toast.error(apiErrorMessage(error)),
  });

  const save = useMutation({
    mutationFn: () => api.terraform.updateFile(project!.id, selectedPath!, draft),
    onSuccess: (result) => {
      toast.success(`Saved · new version v${result.version}`);
      invalidate();
      const updated = result.files?.find((file) => file.path === selectedPath);
      if (updated) {
        setOriginal(updated.content);
        setDraft(updated.content);
      }
    },
    onError: (error) => toast.error(apiErrorMessage(error)),
  });

  const createFile = useMutation({
    mutationFn: () => api.terraform.createFile(project!.id, newPath, "# New Terraform file\n"),
    onSuccess: (result) => {
      toast.success(`File created · v${result.version}`);
      setNewFileOpen(false);
      setSelectedPath(newPath);
      setNewPath("");
      invalidate();
    },
    onError: (error) => toast.error(apiErrorMessage(error)),
  });

  const removeFile = useMutation({
    mutationFn: () => api.terraform.deleteFile(project!.id, selectedPath!),
    onSuccess: () => {
      toast.success("File deleted · new version created");
      setSelectedPath(null);
      invalidate();
    },
    onError: (error) => toast.error(apiErrorMessage(error)),
  });

  const validate = useMutation({
    mutationFn: () => api.terraform.validate(project!.id),
    onSuccess: () => {
      toast.success("Validation started");
      queryClient.invalidateQueries({ queryKey: ["validation", projectId] });
      navigate(`/projects/${projectId}/validation`);
    },
    onError: (error) => toast.error(apiErrorMessage(error)),
  });

  const visibleFiles = useMemo(
    () => files.filter((file) => file.path.toLowerCase().includes(filter.toLowerCase())),
    [files, filter],
  );
  const dirty = draft !== original;

  if (terraform.isLoading) return <LoadingBlock label="Loading Terraform project…" />;

  if (!project) {
    return (
      <EmptyState
        icon={<FileCode2 size={28} />}
        title="No Terraform project yet"
        description="After the architecture is approved, the platform generates a modular Terraform project with environments, validation and documentation. The LLM never executes Terraform."
        action={
          <div className="flex flex-wrap justify-center gap-2">
            <button className="btn btn-primary" onClick={() => generate.mutate()} disabled={generate.isPending}>
              {generate.isPending ? <Loader2 size={15} className="animate-spin" /> : <Play size={15} />}
              Generate Terraform
            </button>
            <Link className="btn" to={`/projects/${projectId}/architecture`}>
              Review architecture
            </Link>
          </div>
        }
      />
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-lg font-semibold text-ink">Terraform project</h2>
            <span className="badge border-cyan-500/30 bg-cyan-500/10 text-cyan-300">v{project.version}</span>
            <StatusBadge status={project.status} />
          </div>
          <p className="mt-1 text-xs text-muted">
            {project.file_count} files · digest{" "}
            <span className="font-mono text-[11px] text-faint">{shortHash(project.source_digest, 18)}</span> · updated{" "}
            {timeAgo(project.created_at)}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button className="btn btn-sm" onClick={() => setMapOpen(true)}>
            <FolderTree size={13} /> Resource map
          </button>
          <button
            className="btn btn-sm"
            onClick={() =>
              api.terraform
                .download(project.id, `terraform-v${project.version}.zip`)
                .catch((error) => toast.error(apiErrorMessage(error)))
            }
          >
            <Download size={13} /> Download ZIP
          </button>
          <button className="btn btn-sm" onClick={() => generate.mutate()} disabled={generate.isPending}>
            {generate.isPending ? <Loader2 size={13} className="animate-spin" /> : <RefreshCw size={13} />}
            Regenerate
          </button>
          <button className="btn btn-primary btn-sm" onClick={() => validate.mutate()} disabled={validate.isPending}>
            {validate.isPending ? <Loader2 size={13} className="animate-spin" /> : <ShieldCheck size={13} />}
            Validate
          </button>
        </div>
      </div>

      {(project.notes ?? []).length > 0 && (
        <div className="rounded-xl border border-amber-500/25 bg-amber-500/5 px-3.5 py-2.5 text-[11px] leading-relaxed text-amber-100">
          {(project.notes ?? []).map((note) => (
            <p key={note}>• {note}</p>
          ))}
        </div>
      )}

      <div className="grid gap-3 lg:grid-cols-[290px_1fr]">
        <div className="card flex max-h-[640px] flex-col overflow-hidden">
          <div className="border-b border-line p-2.5">
            <div className="relative">
              <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-faint" />
              <input
                className="input py-1.5 pl-8 text-xs"
                placeholder="Filter files…"
                value={filter}
                onChange={(event) => setFilter(event.target.value)}
              />
            </div>
            <button className="btn btn-ghost btn-sm mt-2 w-full justify-start" onClick={() => setNewFileOpen(true)}>
              <FilePlus2 size={13} /> New file
            </button>
          </div>
          <div className="flex-1 overflow-y-auto p-1.5">
            {visibleFiles.map((file) => (
              <button
                key={file.path}
                onClick={() => setSelectedPath(file.path)}
                className={clsx(
                  "flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-[11px] transition",
                  selectedPath === file.path
                    ? "bg-cyan-500/10 text-cyan-200"
                    : "text-muted hover:bg-surface-3 hover:text-ink",
                )}
                title={file.path}
              >
                {fileIcon(file.path)}
                <span className="truncate font-mono">{file.path}</span>
              </button>
            ))}
            {visibleFiles.length === 0 && <p className="px-2 py-4 text-center text-[11px] text-faint">No files match.</p>}
          </div>
          <div className="border-t border-line p-2.5 text-[10px] text-faint">
            Edits create an immutable new version and invalidate approvals.
          </div>
        </div>

        <div className="card overflow-hidden">
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line px-3 py-2">
            <div className="flex min-w-0 items-center gap-2">
              <FileCode2 size={14} className="shrink-0 text-cyan-300" />
              <span className="truncate font-mono text-xs text-slate-300">{selectedPath ?? "Select a file"}</span>
              {dirty && <span className="badge border-amber-500/30 bg-amber-500/10 text-[9px] text-amber-300">unsaved</span>}
            </div>
            <div className="flex items-center gap-1">
              <button className="btn btn-sm" onClick={() => setDraft(original)} disabled={!dirty}>
                Revert
              </button>
              <button className="btn btn-primary btn-sm" onClick={() => save.mutate()} disabled={!dirty || save.isPending}>
                {save.isPending ? <Loader2 size={13} className="animate-spin" /> : <Save size={13} />}
                Save as new version
              </button>
              <button
                className="btn btn-ghost btn-sm text-rose-300"
                onClick={() => {
                  if (selectedPath && window.confirm(`Delete ${selectedPath}? This creates a new version.`)) removeFile.mutate();
                }}
                disabled={!selectedPath || removeFile.isPending}
              >
                <Trash2 size={13} />
              </button>
            </div>
          </div>
          <div className="h-[560px]">
            <CodeEditor value={draft} onChange={setDraft} />
          </div>
          <div className="flex flex-wrap items-center justify-between gap-2 border-t border-line px-3 py-2 text-[10px] text-faint">
            <span className="inline-flex items-center gap-1">
              <CheckCircle2 size={11} className="text-emerald-400" /> Formatting is normalized by terraform fmt
            </span>
            <span>Source digest binds plans and approvals</span>
          </div>
        </div>
      </div>

      <Modal
        open={newFileOpen}
        onClose={() => setNewFileOpen(false)}
        title="Create Terraform file"
        description="New files are added in a new project version."
        footer={
          <>
            <button className="btn" onClick={() => setNewFileOpen(false)}>
              Cancel
            </button>
            <button
              className="btn btn-primary"
              disabled={!newPath.trim() || createFile.isPending}
              onClick={() => createFile.mutate()}
            >
              {createFile.isPending ? <Loader2 size={15} className="animate-spin" /> : <FilePlus2 size={15} />}
              Create
            </button>
          </>
        }
      >
        <input
          className="input font-mono text-xs"
          placeholder="modules/networking/outputs.tf"
          value={newPath}
          onChange={(event) => setNewPath(event.target.value)}
        />
        <p className="text-xs text-faint">Use a relative path. Supported: .tf, .tfvars, .hcl, .md, .json, .yaml, .sh, .ps1.</p>
      </Modal>

      <Modal open={mapOpen} onClose={() => setMapOpen(false)} title="Resource to module mapping" wide>
        <div className="max-h-[60vh] overflow-auto">
          <table className="table">
            <thead>
              <tr>
                <th>Resource</th>
                <th>Terraform type</th>
                <th>Module</th>
                <th>File</th>
              </tr>
            </thead>
            <tbody>
              {project.module_map.map((row) => (
                <tr key={`${row.terraform_type}-${row.resource}`}>
                  <td className="text-slate-300">{row.resource}</td>
                  <td className="font-mono text-[10px] text-cyan-200">{row.terraform_type}</td>
                  <td className="font-mono text-[10px] text-muted">{row.module}</td>
                  <td className="font-mono text-[10px] text-faint">{row.file}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Modal>

      <section className="card p-4">
        <SectionTitle title="Validation status" description="Validation must pass for the current source digest before planning." />
        <Link className="btn btn-sm" to={`/projects/${projectId}/validation`}>
          Open validation <ShieldCheck size={13} />
        </Link>
      </section>
    </div>
  );
}
