"""Deterministic validation/normalization of architecture specs (LLM or heuristic)."""
from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from typing import Any

from app.agents import catalog
from app.agents.costing import estimate_cost
from app.agents.schemas import ArchitectureSpec, NetworkSpec, SubnetSpec
from app.config import get_settings

ALLOWED_TYPE_PREFIXES = ("azurerm_", "azuread_", "azapi_")


class ArchitectureValidationError(ValueError):
    pass


@dataclass
class ValidationOutcome:
    spec: ArchitectureSpec
    warnings: list[str]


def normalize_and_validate(
    spec: ArchitectureSpec, *, environment: str, use_live_pricing: bool | None = None
) -> ValidationOutcome:
    warnings: list[str] = []
    settings = get_settings()

    if spec.region not in catalog.VALID_REGIONS:
        warnings.append(f"Region '{spec.region}' is not in the known region list; defaulting to eastus.")
        spec.region = "eastus"
    if environment not in spec.environments:
        spec.environments.append(environment)
    spec.environments = list(dict.fromkeys(spec.environments))

    if not spec.resources:
        raise ArchitectureValidationError("Architecture must contain at least one resource")
    if not spec.networks:
        raise ArchitectureValidationError("Architecture must define at least one network")

    for resource in spec.resources:
        if not resource.type.startswith(ALLOWED_TYPE_PREFIXES):
            raise ArchitectureValidationError(
                f"Unsupported Terraform resource type for resource '{resource.id}': {resource.type}"
            )
        if not resource.id or not resource.name:
            raise ArchitectureValidationError("Every resource requires an id and name")
        if resource.module not in {
            "resource-group", "networking", "compute", "database", "storage",
            "key-vault", "monitoring", "security",
        }:
            raise ArchitectureValidationError(
                f"Unknown module '{resource.module}' for resource '{resource.id}'"
            )

    for network in spec.networks:
        _validate_network(network, warnings)

    if spec.security.private_endpoints:
        pe_subnets = [
            s
            for network in spec.networks
            for s in network.subnets
            if s.purpose == "private endpoints"
        ]
        if not pe_subnets:
            raise ArchitectureValidationError(
                "Private endpoints are enabled but no subnet with purpose 'private endpoints' exists"
            )

    if spec.security.key_vault and not any(r.type == "azurerm_key_vault" for r in spec.resources):
        warnings.append("Key Vault is enabled in security settings but no Key Vault resource is defined.")
    if spec.security.diagnostics and not any(
        r.type == "azurerm_log_analytics_workspace" for r in spec.resources
    ):
        warnings.append("Diagnostics are enabled but no Log Analytics workspace is defined.")

    allowed_engines = {"mssql", "postgres", "mysql"}
    for resource in spec.resources:
        if resource.type == "azurerm_mssql_server":
            if resource.config.get("engine", "mssql") not in allowed_engines:
                raise ArchitectureValidationError("Invalid database engine in architecture config")

    spec.cost = estimate_cost(
        spec,
        use_live=settings.live_pricing if use_live_pricing is None else use_live_pricing,
    )
    if spec.summary:
        import re

        spec.summary = re.sub(
            r"approximately \$[\d,]+/month",
            f"approximately ${spec.cost.monthly_total:,.0f}/month",
            spec.summary,
        )
    return ValidationOutcome(spec=spec, warnings=warnings)


def _validate_network(network: NetworkSpec, warnings: list[str]) -> None:
    if not network.address_space:
        raise ArchitectureValidationError(f"Network '{network.name}' has no address space")
    vnet_nets: list[ipaddress.IPv4Network] = []
    for cidr in network.address_space:
        try:
            vnet_nets.append(ipaddress.ip_network(cidr, strict=False))
        except ValueError as exc:
            raise ArchitectureValidationError(f"Invalid VNet CIDR '{cidr}': {exc}") from exc
    if len(vnet_nets) > 1:
        for i, left in enumerate(vnet_nets):
            for right in vnet_nets[i + 1 :]:
                if left.overlaps(right):
                    raise ArchitectureValidationError(f"VNet address spaces overlap: {left} / {right}")

    if not network.subnets:
        raise ArchitectureValidationError(f"Network '{network.name}' has no subnets")

    seen: list[tuple[SubnetSpec, ipaddress.IPv4Network]] = []
    for subnet in network.subnets:
        try:
            subnet_net = ipaddress.ip_network(subnet.cidr, strict=False)
        except ValueError as exc:
            raise ArchitectureValidationError(f"Invalid subnet CIDR '{subnet.cidr}': {exc}") from exc
        if not any(subnet_net.subnet_of(vnet) for vnet in vnet_nets):
            raise ArchitectureValidationError(
                f"Subnet '{subnet.name}' ({subnet.cidr}) is outside the VNet address space"
            )
        for other, other_net in seen:
            if subnet_net.overlaps(other_net):
                raise ArchitectureValidationError(
                    f"Subnets overlap: {subnet.name} ({subnet.cidr}) and {other.name} ({other.cidr})"
                )
        seen.append((subnet, subnet_net))
        if subnet.purpose == "application gateway" and subnet_net.prefixlen > 24:
            warnings.append(
                f"Gateway subnet '{subnet.name}' is smaller than /24; Azure recommends a /24 or larger."
            )
        if subnet.purpose == "private endpoints" and subnet_net.prefixlen > 28:
            raise ArchitectureValidationError("Private endpoint subnet is too small")
