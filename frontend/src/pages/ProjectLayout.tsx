import { useEffect, useMemo, useRef } from "react";
import { Link, NavLink, Outlet, useParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Activity,
  ArrowLeft,
  ArrowRight,
  Blocks,
  BookOpen,
  Bot,
  ClipboardCheck,
  Cloud,
  Download,
  FileCode2,
  FileStack,
  GitBranch,
  LayoutList,
  ListChecks,
  Rocket,
  ScrollText,
  Settings2,
  ShieldCheck,
  Terminal,
} from "lucide-react";
import clsx from "clsx";
import { api, apiErrorMessage } from "../lib/api";
import { subscribeProjectEvents } from "../lib/sse";
import { environmentLabel, statusLabel, statusTone } from "../lib/format";
import { StatusBadge } from "../components/StatusBadge";
import { LoadingBlock } from "../components/UI";
import { useToast } from "../context/ToastContext";
import { getToken } from "../lib/api";

const TABS = [
  { to: "", label: "Overview", icon: LayoutList, end: true },
  { to: "chat", label: "Chat", icon: Bot },
  { to: "documents", label: "Documents", icon: FileStack },
  { to: "architecture", label: "Architecture", icon: Blocks },
  { to: "review", label: "Review", icon: ClipboardCheck },
  { to: "terraform", label: "Terraform", icon: FileCode2 },
  { to: "validation", label: "Validation", icon: ListChecks },
  { to: "plan", label: "Plan", icon: GitBranch },
  { to: "deployment", label: "Deployment", icon: Rocket },
  { to: "audit", label: "Audit", icon: ScrollText },
  { to: "settings", label: "Settings", icon: Settings2 },
];

export function useProjectEvents(projectId: string) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const timer = useRef<number | null>(null);

  useEffect(() => {
    if (!getToken()) return;
    const schedule = () => {
      if (timer.current) return;
      timer.current = window.setTimeout(() => {
        timer.current = null;
        queryClient.invalidateQueries({ queryKey: ["project", projectId] });
        queryClient.invalidateQueries({ queryKey: ["overview", projectId] });
        queryClient.invalidateQueries({ queryKey: ["validation", projectId] });
        queryClient.invalidateQueries({ queryKey: ["plans", projectId] });
        queryClient.invalidateQueries({ queryKey: ["deployments", projectId] });
        queryClient.invalidateQueries({ queryKey: ["dashboard"] });
      }, 400);
    };

    const unsubscribe = subscribeProjectEvents(
      projectId,
      (event) => {
        schedule();
        const payload = event.data as { type?: string; payload?: Record<string, unknown> };
        if (payload?.type === "job" && payload.payload?.status === "failed") {
          toast.error(String(payload.payload.error ?? "Background job failed"));
        }
      },
      {
        onError: () => undefined,
      },
    );
    return () => {
      unsubscribe();
      if (timer.current) window.clearTimeout(timer.current);
    };
  }, [projectId, queryClient, toast]);
}

const CTA_BY_STATUS: Record<string, { label: string; tab: string }> = {
  draft: { label: "Start discovery", tab: "chat" },
  gathering_requirements: { label: "Continue requirements", tab: "chat" },
  architecture_ready: { label: "Review architecture", tab: "review" },
  awaiting_architecture_approval: { label: "Approve architecture", tab: "architecture" },
  architecture_approved: { label: "Generate Terraform", tab: "terraform" },
  generating_terraform: { label: "Terraform", tab: "terraform" },
  validating: { label: "Validation", tab: "validation" },
  validated: { label: "Generate plan", tab: "plan" },
  validation_failed: { label: "Fix validation", tab: "validation" },
  planning: { label: "Plan running", tab: "plan" },
  planning_failed: { label: "Plan diagnostics", tab: "plan" },
  awaiting_deployment_approval: { label: "Review plan", tab: "plan" },
  deploying: { label: "Deployment logs", tab: "deployment" },
  deployment_successful: { label: "Results", tab: "deployment" },
  deployment_failed: { label: "Deployment diagnostics", tab: "deployment" },
  verification_failed: { label: "Verification", tab: "deployment" },
  completed: { label: "Deployment results", tab: "deployment" },
};

