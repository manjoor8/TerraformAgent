import { useRef, useState, type DragEvent } from "react";
import { Link, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  CheckCircle2,
  FileSpreadsheet,
  FileText,
  Loader2,
  RefreshCw,
  Trash2,
  Upload,
} from "lucide-react";
import clsx from "clsx";
import { api, apiErrorMessage } from "../../lib/api";
import { formatBytes, formatDate, statusLabel } from "../../lib/format";
import { StatusBadge } from "../../components/StatusBadge";
import { EmptyState, LoadingBlock, PageHeader } from "../../components/UI";
import { useToast } from "../../context/ToastContext";
import type { ProjectDocument } from "../../lib/types";

function DocumentRow({ document, projectId }: { document: ProjectDocument; projectId: string }) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const extraction = document.extraction ?? {};

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["documents", projectId] });
    queryClient.invalidateQueries({ queryKey: ["overview", projectId] });
  };

  const reextract = useMutation({
    mutationFn: () => api.documents.reextract(document.id),
    onSuccess: () => {
      toast.success("Extraction re-run");
      invalidate();
    },
    onError: (error) => toast.error(apiErrorMessage(error)),
  });

  const remove = useMutation({
    mutationFn: () => api.documents.remove(document.id),
    onSuccess: () => {
      toast.success("Document removed");
      invalidate();
    },
    onError: (error) => toast.error(apiErrorMessage(error)),
  });

  const isSpreadsheet = document.filename.match(/\.(xlsx|xlsm|csv)$/i);

  return (
    <div className="rounded-xl border border-line bg-surface-2/40">
      <div className="flex flex-wrap items-center gap-3 p-3.5">
        <div className="grid h-9 w-9 place-items-center rounded-lg bg-cyan-500/10 text-cyan-300">
          {isSpreadsheet ? <FileSpreadsheet size={17} /> : <FileText size={17} />}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <p className="truncate text-sm font-medium text-ink">{document.filename}</p>
            <StatusBadge status={document.extraction_status} />
            <span className="badge text-[9px]">{document.document_type}</span>
          </div>
          <p className="mt-0.5 text-[11px] text-faint">
            {formatBytes(document.size_bytes)} · {formatDate(document.created_at)} ·{" "}
            {(extraction.word_count ?? 0).toLocaleString()} words
            {extraction.pages ? ` · ${extraction.pages} pages` : ""}
          </p>
        </div>
        <div className="flex items-center gap-1">
          <button className="btn btn-ghost btn-sm" onClick={() => setOpen((value) => !value)}>
            {open ? "Hide" : "Details"}
          </button>
          <button
            className="btn btn-ghost btn-sm"
            onClick={() => api.documents.download(document.id, document.filename).catch((error) => toast.error(apiErrorMessage(error)))}
          >
            Download
          </button>
          <button className="btn btn-ghost btn-sm" onClick={() => reextract.mutate()} disabled={reextract.isPending}>
            {reextract.isPending ? <Loader2 size={13} className="animate-spin" /> : <RefreshCw size={13} />}
          </button>
          <button
            className="btn btn-ghost btn-sm text-rose-300"
            onClick={() => {
              if (window.confirm(`Remove ${document.filename}?`)) remove.mutate();
            }}
            disabled={remove.isPending}
          >
            <Trash2 size={13} />
          </button>
        </div>
      </div>

      {document.extraction_status === "failed" && (
        <div className="border-t border-line px-3.5 py-2 text-xs text-rose-300">
          {document.extraction_error ?? "Extraction failed."}
        </div>
      )}

      {open && (
        <div className="space-y-3 border-t border-line p-3.5">
          {extraction.untrusted_instruction_detected && (
            <div className="flex items-start gap-2 rounded-lg border border-rose-500/30 bg-rose-500/5 px-3 py-2 text-xs text-rose-200">
              <AlertTriangle size={14} className="mt-0.5 shrink-0" />
              This document contains text that looks like instructions to the agent. It was treated as data and
              did not affect policy, approvals or execution.
            </div>
          )}
          <div className="grid gap-3 sm:grid-cols-2">
            <div>
              <p className="text-[10px] font-semibold uppercase tracking-wider text-faint">Components detected</p>
              <div className="mt-1.5 flex flex-wrap gap-1">
                {(extraction.components ?? []).length === 0 ? (
                  <span className="text-[11px] text-faint">None detected</span>
                ) : (
                  (extraction.components ?? []).map((component) => (
                    <span key={component} className="badge border-cyan-500/30 bg-cyan-500/10 text-[10px] text-cyan-200">
                      {component}
                    </span>
                  ))
                )}
              </div>
            </div>
            <div>
              <p className="text-[10px] font-semibold uppercase tracking-wider text-faint">Sheets</p>
              <p className="mt-1.5 text-[11px] text-muted">
                {(extraction.sheets ?? []).join(", ") || "—"}
              </p>
            </div>
          </div>

          {[
            ["Requirements extracted", extraction.requirements ?? [], "text-slate-300"],
            ["Constraints", extraction.constraints ?? [], "text-slate-300"],
            ["Dependencies", extraction.dependencies ?? [], "text-slate-300"],
            ["Missing information", extraction.missing_information ?? [], "text-amber-200"],
            ["Conflicts", extraction.conflicts ?? [], "text-rose-200"],
          ].map(([label, values, color]) =>
            (values as string[]).length > 0 ? (
              <div key={String(label)}>
                <p className="text-[10px] font-semibold uppercase tracking-wider text-faint">{label}</p>
                <ul className="mt-1 space-y-1">
                  {(values as string[]).slice(0, 8).map((value, index) => (
                    <li key={`${value}-${index}`} className={clsx("text-[11px] leading-relaxed", color)}>
                      • {value}
                    </li>
                  ))}
                </ul>
              </div>
            ) : null,
          )}

          <div className="flex items-center justify-between border-t border-line pt-2 text-[10px] text-faint">
            <span className="font-mono">{document.content_hash.slice(0, 24)}…</span>
            <Link to={`/projects/${projectId}/chat`} className="text-accent hover:underline">
              Discuss extraction in chat →
            </Link>
          </div>
        </div>
      )}
    </div>
  );
}

