"""Deterministic Terraform project generator driven by an approved architecture."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

from app.agents import catalog
from app.agents.schemas import ArchitectureSpec
from app.terraform import templates_compute as tc
from app.terraform import templates_core as tcore
from app.terraform import templates_data as tdata

_MISSING = object()


@dataclass
class ProjectGenInput:
    project_name: str
    project_id: str
    environment: str
    region: str
    tags: dict[str, str] = field(default_factory=dict)
    backend: dict[str, Any] = field(default_factory=lambda: {"mode": "local"})
    subscription_id: str | None = None
    admin_ssh_public_key_hint: bool = True


@dataclass
class GeneratedProject:
    files: dict[str, str]
    module_map: list[dict[str, str]]
    notes: list[str] = field(default_factory=list)
    environment: str = "dev"


def _s(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _hcl(value: Any, indent: int = 0) -> str:
    pad = "  " * indent
    inner_pad = "  " * (indent + 1)
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return _s(value)
    if isinstance(value, list):
        if not value:
            return "[]"
        if all(isinstance(v, (str, int, float, bool)) for v in value):
            return "[" + ", ".join(_hcl(v) for v in value) + "]"
        items = ",\n".join(inner_pad + _hcl(v, indent + 1) for v in value)
        return "[\n" + items + "\n" + pad + "]"
    if isinstance(value, dict):
        if not value:
            return "{}"
        items = []
        for key in sorted(value):
            items.append(f"{inner_pad}{_s(str(key))} = {_hcl(value[key], indent + 1)}")
        return "{\n" + ",\n".join(items) + "\n" + pad + "}"
    return _s(str(value))


def _slug(value: str, max_len: int = 30) -> str:
    cleaned = re.sub(r"[^a-z0-9-]+", "-", value.lower()).strip("-")
    cleaned = re.sub(r"-{2,}", "-", cleaned) or "app"
    return cleaned[:max_len].strip("-")


def _alnum(value: str, max_len: int = 24) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "", value.lower()) or "app"
    return cleaned[:max_len]


def _short_hash(value: str, length: int = 4) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:length]


def _var(
    name: str,
    type_expr: str,
    *,
    description: str,
    default: Any = _MISSING,
    sensitive: bool = False,
    validation: str | None = None,
) -> str:
    lines = [f'variable "{name}" {{', f"  description = {_s(description)}", f"  type        = {type_expr}"]
    if default is not _MISSING:
        lines.append(f"  default     = {_hcl(default, 1)}")
    if sensitive:
        lines.append("  sensitive   = true")
    if validation:
        lines.append(validation.rstrip())
    lines.append("}")
    return "\n".join(lines)


class TerraformProjectGenerator:
    def __init__(self, spec: ArchitectureSpec, info: ProjectGenInput) -> None:
        self.spec = spec
        self.info = info
        self.notes: list[str] = []
        self.env = info.environment or (spec.environments[0] if spec.environments else "dev")
        self.region = spec.region or info.region
        self.network = spec.networks[0] if spec.networks else None
        self.resources_by_type: dict[str, list[Any]] = {}
        for resource in spec.resources:
            self.resources_by_type.setdefault(resource.type, []).append(resource)

        self.has_vm = bool(
            self.resources_by_type.get("azurerm_linux_virtual_machine")
            or self.resources_by_type.get("azurerm_windows_virtual_machine")
        )
        self.has_aks = bool(self.resources_by_type.get("azurerm_kubernetes_cluster"))
        self.has_app_service = bool(
            self.resources_by_type.get("azurerm_linux_web_app")
            or self.resources_by_type.get("azurerm_service_plan")
        )
        self.compute_kind = (
            "vm" if self.has_vm else ("aks" if self.has_aks else ("app_service" if self.has_app_service else "none"))
        )
        self.has_app_gateway = bool(self.resources_by_type.get("azurerm_application_gateway"))
        self.db_engine = self._db_engine()
        self.has_storage = bool(self.resources_by_type.get("azurerm_storage_account"))
        self.has_key_vault = bool(self.resources_by_type.get("azurerm_key_vault"))
        # Monitoring is generated when the architecture includes telemetry, compute
        # or a database. The compute module always needs a Log Analytics workspace.
        self.has_monitoring = bool(
            self.resources_by_type.get("azurerm_log_analytics_workspace")
            or self.resources_by_type.get("azurerm_application_insights")
            or self.compute_kind != "none"
            or self.db_engine != "none"
        )
        self.private = bool(spec.security.private_endpoints)
        self.enable_lock = self.env == "production"
        self.needs_identity = self.compute_kind != "none"

        self.prefix = _slug(f"{info.project_name}", 20) + f"-{self.env}"
        self.suffix = _short_hash(info.project_id or info.project_name)
        self.vnet_name = (self.network.name if self.network else f"vnet-{self.prefix}")[:64]
        self.subnet_nsgs: dict[str, str] = {}
        self.subnets = self._build_subnets()
        self.nsg_rules = self._build_nsg_rules()
        self.private_dns_zones = self._private_dns_zones()

    # -- derivation ---------------------------------------------------------
    def _db_engine(self) -> str:
        if self.resources_by_type.get("azurerm_mssql_server"):
            return "mssql"
        if self.resources_by_type.get("azurerm_postgresql_flexible_server"):
            return "postgres"
        if self.resources_by_type.get("azurerm_mysql_flexible_server"):
            return "mysql"
        return "none"

    def _build_subnets(self) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        if not self.network:
            return {"snet-app": {"cidr": "10.0.1.0/24", "delegation_service": None, "service_endpoints": []}}
        for subnet in self.network.subnets:
            delegation = subnet.delegation
            if not delegation and subnet.purpose == "database":
                if self._db_engine() == "postgres":
                    delegation = "Microsoft.DBforPostgreSQL/flexibleServers"
                elif self._db_engine() == "mysql":
                    delegation = "Microsoft.DBforMySQL/flexibleServers"
            nsg = subnet.nsg
            if not nsg:
                if subnet.purpose == "application":
                    nsg = "nsg-app"
                elif subnet.purpose == "database":
                    nsg = "nsg-data"
            if nsg:
                self.subnet_nsgs[subnet.name] = nsg
            result[subnet.name] = {
                "cidr": subnet.cidr,
                "delegation_service": delegation,
                "service_endpoints": subnet.service_endpoints or [],
            }
        if not result:
            result["snet-app"] = {"cidr": "10.0.1.0/24", "delegation_service": None, "service_endpoints": []}
        return result

    def _build_nsg_rules(self) -> dict[str, list[dict[str, Any]]]:
        if self.network and self.network.nsg_rules:
            return self.network.nsg_rules
        app_cidr = self._subnet_cidr("application") or "10.0.1.0/24"
        gateway_cidr = self._subnet_cidr("application gateway")
        db_cidr = self._subnet_cidr("database") or "10.0.2.0/24"
        app_source = gateway_cidr or "Internet"
        rules: dict[str, list[dict[str, Any]]] = {
            "nsg-app": [
                {
                    "name": "allow-https",
                    "priority": 100,
                    "direction": "Inbound",
                    "access": "Allow",
                    "protocol": "Tcp",
                    "destination_port_ranges": ["443"],
                    "source_address_prefixes": [app_source],
                },
                {
                    "name": "deny-all-inbound",
                    "priority": 4000,
                    "direction": "Inbound",
                    "access": "Deny",
                    "protocol": "*",
                    "destination_port_ranges": ["*"],
                    "source_address_prefixes": ["*"],
                },
            ]
        }
        if self.db_engine != "none":
            port = "1433" if self.db_engine == "mssql" else "5432"
            rules["nsg-data"] = [
                {
                    "name": "allow-db-from-app",
                    "priority": 100,
                    "direction": "Inbound",
                    "access": "Allow",
                    "protocol": "Tcp",
                    "destination_port_ranges": [port],
                    "source_address_prefixes": [app_cidr],
                },
                {
                    "name": "deny-all-inbound",
                    "priority": 4000,
                    "direction": "Inbound",
                    "access": "Deny",
                    "protocol": "*",
                    "destination_port_ranges": ["*"],
                    "source_address_prefixes": ["*"],
                },
            ]
        return rules

    def _subnet_cidr(self, purpose: str) -> str | None:
        if not self.network:
            return None
        for subnet in self.network.subnets:
            if subnet.purpose == purpose:
                return subnet.cidr
        return None

    def _subnet_name(self, purpose: str) -> str | None:
        if not self.network:
            return None
        for subnet in self.network.subnets:
            if subnet.purpose == purpose:
                return subnet.name
        return None

    def _private_dns_zones(self) -> list[str]:
        zones: list[str] = list(self.spec.networks[0].private_dns_zones) if self.network else []
        if self.private:
            if self.has_storage:
                zones.append(catalog.PRIVATE_DNS_ZONES["storage"])
            if self.has_key_vault:
                zones.append(catalog.PRIVATE_DNS_ZONES["key_vault"])
            if self.has_app_service and self._app_service_private():
                zones.append(catalog.PRIVATE_DNS_ZONES["app_service"])
        if self.db_engine != "none":
            # Private endpoints (mssql) and delegated-subnet flexible servers
            # (postgres/mysql) both require the matching private DNS zone.
            zones.append(catalog.PRIVATE_DNS_ZONES[self.db_engine])
        return list(dict.fromkeys(z for z in zones if z))

    def _app_service_private(self) -> bool:
        return self.has_app_service and self.private

    def _first(self, type_name: str) -> Any:
        items = self.resources_by_type.get(type_name) or []
        return items[0] if items else None

    def _resource_name(self, type_name: str, fallback: str) -> str:
        resource = self._first(type_name)
        return resource.name if resource else fallback

    # -- names --------------------------------------------------------------
    @property
    def rg_name(self) -> str:
        return self._resource_name("azurerm_resource_group", f"rg-{self.prefix}")[:90]

    @property
    def storage_name(self) -> str:
        explicit = self._resource_name("azurerm_storage_account", "")
        if explicit and re.fullmatch(r"[a-z0-9]{3,24}", explicit):
            return explicit
        base = _alnum(f"{self.info.project_name}{self.env}", 20)
        return f"{base}{self.suffix}"[:24].ljust(3, "0")

    @property
    def key_vault_name(self) -> str:
        explicit = self._resource_name("azurerm_key_vault", "")
        if explicit and 3 <= len(explicit) <= 24:
            return explicit
        base = re.sub(r"[^a-zA-Z0-9-]", "", f"kv-{_slug(self.info.project_name, 10)}-{self.suffix}")
        return base[:24]

    @property
    def app_name(self) -> str:
        explicit = self._resource_name("azurerm_linux_web_app", "")
        if explicit:
            return explicit[:60]
        return f"app-{self.prefix}-{self.suffix}"[:60]

    # -- root variables -----------------------------------------------------
    def _root_variables(self) -> str:
        vars_: list[str] = [
            _var("project_name", "string", description="Project name used for tagging.", default=self.info.project_name),
            _var("environment", "string", description="Deployment environment.", default=self.env),
            _var("location", "string", description="Azure region.", default=self.region),
            _var("name_prefix", "string", description="Naming prefix for resources.", default=self.prefix),
            _var("tags", "map(string)", description="Additional tags applied to every resource.", default=self.info.tags),
            _var("vnet_name", "string", description="Virtual network name.", default=self.vnet_name),
            _var(
                "vnet_address_space",
                "list(string)",
                description="Virtual network address space.",
                default=self.network.address_space if self.network else ["10.0.0.0/16"],
            ),
            _var(
                "subnets",
                """map(object({
    cidr               = string
    delegation_service = optional(string)
    service_endpoints  = optional(list(string), [])
  }))""",
                description="Subnet definitions keyed by subnet name.",
                default=self.subnets,
            ),
            _var("subnet_nsgs", "map(string)", description="Subnet to NSG associations.", default=self.subnet_nsgs),
            _var(
                "nsg_rules",
                """map(list(object({
    name                    = string
    priority                = number
    direction               = string
    access                  = string
    protocol                = string
    destination_port_ranges = list(string)
    source_address_prefixes = list(string)
  })))""",
                description="Network security group rules keyed by NSG name.",
                default=self.nsg_rules,
            ),
            _var(
                "enable_nat_gateway",
                "bool",
                description="Create a NAT gateway for outbound egress.",
                default=bool(self.network.nat_gateway) if self.network else False,
            ),
            _var(
                "nat_subnet_key",
                "string",
                description="Subnet routed through the NAT gateway.",
                default=self._subnet_name("application") or "snet-app",
            ),
            _var(
                "private_dns_zones",
                "list(string)",
                description="Private DNS zones to create and link.",
                default=self.private_dns_zones,
            ),
            _var(
                "enable_private_endpoints",
                "bool",
                description="Use private endpoints for supported services.",
                default=self.private,
            ),
            _var(
                "log_retention_days",
                "number",
                description="Log Analytics retention in days.",
                default=90 if self.env == "production" else 30,
            ),
            _var(
                "enable_resource_lock",
                "bool",
                description="Add a CanNotDelete lock to the resource group.",
                default=self.enable_lock,
            ),
        ]

        if self.compute_kind != "none":
            compute_resource = (
                self._first("azurerm_linux_virtual_machine")
                or self._first("azurerm_windows_virtual_machine")
                or self._first("azurerm_linux_web_app")
                or self._first("azurerm_kubernetes_cluster")
            )
            count = int((compute_resource.config or {}).get("count", 2)) if compute_resource else 2
            vars_.extend(
                [
                    _var(
                        "compute_kind",
                        "string",
                        description="Compute platform: vm, app_service or aks.",
                        default=self.compute_kind,
                        validation='''  validation {
    condition     = contains(["vm", "app_service", "aks"], var.compute_kind)
    error_message = "compute_kind must be vm, app_service or aks."
  }''',
                    ),
                    _var(
                        "vm_size",
                        "string",
                        description="Virtual machine size.",
                        default=(compute_resource.sku if compute_resource and compute_resource.sku else "Standard_D2s_v5"),
                    ),
                    _var("vm_count", "number", description="Number of VMs or AKS nodes.", default=count),
                    _var(
                        "os_type",
                        "string",
                        description="VM operating system.",
                        default="windows" if self.resources_by_type.get("azurerm_windows_virtual_machine") else "linux",
                    ),
                    _var("admin_username", "string", description="VM administrator username.", default="azureadmin"),
                    _var(
                        "admin_ssh_public_key",
                        "string",
                        description="SSH public key for Linux VMs. Supply per environment.",
                        default="",
                    ),
                    _var(
                        "admin_password",
                        "string",
                        description="Administrator password for Windows VMs. Supply from a secret store.",
                        default="",
                        sensitive=True,
                    ),
                    _var(
                        "app_service_sku",
                        "string",
                        description="App Service plan SKU.",
                        default=(self._first("azurerm_service_plan").sku if self._first("azurerm_service_plan") and self._first("azurerm_service_plan").sku else "P1v3"),
                    ),
                    _var(
                        "zone_redundant",
                        "bool",
                        description="Spread compute across availability zones.",
                        default=bool(compute_resource.zone_redundant) if compute_resource else False,
                    ),
                ]
            )

        if self.has_app_gateway:
            vars_.extend(
                [
                    _var(
                        "app_gateway_certificate_secret_id",
                        "string",
                        description="Key Vault secret ID containing the TLS certificate (PFX).",
                        default="",
                    ),
                    _var(
                        "app_gateway_name",
                        "string",
                        description="Application Gateway name.",
                        default=(self._first("azurerm_application_gateway").name or "appgw")[:80],
                    ),
                ]
            )

        if self.db_engine != "none":
            db_server = {
                "mssql": "azurerm_mssql_server",
                "postgres": "azurerm_postgresql_flexible_server",
                "mysql": "azurerm_mysql_flexible_server",
            }[self.db_engine]
            db_resource = self._first(db_server)
            db_name_resource = self._first("azurerm_mssql_database")
            vars_.extend(
                [
                    _var("database_engine", "string", description="Database engine.", default=self.db_engine),
                    _var(
                        "database_server_name",
                        "string",
                        description="Database server name.",
                        default=(db_resource.name if db_resource else f"sql-{self.prefix}")[:63],
                    ),
                    _var(
                        "database_name",
                        "string",
                        description="Application database name.",
                        default=(db_name_resource.name if db_name_resource else _slug(f"{self.info.project_name}-db", 50)),
                    ),
                    _var(
                        "database_sku",
                        "string",
                        description="Database SKU.",
                        default=(db_resource.sku if db_resource and db_resource.sku else "GP_Gen5_2"),
                    ),
                    _var(
                        "database_max_size_gb",
                        "number",
                        description="Maximum database size in GB.",
                        default=int((db_name_resource.config or {}).get("max_size_gb", 32)) if db_name_resource else 32,
                    ),
                    _var(
                        "database_admin_login",
                        "string",
                        description="Database administrator login.",
                        default=f"dbadmin{self.suffix}",
                    ),
                    _var(
                        "database_admin_password",
                        "string",
                        description="Database administrator password. Supply from a secret store.",
                        default="",
                        sensitive=True,
                    ),
                    _var(
                        "database_zone_redundant",
                        "bool",
                        description="Enable database zone redundancy.",
                        default=bool(db_resource.zone_redundant) if db_resource else False,
                    ),
                    _var(
                        "database_backup_retention_days",
                        "number",
                        description="Database backup retention in days.",
                        default=int((db_resource.config or {}).get("backup_retention_days", 7)) if db_resource else 7,
                    ),
                ]
            )

        if self.has_storage:
            replication = "LRS"
            storage = self._first("azurerm_storage_account")
            if storage and storage.sku:
                replication = storage.sku.replace("Standard_", "") or "LRS"
            containers = [
                r.name for r in self.resources_by_type.get("azurerm_storage_container", [])
            ] or ["app-data"]
            vars_.extend(
                [
                    _var("storage_account_name", "string", description="Storage account name.", default=self.storage_name),
                    _var("storage_replication_type", "string", description="Storage replication type.", default=replication),
                    _var("storage_containers", "list(string)", description="Blob container names.", default=containers),
                ]
            )

        if self.has_key_vault:
            vars_.extend(
                [
                    _var("key_vault_name", "string", description="Key Vault name.", default=self.key_vault_name),
                    _var(
                        "key_vault_secrets",
                        "map(string)",
                        description="Secrets to create. Supply values from a secret store; never commit values.",
                        default={},
                    ),
                ]
            )

        if self.has_monitoring:
            law = self._first("azurerm_log_analytics_workspace")
            appi = self._first("azurerm_application_insights")
            vars_.extend(
                [
                    _var(
                        "log_analytics_workspace_name",
                        "string",
                        description="Log Analytics workspace name.",
                        default=(law.name if law else f"log-{self.prefix}")[:63],
                    ),
                    _var(
                        "enable_app_insights",
                        "bool",
                        description="Deploy Application Insights.",
                        default=bool(appi),
                    ),
                    _var(
                        "app_insights_name",
                        "string",
                        description="Application Insights name.",
                        default=(appi.name if appi else f"appi-{self.prefix}")[:63],
                    ),
                ]
            )

        vars_.append(
            _var(
                "subscription_id",
                "string",
                description="Target Azure subscription ID. Empty uses the authenticated context.",
                default=self.info.subscription_id or "",
            )
        )
        return "\n\n".join(vars_) + "\n"

    # -- root locals & main -------------------------------------------------
    def _root_locals(self) -> str:
        role_entries = []
        if self.has_storage and self.compute_kind != "none":
            role_entries.append(
                """    storage_blob = {
      scope                = module.storage[0].id
      principal_id         = azurerm_user_assigned_identity.workload.principal_id
      role_definition_name = "Storage Blob Data Contributor"
    }"""
            )
        role_block = "\n".join(role_entries) if role_entries else ""
        if self.has_key_vault and self.needs_identity:
            kv_roles = """{
      workload = {
        principal_id         = azurerm_user_assigned_identity.workload.principal_id
        role_definition_name = "Key Vault Secrets User"
      }
    }"""
            if self.has_app_gateway:
                kv_roles = """merge(
      {
        workload = {
          principal_id         = azurerm_user_assigned_identity.workload.principal_id
          role_definition_name = "Key Vault Secrets User"
        }
      },
      var.compute_kind == "vm" && var.app_gateway_name != "" ? {
        gateway = {
          principal_id         = azurerm_user_assigned_identity.appgw[0].principal_id
          role_definition_name = "Key Vault Secrets User"
        }
      } : {}
    )"""
        else:
            kv_roles = "{}"
        return f'''locals {{
  common_tags = merge(
    var.tags,
    {{
      project     = var.project_name
      environment = var.environment
      managed_by  = "terraform"
    }}
  )

  key_vault_role_assignments = {kv_roles}

  security_role_assignments = {{
{role_block}
  }}
}}
'''

    def _root_main(self) -> str:
        parts: list[str] = []
        if self.has_key_vault:
            parts.extend(['data "azurerm_client_config" "current" {}', ""])
        if self.needs_identity:
            parts.extend(
                [
                    'resource "azurerm_user_assigned_identity" "workload" {',
                    '  name                = "id-${var.name_prefix}"',
                    "  location            = var.location",
                    "  resource_group_name = module.resource_group.name",
                    "  tags                = local.common_tags",
                    "}",
                    "",
                ]
            )
        if self.has_app_gateway:
            parts.extend(
                [
                    'resource "azurerm_user_assigned_identity" "appgw" {',
                    "  count               = var.compute_kind == \"vm\" ? 1 : 0",
                    '  name                = "id-appgw-${var.name_prefix}"',
                    "  location            = var.location",
                    "  resource_group_name = module.resource_group.name",
                    "  tags                = local.common_tags",
                    "}",
                    "",
                ]
            )

        parts.extend(
            [
                'module "resource_group" {',
                '  source = "./modules/resource-group"',
                "",
                "  name     = " + _s(self.rg_name),
                "  location = var.location",
                "  tags     = local.common_tags",
                "}",
                "",
                'module "networking" {',
                '  source = "./modules/networking"',
                "",
                "  name                = var.vnet_name",
                "  location            = var.location",
                "  resource_group_name = module.resource_group.name",
                "  tags                = local.common_tags",
                "  address_space       = var.vnet_address_space",
                "  subnets             = var.subnets",
                "  subnet_nsgs         = var.subnet_nsgs",
                "  nsg_rules           = var.nsg_rules",
                "  enable_nat_gateway  = var.enable_nat_gateway",
                "  nat_subnet_key      = var.nat_subnet_key",
                "  private_dns_zones   = var.private_dns_zones",
                "}",
                "",
            ]
        )

        diagnostic_targets: list[str] = []
        if self.has_key_vault:
            diagnostic_targets.append("key_vault = module.key_vault[0].id")
        if self.db_engine != "none":
            diagnostic_targets.append("database = module.database[0].server_id")

        if self.has_monitoring:
            target_block = "\n".join(f"      {line}" for line in diagnostic_targets) or ""
            parts.extend(
                [
                    'module "monitoring" {',
                    '  source = "./modules/monitoring"',
                    "",
                    '  workspace_name    = var.log_analytics_workspace_name',
                    "  location          = var.location",
                    "  resource_group_name = module.resource_group.name",
                    "  tags              = local.common_tags",
                    "  retention_days    = var.log_retention_days",
                    "  enable_app_insights = var.enable_app_insights",
                    "  app_insights_name = var.app_insights_name",
                    "",
                    "  diagnostic_targets = {",
                    target_block,
                    "  }",
                    "}",
                    "",
                ]
            )
            if self.db_engine != "none":
                # database module is defined below; Terraform resolves the dependency graph.
                pass

        if self.has_key_vault:
            parts.extend(
                [
                    'module "key_vault" {',
                    '  source = "./modules/key-vault"',
                    "",
                    "  count = 1",
                    "",
                    "  name                          = var.key_vault_name",
                    "  location                      = var.location",
                    "  resource_group_name           = module.resource_group.name",
                    "  tenant_id                     = data.azurerm_client_config.current.tenant_id",
                    "  tags                          = local.common_tags",
                    "  purge_protection_enabled      = var.environment == \"production\"",
                    "  public_network_access_enabled = !var.enable_private_endpoints",
                    "  enable_private_endpoint       = var.enable_private_endpoints",
                    "  subnet_id                     = var.enable_private_endpoints ? lookup(module.networking.subnet_ids, "
                    + _s(self._subnet_name("private endpoints") or "snet-private-endpoints")
                    + ", null) : null",
                    "  private_dns_zone_id           = lookup(module.networking.private_dns_zone_ids, "
                    + _s(catalog.PRIVATE_DNS_ZONES["key_vault"])
                    + ", null)",
                    "  secrets                       = var.key_vault_secrets",
                    "  role_assignments              = local.key_vault_role_assignments",
                    "}",
                    "",
                ]
            )

        if self.has_storage:
            parts.extend(
                [
                    'module "storage" {',
                    '  source = "./modules/storage"',
                    "",
                    "  count = 1",
                    "",
                    "  name                          = var.storage_account_name",
                    "  location                      = var.location",
                    "  resource_group_name           = module.resource_group.name",
                    "  tags                          = local.common_tags",
                    "  replication_type              = var.storage_replication_type",
                    "  public_network_access_enabled = !var.enable_private_endpoints",
                    "  containers                    = var.storage_containers",
                    "  enable_private_endpoint       = var.enable_private_endpoints",
                    "  subnet_id                     = var.enable_private_endpoints ? lookup(module.networking.subnet_ids, "
                    + _s(self._subnet_name("private endpoints") or "snet-private-endpoints")
                    + ", null) : null",
                    "  private_dns_zone_id           = lookup(module.networking.private_dns_zone_ids, "
                    + _s(catalog.PRIVATE_DNS_ZONES["storage"])
                    + ", null)",
                    "}",
                    "",
                ]
            )

        if self.db_engine != "none":
            db_subnet_purpose = "private endpoints" if self.db_engine == "mssql" else "database"
            db_subnet_name = self._subnet_name(db_subnet_purpose) or self._subnet_name("database") or "snet-data"
            dns_zone = catalog.PRIVATE_DNS_ZONES[self.db_engine]
            parts.extend(
                [
                    'module "database" {',
                    '  source = "./modules/database"',
                    "",
                    "  count = 1",
                    "",
                    "  engine                        = var.database_engine",
                    "  server_name                   = var.database_server_name",
                    "  database_name                 = var.database_name",
                    "  location                      = var.location",
                    "  resource_group_name           = module.resource_group.name",
                    "  tags                          = local.common_tags",
                    "  sku_name                      = var.database_sku",
                    "  max_size_gb                   = var.database_max_size_gb",
                    "  admin_login                   = var.database_admin_login",
                    "  admin_password                = var.database_admin_password",
                    "  zone_redundant                = var.database_zone_redundant",
                    "  backup_retention_days         = var.database_backup_retention_days",
                    "  public_network_access_enabled = var.database_engine == \"mssql\" ? false : !var.enable_private_endpoints",
                    "  enable_private_endpoint       = var.database_engine == \"mssql\" ? var.enable_private_endpoints : false",
                    "  subnet_id                     = var.enable_private_endpoints ? lookup(module.networking.subnet_ids, "
                    + _s(db_subnet_name)
                    + ", null) : null",
                    "  private_dns_zone_id           = lookup(module.networking.private_dns_zone_ids, "
                    + _s(dns_zone)
                    + ", null)",
                    "}",
                    "",
                ]
            )

        if self.compute_kind != "none":
            parts.extend(
                [
                    'module "compute" {',
                    '  source = "./modules/compute"',
                    "",
                    "  kind                        = var.compute_kind",
                    '  name_prefix                 = var.name_prefix',
                    "  location                    = var.location",
                    "  resource_group_name         = module.resource_group.name",
                    "  tags                        = local.common_tags",
                    "  app_subnet_id               = lookup(module.networking.subnet_ids, "
                    + _s(self._subnet_name("application") or "snet-app")
                    + ", null)",
                    "  workload_identity_id        = azurerm_user_assigned_identity.workload.id",
                    "  log_analytics_workspace_id  = module.monitoring.workspace_id",
                    "  app_service_sku             = var.app_service_sku",
                    "  vm_size                     = var.vm_size",
                    "  vm_count                    = var.vm_count",
                    "  os_type                     = var.os_type",
                    "  admin_username              = var.admin_username",
                    "  admin_ssh_public_key        = var.admin_ssh_public_key",
                    "  admin_password              = var.admin_password",
                    "  zone_redundant              = var.zone_redundant",
                    "  public_network_access_enabled = var.compute_kind == \"app_service\" ? !var.enable_private_endpoints : false",
                    "  enable_private_endpoint     = var.compute_kind == \"app_service\" ? var.enable_private_endpoints : false",
                    "  private_endpoint_subnet_id  = lookup(module.networking.subnet_ids, "
                    + _s(self._subnet_name("private endpoints") or "snet-private-endpoints")
                    + ", null)",
                    "  app_service_private_dns_zone_id = lookup(module.networking.private_dns_zone_ids, "
                    + _s(catalog.PRIVATE_DNS_ZONES["app_service"])
                    + ", null)",
                    "  key_vault_uri               = " + ("module.key_vault[0].uri" if self.has_key_vault else '""'),
                    "  app_insights_connection_string = " + ("module.monitoring[0].app_insights_connection_string" if False else ("module.monitoring.app_insights_connection_string" if self.has_monitoring else '""')),
                    "  app_name                    = " + _s(self.app_name),
                    "  service_plan_name           = " + _s(f"asp-{self.prefix}"[:60]),
                    "  aks_name                    = " + _s(f"aks-{self.prefix}"[:63]),
                    "  enable_app_gateway          = " + ("var.compute_kind == \"vm\"" if self.has_app_gateway else "false"),
                    "  app_gateway_name            = " + (_s((self._first("azurerm_application_gateway").name if self._first("azurerm_application_gateway") else f"agw-{self.prefix}"))[:80] if self.has_app_gateway else _s(f"agw-{self.prefix}")),
                    "  app_gateway_public_ip_name  = " + _s(f"pip-appgw-{self.prefix}"[:80]),
                    "  app_gateway_certificate_secret_id = " + ("var.app_gateway_certificate_secret_id" if self.has_app_gateway else '""'),
                    "  gateway_identity_id         = " + ("var.compute_kind == \"vm\" ? azurerm_user_assigned_identity.appgw[0].id : null" if self.has_app_gateway else "null"),
                    "  gateway_subnet_id           = lookup(module.networking.subnet_ids, "
                    + _s(self._subnet_name("application gateway") or "snet-gateway")
                    + ", null)",
                    "",
                    "  depends_on = [" + (", ".join(filter(None, ["module.key_vault" if self.has_key_vault else "", "module.monitoring" if self.has_monitoring else ""])) or "") + "]",
                    "}",
                    "",
                ]
            )

        security_needed = (self.has_storage and self.compute_kind != "none") or self.enable_lock
        if security_needed:
            parts.extend(
                [
                    'module "security" {',
                    '  source = "./modules/security"',
                    "",
                    "  resource_group_id   = module.resource_group.id",
                    "  enable_resource_lock = var.enable_resource_lock",
                    '  lock_name           = "lock-${var.name_prefix}"',
                    "  role_assignments    = local.security_role_assignments",
                    "}",
                    "",
                ]
            )
        else:
            # avoid an unused local warning by keeping the local referenced in outputs
            pass

        if not (self.has_storage and self.compute_kind != "none") and not self.enable_lock:
            parts.append("# No cross-service RBAC assignments or resource lock were required for this architecture.")

        return "\n".join(parts) + "\n"

    def _root_outputs(self) -> str:
        outputs = [
            'output "resource_group_name" {\n  description = "Resource group name."\n  value       = module.resource_group.name\n}',
            'output "resource_group_id" {\n  description = "Resource group ID."\n  value       = module.resource_group.id\n}',
            'output "vnet_id" {\n  description = "Virtual network ID."\n  value       = module.networking.vnet_id\n}',
            'output "subnet_ids" {\n  description = "Subnet IDs keyed by name."\n  value       = module.networking.subnet_ids\n}',
        ]
        if self.has_key_vault:
            outputs.append('output "key_vault_uri" {\n  description = "Key Vault URI."\n  value       = module.key_vault[0].uri\n}')
        if self.has_storage:
            outputs.append('output "storage_account_name" {\n  description = "Storage account name."\n  value       = module.storage[0].name\n}')
        if self.db_engine != "none":
            outputs.append('output "database_fqdn" {\n  description = "Database server FQDN."\n  value       = module.database[0].server_fqdn\n  sensitive   = true\n}')
        if self.has_monitoring:
            outputs.append('output "log_analytics_workspace_id" {\n  description = "Log Analytics workspace ID."\n  value       = module.monitoring.workspace_id\n}')
        if self.compute_kind != "none":
            outputs.append('output "application_url" {\n  description = "Application URL."\n  value       = module.compute.application_url\n}')
            outputs.append('output "vm_private_ips" {\n  description = "Application VM private IPs."\n  value       = module.compute.vm_private_ips\n}')
        return "\n\n".join(outputs) + "\n"

    # -- env files ----------------------------------------------------------
    def _env_files(self) -> dict[str, str]:
        backend = self.info.backend or {"mode": "local"}
        mode = backend.get("mode", "local")
        if mode == "azurerm":
            backend_block = '  backend "azurerm" {}'
            backend_hcl = (
                f'resource_group_name  = {_s(backend.get("resource_group", "rg-tfstate"))}\n'
                f'storage_account_name = {_s(backend.get("storage_account", "tfstate"))}\n'
                f'container_name       = {_s(backend.get("container", "tfstate"))}\n'
                f'key                  = {_s(str(backend.get("key_prefix", "terraformagent")) + "/" + self.env + ".tfstate")}\n'
                "use_azuread_auth     = true\n"
            )
            init_extra = " \\\n  -backend-config=backend.hcl"
        else:
            backend_block = '  backend "local" {\n    path = "terraform.tfstate"\n  }'
            backend_hcl = "# Local backend: state is stored in this directory.\n# For team use configure the azurerm backend in project settings.\n"
            init_extra = ""

        versions = f'''terraform {{
  required_version = ">= 1.6.0"

  required_providers {{
    azurerm = {{
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
    }}
  }}

{backend_block}
}}
'''

        pass_through: list[str] = [
            "  project_name = var.project_name",
            f"  environment  = {_s(self.env)}",
            "  location     = var.location",
            "  tags         = var.tags",
        ]
        if self.compute_kind != "none":
            pass_through.append("  admin_ssh_public_key = var.admin_ssh_public_key")
            if self.resources_by_type.get("azurerm_windows_virtual_machine"):
                pass_through.append("  admin_password       = var.admin_password")
        if self.db_engine != "none":
            pass_through.append("  database_admin_password = var.database_admin_password")
        if self.has_app_gateway:
            pass_through.append("  app_gateway_certificate_secret_id = var.app_gateway_certificate_secret_id")

        main_tf = (
            'module "stack" {\n'
            '  source = "../../"\n\n'
            + "\n".join(pass_through)
            + "\n}\n"
        )

        variable_blocks = [
            _var("project_name", "string", description="Project name.", default=self.info.project_name),
            _var("location", "string", description="Azure region.", default=self.region),
            _var("tags", "map(string)", description="Additional tags.", default=self.info.tags),
            _var(
                "subscription_id",
                "string",
                description="Target Azure subscription ID. Empty uses the authenticated context.",
                default=self.info.subscription_id or "",
            ),
        ]
        if self.compute_kind != "none":
            variable_blocks.append(
                _var("admin_ssh_public_key", "string", description="SSH public key for Linux VMs.", default="")
            )
            if self.resources_by_type.get("azurerm_windows_virtual_machine"):
                variable_blocks.append(
                    _var(
                        "admin_password",
                        "string",
                        description="Windows VM administrator password.",
                        default="",
                        sensitive=True,
                    )
                )
        if self.db_engine != "none":
            variable_blocks.append(
                _var(
                    "database_admin_password",
                    "string",
                    description="Database administrator password. Supply from a secret store.",
                    default="",
                    sensitive=True,
                )
            )
        if self.has_app_gateway:
            variable_blocks.append(
                _var(
                    "app_gateway_certificate_secret_id",
                    "string",
                    description="Key Vault secret ID with the TLS certificate.",
                    default="",
                )
            )

        provider_tf = '''provider "azurerm" {
  features {
    key_vault {
      purge_soft_delete_on_destroy    = false
      recover_soft_deleted_key_vaults = true
    }
    resource_group {
      prevent_deletion_if_contains_resources = false
    }
  }

  subscription_id = var.subscription_id != "" ? var.subscription_id : null
}
'''

        root_outputs = [
            ("resource_group_name", "Resource group name.", "module.stack.resource_group_name", False),
            ("vnet_id", "Virtual network ID.", "module.stack.vnet_id", False),
        ]
        if self.compute_kind != "none":
            root_outputs.append(("application_url", "Application URL.", "module.stack.application_url", False))
        if self.has_key_vault:
            root_outputs.append(("key_vault_uri", "Key Vault URI.", "module.stack.key_vault_uri", False))
        if self.db_engine != "none":
            root_outputs.append(("database_fqdn", "Database FQDN.", "module.stack.database_fqdn", True))

        outputs_tf = "\n\n".join(
            f'output "{name}" {{\n  description = {_s(desc)}\n  value       = {value}'
            + ("\n  sensitive   = true" if sensitive else "")
            + "\n}"
            for name, desc, value, sensitive in root_outputs
        )

        tfvars_lines = [
            f"project_name = {_s(self.info.project_name)}",
            f"location     = {_s(self.region)}",
            "",
            "tags = {",
            f'  owner = {_s("platform-team")}',
            "}",
            "",
            "# Never commit secret values. Supply them with TF_VAR_* environment variables",
            "# from your secret store or CI pipeline.",
        ]
        if self.compute_kind == "vm":
            tfvars_lines.append('# admin_ssh_public_key = "ssh-rsa AAAA..."')
        if self.resources_by_type.get("azurerm_windows_virtual_machine"):
            tfvars_lines.append('# admin_password = "<from-secret-store>"')
        if self.db_engine != "none":
            tfvars_lines.append('# database_admin_password = "<from-secret-store>"')
        if self.has_app_gateway:
            tfvars_lines.append('# app_gateway_certificate_secret_id = "https://<vault>.vault.azure.net/secrets/appgw-cert/<version>"')
        tfvars = "\n".join(tfvars_lines) + "\n"

        return {
            f"environments/{self.env}/versions.tf": versions,
            f"environments/{self.env}/providers.tf": provider_tf,
            f"environments/{self.env}/main.tf": main_tf,
            f"environments/{self.env}/variables.tf": "\n\n".join(variable_blocks) + "\n",
            f"environments/{self.env}/outputs.tf": outputs_tf + "\n",
            f"environments/{self.env}/terraform.tfvars": tfvars,
            f"environments/{self.env}/backend.hcl": backend_hcl,
            f"environments/{self.env}/README.md": self._env_readme(init_extra),
        }

    def _env_readme(self, init_extra: str) -> str:
        backend = self.info.backend or {"mode": "local"}
        backend_note = (
            "Remote Azure Blob backend configured in `backend.hcl`. State locking, versioning and\n"
            "soft delete must be enabled on the storage account."
            if backend.get("mode") == "azurerm"
            else "Local backend. State is stored in `terraform.tfstate` in this directory and is\n"
            "**not suitable for shared production use** — configure an Azure Blob backend first."
        )
        return f'''# Environment: {self.env}

{backend_note}

## Usage

```bash
cd environments/{self.env}
terraform init -input=false{init_extra}
terraform validate
terraform plan -out=approved.tfplan
# review the plan
terraform apply approved.tfplan
```

Set secrets through the environment, never in `terraform.tfvars` committed to git:

```bash
export TF_VAR_database_admin_password="..."
```

## Files

- `versions.tf` — Terraform/providers/backend.
- `providers.tf` — azurerm provider configuration.
- `main.tf` — calls the root stack module with environment values.
- `variables.tf` — environment inputs.
- `terraform.tfvars` — non-secret environment values.
- `backend.hcl` — remote state configuration.
'''

    # -- scripts ------------------------------------------------------------
    def _scripts(self) -> dict[str, str]:
        env = self.env
        backend_mode = (self.info.backend or {}).get("mode", "local")
        init_args = " -backend-config=backend.hcl" if backend_mode == "azurerm" else ""
        validate_sh = f'''#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${{BASH_SOURCE[0]}}")/.." && pwd)"

echo "==> terraform fmt -check -recursive"
cd "$ROOT"
terraform fmt -check -recursive .

echo "==> terraform init (backend disabled)"
cd "$ROOT/environments/{env}"
terraform init -backend=false -input=false

echo "==> terraform validate"
terraform validate

echo "Validation passed."
'''
        deploy_sh = f'''#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${{BASH_SOURCE[0]}}")/.." && pwd)"
cd "$ROOT/environments/{env}"

echo "==> terraform init"
terraform init -input=false{init_args}

echo "==> terraform plan"
terraform plan -input=false -out=approved.tfplan

cat <<'EOF'

Plan written to approved.tfplan.
Review it carefully, then apply the EXACT saved plan:

  terraform apply approved.tfplan

TerraformAgent deployments must use the reviewed plan artifact. Never run a
fresh plan during apply.
EOF
'''
        validate_ps1 = f'''$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

Write-Host "==> terraform fmt -check -recursive"
Push-Location $Root
terraform fmt -check -recursive .
Pop-Location

Write-Host "==> terraform init (backend disabled)"
Push-Location (Join-Path $Root "environments/{env}")
terraform init -backend=false -input=false

Write-Host "==> terraform validate"
terraform validate

Write-Host "Validation passed."
'''
        deploy_ps1 = f'''$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Push-Location (Join-Path $Root "environments/{env}")

Write-Host "==> terraform init"
terraform init -input=false{init_args}

Write-Host "==> terraform plan"
terraform plan -input=false -out=approved.tfplan

Write-Host ""
Write-Host "Plan written to approved.tfplan. Review it, then apply the exact plan:"
Write-Host "  terraform apply approved.tfplan"
Pop-Location
'''
        return {
            "scripts/validate.sh": validate_sh,
            "scripts/deploy.sh": deploy_sh,
            "scripts/validate.ps1": validate_ps1,
            "scripts/deploy.ps1": deploy_ps1,
        }

    # -- README & resource map ---------------------------------------------
    def _module_map(self) -> list[dict[str, str]]:
        module_for: dict[str, str] = {
            "azurerm_resource_group": "resource-group",
            "azurerm_virtual_network": "networking",
            "azurerm_subnet": "networking",
            "azurerm_network_security_group": "networking",
            "azurerm_nat_gateway": "networking",
            "azurerm_public_ip": "networking",
            "azurerm_private_dns_zone": "networking",
            "azurerm_private_endpoint": "networking",
            "azurerm_linux_virtual_machine": "compute",
            "azurerm_windows_virtual_machine": "compute",
            "azurerm_linux_web_app": "compute",
            "azurerm_service_plan": "compute",
            "azurerm_kubernetes_cluster": "compute",
            "azurerm_application_gateway": "compute",
            "azurerm_mssql_server": "database",
            "azurerm_mssql_database": "database",
            "azurerm_postgresql_flexible_server": "database",
            "azurerm_mysql_flexible_server": "database",
            "azurerm_storage_account": "storage",
            "azurerm_storage_container": "storage",
            "azurerm_key_vault": "key-vault",
            "azurerm_log_analytics_workspace": "monitoring",
            "azurerm_application_insights": "monitoring",
            "azurerm_monitor_diagnostic_setting": "monitoring",
            "azurerm_role_assignment": "security",
            "azurerm_management_lock": "security",
        }
        pe_module = {
            "pe-storage": "storage",
            "pe-key-vault": "key-vault",
            "pe-db-server": "database",
            "pe-app": "compute",
        }
        mapping: list[dict[str, str]] = []
        for resource in self.spec.resources:
            module = pe_module.get(resource.id) or module_for.get(resource.type, resource.module)
            mapping.append(
                {
                    "resource": resource.name,
                    "terraform_type": resource.type,
                    "module": f"modules/{module}",
                    "file": f"modules/{module}/main.tf",
                }
            )
        return mapping

    def _readme(self, module_map: list[dict[str, str]]) -> str:
        table = "\n".join(
            f"| {row['resource']} | `{row['terraform_type']}` | `{row['module']}` |" for row in module_map
        )
        modules = sorted({row["module"] for row in module_map})
        module_list = "\n".join(f"- `{m}`" for m in modules)
        backend = self.info.backend or {"mode": "local"}
        backend_note = (
            "This project uses the **local** backend by default. Configure an Azure Storage backend\n"
            "before shared or production use (project Settings → State backend)."
            if backend.get("mode") != "azurerm"
            else "This project uses an **Azure Blob** remote backend (`environments/*/backend.hcl`)."
        )
        return f'''# {self.info.project_name} — Terraform

Generated by TerraformAgent from architecture version. Do not edit generated
files without re-running validation; every manual change is versioned and
requires a new plan and approval.

## Architecture overview

{self.spec.summary}

- **Region:** `{self.region}`
- **Environment:** `{self.env}`
- **State backend:** {backend_note}
- **Terraform:** `>= 1.6.0`, azurerm `~> 4.0`

## Modules

{module_list}

## Resource to module mapping

| Resource | Terraform type | Module |
|---|---|---|
{table}

## Prerequisites

1. Terraform CLI 1.6+ (`terraform version`).
2. Azure CLI for local authentication, or the following environment variables for
   service principal / OIDC authentication:
   - `ARM_CLIENT_ID`, `ARM_TENANT_ID`, `ARM_SUBSCRIPTION_ID`
   - `ARM_CLIENT_SECRET` **or** `ARM_USE_OIDC=true` + `ARM_OIDC_TOKEN`
3. Permissions: `Contributor` on the target subscription plus `User Access Administrator`
   for role assignments, or a custom role covering the resource types above.
4. An SSH key pair for Linux VMs (public key supplied via `TF_VAR_admin_ssh_public_key`).
5. A TLS certificate in Key Vault for Application Gateway (`app_gateway_certificate_secret_id`).

## Azure authentication

```bash
az login
az account set --subscription "<subscription-id>"
```

or for a service principal:

```bash
export ARM_CLIENT_ID="..."
export ARM_CLIENT_SECRET="..."
export ARM_TENANT_ID="..."
export ARM_SUBSCRIPTION_ID="..."
```

TerraformAgent never stores these values; the execution worker injects them from
the configured secret store / managed identity.

## Configure

```bash
cd environments/{self.env}
cp terraform.tfvars.example terraform.tfvars   # if present
terraform init -input=false
```

Set secrets via environment variables (never commit them):

```bash
export TF_VAR_admin_ssh_public_key="$(cat ~/.ssh/id_ed25519.pub)"
export TF_VAR_database_admin_password="..."
export TF_VAR_app_gateway_certificate_secret_id="https://<vault>.vault.azure.net/secrets/appgw-cert/<version>"
```

## Validate

```bash
cd ../..
./scripts/validate.sh        # bash
./scripts/validate.ps1       # PowerShell
```

This runs `terraform fmt -check -recursive`, `terraform init -backend=false` and
`terraform validate`. TFLint, Checkov and policy checks are executed by the
TerraformAgent validation pipeline.

## Plan

```bash
cd environments/{self.env}
terraform plan -input=false -out=approved.tfplan
```

The TerraformAgent execution engine stores the binary plan, hashes it, and binds
the deployment approval to that hash.

## Apply

```bash
terraform apply approved.tfplan
```

Apply the **exact reviewed plan**. Do not run a fresh plan during apply.

## Destroy

```bash
cd environments/{self.env}
terraform plan -destroy -out=destroy.tfplan
terraform apply destroy.tfplan
```

Destroying stateful resources (databases, storage, Key Vault) is irreversible.
TerraformAgent never runs destroy automatically.

## State

{backend_note}
Enable blob versioning, soft delete, and a `CanNotDelete` lock on the state
storage account. State may contain sensitive values; never commit it.

## Troubleshooting

| Symptom | Resolution |
|---|---|
| `AADSTS...` during init/plan | Re-authenticate; verify tenant and subscription IDs |
| `Error acquiring the state lock` | Another run is active; wait or inspect the lock ID |
| `Insufficient permissions` | Grant the deployment identity the roles listed in Prerequisites |
| `The policy definition ... is not allowed` | Azure Policy blocks the resource; adjust SKU/region |
| `QuotaExceeded` | Request quota in the target subscription/region |
| `KeyVault certificate not found` | Verify `app_gateway_certificate_secret_id` and gateway RBAC |
| `Invalid CIDR / subnet overlap` | Adjust `vnet_address_space` and `subnets` |
| `StatusCode=409 ... already exists` | Import the resource or change the generated name |

## Notes

- No credentials or secrets are embedded in this project.
- Provider and module versions are pinned.
- Generated Terraform is not trusted by default: the platform validates it on
  every change before planning.
'''

    # -- module assembly ----------------------------------------------------
    def _module_files(self) -> dict[str, str]:
        files: dict[str, str] = {
            "modules/resource-group/main.tf": tcore.RESOURCE_GROUP_MAIN,
            "modules/resource-group/variables.tf": tcore.RESOURCE_GROUP_VARIABLES,
            "modules/resource-group/outputs.tf": tcore.RESOURCE_GROUP_OUTPUTS,
            "modules/resource-group/README.md": tcore.RESOURCE_GROUP_README,
            "modules/networking/main.tf": tcore.NETWORKING_MAIN,
            "modules/networking/variables.tf": tcore.NETWORKING_VARIABLES,
            "modules/networking/outputs.tf": tcore.NETWORKING_OUTPUTS,
            "modules/networking/README.md": tcore.NETWORKING_README,
            "modules/monitoring/main.tf": tcore.MONITORING_MAIN,
            "modules/monitoring/variables.tf": tcore.MONITORING_VARIABLES,
            "modules/monitoring/outputs.tf": tcore.MONITORING_OUTPUTS,
            "modules/monitoring/README.md": tcore.MONITORING_README,
        }
        if self.has_key_vault:
            files.update(
                {
                    "modules/key-vault/main.tf": tdata.KEY_VAULT_MAIN,
                    "modules/key-vault/variables.tf": tdata.KEY_VAULT_VARIABLES,
                    "modules/key-vault/outputs.tf": tdata.KEY_VAULT_OUTPUTS,
                    "modules/key-vault/README.md": tdata.KEY_VAULT_README,
                }
            )
        if self.has_storage:
            files.update(
                {
                    "modules/storage/main.tf": tdata.STORAGE_MAIN,
                    "modules/storage/variables.tf": tdata.STORAGE_VARIABLES,
                    "modules/storage/outputs.tf": tdata.STORAGE_OUTPUTS,
                    "modules/storage/README.md": tdata.STORAGE_README,
                }
            )
        if self.db_engine != "none":
            files.update(
                {
                    "modules/database/main.tf": tdata.DATABASE_MAIN,
                    "modules/database/variables.tf": tdata.DATABASE_VARIABLES,
                    "modules/database/outputs.tf": tdata.DATABASE_OUTPUTS,
                    "modules/database/README.md": tdata.DATABASE_README,
                }
            )
        if self.compute_kind != "none":
            files.update(
                {
                    "modules/compute/main.tf": tc.COMPUTE_MAIN,
                    "modules/compute/variables.tf": tc.COMPUTE_VARIABLES,
                    "modules/compute/outputs.tf": tc.COMPUTE_OUTPUTS,
                    "modules/compute/README.md": tc.COMPUTE_README,
                }
            )
        security_needed = (self.has_storage and self.compute_kind != "none") or self.enable_lock
        if security_needed:
            files.update(
                {
                    "modules/security/main.tf": tc.SECURITY_MAIN,
                    "modules/security/variables.tf": tc.SECURITY_VARIABLES,
                    "modules/security/outputs.tf": tc.SECURITY_OUTPUTS,
                    "modules/security/README.md": tc.SECURITY_README,
                }
            )
        return files

    def _tfvars_example(self) -> str:
        lines = [
            "# Copy to terraform.tfvars and adjust values.",
            "# Never put secrets in this file if it is committed to source control.",
            f"project_name = {_s(self.info.project_name)}",
            f"environment  = {_s(self.env)}",
            f"location     = {_s(self.region)}",
            "",
            "tags = {",
            '  owner       = "platform-team"',
            f'  cost_center = "unassigned"',
            "}",
            "",
        ]
        if self.compute_kind == "vm":
            lines.append("# SSH public key for Linux VMs.")
            lines.append('# admin_ssh_public_key = "ssh-ed25519 AAAA..."')
            lines.append("")
        if self.resources_by_type.get("azurerm_windows_virtual_machine"):
            lines.append("# Provide via TF_VAR_admin_password from a secret store.")
            lines.append("# admin_password = \"...\"")
            lines.append("")
        if self.db_engine != "none":
            lines.append("# Provide via TF_VAR_database_admin_password from a secret store.")
            lines.append("# database_admin_password = \"...\"")
            lines.append("")
        if self.has_app_gateway:
            lines.append("# PEM/PFX certificate stored in Key Vault (secret ID including version).")
            lines.append("# app_gateway_certificate_secret_id = \"https://<vault>.vault.azure.net/secrets/appgw-cert/<version>\"")
            lines.append("")
        return "\n".join(lines) + "\n"

    def build(self) -> GeneratedProject:
        module_map = self._module_map()
        files: dict[str, str] = {
            "README.md": self._readme(module_map),
            "versions.tf": '''terraform {
  required_version = ">= 1.6.0"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
    }
  }
}
''',
            "variables.tf": self._root_variables(),
            "locals.tf": self._root_locals(),
            "main.tf": self._root_main(),
            "outputs.tf": self._root_outputs(),
            "terraform.tfvars.example": self._tfvars_example(),
        }
        files.update(self._module_files())
        files.update(self._env_files())
        files.update(self._scripts())

        if self.has_app_gateway and not self._subnet_name("application gateway"):
            self.notes.append(
                "Application Gateway is present but the architecture has no gateway subnet; "
                "a /24 subnet with purpose 'application gateway' is required."
            )
        if self.compute_kind == "vm" and self.info.admin_ssh_public_key_hint:
            self.notes.append(
                "Linux VMs require an SSH public key: set TF_VAR_admin_ssh_public_key or "
                "environments/<env>/terraform.tfvars before planning."
            )
        if self.has_app_gateway:
            self.notes.append(
                "Application Gateway TLS requires a PFX certificate in Key Vault and its secret ID "
                "in app_gateway_certificate_secret_id."
            )
        if (self.info.backend or {}).get("mode") != "azurerm":
            self.notes.append(
                "Local Terraform state is configured. Use an Azure Blob backend for shared or production use."
            )
        if self.db_engine in ("postgres", "mysql"):
            self.notes.append(
                "PostgreSQL/MySQL flexible servers require database_admin_password from a secret store."
            )

        return GeneratedProject(
            files=files,
            module_map=module_map,
            notes=list(dict.fromkeys(self.notes)),
            environment=self.env,
        )
