import { useState } from "react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Activity,
  BookOpen,
  Cloud,
  Cpu,
  FolderKanban,
  LayoutDashboard,
  LogOut,
  Menu,
  ScrollText,
  Settings2,
  ShieldCheck,
  Sparkles,
  X,
} from "lucide-react";
import clsx from "clsx";
import { useAuth } from "../context/AuthContext";
import { api, getOrganizationId, setOrganizationId } from "../lib/api";
import { statusLabel } from "../lib/format";
import { StatusBadge } from "./StatusBadge";

const NAV = [
  { to: "/", label: "Dashboard", icon: LayoutDashboard, end: true },
  { to: "/projects", label: "Projects", icon: FolderKanban },
  { to: "/subscriptions", label: "Subscriptions", icon: Cloud },
  { to: "/audit", label: "Audit log", icon: ScrollText },
  { to: "/system", label: "System", icon: Settings2 },
];

export function AppShell() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [mobileOpen, setMobileOpen] = useState(false);
  const [orgId, setOrg] = useState(getOrganizationId());

  const systemQuery = useQuery({ queryKey: ["system"], queryFn: api.system.info, staleTime: 60_000 });

  const switchOrg = (next: string) => {
    setOrganizationId(next);
    setOrg(next);
    queryClient.clear();
    navigate("/");
  };

  const initials = (user?.name ?? "?")
    .split(" ")
    .map((part) => part[0])
    .join("")
    .slice(0, 2)
    .toUpperCase();

  const sidebar = (
    <div className="flex h-full flex-col">
      <div className="flex h-16 items-center gap-2.5 border-b border-line px-5">
        <div className="grid h-8 w-8 place-items-center rounded-lg bg-gradient-to-br from-cyan-400 to-sky-600 text-slate-950 shadow-lg shadow-cyan-500/20">
          <Cpu size={18} strokeWidth={2.4} />
        </div>
        <div className="leading-tight">
          <p className="text-sm font-semibold tracking-tight text-ink">TerraformAgent</p>
          <p className="text-[10px] uppercase tracking-[0.16em] text-faint">Azure · IaC</p>
        </div>
      </div>

      <nav className="flex-1 space-y-1 overflow-y-auto p-3">
        <p className="px-2 pb-1 pt-2 text-[10px] font-semibold uppercase tracking-[0.14em] text-faint">
          Workspace
        </p>
        {NAV.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.end}
            onClick={() => setMobileOpen(false)}
            className={({ isActive }) => clsx("nav-item", isActive && "nav-item-active")}
          >
            <item.icon size={16} />
            <span className="flex-1">{item.label}</span>
          </NavLink>
        ))}
        {user?.is_platform_admin && (
          <NavLink
            to="/llm"
            onClick={() => setMobileOpen(false)}
            className={({ isActive }) => clsx("nav-item", isActive && "nav-item-active")}
          >
            <Sparkles size={16} />
            <span className="flex-1">AI provider</span>
          </NavLink>
        )}

        <div className="mt-5 rounded-xl border border-line/70 bg-surface/50 p-3">
          <div className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-wider text-faint">
            <ShieldCheck size={13} /> Execution guard
          </div>
          <p className="mt-2 text-xs leading-relaxed text-muted">
            Terraform apply is blocked until a human approves the exact plan hash.
          </p>
          <div className="mt-3 flex flex-wrap gap-1.5">
            <StatusBadge status={systemQuery.data?.terraform.available ? "active" : "failed"} label={systemQuery.data?.terraform.terraform_version ? `tf ${systemQuery.data.terraform.terraform_version}` : "tf missing"} />
            <StatusBadge
              tone={systemQuery.data?.llm.available ? "violet" : "neutral"}
              label={systemQuery.data?.llm.available ? systemQuery.data.llm.active_provider : "offline reasoner"}
            />
          </div>
        </div>
      </nav>

      <div className="border-t border-line p-3">
        <div className="flex items-center gap-2.5 rounded-xl p-2">
          <div className="grid h-8 w-8 place-items-center rounded-full bg-gradient-to-br from-violet-400 to-fuchsia-600 text-xs font-bold text-white">
            {initials}
          </div>
          <div className="min-w-0 flex-1">
            <p className="truncate text-xs font-medium text-ink">{user?.name}</p>
            <p className="truncate text-[11px] text-faint">{user?.email}</p>
          </div>
          <button
            className="btn btn-ghost btn-sm"
            onClick={() => {
              logout();
              navigate("/login");
            }}
            aria-label="Sign out"
            title="Sign out"
          >
            <LogOut size={15} />
          </button>
        </div>
      </div>
    </div>
  );

  return (
    <div className="flex h-screen overflow-hidden">
      <aside className="hidden w-[248px] shrink-0 border-r border-line bg-surface/40 md:block">
        {sidebar}
      </aside>

      {mobileOpen && (
        <div className="fixed inset-0 z-50 md:hidden">
          <div className="absolute inset-0 bg-black/70" onClick={() => setMobileOpen(false)} />
          <aside className="absolute left-0 top-0 h-full w-[270px] border-r border-line bg-surface">
            <button className="absolute right-3 top-4 z-10 btn btn-ghost btn-sm" onClick={() => setMobileOpen(false)}>
              <X size={16} />
            </button>
            {sidebar}
          </aside>
        </div>
      )}

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-16 shrink-0 items-center gap-3 border-b border-line bg-canvas/60 px-4 backdrop-blur sm:px-6">
          <button className="btn btn-ghost btn-sm md:hidden" onClick={() => setMobileOpen(true)} aria-label="Open menu">
            <Menu size={18} />
          </button>

          <div className="hidden items-center gap-2 text-xs text-muted sm:flex">
            <Activity size={14} className="text-emerald-400" />
            <span>Control plane</span>
            <span className="text-faint">/</span>
            <span className="text-ink">{statusLabel(systemQuery.data?.environment)}</span>
          </div>

          <div className="ml-auto flex items-center gap-2">
            {user && user.organizations.length > 1 && (
              <select
                className="select max-w-[190px] py-1.5 text-xs"
                value={orgId ?? ""}
                onChange={(event) => switchOrg(event.target.value)}
                aria-label="Organization"
              >
                {user.organizations.map((org) => (
                  <option key={org.id} value={org.id}>
                    {org.name}
                  </option>
                ))}
              </select>
            )}
            {user && user.organizations.length === 1 && (
              <span className="hidden rounded-lg border border-line px-2.5 py-1.5 text-xs text-muted sm:inline">
                {user.organizations[0].name}
              </span>
            )}
            <a
              className="btn btn-ghost btn-sm"
              href="/api/docs"
              target="_blank"
              rel="noreferrer"
              title="OpenAPI documentation"
            >
              <BookOpen size={15} />
              <span className="hidden sm:inline">API</span>
            </a>
          </div>
        </header>

        <main className="min-h-0 flex-1 overflow-y-auto">
          <div className="mx-auto w-full max-w-[1500px] px-4 py-6 sm:px-6 lg:px-8">
            <Outlet />
          </div>
        </main>
      </div>
    </div>
  );
}
