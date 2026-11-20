import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Cloud, KeyRound, Loader2, Plus, RefreshCw, ShieldCheck, Trash2 } from "lucide-react";
import { api, apiErrorMessage } from "../lib/api";
import { AUTH_METHODS, REGIONS } from "../lib/constants";
import { formatDate } from "../lib/format";
import { Modal } from "../components/Modal";
import { StatusBadge } from "../components/StatusBadge";
import { EmptyState, LoadingBlock, PageHeader } from "../components/UI";
import { useToast } from "../context/ToastContext";

export function SubscriptionsPage() {
  const queryClient = useQueryClient();
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [tenantId, setTenantId] = useState("");
  const [subscriptionId, setSubscriptionId] = useState("");
  const [subscriptionName, setSubscriptionName] = useState("");
  const [authMethod, setAuthMethod] = useState("service_principal");
  const [region, setRegion] = useState("uaenorth");

  const subscriptions = useQuery({ queryKey: ["subscriptions"], queryFn: api.subscriptions.list });

  const create = useMutation({
    mutationFn: () =>
      api.subscriptions.create({
        tenant_id: tenantId,
        subscription_id: subscriptionId,
        subscription_name: subscriptionName,
        auth_method: authMethod,
        default_region: region,
      }),
    onSuccess: () => {
      toast.success("Subscription metadata saved");
      queryClient.invalidateQueries({ queryKey: ["subscriptions"] });
      queryClient.invalidateQueries({ queryKey: ["dashboard"] });
      setOpen(false);
      setTenantId("");
      setSubscriptionId("");
      setSubscriptionName("");
    },
    onError: (error) => toast.error(apiErrorMessage(error)),
  });

  const verify = useMutation({
    mutationFn: (id: string) => api.subscriptions.verify(id),
    onSuccess: (subscription) => {
      toast.success(`Verified: ${subscription.connection_status}`);
      queryClient.invalidateQueries({ queryKey: ["subscriptions"] });
    },
    onError: (error) => toast.error(apiErrorMessage(error)),
  });

  const remove = useMutation({
    mutationFn: (id: string) => api.subscriptions.remove(id),
    onSuccess: () => {
      toast.success("Subscription disconnected");
      queryClient.invalidateQueries({ queryKey: ["subscriptions"] });
    },
    onError: (error) => toast.error(apiErrorMessage(error)),
  });

  const items = subscriptions.data?.items ?? [];

  return (
    <div className="space-y-5">
      <PageHeader
        eyebrow="Azure"
        title="Subscriptions"
        description="Non-secret connection metadata. Credentials are supplied by the execution worker, never stored here or sent to the model."
        actions={
          <button className="btn btn-primary" onClick={() => setOpen(true)}>
            <Plus size={15} /> Connect subscription
          </button>
        }
      />

      <div className="card flex items-start gap-3 border-cyan-500/20 bg-cyan-500/[0.03] p-4">
        <ShieldCheck size={18} className="mt-0.5 shrink-0 text-cyan-300" />
        <div className="text-sm text-slate-300">
          <p className="font-medium text-ink">How authentication works</p>
          <p className="mt-1 text-xs leading-relaxed text-muted">
            Terraform runs in an isolated worker with an allow-listed environment. Configure
            <span className="mx-1 font-mono text-[11px] text-cyan-200">ARM_CLIENT_ID / ARM_TENANT_ID / ARM_CLIENT_SECRET</span>
            (or OIDC / managed identity) on the worker. The platform stores only tenant, subscription,
            auth method and the last verified permission summary.
          </p>
        </div>
      </div>

      {subscriptions.isLoading ? (
        <LoadingBlock />
      ) : items.length === 0 ? (
        <EmptyState
          icon={<Cloud size={28} />}
          title="No Azure subscription connected"
          description="Add subscription metadata to enable real Terraform plans and deployment verification."
          action={
            <button className="btn btn-primary" onClick={() => setOpen(true)}>
              <Plus size={15} /> Connect subscription
            </button>
          }
        />
      ) : (
        <div className="card overflow-hidden">
          <div className="overflow-x-auto">
            <table className="table">
              <thead>
                <tr>
                  <th>Subscription</th>
                  <th>Tenant</th>
                  <th>Auth method</th>
                  <th>Status</th>
                  <th>Default region</th>
                  <th>Permissions</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {items.map((subscription) => (
                  <tr key={subscription.id}>
                    <td>
                      <p className="font-medium text-ink">{subscription.subscription_name}</p>
                      <p className="font-mono text-[11px] text-faint">{subscription.subscription_id}</p>
                    </td>
                    <td className="font-mono text-[11px] text-muted">{subscription.tenant_id}</td>
                    <td className="text-muted">{AUTH_METHODS.find((method) => method.value === subscription.auth_method)?.label ?? subscription.auth_method}</td>
                    <td>
                      <StatusBadge status={subscription.connection_status} />
                    </td>
                    <td className="text-muted">{subscription.default_region}</td>
                    <td className="max-w-[260px] text-xs text-muted">
                      {subscription.permissions?.can_deploy === true
                        ? "Deployment permissions detected"
                        : subscription.permissions?.error
                          ? String(subscription.permissions.error).slice(0, 120)
                          : "Not verified"}
                      <p className="mt-0.5 text-[10px] text-faint">{formatDate(subscription.created_at)}</p>
                    </td>
                    <td>
                      <div className="flex justify-end gap-1">
                        <button
                          className="btn btn-ghost btn-sm"
                          onClick={() => verify.mutate(subscription.id)}
                          disabled={verify.isPending}
                        >
                          {verify.isPending ? <Loader2 size={13} className="animate-spin" /> : <RefreshCw size={13} />}
                          Verify
                        </button>
                        <button
                          className="btn btn-ghost btn-sm text-rose-300"
                          onClick={() => {
                            if (window.confirm("Disconnect this subscription?")) remove.mutate(subscription.id);
                          }}
                          disabled={remove.isPending}
                        >
                          <Trash2 size={13} />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <Modal
        open={open}
        onClose={() => setOpen(false)}
        title="Connect Azure subscription"
        description="Store non-secret metadata. The worker authenticates using its own identity."
        footer={
          <>
            <button className="btn" onClick={() => setOpen(false)}>
              Cancel
            </button>
            <button
              className="btn btn-primary"
              disabled={!tenantId.trim() || !subscriptionId.trim() || create.isPending}
              onClick={() => create.mutate()}
            >
              {create.isPending ? <Loader2 size={15} className="animate-spin" /> : <Plus size={15} />}
              Save connection
            </button>
          </>
        }
      >
        <div>
          <label className="label">Tenant ID</label>
          <input className="input font-mono text-xs" value={tenantId} onChange={(event) => setTenantId(event.target.value)} placeholder="00000000-0000-0000-0000-000000000000" />
        </div>
        <div>
          <label className="label">Subscription ID</label>
          <input className="input font-mono text-xs" value={subscriptionId} onChange={(event) => setSubscriptionId(event.target.value)} placeholder="00000000-0000-0000-0000-000000000000" />
        </div>
        <div>
          <label className="label">Display name</label>
          <input className="input" value={subscriptionName} onChange={(event) => setSubscriptionName(event.target.value)} placeholder="Production Platform" />
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          <div>
            <label className="label">Authentication method</label>
            <select className="select" value={authMethod} onChange={(event) => setAuthMethod(event.target.value)}>
              {AUTH_METHODS.map((method) => (
                <option key={method.value} value={method.value}>
                  {method.label}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="label">Default region</label>
            <select className="select" value={region} onChange={(event) => setRegion(event.target.value)}>
              {REGIONS.map((region) => (
                <option key={region.value} value={region.value}>
                  {region.label}
                </option>
              ))}
            </select>
          </div>
        </div>
        <div className="flex items-start gap-2 rounded-lg border border-line bg-surface/50 p-3 text-xs text-muted">
          <KeyRound size={14} className="mt-0.5 shrink-0" />
          Never paste a client secret here. Configure it on the execution worker or in Key Vault.
        </div>
      </Modal>
    </div>
  );
}
