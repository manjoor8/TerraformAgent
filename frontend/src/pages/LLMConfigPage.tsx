import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowRight,
  CheckCircle2,
  Cpu,
  KeyRound,
  Loader2,
  Lock,
  RotateCcw,
  Save,
  ShieldCheck,
  Sparkles,
  TestTube2,
  TriangleAlert,
  Zap,
} from "lucide-react";
import clsx from "clsx";
import { api, apiErrorMessage } from "../lib/api";
import { formatDate } from "../lib/format";
import { useAuth } from "../context/AuthContext";
import { useToast } from "../context/ToastContext";
import { LoadingBlock, PageHeader, SectionTitle } from "../components/UI";
import { StatusBadge } from "../components/StatusBadge";
import type { LLMPreset, LLMTestResult } from "../lib/types";

const FALLBACK_PRESETS: Record<string, LLMPreset> = {
  deepseek: {
    label: "DeepSeek",
    model: "deepseek-chat",
    base_url: "https://api.deepseek.com/v1",
    key_hint: "DeepSeek API key (sk-…)",
  },
  openai_compatible: {
    label: "OpenAI-compatible",
    model: "gpt-4o-mini",
    base_url: "https://api.openai.com/v1",
    key_hint: "Provider API key",
  },
  azure_openai: {
    label: "Azure OpenAI",
    model: "gpt-4o",
    base_url: "https://<resource>.openai.azure.com",
    key_hint: "Azure OpenAI API key",
  },
  offline: {
    label: "Deterministic offline reasoner",
    model: "deterministic-heuristics",
    base_url: "",
    key_hint: "No key required",
  },
};

