import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Bot,
  Check,
  ChevronDown,
  ChevronRight,
  Loader2,
  MessageSquare,
  Plus,
  Send,
  Sparkles,
  UserRound,
} from "lucide-react";
import clsx from "clsx";
import { api, apiErrorMessage } from "../../lib/api";
import { statusLabel, timeAgo } from "../../lib/format";
import { Markdown } from "../../components/Markdown";
import { StatusBadge } from "../../components/StatusBadge";
import { LoadingBlock, SectionTitle } from "../../components/UI";
import { useToast } from "../../context/ToastContext";
import type { Question } from "../../lib/types";

const SUGGESTIONS = [
  "Design a highly available web app with App Service, Azure SQL and private networking in UAE North. Budget: $2,000/month.",
  "We need two Linux application servers behind an Application Gateway, plus PostgreSQL and a Key Vault.",
  "Review our current design for security gaps and cost optimizations.",
  "Add a staging environment with the same topology but smaller SKUs.",
];

function QuestionCard({ question, projectId }: { question: Question; projectId: string }) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const [custom, setCustom] = useState("");
  const [open, setOpen] = useState(true);

  const answer = useMutation({
    mutationFn: (value: string) => api.questions.answer(projectId, question.id, value),
    onSuccess: () => {
      toast.success("Answer recorded");
      queryClient.invalidateQueries({ queryKey: ["questions", projectId] });
      queryClient.invalidateQueries({ queryKey: ["requirements", projectId] });
      queryClient.invalidateQueries({ queryKey: ["overview", projectId] });
      setCustom("");
    },
    onError: (error) => toast.error(apiErrorMessage(error)),
  });

  return (
    <div className="rounded-xl border border-amber-500/25 bg-amber-500/[0.04] p-3">
      <button className="flex w-full items-start gap-2 text-left" onClick={() => setOpen((value) => !value)}>
        <span className="mt-0.5 text-amber-300">
          {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-xs font-semibold text-amber-100">{question.question}</p>
          <p className="mt-1 text-[11px] leading-relaxed text-amber-200/70">{question.why_it_matters}</p>
        </div>
        {question.blocking && <span className="badge border-amber-500/30 bg-amber-500/10 text-amber-300">blocking</span>}
      </button>

      {open && (
        <div className="mt-2.5 space-y-2 pl-5">
          {question.options.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {question.options.map((option) => (
                <button
                  key={option}
                  className={clsx(
                    "rounded-lg border px-2.5 py-1.5 text-[11px] transition",
                    option === question.recommended
                      ? "border-cyan-500/40 bg-cyan-500/10 text-cyan-200 hover:bg-cyan-500/20"
                      : "border-line bg-surface/60 text-slate-300 hover:border-line-bright",
                  )}
                  disabled={answer.isPending}
                  onClick={() => answer.mutate(option)}
                >
                  {option}
                  {option === question.recommended && <Check size={11} className="ml-1 inline" />}
                </button>
              ))}
            </div>
          )}
          <div className="flex gap-2">
            <input
              className="input py-1.5 text-xs"
              placeholder="Or type a custom answer…"
              value={custom}
              onChange={(event) => setCustom(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && custom.trim()) answer.mutate(custom.trim());
              }}
            />
            <button
              className="btn btn-sm"
              disabled={!custom.trim() || answer.isPending}
              onClick={() => custom.trim() && answer.mutate(custom.trim())}
            >
              {answer.isPending ? <Loader2 size={13} className="animate-spin" /> : <Check size={13} />}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

export function ChatTab() {
  const { projectId = "" } = useParams();
  const queryClient = useQueryClient();
  const toast = useToast();
  const [content, setContent] = useState("");
  const [showRequirements, setShowRequirements] = useState(false);
  const [newRequirement, setNewRequirement] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);

  const messages = useQuery({ queryKey: ["messages", projectId], queryFn: () => api.messages.list(projectId) });
  const questions = useQuery({ queryKey: ["questions", projectId], queryFn: () => api.questions.list(projectId) });
  const requirements = useQuery({ queryKey: ["requirements", projectId], queryFn: () => api.requirements.list(projectId) });

  const send = useMutation({
    mutationFn: (text: string) => api.messages.send(projectId, text),
    onSuccess: () => {
      setContent("");
      queryClient.invalidateQueries({ queryKey: ["messages", projectId] });
      queryClient.invalidateQueries({ queryKey: ["questions", projectId] });
      queryClient.invalidateQueries({ queryKey: ["requirements", projectId] });
      queryClient.invalidateQueries({ queryKey: ["overview", projectId] });
      queryClient.invalidateQueries({ queryKey: ["project", projectId] });
      queryClient.invalidateQueries({ queryKey: ["architecture", projectId] });
    },
    onError: (error) => toast.error(apiErrorMessage(error)),
  });

  const design = useMutation({
    mutationFn: () => api.architecture.generate(projectId, {}),
    onSuccess: (version) => {
      toast.success(`Architecture v${version.version} generated`);
      queryClient.invalidateQueries({ queryKey: ["architecture", projectId] });
      queryClient.invalidateQueries({ queryKey: ["overview", projectId] });
      queryClient.invalidateQueries({ queryKey: ["project", projectId] });
    },
    onError: (error) => toast.error(apiErrorMessage(error)),
  });

  const createRequirement = useMutation({
    mutationFn: () => api.requirements.create(projectId, { description: newRequirement }),
    onSuccess: () => {
      setNewRequirement("");
      toast.success("Requirement added");
      queryClient.invalidateQueries({ queryKey: ["requirements", projectId] });
    },
    onError: (error) => toast.error(apiErrorMessage(error)),
  });

  const items = messages.data?.items ?? [];
  const openQuestions = questions.data?.items ?? [];
  const requirementItems = requirements.data?.items ?? [];
  const hasUserMessage = useMemo(() => items.some((message) => message.role === "user"), [items]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [items.length, send.isPending]);

  const submit = () => {
    const text = content.trim();
    if (!text || send.isPending) return;
    send.mutate(text);
  };

  return (
    <div className="grid gap-4 xl:grid-cols-[1.55fr_1fr]">
      <div className="flex min-h-[620px] flex-col">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <div>
            <h3 className="text-sm font-semibold text-ink">Requirement conversation</h3>
            <p className="text-xs text-muted">
              The agent asks only questions that change security, cost, availability or architecture.
            </p>
          </div>
          <div className="flex items-center gap-2">
            <button className="btn btn-ghost btn-sm" onClick={() => setShowRequirements((value) => !value)}>
              {showRequirements ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
              Requirements ({requirementItems.length})
            </button>
            <button className="btn btn-sm" onClick={() => design.mutate()} disabled={design.isPending}>
              {design.isPending ? <Loader2 size={14} className="animate-spin" /> : <Sparkles size={14} />}
              Design
            </button>
          </div>
        </div>

        {showRequirements && (
          <div className="mb-3 rounded-xl border border-line bg-surface/40 p-3">
            <div className="mb-2 flex gap-2">
              <input
                className="input py-1.5 text-xs"
                placeholder="Add a requirement explicitly…"
                value={newRequirement}
                onChange={(event) => setNewRequirement(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && newRequirement.trim()) createRequirement.mutate();
                }}
              />
              <button
                className="btn btn-sm"
                disabled={!newRequirement.trim() || createRequirement.isPending}
                onClick={() => createRequirement.mutate()}
              >
                <Plus size={13} />
              </button>
            </div>
            <div className="max-h-40 space-y-1.5 overflow-auto">
              {requirementItems.map((requirement) => (
                <div key={requirement.id} className="flex items-start gap-2 rounded-lg border border-line/60 bg-canvas/40 px-2 py-1.5">
                  <span className="badge shrink-0 text-[9px]">{requirement.requirement_type}</span>
                  <p className="text-[11px] leading-relaxed text-slate-300">{requirement.description}</p>
                </div>
              ))}
              {requirementItems.length === 0 && <p className="text-[11px] text-faint">No requirements captured yet.</p>}
            </div>
          </div>
        )}

        <div className="card flex-1 overflow-hidden">
          <div className="max-h-[560px] min-h-[440px] space-y-4 overflow-y-auto p-4">
            {messages.isLoading ? (
              <LoadingBlock />
            ) : (
              items.map((message) => (
                <div
                  key={message.id}
                  className={clsx("flex gap-2.5", message.role === "user" ? "flex-row-reverse" : "flex-row")}
                >
                  <div
                    className={clsx(
                      "mt-0.5 grid h-7 w-7 shrink-0 place-items-center rounded-full",
                      message.role === "user"
                        ? "bg-gradient-to-br from-slate-500 to-slate-700 text-slate-100"
                        : "bg-gradient-to-br from-cyan-500/30 to-violet-500/30 text-cyan-200",
                    )}
                  >
                    {message.role === "user" ? <UserRound size={14} /> : <Bot size={14} />}
                  </div>
                  <div className={clsx("max-w-[85%] min-w-0", message.role === "user" && "text-right")}>
                    <div
                      className={clsx(
                        "inline-block rounded-xl px-3.5 py-2.5 text-left",
                        message.role === "user"
                          ? "border border-cyan-500/25 bg-cyan-500/10 text-cyan-50"
                          : "border border-line bg-surface-2/70 text-slate-200",
                      )}
                    >
                      <Markdown>{message.message}</Markdown>
                    </div>
                    <p className="mt-1 text-[10px] text-faint">{timeAgo(message.created_at)}</p>
                  </div>
                </div>
              ))
            )}

            {send.isPending && (
              <div className="flex items-center gap-2.5">
                <div className="grid h-7 w-7 place-items-center rounded-full bg-gradient-to-br from-cyan-500/30 to-violet-500/30 text-cyan-200">
                  <Bot size={14} />
                </div>
                <div className="flex items-center gap-2 rounded-xl border border-line bg-surface-2/70 px-3 py-2 text-xs text-muted">
                  <Loader2 size={13} className="animate-spin" /> Analysing requirements…
                </div>
              </div>
            )}

            {!hasUserMessage && !send.isPending && (
              <div className="rounded-xl border border-line/70 bg-surface/30 p-3">
                <p className="text-[11px] font-medium uppercase tracking-wider text-faint">Try a prompt</p>
                <div className="mt-2 space-y-1.5">
                  {SUGGESTIONS.map((suggestion) => (
                    <button
                      key={suggestion}
                      className="block w-full rounded-lg border border-line/70 bg-canvas/40 px-2.5 py-2 text-left text-[11px] leading-relaxed text-slate-300 transition hover:border-cyan-500/30 hover:text-ink"
                      onClick={() => setContent(suggestion)}
                    >
                      {suggestion}
                    </button>
                  ))}
                </div>
              </div>
            )}
            <div ref={bottomRef} />
          </div>

          <div className="border-t border-line bg-surface/40 p-3">
            <div className="flex items-end gap-2">
              <textarea
                className="textarea max-h-36 min-h-[46px] resize-none py-2.5 text-sm"
                placeholder="Describe the workload, constraint or change… (Enter to send, Shift+Enter for newline)"
                value={content}
                onChange={(event) => setContent(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.shiftKey) {
                    event.preventDefault();
                    submit();
                  }
                }}
                rows={1}
              />
              <button className="btn btn-primary h-[46px] px-3" onClick={submit} disabled={!content.trim() || send.isPending}>
                {send.isPending ? <Loader2 size={16} className="animate-spin" /> : <Send size={16} />}
              </button>
            </div>
            <p className="mt-1.5 text-[10px] text-faint">
              Uploaded documents are treated as untrusted data. Embedded instructions never override policy or approvals.
            </p>
          </div>
        </div>
      </div>

      <div className="space-y-4">
        <section className="card p-4">
          <SectionTitle
            title="Decisions needed"
            description={`${openQuestions.length} open question(s)`}
            actions={
              <Link className="btn btn-ghost btn-sm" to={`/projects/${projectId}/documents`}>
                Documents
              </Link>
            }
          />
          {openQuestions.length === 0 ? (
            <p className="rounded-lg border border-emerald-500/20 bg-emerald-500/5 px-3 py-2 text-xs text-emerald-200">
              No open questions. Generate or revise the architecture when ready.
            </p>
          ) : (
            <div className="space-y-2">
              {openQuestions.map((question) => (
                <QuestionCard key={question.id} question={question} projectId={projectId} />
              ))}
            </div>
          )}
        </section>

        <section className="card p-4">
          <SectionTitle title="Captured requirements" description="Traceable to conversation or documents." />
          <div className="max-h-[360px] space-y-1.5 overflow-auto">
            {requirementItems.length === 0 ? (
              <p className="py-6 text-center text-xs text-faint">Requirements will appear after the first message.</p>
            ) : (
              requirementItems.map((requirement) => (
                <div key={requirement.id} className="rounded-lg border border-line/70 bg-surface/30 px-2.5 py-2">
                  <div className="flex items-center gap-1.5">
                    <span className="badge text-[9px]">{requirement.requirement_type}</span>
                    <span className="text-[9px] uppercase tracking-wider text-faint">{requirement.source}</span>
                    {requirement.priority === "assumption" && (
                      <span className="badge border-violet-500/30 bg-violet-500/10 text-[9px] text-violet-300">
                        assumption
                      </span>
                    )}
                  </div>
                  <p className="mt-1 text-[11px] leading-relaxed text-slate-300">{requirement.description}</p>
                </div>
              ))
            )}
          </div>
        </section>

        <section className="card p-4 text-xs text-muted">
          <div className="flex items-center gap-2 text-ink">
            <MessageSquare size={14} className="text-cyan-300" />
            <span className="font-medium">Conversation rules</span>
          </div>
          <ul className="mt-2 space-y-1.5">
            <li>Answers are stored as confirmed requirements.</li>
            <li>Conflicts are surfaced, never silently resolved.</li>
            <li>Changing requirements after approval invalidates approvals.</li>
          </ul>
        </section>
      </div>
    </div>
  );
}