export function DocumentsTab() {
  const { projectId = "" } = useParams();
  const queryClient = useQueryClient();
  const toast = useToast();
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [uploading, setUploading] = useState<string | null>(null);

  const documents = useQuery({ queryKey: ["documents", projectId], queryFn: () => api.documents.list(projectId) });

  const upload = useMutation({
    mutationFn: (file: File) => api.documents.upload(projectId, file),
    onMutate: (file) => setUploading(file.name),
    onSuccess: (document) => {
      toast.success(`Extracted ${document.filename}`);
      queryClient.invalidateQueries({ queryKey: ["documents", projectId] });
      queryClient.invalidateQueries({ queryKey: ["dashboard"] });
      queryClient.invalidateQueries({ queryKey: ["requirements", projectId] });
    },
    onError: (error) => toast.error(apiErrorMessage(error)),
    onSettled: () => setUploading(null),
  });

  const handleFiles = (files: FileList | null) => {
    if (!files) return;
    Array.from(files).forEach((file) => upload.mutate(file));
  };

  const onDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    setDragging(false);
    handleFiles(event.dataTransfer.files);
  };

  const items = documents.data?.items ?? [];

  return (
    <div className="space-y-4">
      <PageHeader
        eyebrow="Evidence"
        title="Architecture documents"
        description="Upload HLDs, requirements, network diagrams, exports or existing Terraform. Extracted facts feed the requirements model with source traceability."
      />

      <div
        className={clsx(
          "rounded-xl border-2 border-dashed p-6 text-center transition",
          dragging ? "border-cyan-400/60 bg-cyan-500/5" : "border-line bg-surface/30",
        )}
        onDragOver={(event) => {
          event.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
      >
        <div className="mx-auto grid h-11 w-11 place-items-center rounded-full bg-cyan-500/10 text-cyan-300">
          {uploading ? <Loader2 size={20} className="animate-spin" /> : <Upload size={20} />}
        </div>
        <p className="mt-3 text-sm font-medium text-ink">
          {uploading ? `Extracting ${uploading}…` : "Drop documents here or choose files"}
        </p>
        <p className="mt-1 text-xs text-muted">
          PDF, DOCX, TXT, Markdown, XLSX, CSV, JSON, YAML, TF/HCL · up to 25 MB each
        </p>
        <button className="btn btn-primary mt-4" onClick={() => inputRef.current?.click()} disabled={Boolean(uploading)}>
          <Upload size={15} /> Choose files
        </button>
        <input
          ref={inputRef}
          type="file"
          multiple
          className="hidden"
          accept=".pdf,.docx,.doc,.txt,.md,.markdown,.xlsx,.xlsm,.csv,.json,.yaml,.yml,.tf,.hcl"
          onChange={(event) => {
            handleFiles(event.target.files);
            event.target.value = "";
          }}
        />
      </div>

      <div className="flex items-start gap-2 rounded-xl border border-line bg-surface/30 px-3.5 py-2.5 text-[11px] text-muted">
        <CheckCircle2 size={14} className="mt-0.5 shrink-0 text-emerald-400" />
        Documents are untrusted input. Embedded instructions cannot change policies, approvals, credentials or
        execution. Legacy `.doc` files must be converted to `.docx` or PDF.
      </div>

      {documents.isLoading ? (
        <LoadingBlock />
      ) : items.length === 0 ? (
        <EmptyState
          icon={<FileText size={26} />}
          title="No documents yet"
          description="Upload the solution architecture document or an infrastructure inventory to ground the design."
        />
      ) : (
        <div className="space-y-2.5">
          {items.map((document) => (
            <DocumentRow key={document.id} document={document} projectId={projectId} />
          ))}
        </div>
      )}

      <p className="text-xs text-faint">
        {items.filter((document) => document.extraction_status === "extracted").length} of {items.length} documents
        extracted. {statusLabel("extracted")} documents are included in the architecture context.
      </p>
    </div>
  );
}