export function ProjectLayout() {
  const { projectId = "" } = useParams();
  const toast = useToast();
  const projectQuery = useQuery({
    queryKey: ["project", projectId],
    queryFn: () => api.projects.get(projectId),
    enabled: Boolean(projectId),
  });
  const overviewQuery = useQuery({
    queryKey: ["overview", projectId],
    queryFn: () => api.projects.overview(projectId),
    enabled: Boolean(projectId),
  });

  useProjectEvents(projectId);

  const cta = useMemo(() => {
    const status = projectQuery.data?.status;
    return status ? CTA_BY_STATUS[status] : undefined;
  }, [projectQuery.data?.status]);

  if (projectQuery.isLoading) return <LoadingBlock label="Loading project workspace…" />;
  if (projectQuery.isError || !projectQuery.data) {
    return (
      <div className="space-y-3">
        <Link to="/projects" className="btn btn-ghost btn-sm">
          <ArrowLeft size={14} /> Back to projects
        </Link>
        <div className="card p-6 text-sm text-rose-200">
          {apiErrorMessage(projectQuery.error)}
        </div>
      </div>
    );
  }

  const project = projectQuery.data;
  const tone = statusTone(project.status);

  return (
    <div className="space-y-0">
      <div className="mb-4 flex flex-wrap items-center gap-2 text-xs text-muted">
        <Link to="/projects" className="hover:text-ink">
          Projects
        </Link>
        <span className="text-faint">/</span>
        <span className="text-ink">{project.name}</span>
      </div>

      <div className="card overflow-hidden">
        <div className="flex flex-wrap items-start justify-between gap-4 border-b border-line p-4 sm:p-5">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="truncate text-xl font-semibold tracking-tight text-ink">{project.name}</h1>
              <StatusBadge status={project.status} pulse={["deploying", "validating", "planning"].includes(project.status)} />
            </div>
            <p className="mt-1 max-w-2xl text-sm text-muted">
              {project.description || "No description yet."}
            </p>
            <div className="mt-2.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-faint">
              <span className="inline-flex items-center gap-1 capitalize">
                <Activity size={12} /> {environmentLabel(project.environment)}
              </span>
              <span className="inline-flex items-center gap-1">
                <Cloud size={12} /> {project.region}
              </span>
              <span className="inline-flex items-center gap-1">
                <ShieldCheck size={12} />
                {project.subscription ? project.subscription.subscription_name : "no subscription"}
              </span>
              <span className="inline-flex items-center gap-1">
                <FileStack size={12} /> {project.counts.documents} docs
              </span>
              <span className="inline-flex items-center gap-1">
                <ListChecks size={12} /> {project.counts.requirements} requirements
              </span>
              {project.counts.open_questions > 0 && (
                <span className="inline-flex items-center gap-1 text-amber-300">
                  <Bot size={12} /> {project.counts.open_questions} open questions
                </span>
              )}
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <button
              className="btn btn-ghost btn-sm"
              onClick={() =>
                api.projects
                  .export(project.id, `${project.name.replaceAll(" ", "-").toLowerCase()}.zip`)
                  .catch((error) => toast.error(apiErrorMessage(error)))
              }
            >
              <Download size={14} /> Export
            </button>
            {cta && (
              <Link className="btn btn-primary btn-sm" to={`/projects/${project.id}/${cta.tab}`}>
                {cta.label} <ArrowRight size={13} />
              </Link>
            )}
          </div>
        </div>

        <div className="flex gap-0.5 overflow-x-auto border-b border-line px-2">
          {TABS.map((tab) => (
            <NavLink
              key={tab.to}
              to={`/projects/${project.id}/${tab.to}`}
              end={tab.end}
              className={({ isActive }) => clsx("tab shrink-0", isActive && "tab-active")}
            >
              <tab.icon size={14} />
              {tab.label}
            </NavLink>
          ))}
        </div>

        {overviewQuery.data?.next_action && tone !== "good" && (
          <div className="border-b border-line bg-surface/30 px-4 py-2 text-xs text-muted sm:px-5">
            <span className="font-medium text-slate-300">Next:</span>{" "}
            {overviewQuery.data.next_action.label}
            <span className="mx-1.5 text-faint">·</span>
            <span className="text-faint">Approvals are human-only and recorded with artifact hashes.</span>
          </div>
        )}

        <div className="p-4 sm:p-5">
          <Outlet />
        </div>
      </div>

      <div className="mt-3 flex flex-wrap items-center justify-between gap-2 px-1 text-[11px] text-faint">
        <span className="inline-flex items-center gap-1">
          <Terminal size={12} /> Apply is locked to the approved plan hash
        </span>
        <span className="inline-flex items-center gap-1">
          <BookOpen size={12} /> Generated by TerraformAgent · {statusLabel(project.status)}
        </span>
      </div>
    </div>
  );
}
