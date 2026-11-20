import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Database, Loader2, Save, ShieldCheck, Trash2 } from "lucide-react";
import { api, apiErrorMessage } from "../../lib/api";
import { ENVIRONMENTS, REGIONS } from "../../lib/constants";
import { StatusBadge } from "../../components/StatusBadge";
import { LoadingBlock, SectionTitle } from "../../components/UI";
import { useToast } from "../../context/ToastContext";

export function SettingsTab() {
  const { projectId = "" } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const toast = useToast();

  const project = useQuery({ queryKey: ["project", projectId], queryFn: () => api.projects.get(projectId) });
  const subscriptions = useQuery({ queryKey: ["subscriptions"], queryFn: api.subscriptions.list });
  const policies = useQuery({ queryKey: ["policies"], queryFn: api.system.policies });

  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [environment, setEnvironment] = useState("dev");
  const [region, setRegion] = useState("uaenorth");
  const [subscriptionId, setSubscriptionId] = useState("");
  const [backendMode, setBackendMode] = useState<"local" | "azurerm">("local");
  const [resourceGroup, setResourceGroup] = useState("");
  const [storageAccount, setStorageAccount] = useState("");
  const [container, setContainer] = useState("tfstate");
  const [keyPrefix, setKeyPrefix] = useState("");
  const [disabledRules, setDisabledRules] = useState<string[]>([]);
  const [deleteConfirm, setDeleteConfirm] = useState("");

  useEffect(() => {
    const data = project.data;
    if (!data) return;
    setName(data.name);
    setDescription(data.description);
    setEnvironment(data.environment);
    setRegion(data.region);
    setSubscriptionId(data.azure_subscription_id ?? "");
    const backend = data.settings?.backend;
    setBackendMode(backend?.mode === "azurerm" ? "azurerm" : "local");
    setResourceGroup(backend?.resource_group ?? "");
    setStorageAccount(backend?.storage_account ?? "");
    setContainer(backend?.container ?? "tfstate");
    setKeyPrefix(backend?.key_prefix ?? "");
    setDisabledRules(data.settings?.policies?.disabled_rules ?? []);
  }, [project.data]);

  const save = useMutation({
    mutationFn: () =>
      api.projects.update(projectId, {
        name,
        description,
        environment,
        region,
        azure_subscription_id: subscriptionId || null,
        settings: {
          backend:
            backendMode === "azurerm"
              ? {
                  mode: "azurerm",
                  resource_group: resourceGroup,
                  storage_account: storageAccount,
                  container,
                  key_prefix: keyPrefix || `projects/${projectId}`,
                }
              : { mode: "local" },
          policies: { disabled_rules: disabledRules },
        },
      }),
    onSuccess: () => {
      toast.success("Project settings saved");
      queryClient.invalidateQueries({ queryKey: ["project", projectId] });
      queryClient.invalidateQueries({ queryKey: ["overview", projectId] });
      queryClient.invalidateQueries({ queryKey: ["terraform", projectId] });
    },
    onError: (error) => toast.error(apiErrorMessage(error)),
  });

  const remove = useMutation({
    mutationFn: () => api.projects.remove(projectId),
    onSuccess: () => {
      toast.success("Project deleted");
      queryClient.invalidateQueries({ queryKey: ["projects"] });
      queryClient.invalidateQueries({ queryKey: ["dashboard"] });
      navigate("/projects");
    },
    onError: (error) => toast.error(apiErrorMessage(error)),
  });

  const configurablePolicies = useMemo(
    () => (policies.data?.items as { id: string; title: string; severity: string; can_disable: boolean }[] | undefined)?.filter((rule) => rule.can_disable) ?? [],
    [policies.data],
  );

  if (project.isLoading) return <LoadingBlock />;
  if (!project.data) return null;
  const data = project.data;

  const toggleRule = (ruleId: string) => {
    setDisabledRules((current) =>
      current.includes(ruleId) ? current.filter((item) => item !== ruleId) : [...current, ruleId],
    );
  };

  return (
    <div className="grid gap-4 xl:grid-cols-[1.4fr_1fr]">
      <div className="space-y-4">
        <section className="card p-4">
          <SectionTitle title="Project" description="Environment or region changes invalidate architecture approvals." />
          <div className="space-y-3">
            <div className="grid gap-3 sm:grid-cols-2">
              <div>
                <label className="label">Name</label>
                <input className="input" value={name} onChange={(event) => setName(event.target.value)} />
              </div>
              <div>
                <label className="label">Environment</label>
                <select className="select" value={environment} onChange={(event) => setEnvironment(event.target.value)}>
                  {ENVIRONMENTS.map((item) => (
                    <option key={item.value} value={item.value}>
                      {item.label}
                    </option>
                  ))}
                </select>
              </div>
            </div>
            <div>
              <label className="label">Description</label>
              <textarea className="textarea min-h-20" value={description} onChange={(event) => setDescription(event.target.value)} />
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <div>
                <label className="label">Region</label>
                <select className="select" value={region} onChange={(event) => setRegion(event.target.value)}>
                  {REGIONS.map((item) => (
                    <option key={item.value} value={item.value}>
                      {item.label}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label className="label">Azure subscription</label>
                <select className="select" value={subscriptionId} onChange={(event) => setSubscriptionId(event.target.value)}>
                  <option value="">Not configured</option>
                  {(subscriptions.data?.items ?? []).map((subscription) => (
                    <option key={subscription.id} value={subscription.id}>
                      {subscription.subscription_name}
                    </option>
                  ))}
                </select>
              </div>
            </div>
          </div>
        </section>

        <section className="card p-4">
          <SectionTitle
            title="Terraform state backend"
            description="Local is fine for a demo. Shared or production deployments require Azure Blob state."
          />
          <div className="space-y-3">
            <div className="flex gap-2">
              {(["local", "azurerm"] as const).map((mode) => (
                <button
                  key={mode}
                  className={`btn btn-sm ${backendMode === mode ? "btn-primary" : ""}`}
                  onClick={() => setBackendMode(mode)}
                >
                  <Database size={13} /> {mode === "local" ? "Local state" : "Azure Blob (recommended)"}
                </button>
              ))}
            </div>
            {backendMode === "azurerm" && (
              <div className="grid gap-3 sm:grid-cols-2">
                <div>
                  <label className="label">State resource group</label>
                  <input className="input" value={resourceGroup} onChange={(event) => setResourceGroup(event.target.value)} placeholder="rg-tfstate" />
                </div>
                <div>
                  <label className="label">State storage account</label>
                  <input className="input" value={storageAccount} onChange={(event) => setStorageAccount(event.target.value)} placeholder="sttfstateprod" />
                </div>
                <div>
                  <label className="label">Container</label>
                  <input className="input" value={container} onChange={(event) => setContainer(event.target.value)} placeholder="tfstate" />
                </div>
                <div>
                  <label className="label">Key prefix</label>
                  <input className="input" value={keyPrefix} onChange={(event) => setKeyPrefix(event.target.value)} placeholder={`projects/${projectId}`} />
                </div>
              </div>
            )}
            <div className="flex items-start gap-2 rounded-lg border border-line bg-surface/40 p-3 text-[11px] leading-relaxed text-muted">
              <ShieldCheck size={14} className="mt-0.5 shrink-0 text-cyan-300" />
              Terraform state never enters the application database, prompts or audit metadata. It stays in the
              configured backend and is accessed by the worker only.
            </div>
          </div>
        </section>

        <section className="card p-4">
          <SectionTitle title="Security policies" description="Critical controls cannot be disabled." />
          <div className="space-y-1.5">
            {configurablePolicies.map((rule) => (
              <label key={rule.id} className="flex items-start gap-2 rounded-lg border border-line/60 bg-surface/30 px-2.5 py-2 text-xs">
                <input
                  type="checkbox"
                  className="mt-0.5 accent-cyan-500"
                  checked={disabledRules.includes(rule.id)}
                  onChange={() => toggleRule(rule.id)}
                />
                <span className="flex-1 text-slate-300">
                  <span className="font-mono text-[10px] text-cyan-200">{rule.id}</span> · {rule.title}
                </span>
                <StatusBadge status={rule.severity} label={rule.severity} />
              </label>
            ))}
          </div>
          <p className="mt-2 text-[10px] text-faint">
            Disabling a policy is recorded in the audit log. Non-disableable controls (public storage, open SSH,
            hardcoded secrets) always run.
          </p>
        </section>

        <button className="btn btn-primary" onClick={() => save.mutate()} disabled={save.isPending}>
          {save.isPending ? <Loader2 size={15} className="animate-spin" /> : <Save size={15} />}
          Save settings
        </button>
      </div>

      <div className="space-y-4">
        <section className="card p-4">
          <SectionTitle title="Project metadata" />
          <dl className="space-y-2 text-xs">
            {[
              ["Project ID", data.id],
              ["Status", data.status],
              ["Created", new Date(data.created_at).toLocaleString()],
              ["Architecture version", data.counts.architecture_version ?? "—"],
              ["Terraform version", data.counts.terraform_version ?? "—"],
              ["Subscription", data.subscription?.subscription_id ?? "—"],
            ].map(([label, value]) => (
              <div key={String(label)} className="flex gap-3">
                <dt className="w-32 shrink-0 text-faint">{label}</dt>
                <dd className="break-all font-mono text-[10px] text-slate-300">{String(value)}</dd>
              </div>
            ))}
          </dl>
        </section>

        <section className="card border-rose-500/25 p-4">
          <SectionTitle title="Danger zone" description="Deletion is permanent and cascades all artifacts." />
          <p className="text-xs text-muted">
            Removing the project deletes its conversations, documents, architecture versions, Terraform files,
            plans, approvals and deployment records. Audit events are retained.
          </p>
          <div className="mt-3 space-y-2">
            <input
              className="input text-xs"
              placeholder={`Type "${data.name}" to confirm`}
              value={deleteConfirm}
              onChange={(event) => setDeleteConfirm(event.target.value)}
            />
            <button
              className="btn btn-danger w-full"
              disabled={deleteConfirm !== data.name || remove.isPending}
              onClick={() => remove.mutate()}
            >
              {remove.isPending ? <Loader2 size={15} className="animate-spin" /> : <Trash2 size={15} />}
              Delete project
            </button>
          </div>
          <p className="mt-2 flex items-center gap-1 text-[10px] text-rose-300/80">
            <AlertTriangle size={11} /> There is no undo. Infrastructures already deployed are not destroyed.
          </p>
        </section>
      </div>
    </div>
  );
}
