import { useState, type FormEvent } from "react";
import { Link, Navigate, useLocation, useNavigate } from "react-router-dom";
import {
  ArrowRight,
  Cloud,
  Cpu,
  GitBranch,
  KeyRound,
  Loader2,
  Lock,
  ShieldCheck,
  Sparkles,
  Workflow,
} from "lucide-react";
import { useAuth } from "../context/AuthContext";
import { apiErrorMessage } from "../lib/api";

const FEATURES = [
  {
    icon: Sparkles,
    title: "Architect, not autocomplete",
    body: "Requirements are challenged, gaps are questioned, and every decision is traceable to a source.",
  },
  {
    icon: GitBranch,
    title: "Real Terraform modules",
    body: "Versioned HCL with pinned providers, environment separation and security policy gates.",
  },
  {
    icon: ShieldCheck,
    title: "Two human checkpoints",
    body: "Architecture approval and plan-hash approval are enforced in backend code — never by the model.",
  },
  {
    icon: Workflow,
    title: "Plan → apply integrity",
    body: "Apply executes the exact saved binary plan. If anything drifts, approval is invalidated.",
  },
];

export function LoginPage() {
  const { user, loading, login, register } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [name, setName] = useState("");
  const [organization, setOrganization] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (user) {
    const target = (location.state as { from?: string } | null)?.from ?? "/";
    return <Navigate to={target} replace />;
  }

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (mode === "login") {
        await login(email, password);
      } else {
        await register({ name, email, password, organization_name: organization || `${name}'s Organization` });
      }
      const target = (location.state as { from?: string } | null)?.from ?? "/";
      navigate(target, { replace: true });
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  if (loading) {
    return (
      <div className="grid h-screen place-items-center">
        <Loader2 className="animate-spin text-accent" />
      </div>
    );
  }

  return (
    <div className="grid min-h-screen lg:grid-cols-[1.05fr_0.95fr]">
      <div className="relative hidden overflow-hidden border-r border-line p-10 lg:flex lg:flex-col lg:justify-between">
        <div className="pointer-events-none absolute -left-24 -top-24 h-96 w-96 rounded-full bg-cyan-500/10 blur-3xl" />
        <div className="pointer-events-none absolute bottom-0 right-0 h-80 w-80 rounded-full bg-violet-500/10 blur-3xl" />
        <div className="relative">
          <div className="flex items-center gap-3">
            <div className="grid h-10 w-10 place-items-center rounded-xl bg-gradient-to-br from-cyan-400 to-sky-600 text-slate-950 shadow-lg shadow-cyan-500/30">
              <Cpu size={22} strokeWidth={2.4} />
            </div>
            <div>
              <p className="text-lg font-semibold tracking-tight">TerraformAgent</p>
              <p className="text-xs uppercase tracking-[0.18em] text-faint">Azure infrastructure agent</p>
            </div>
          </div>

          <h1 className="mt-14 max-w-xl text-4xl font-semibold leading-[1.12] tracking-tight text-ink">
            From a sentence to reviewed Azure infrastructure.
          </h1>
          <p className="mt-4 max-w-lg text-sm leading-relaxed text-muted">
            Describe the workload or upload the architecture document. The agent extracts
            requirements, asks what matters, designs the platform, generates modular Terraform,
            validates it, and deploys only what you approve.
          </p>

          <div className="mt-10 grid max-w-xl grid-cols-1 gap-3 sm:grid-cols-2">
            {FEATURES.map((feature) => (
              <div key={feature.title} className="card-flat p-3.5">
                <feature.icon size={17} className="text-accent" />
                <p className="mt-2 text-sm font-medium text-ink">{feature.title}</p>
                <p className="mt-1 text-xs leading-relaxed text-muted">{feature.body}</p>
              </div>
            ))}
          </div>
        </div>

        <div className="relative flex items-center gap-5 text-[11px] text-faint">
          <span className="flex items-center gap-1.5">
            <Cloud size={13} /> AzureRM 4.x
          </span>
          <span className="flex items-center gap-1.5">
            <Lock size={13} /> Human approvals
          </span>
          <span className="flex items-center gap-1.5">
            <KeyRound size={13} /> Secrets never in prompts
          </span>
        </div>
      </div>

      <div className="flex items-center justify-center p-6 sm:p-10">
        <div className="w-full max-w-md">
          <div className="mb-8 flex items-center gap-3 lg:hidden">
            <div className="grid h-9 w-9 place-items-center rounded-lg bg-gradient-to-br from-cyan-400 to-sky-600 text-slate-950">
              <Cpu size={19} />
            </div>
            <p className="font-semibold">TerraformAgent</p>
          </div>

          <div className="mb-6 flex gap-1 rounded-xl border border-line bg-surface/60 p-1">
            {(["login", "register"] as const).map((item) => (
              <button
                key={item}
                onClick={() => {
                  setMode(item);
                  setError(null);
                }}
                className={`flex-1 rounded-lg px-3 py-2 text-sm font-medium transition ${
                  mode === item ? "bg-surface-3 text-ink shadow" : "text-muted hover:text-ink"
                }`}
              >
                {item === "login" ? "Sign in" : "Create account"}
              </button>
            ))}
          </div>

          <h2 className="text-2xl font-semibold tracking-tight">
            {mode === "login" ? "Welcome back" : "Start a new workspace"}
          </h2>
          <p className="mt-1 text-sm text-muted">
            {mode === "login"
              ? "Sign in to review architectures and approve deployments."
              : "You become the owner of the first organization."}
          </p>

          <form onSubmit={submit} className="mt-6 space-y-4">
            {mode === "register" && (
              <>
                <div>
                  <label className="label" htmlFor="name">
                    Full name
                  </label>
                  <input
                    id="name"
                    className="input"
                    value={name}
                    onChange={(event) => setName(event.target.value)}
                    placeholder="Aisha Rahman"
                    required
                    autoComplete="name"
                  />
                </div>
                <div>
                  <label className="label" htmlFor="organization">
                    Organization
                  </label>
                  <input
                    id="organization"
                    className="input"
                    value={organization}
                    onChange={(event) => setOrganization(event.target.value)}
                    placeholder="Contoso Platform Engineering"
                  />
                </div>
              </>
            )}
            <div>
              <label className="label" htmlFor="email">
                Email
              </label>
              <input
                id="email"
                className="input"
                type="email"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                placeholder="you@company.com"
                required
                autoComplete="email"
              />
            </div>
            <div>
              <label className="label" htmlFor="password">
                Password
              </label>
              <input
                id="password"
                className="input"
                type="password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                placeholder={mode === "register" ? "At least 8 characters" : "••••••••"}
                required
                minLength={mode === "register" ? 8 : undefined}
                autoComplete={mode === "login" ? "current-password" : "new-password"}
              />
            </div>

            {error && (
              <div className="rounded-lg border border-rose-500/40 bg-rose-950/40 px-3 py-2 text-sm text-rose-200">
                {error}
              </div>
            )}

            <button className="btn btn-primary w-full py-2.5" disabled={busy}>
              {busy ? <Loader2 size={16} className="animate-spin" /> : null}
              {mode === "login" ? "Sign in" : "Create workspace"}
              {!busy && <ArrowRight size={15} />}
            </button>
          </form>

          {mode === "login" && (
            <div className="mt-5 rounded-xl border border-line bg-surface/50 p-3 text-xs text-muted">
              <p className="font-medium text-ink">Local bootstrap account</p>
              <p className="mt-1 font-mono text-[11px]">admin@terraformagent.local / ChangeMe123!</p>
              <p className="mt-1">Change these credentials before exposing the platform.</p>
            </div>
          )}

          <p className="mt-6 text-center text-xs text-faint">
            By continuing you accept that generated infrastructure must be reviewed before apply.{" "}
            <Link to="/" className="text-accent hover:underline">
              Read the flow
            </Link>
          </p>
        </div>
      </div>
    </div>
  );
}
