"""Deterministic Terraform security policy engine (Checkov-equivalent baseline).

The model cannot disable critical rules: `can_disable=False` findings always fail
validation regardless of project settings.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import hcl2

from app.agents.schemas import PolicyFinding


@dataclass(frozen=True)
class Rule:
    id: str
    title: str
    severity: str
    message: str
    remediation: str
    can_disable: bool = True


RULES: dict[str, Rule] = {
    "TG001": Rule(
        "TG001",
        "Storage account allows public network access",
        "critical",
        "A storage account has public_network_access_enabled = true or leaves it at the insecure default.",
        "Set public_network_access_enabled = false and use a private endpoint.",
        can_disable=False,
    ),
    "TG002": Rule(
        "TG002",
        "Storage account allows public blob access",
        "high",
        "allow_nested_items_to_be_public is true, allowing anonymous blob access.",
        "Set allow_nested_items_to_be_public = false.",
        can_disable=False,
    ),
    "TG003": Rule(
        "TG003",
        "Database allows public network access",
        "critical",
        "A SQL/PostgreSQL/MySQL server is publicly reachable.",
        "Disable public network access and use a private endpoint or delegated subnet.",
        can_disable=False,
    ),
    "TG004": Rule(
        "TG004",
        "Key Vault allows public network access",
        "high",
        "Key Vault is reachable from the public internet.",
        "Set public_network_access_enabled = false and add a private endpoint.",
        can_disable=False,
    ),
    "TG005": Rule(
        "TG005",
        "Unrestricted inbound SSH or RDP",
        "critical",
        "An NSG rule allows SSH (22) or RDP (3389) from any address.",
        "Restrict the source to a management CIDR, VPN or Azure Bastion.",
        can_disable=False,
    ),
    "TG006": Rule(
        "TG006",
        "Overly permissive NSG rule",
        "high",
        "An NSG rule allows all inbound traffic from any source.",
        "Restrict protocol, ports and source prefixes to the minimum required.",
        can_disable=False,
    ),
    "TG007": Rule(
        "TG007",
        "Insecure TLS version",
        "high",
        "A service allows TLS below 1.2.",
        "Set minimum_tls_version to TLS1_2 (or 1.2).",
        can_disable=False,
    ),
    "TG008": Rule(
        "TG008",
        "HTTPS not enforced",
        "high",
        "An App Service or listener permits plain HTTP without redirect.",
        "Set https_only = true and redirect HTTP listeners to HTTPS.",
        can_disable=False,
    ),
    "TG009": Rule(
        "TG009",
        "Key Vault purge protection disabled",
        "medium",
        "Key Vault can be purged immediately after deletion.",
        "Set purge_protection_enabled = true.",
    ),
    "TG010": Rule(
        "TG010",
        "Missing diagnostic settings",
        "medium",
        "The configuration has no diagnostic settings forwarding telemetry to Log Analytics.",
        "Add azurerm_monitor_diagnostic_setting for each service.",
    ),
    "TG011": Rule(
        "TG011",
        "Hardcoded secret material",
        "critical",
        "A credential-like literal was found in the Terraform files.",
        "Remove the value and inject it from Key Vault / pipeline secrets.",
        can_disable=False,
    ),
    "TG012": Rule(
        "TG012",
        "Unpinned provider version",
        "medium",
        "The azurerm provider constraint is missing or too loose.",
        'Pin the provider with an exact or pessimistic constraint such as "~> 4.0".',
    ),
    "TG013": Rule(
        "TG013",
        "Storage shared key access enabled",
        "high",
        "Storage account keys are enabled, allowing key-based authentication.",
        "Set shared_access_key_enabled = false and use managed identity.",
        can_disable=False,
    ),
    "TG014": Rule(
        "TG014",
        "Missing private endpoint for private service",
        "medium",
        "A data-plane service has public access disabled but no private endpoint or delegated network integration.",
        "Add a private endpoint with a private DNS zone.",
    ),
    "TG015": Rule(
        "TG015",
        "Missing management lock in production",
        "low",
        "Production resources have no CanNotDelete lock.",
        "Add azurerm_management_lock with lock_level CanNotDelete.",
    ),
    "TG016": Rule(
        "TG016",
        "Unrestricted egress / missing network segmentation",
        "low",
        "No NAT gateway or route table is configured for controlled egress.",
        "Use a NAT gateway or central firewall for outbound traffic.",
    ),
}

NON_DISABLEABLE = {rule.id for rule in RULES.values() if not rule.can_disable}


@dataclass
class PolicyResult:
    findings: list[PolicyFinding] = field(default_factory=list)
    checked_files: int = 0
    parse_errors: list[str] = field(default_factory=list)

    @property
    def critical(self) -> list[PolicyFinding]:
        return [f for f in self.findings if f.severity == "critical"]

    @property
    def status(self) -> str:
        if any(f.severity in ("critical", "high") for f in self.findings):
            return "failed"
        return "passed"


_SECRET_PATTERNS = [
    re.compile(r'(?i)(password|passwd|pwd|client_secret|api_key|apikey|account_key)\s*=\s*"([^"$%{][^"]{6,})"'),
    re.compile(r"(?i)AccountKey=([A-Za-z0-9+/=]{20,})"),
    re.compile(r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"(?i)SharedAccessSignature=([A-Za-z0-9%+/=]{20,})"),
]


def scan_secrets(files: dict[str, str]) -> list[PolicyFinding]:
    findings: list[PolicyFinding] = []
    placeholders = ("<", ">", "replace", "changeme", "example", "your-", "xxxx", "${", "todo")
    key_vault_value = re.compile(r'(?i)^\s*value\s*=\s*"([^"$%{<][^"]{7,})"')
    for path, content in files.items():
        if not path.endswith((".tf", ".tfvars", ".hcl", ".yaml", ".yml", ".json")):
            continue
        is_key_vault = "key-vault" in path or "key_vault" in path
        patterns = [*_SECRET_PATTERNS]
        if is_key_vault:
            patterns.append(key_vault_value)
        for line_number, line in enumerate(content.splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith(("#", "//", "/*", "*")):
                continue
            for pattern in patterns:
                match = pattern.search(line)
                if not match:
                    continue
                matched = match.group(0)
                if any(marker in matched.lower() for marker in placeholders):
                    continue
                findings.append(
                    PolicyFinding(
                        rule_id="TG011",
                        title=RULES["TG011"].title,
                        severity="critical",
                        message=f"Possible hardcoded secret in {path}:{line_number}",
                        file=path,
                        line=line_number,
                        remediation=RULES["TG011"].remediation,
                    )
                )
                break
    return findings


def _normalize_resources(document: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    resources: list[tuple[str, str, dict[str, Any]]] = []
    raw = document.get("resource", [])
    if isinstance(raw, dict):
        raw = [raw]
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        for resource_type, instances in entry.items():
            if not isinstance(instances, dict):
                continue
            for name, config in instances.items():
                resources.append((resource_type, name, config if isinstance(config, dict) else {}))
    return resources


def _unquote(value: str) -> str:
    if len(value) >= 2 and value.startswith('"') and value.endswith('"'):
        try:
            import json

            return json.loads(value)
        except (ValueError, TypeError):
            return value[1:-1]
    return value


def _clean_hcl(value: Any) -> Any:
    """normalize python-hcl2's quoted keys/values into plain Python values."""
    if isinstance(value, str):
        return _unquote(value)
    if isinstance(value, list):
        return [_clean_hcl(item) for item in value]
    if isinstance(value, dict):
        return {_unquote(str(key)): _clean_hcl(item) for key, item in value.items()}
    return value


