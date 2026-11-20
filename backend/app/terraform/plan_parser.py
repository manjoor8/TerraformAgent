"""Parse `terraform show -json` output into a summarised, risk-classified plan."""
from __future__ import annotations

from typing import Any

from app.agents.schemas import DestructiveOperation, PlanResourceChange, PlanSummary

DANGEROUS_TYPES = {
    "azurerm_mssql_database": ("critical", "Database replacement or deletion can cause permanent data loss."),
    "azurerm_mssql_server": ("critical", "SQL server deletion removes all contained databases."),
    "azurerm_postgresql_flexible_server": ("critical", "PostgreSQL server deletion removes all databases."),
    "azurerm_mysql_flexible_server": ("critical", "MySQL server deletion removes all databases."),
    "azurerm_storage_account": ("critical", "Storage account deletion removes all blobs and file shares."),
    "azurerm_key_vault": ("high", "Key Vault deletion can break applications and lose secret material."),
    "azurerm_virtual_network": ("high", "VNet replacement disconnects every attached workload."),
    "azurerm_subnet": ("high", "Subnet replacement can disrupt private endpoints and workloads."),
    "azurerm_network_security_group": ("medium", "Network security rule changes can expose or block traffic."),
    "azurerm_private_endpoint": ("high", "Removing a private endpoint removes private connectivity."),
    "azurerm_linux_virtual_machine": ("high", "VM replacement causes downtime and potentially data loss on the OS disk."),
    "azurerm_windows_virtual_machine": ("high", "VM replacement causes downtime and potentially data loss on the OS disk."),
    "azurerm_kubernetes_cluster": ("critical", "AKS cluster replacement destroys workloads and persistent volumes."),
}


def _action(change: dict[str, Any]) -> str:
    actions = change.get("actions") or []
    if actions == ["no-op"] or actions == ["read"]:
        return "noop"
    if actions == ["create"]:
        return "create"
    if actions == ["update"]:
        return "update"
    if actions == ["delete"]:
        return "delete"
    if actions in (["delete", "create"], ["create", "delete"]):
        return "replace"
    return "update" if actions else "noop"


def _reason(change: dict[str, Any], resource_type: str, action: str) -> str:
    if action == "create":
        return "New resource."
    if action == "delete":
        severity, reason = DANGEROUS_TYPES.get(resource_type, ("medium", "Resource will be destroyed."))
        return reason
    if action == "replace":
        replace_paths = change.get("replace_paths") or []
        if replace_paths:
            paths = ", ".join(".".join(str(p) for p in path) for path in replace_paths[:4])
            return f"Resource is replaced because of changes to: {paths}."
        return "Resource must be replaced (destroy then create)."
    return "Resource is updated in place."


def parse_plan_json(
    data: dict[str, Any], *, plan_hash: str = "", source_digest: str = ""
) -> PlanSummary:
    summary = PlanSummary(plan_hash=plan_hash, source_digest=source_digest)
    summary.terraform_version = str(data.get("terraform_version", ""))

    for change in data.get("resource_changes") or []:
        if not isinstance(change, dict):
            continue
        action = _action(change.get("change") or {})
        if action == "noop":
            continue
        resource_type = str(change.get("type", ""))
        address = str(change.get("address", ""))
        module = str(change.get("module_address", "") or "")
        reason = _reason(change.get("change") or {}, resource_type, action)
        summary.resources.append(
            PlanResourceChange(
                address=address,
                type=resource_type,
                action=action,
                module=module,
                replacing=action == "replace",
                reason=reason,
            )
        )
        if action == "create":
            summary.add += 1
        elif action == "update":
            summary.change += 1
        elif action == "delete":
            summary.destroy += 1
        elif action == "replace":
            summary.replace += 1

        if action in ("delete", "replace"):
            severity, base_reason = DANGEROUS_TYPES.get(
                resource_type, ("medium", "Resource is destroyed.")
            )
            summary.destructive_operations.append(
                DestructiveOperation(
                    address=address,
                    type=resource_type,
                    action=action,
                    severity=severity,  # type: ignore[arg-type]
                    reason=f"{base_reason} {reason}".strip(),
                )
            )
        elif action == "update" and resource_type in (
            "azurerm_network_security_group",
            "azurerm_network_security_rule",
            "azurerm_key_vault",
        ):
            summary.destructive_operations.append(
                DestructiveOperation(
                    address=address,
                    type=resource_type,
                    action="update",
                    severity="medium",
                    reason="In-place update changes security posture; review the diff carefully.",
                )
            )

    summary.total = summary.add + summary.change + summary.destroy + summary.replace

    output_changes = data.get("output_changes") or {}
    for name, change in output_changes.items():
        if not isinstance(change, dict):
            continue
        if change.get("sensitive"):
            summary.outputs[name] = "[sensitive]"
        else:
            summary.outputs[name] = change.get("after")

    summary.destructive_operations.sort(
        key=lambda op: {"critical": 0, "high": 1, "medium": 2, "low": 3}.get(op.severity, 4)
    )
    return summary