export function LLMConfigPage() {
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const toast = useToast();

  const config = useQuery({
    queryKey: ["llm-config"],
    queryFn: api.system.llm,
    enabled: Boolean(user?.is_platform_admin),
  });

  const [provider, setProvider] = useState("deepseek");
  const [model, setModel] = useState("deepseek-chat");
  const [baseUrl, setBaseUrl] = useState("https://api.deepseek.com/v1");
  const [apiKey, setApiKey] = useState("");
  const [testResult, setTestResult] = useState<LLMTestResult | null>(null);
  const initialized = useRef(false);

  const presets = config.data?.presets ?? FALLBACK_PRESETS;

  useEffect(() => {
    if (!config.data || initialized.current) return;
    initialized.current = true;
    const nextProvider = config.data.configured ? config.data.provider : "deepseek";
    const preset = presets[nextProvider] ?? FALLBACK_PRESETS.deepseek;
    setProvider(nextProvider);
    setModel(config.data.configured ? config.data.model : preset.model);
    setBaseUrl(config.data.configured ? config.data.base_url : preset.base_url);
  }, [config.data, presets]);

  const chooseProvider = (value: string) => {
    setProvider(value);
    const preset = presets[value] ?? FALLBACK_PRESETS.deepseek;
    setModel(preset.model);
    setBaseUrl(preset.base_url);
    setTestResult(null);
  };

  const test = useMutation({
    mutationFn: () =>
      api.system.testLlm({ provider, model, base_url: baseUrl, api_key: apiKey }),
    onSuccess: (result) => {
      setTestResult(result);
      if (result.ok) toast.success(result.message ?? "Connection succeeded");
      else toast.error(result.error ?? "Connection failed");
    },
    onError: (error) => toast.error(apiErrorMessage(error)),
  });

  const save = useMutation({
    mutationFn: () =>
      api.system.updateLlm({ provider, model, base_url: baseUrl, api_key: apiKey }),
    onSuccess: (result) => {
      toast.success("Model provider updated — applied immediately");
      setApiKey("");
      setProvider(result.provider);
      setModel(result.model);
      setBaseUrl(result.base_url);
      setTestResult(null);
      queryClient.setQueryData(["llm-config"], result);
      queryClient.invalidateQueries({ queryKey: ["system"] });
    },
    onError: (error) => toast.error(apiErrorMessage(error)),
  });

  const reset = useMutation({
    mutationFn: api.system.resetLlm,
    onSuccess: (result) => {
      toast.success("Runtime provider removed — environment configuration is active");
      setApiKey("");
      setTestResult(null);
      setProvider("deepseek");
      setModel("deepseek-chat");
      setBaseUrl("https://api.deepseek.com/v1");
      queryClient.setQueryData(["llm-config"], result);
      queryClient.invalidateQueries({ queryKey: ["system"] });
    },
    onError: (error) => toast.error(apiErrorMessage(error)),
  });

  if (!user?.is_platform_admin) {
    return (
      <div className="space-y-5">
        <PageHeader
          eyebrow="Platform"
          title="AI model provider"
          description="Configure the LLM used by the requirement analyst, architect, reviewer and plan analyst."
        />
        <div className="card flex items-start gap-3 p-5">
          <Lock size={20} className="mt-0.5 shrink-0 text-amber-300" />
          <div>
            <p className="text-sm font-semibold text-ink">Platform administrator only</p>
            <p className="mt-1 text-sm leading-relaxed text-muted">
              Model provider credentials affect every organization. Only the bootstrap administrator
              or an email listed in <span className="font-mono text-xs">PLATFORM_ADMIN_EMAILS</span>{" "}
              can change them.
            </p>
            <Link to="/system" className="btn btn-sm mt-3">
              Back to system <ArrowRight size={13} />
            </Link>
          </div>
        </div>
      </div>
    );
  }

  if (config.isLoading) return <LoadingBlock label="Loading model configuration…" />;
  if (!config.data) {
    return (
      <div className="card p-5 text-sm text-rose-200">
        {apiErrorMessage(config.error ?? "Could not load model configuration")}
      </div>
    );
  }

  const data = config.data;
  const effective = data.effective;
  const preset = presets[provider] ?? FALLBACK_PRESETS.deepseek;
  const deepseek = provider === "deepseek";
  const needsKey = provider !== "offline";

  return (
    <div className="space-y-5">
      <PageHeader
        eyebrow="Platform"
        title="AI model provider"
        description="Connect DeepSeek (or another OpenAI-compatible provider). The key is encrypted at rest and never leaves the backend."
        actions={
          <Link className="btn btn-ghost btn-sm" to="/system">
            System <ArrowRight size={13} />
          </Link>
        }
      />

      <div className="grid gap-4 xl:grid-cols-[1.45fr_1fr]">
        <section className="card p-5">
          <SectionTitle
            title="Provider credentials"
            description="Changes are applied to new agent calls immediately — no restart."
          />

          {deepseek && (
            <div className="mb-4 rounded-xl border border-cyan-500/25 bg-cyan-500/[0.04] p-3 text-xs leading-relaxed text-cyan-100">
              <p className="flex items-center gap-1.5 font-semibold">
                <Sparkles size={14} /> DeepSeek preset
              </p>
              <p className="mt-1 text-cyan-100/75">
                Create a key at <span className="font-mono">platform.deepseek.com</span>. The endpoint is{" "}
                <span className="font-mono">https://api.deepseek.com/v1</span> and the recommended model
                is <span className="font-mono">deepseek-chat</span>. DeepSeek is OpenAI-compatible, so the
                same structured-output validation applies.
              </p>
            </div>
          )}

          <div className="space-y-4">
            <div>
              <label className="label" htmlFor="llm-provider">
                Provider
              </label>
              <select
                id="llm-provider"
                className="select"
                value={provider}
                onChange={(event) => chooseProvider(event.target.value)}
              >
                {Object.entries(presets).map(([key, value]) => (
                  <option key={key} value={key}>
                    {value.label}
                  </option>
                ))}
              </select>
            </div>

            {provider !== "offline" && (
              <div className="grid gap-3 sm:grid-cols-2">
                <div>
                  <label className="label" htmlFor="llm-model">
                    Model
                  </label>
                  <input
                    id="llm-model"
                    className="input font-mono text-xs"
                    value={model}
                    onChange={(event) => setModel(event.target.value)}
                    placeholder={preset.model}
                  />
                </div>
                <div>
                  <label className="label" htmlFor="llm-base-url">
                    Base URL
                  </label>
                  <input
                    id="llm-base-url"
                    className="input font-mono text-xs"
                    value={baseUrl}
                    onChange={(event) => setBaseUrl(event.target.value)}
                    placeholder={preset.base_url}
                  />
                </div>
              </div>
            )}

            {provider !== "offline" && (
              <div>
                <label className="label" htmlFor="llm-key">
                  API key
                </label>
                <input
                  id="llm-key"
                  className="input font-mono text-xs"
                  type="password"
                  autoComplete="new-password"
                  value={apiKey}
                  onChange={(event) => setApiKey(event.target.value)}
                  placeholder={
                    data.has_api_key
                      ? `••••••••${data.api_key_last4 ?? ""} — leave blank to keep the current key`
                      : preset.key_hint
                  }
                />
                <p className="mt-1.5 flex items-center gap-1 text-[11px] text-faint">
                  <KeyRound size={11} />
                  The key is never returned by the API. Leaving this blank keeps the stored key.
                </p>
              </div>
            )}

            {provider === "offline" && (
              <div className="rounded-lg border border-line bg-surface/40 p-3 text-xs leading-relaxed text-muted">
                The deterministic Azure architect will be used. No model-backed reasoning, no external
                calls. This mode still produces schema-valid architecture and real Terraform.
              </div>
            )}

            <div className="flex flex-wrap gap-2 border-t border-line pt-3">
              <button
                className="btn"
                onClick={() => test.mutate()}
                disabled={test.isPending || save.isPending}
              >
                {test.isPending ? <Loader2 size={15} className="animate-spin" /> : <TestTube2 size={15} />}
                Test connection
              </button>
              <button
                className="btn btn-primary"
                onClick={() => save.mutate()}
                disabled={save.isPending || (needsKey && !apiKey.trim() && !data.has_api_key)}
              >
                {save.isPending ? <Loader2 size={15} className="animate-spin" /> : <Save size={15} />}
                Save provider
              </button>
              {data.configured && (
                <button
                  className="btn btn-ghost btn-sm text-rose-300"
                  onClick={() => {
                    if (window.confirm("Remove the runtime provider and use the environment configuration?")) {
                      reset.mutate();
                    }
                  }}
                  disabled={reset.isPending}
                >
                  {reset.isPending ? <Loader2 size={13} className="animate-spin" /> : <RotateCcw size={13} />}
                  Reset to environment
                </button>
              )}
            </div>

            {testResult && (
              <div
                className={clsx(
                  "rounded-xl border p-3 text-xs",
                  testResult.ok
                    ? "border-emerald-500/30 bg-emerald-500/5 text-emerald-100"
                    : "border-rose-500/30 bg-rose-500/5 text-rose-100",
                )}
              >
                <p className="flex items-center gap-1.5 font-semibold">
                  {testResult.ok ? <CheckCircle2 size={14} /> : <TriangleAlert size={14} />}
                  {testResult.ok ? "Connection succeeded" : "Connection failed"}
                </p>
                <p className="mt-1 text-current/80">
                  {testResult.ok
                    ? `${testResult.provider}${testResult.model ? ` · ${testResult.model}` : ""}${
                        testResult.latency_ms !== undefined ? ` · ${testResult.latency_ms} ms` : ""
                      }`
                    : testResult.error}
                </p>
                {testResult.preview && (
                  <p className="mt-1 truncate font-mono text-[10px] opacity-70">{testResult.preview}</p>
                )}
              </div>
            )}
          </div>
        </section>

        <div className="space-y-4">
          <section className="card p-4">
            <SectionTitle title="Effective runtime" description="What the agents use right now." />
            <div className="flex items-center gap-2">
              <span
                className={clsx(
                  "h-2 w-2 rounded-full",
                  effective.available ? "bg-emerald-400" : "bg-amber-400",
                )}
              />
              <p className="text-sm font-semibold text-ink">
                {effective.available ? effective.active_provider : "Deterministic reasoner"}
              </p>
              <StatusBadge
                className="ml-auto"
                tone={effective.available ? "good" : "info"}
                label={effective.available ? "LLM active" : "offline mode"}
              />
            </div>
            <dl className="mt-3 space-y-2 text-xs">
              {[
                ["Model", effective.model],
                ["Source", effective.source === "runtime" ? "This screen" : "Environment variables"],
                ["Stored key", data.has_api_key ? `configured · ••••${data.api_key_last4 ?? ""}` : "not set"],
                ["Updated", data.updated_at ? formatDate(data.updated_at) : "—"],
                ["Env fallback", data.env_provider],
              ].map(([label, value]) => (
                <div key={label} className="flex gap-3">
                  <dt className="w-24 shrink-0 text-faint">{label}</dt>
                  <dd className="break-all font-mono text-[10px] text-slate-300">{String(value)}</dd>
                </div>
              ))}
            </dl>
            {effective.hint && (
              <p className="mt-3 rounded-lg border border-line/70 bg-surface/40 p-2.5 text-[11px] leading-relaxed text-muted">
                {effective.hint}
              </p>
            )}
          </section>

          <section className="card p-4">
            <SectionTitle title="Secret handling" />
            <ul className="space-y-2.5 text-[11px] leading-relaxed text-muted">
              {[
                "The API key is encrypted at rest with a keyed HMAC-SHA256 envelope derived from SECRET_KEY.",
                "It is never returned by GET /system/llm and never written to prompts, logs or audit metadata.",
                "Only platform administrators can change it; changes are audited without the secret.",
                "Rotating SECRET_KEY invalidates stored keys — use Azure Key Vault / managed identity in production.",
              ].map((item) => (
                <li key={item} className="flex gap-2">
                  <ShieldCheck size={12} className="mt-0.5 shrink-0 text-cyan-400" />
                  {item}
                </li>
              ))}
            </ul>
          </section>

          <section className="card p-4">
            <SectionTitle title="Environment fallback" />
            <p className="text-[11px] leading-relaxed text-muted">
              If no provider is stored here, the backend uses{" "}
              <span className="font-mono text-cyan-200">LLM_PROVIDER</span>,{" "}
              <span className="font-mono text-cyan-200">LLM_API_KEY</span>,{" "}
              <span className="font-mono text-cyan-200">LLM_BASE_URL</span> and{" "}
              <span className="font-mono text-cyan-200">LLM_MODEL</span>. The screen configuration takes
              precedence until it is removed or the process restarts without a stored row.
            </p>
            <div className="mt-3 flex items-center gap-2 text-[10px] text-faint">
              <Cpu size={12} /> Structured outputs remain validated against Pydantic schemas.
            </div>
          </section>

          <div className="flex items-center gap-2 rounded-xl border border-violet-500/25 bg-violet-500/[0.04] p-3 text-[11px] leading-relaxed text-violet-100/80">
            <Zap size={14} className="shrink-0 text-violet-300" />
            After saving, the next chat turn, architecture review or plan explanation will use the new
            provider.
          </div>
        </div>
      </div>
    </div>
  );
}