def _variable_defaults(document: dict[str, Any]) -> dict[str, Any]:
    defaults: dict[str, Any] = {}
    for entry in _as_list(document.get("variable", [])):
        if not isinstance(entry, dict):
            continue
        for name, config in entry.items():
            if isinstance(config, dict) and "default" in config:
                defaults[str(name)] = config["default"]
    return defaults


_VAR_REF = re.compile(r"^\$\{?var\.([A-Za-z0-9_]+)\}?$")


def _resolve(value: Any, defaults: dict[str, Any]) -> Any:
    if not isinstance(value, str):
        return value
    match = _VAR_REF.match(value.strip())
    if match:
        return defaults.get(match.group(1), _UNRESOLVED)
    return value


_UNRESOLVED = object()


def _truthy(value: Any, default: bool | None = None) -> bool | None:
    if value is _UNRESOLVED or value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("true", "1"):
            return True
        if lowered in ("false", "0"):
            return False
        return default
    return default


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _port_matches(ports: list[Any], target: int) -> bool:
    for port in ports:
        text = str(port)
        if text == "*" or text == str(target):
            return True
        if "-" in text:
            try:
                low, high = text.split("-", 1)
                if int(low) <= target <= int(high):
                    return True
            except ValueError:
                continue
    return False


def _source_is_any(prefixes: list[Any]) -> bool:
    return any(str(p).strip().lower() in ("*", "0.0.0.0/0", "::/0", "internet", "any") for p in prefixes)


