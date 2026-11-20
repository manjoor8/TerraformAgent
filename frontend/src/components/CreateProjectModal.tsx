import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, Plus } from "lucide-react";
import { Modal } from "./Modal";
import { api, apiErrorMessage } from "../lib/api";
import { ENVIRONMENTS, REGIONS } from "../lib/constants";
import { useToast } from "../context/ToastContext";
import type { Project } from "../lib/types";

export function CreateProjectModal({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated?: (project: Project) => void;
}) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [environment, setEnvironment] = useState("dev");
  const [region, setRegion] = useState("uaenorth");
  const [subscriptionId, setSubscriptionId] = useState("");

  const subscriptions = useQuery({ queryKey: ["subscriptions"], queryFn: api.subscriptions.list });

  const create = useMutation({
    mutationFn: () =>
      api.projects.create({
        name,
        description,
        environment,
        region,
        azure_subscription_id: subscriptionId || null,
      } as Partial<Project>),
    onSuccess: (project) => {
      toast.success("Project created");
      queryClient.invalidateQueries({ queryKey: ["projects"] });
      queryClient.invalidateQueries({ queryKey: ["dashboard"] });
      setName("");
      setDescription("");
      setSubscriptionId("");
      onClose();
      onCreated?.(project);
    },
    onError: (error) => toast.error(apiErrorMessage(error)),
  });

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="New infrastructure project"
      description="Start with a name and target environment. You can refine the requirements in conversation."
      footer={
        <>
          <button className="btn" onClick={onClose}>
            Cancel
          </button>
          <button
            className="btn btn-primary"
            disabled={!name.trim() || create.isPending}
            onClick={() => create.mutate()}
          >
            {create.isPending ? <Loader2 size={15} className="animate-spin" /> : <Plus size={15} />}
            Create project
          </button>
        </>
      }
    >
      <div>
        <label className="label" htmlFor="project-name">
          Project name
        </label>
        <input
          id="project-name"
          className="input"
          value={name}
          onChange={(event) => setName(event.target.value)}
          placeholder="Flight Booking Platform"
          autoFocus
        />
      </div>
      <div>
        <label className="label" htmlFor="project-description">
          Short description
        </label>
        <textarea
          id="project-description"
          className="textarea min-h-20"
          value={description}
          onChange={(event) => setDescription(event.target.value)}
          placeholder="Highly available customer-facing booking platform for 5,000 concurrent users."
        />
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <div>
          <label className="label" htmlFor="project-env">
            Environment
          </label>
          <select
            id="project-env"
            className="select"
            value={environment}
            onChange={(event) => setEnvironment(event.target.value)}
          >
            {ENVIRONMENTS.map((environment) => (
              <option key={environment.value} value={environment.value}>
                {environment.label}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label className="label" htmlFor="project-region">
            Azure region
          </label>
          <select
            id="project-region"
            className="select"
            value={region}
            onChange={(event) => setRegion(event.target.value)}
          >
            {REGIONS.map((region) => (
              <option key={region.value} value={region.value}>
                {region.label}
              </option>
            ))}
          </select>
        </div>
      </div>
      <div>
        <label className="label" htmlFor="project-subscription">
          Azure subscription (optional now)
        </label>
        <select
          id="project-subscription"
          className="select"
          value={subscriptionId}
          onChange={(event) => setSubscriptionId(event.target.value)}
        >
          <option value="">Decide later</option>
          {(subscriptions.data?.items ?? []).map((subscription) => (
            <option key={subscription.id} value={subscription.id}>
              {subscription.subscription_name} · {subscription.subscription_id.slice(0, 8)}…
            </option>
          ))}
        </select>
        <p className="mt-1 text-xs text-faint">
          Plans require a subscription. Plan approval is bound to its ID.
        </p>
      </div>
    </Modal>
  );
}