def evaluate_policy(
    files: dict[str, str],
    *,
    environment: str = "dev",
    disabled_rules: list[str] | None = None,
) -> PolicyResult:
    disabled = set(disabled_rules or []) - NON_DISABLEABLE
    result = PolicyResult()

    documents: list[tuple[str, dict[str, Any]]] = []
    for path, content in files.items():
        if not path.endswith(".tf"):
            continue
        result.checked_files += 1
        try:
            documents.append((path, _clean_hcl(hcl2.loads(content))))
        except Exception as exc:  # noqa: BLE001 - parser errors are findings
            result.parse_errors.append(f"{path}: {exc}")
            result.findings.append(
                PolicyFinding(
                    rule_id="TG000",
                    title="Terraform parse error",
                    severity="high",
                    message=f"Could not parse {path}: {exc}",
                    file=path,
                    remediation="Fix HCL syntax and re-run validation.",
                )
            )

    defaults_by_directory: dict[str, dict[str, Any]] = {}
    for path, document in documents:
        directory = str(Path(path).parent)
        defaults_by_directory.setdefault(directory, {}).update(_variable_defaults(document))

    has_diagnostics = any(
        any(rt == "azurerm_monitor_diagnostic_setting" for rt, _, _ in _normalize_resources(doc))
        for _, doc in documents
    )
    has_lock = any(
        any(rt == "azurerm_management_lock" for rt, _, _ in _normalize_resources(doc))
        for _, doc in documents
    )
    provider_pinned = False

    def add(rule_id: str, *, file: str, resource: str | None = None, message: str | None = None) -> None:
        if rule_id in disabled:
            return
        rule = RULES[rule_id]
        result.findings.append(
            PolicyFinding(
                rule_id=rule.id,
                title=rule.title,
                severity=rule.severity,  # type: ignore[arg-type]
                message=message or rule.message,
                file=file,
                resource=resource,
                remediation=rule.remediation,
            )
        )

    for path, document in documents:
        defaults = defaults_by_directory.get(str(Path(path).parent), {})
        if "terraform" in document:
            blocks = _as_list(document["terraform"])
            for block in blocks:
                if not isinstance(block, dict):
                    continue
                required = block.get("required_providers")
                providers = _as_list(required)
                for provider_block in providers:
                    if not isinstance(provider_block, dict):
                        continue
                    azurerm = provider_block.get("azurerm")
                    candidates = _as_list(azurerm)
                    for candidate in candidates:
                        if isinstance(candidate, dict) and str(candidate.get("version", "")).strip():
                            version = str(candidate["version"])
                            if version not in ("latest", "*") and (
                                "~>" in version or "=" in version or version[0].isdigit()
                            ):
                                provider_pinned = True

        for resource_type, name, config in _normalize_resources(document):
            address = f"{resource_type}.{name}"
            if resource_type == "azurerm_storage_account":
                public = _truthy(_resolve(config.get("public_network_access_enabled"), defaults), True)
                if public is not False:
                    add("TG001", file=path, resource=address)
                if _truthy(_resolve(config.get("allow_nested_items_to_be_public"), defaults), True) is not False:
                    add("TG002", file=path, resource=address)
                if _truthy(_resolve(config.get("shared_access_key_enabled"), defaults), True) is not False:
                    add("TG013", file=path, resource=address)
                tls = str(_resolve(config.get("min_tls_version", "TLS1_0"), defaults))
                if tls not in ("TLS1_2", "TLS1_3"):
                    add("TG007", file=path, resource=address)

            if resource_type in (
                "azurerm_mssql_server",
                "azurerm_postgresql_flexible_server",
            ):
                if _truthy(_resolve(config.get("public_network_access_enabled"), defaults), True) is not False:
                    add("TG003", file=path, resource=address)
                tls = str(_resolve(config.get("minimum_tls_version", "1.0"), defaults))
                if resource_type == "azurerm_mssql_server" and tls not in ("1.2", "1.3"):
                    add("TG007", file=path, resource=address)

            if resource_type == "azurerm_key_vault":
                if _truthy(_resolve(config.get("public_network_access_enabled"), defaults), True) is not False:
                    add("TG004", file=path, resource=address)
                if _truthy(_resolve(config.get("purge_protection_enabled"), defaults), False) is not True and environment == "production":
                    add("TG009", file=path, resource=address)
                acls = config.get("network_acls")
                for acl in _as_list(acls):
                    if isinstance(acl, dict) and str(acl.get("default_action", "Allow")).lower() == "allow":
                        if _truthy(_resolve(config.get("public_network_access_enabled"), defaults), False) is not False:
                            add(
                                "TG004",
                                file=path,
                                resource=address,
                                message="Key Vault network ACLs default to Allow.",
                            )

            if resource_type == "azurerm_network_security_group":
                for rule in _as_list(config.get("security_rule")):
                    if not isinstance(rule, dict):
                        continue
                    if str(rule.get("direction", "Inbound")).lower() != "inbound":
                        continue
                    if str(rule.get("access", "Allow")).lower() != "allow":
                        continue
                    source = _as_list(rule.get("source_address_prefix")) + _as_list(
                        rule.get("source_address_prefixes")
                    )
                    ports = _as_list(rule.get("destination_port_range")) + _as_list(
                        rule.get("destination_port_ranges")
                    )
                    if not _source_is_any(source):
                        continue
                    if _port_matches(ports, 22) or _port_matches(ports, 3389):
                        add(
                            "TG005",
                            file=path,
                            resource=f"{address}.{rule.get('name', 'rule')}",
                        )
                    elif _port_matches(ports, 0) or any(str(p) == "*" for p in ports):
                        add(
                            "TG006",
                            file=path,
                            resource=f"{address}.{rule.get('name', 'rule')}",
                        )

            if resource_type == "azurerm_linux_web_app" and _truthy(
                _resolve(config.get("https_only"), defaults), False
            ) is not True:
                add("TG008", file=path, resource=address)
            if resource_type in ("azurerm_application_gateway",):
                listeners = _as_list(config.get("http_listener"))
                for listener in listeners:
                    if isinstance(listener, dict) and str(listener.get("protocol", "")).lower() == "http":
                        redirects = _as_list(config.get("redirect_configuration"))
                        if not redirects:
                            add("TG008", file=path, resource=address)

            if resource_type == "azurerm_monitor_diagnostic_setting":
                has_diagnostics = True
            if resource_type == "azurerm_management_lock":
                has_lock = True

    if not provider_pinned:
        add(
            "TG012",
            file="versions.tf",
            message="No pinned azurerm provider constraint found.",
        )
    if not has_diagnostics:
        add("TG010", file="modules/monitoring/main.tf")
    if environment == "production" and not has_lock:
        add("TG015", file="main.tf")

    result.findings.extend(scan_secrets(files))
    return result


def findings_to_dict(findings: list[PolicyFinding]) -> list[dict[str, Any]]:
    return [finding.model_dump(mode="json") for finding in findings]


def policy_summary(result: PolicyResult) -> dict[str, Any]:
    by_severity: dict[str, int] = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
    for finding in result.findings:
        by_severity[finding.severity] = by_severity.get(finding.severity, 0) + 1
    return {
        "status": result.status,
        "checked_files": result.checked_files,
        "findings": len(result.findings),
        "by_severity": by_severity,
        "parse_errors": result.parse_errors,
    }
