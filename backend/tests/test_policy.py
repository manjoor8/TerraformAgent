from __future__ import annotations

from app.terraform.policy import evaluate_policy, scan_secrets

VERSIONS = '''
terraform {
  required_version = ">= 1.6.0"
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
    }
  }
}
'''

DIAGNOSTICS = '''
resource "azurerm_monitor_diagnostic_setting" "this" {
  name                       = "diag"
  target_resource_id         = "/subscriptions/x/resourceGroups/rg/providers/Microsoft.KeyVault/vaults/kv"
  log_analytics_workspace_id = "/subscriptions/x/resourceGroups/rg/providers/Microsoft.OperationalInsights/workspaces/law"
  enabled_log {
    category_group = "allLogs"
  }
}
'''

LOCK = '''
resource "azurerm_management_lock" "this" {
  name       = "lock"
  scope      = "/subscriptions/x/resourceGroups/rg"
  lock_level = "CanNotDelete"
}
'''


def _secure_files() -> dict[str, str]:
    return {
        "versions.tf": VERSIONS,
        "modules/monitoring/main.tf": DIAGNOSTICS,
        "modules/security/main.tf": LOCK,
        "modules/storage/main.tf": '''
resource "azurerm_storage_account" "this" {
  name                            = "stsecure"
  resource_group_name             = "rg"
  location                        = "uaenorth"
  account_tier                    = "Standard"
  account_replication_type        = "LRS"
  min_tls_version                 = "TLS1_2"
  public_network_access_enabled   = false
  allow_nested_items_to_be_public = false
  shared_access_key_enabled       = false
}
''',
    }


def test_secure_configuration_passes() -> None:
    result = evaluate_policy(_secure_files(), environment="production")
    assert result.status == "passed", [finding.model_dump() for finding in result.findings]
    assert result.findings == []


def test_insecure_configuration_is_blocked() -> None:
    files = {
        "versions.tf": VERSIONS,
        "modules/storage/main.tf": '''
resource "azurerm_storage_account" "this" {
  name                          = "stinsecure"
  public_network_access_enabled = true
  allow_nested_items_to_be_public = true
  shared_access_key_enabled     = true
  min_tls_version               = "TLS1_0"
}
''',
        "modules/key-vault/main.tf": '''
resource "azurerm_key_vault" "this" {
  name                          = "kvinsecure"
  public_network_access_enabled = true
  purge_protection_enabled      = false
}
''',
        "modules/database/main.tf": '''
resource "azurerm_mssql_server" "this" {
  name                          = "sqlinsecure"
  public_network_access_enabled = true
  minimum_tls_version           = "1.0"
}
''',
        "modules/networking/main.tf": '''
resource "azurerm_network_security_group" "this" {
  name = "nsg"
  security_rule {
    name                       = "open-ssh"
    priority                   = 100
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_range     = "22"
    source_address_prefix      = "*"
    destination_address_prefix = "*"
  }
}
''',
        "environments/production/terraform.tfvars": '''
database_admin_password = "SuperSecretValue123!"
''',
    }
    result = evaluate_policy(files, environment="production")
    ids = {finding.rule_id for finding in result.findings}
    assert result.status == "failed"
    for expected in {"TG001", "TG003", "TG004", "TG005", "TG009", "TG010", "TG011", "TG013", "TG015"}:
        assert expected in ids, f"missing {expected}: {sorted(ids)}"
    critical = [finding for finding in result.findings if finding.severity == "critical"]
    assert critical, "expected critical findings"


def test_variable_defaults_are_resolved_across_module_files() -> None:
    files = {
        "versions.tf": VERSIONS,
        "modules/monitoring/main.tf": DIAGNOSTICS,
        "modules/security/main.tf": LOCK,
        "modules/storage/variables.tf": '''
variable "public_network_access_enabled" {
  type    = bool
  default = false
}
''',
        "modules/storage/main.tf": '''
resource "azurerm_storage_account" "this" {
  name                            = "stvarsafe"
  min_tls_version                 = "TLS1_2"
  public_network_access_enabled   = var.public_network_access_enabled
  allow_nested_items_to_be_public = false
  shared_access_key_enabled       = false
}
''',
    }
    result = evaluate_policy(files, environment="production")
    assert not [finding for finding in result.findings if finding.rule_id in ("TG001", "TG013")]


def test_secret_scan_ignores_comments_and_placeholders() -> None:
    files = {
        "environments/production/terraform.tfvars": '''
# database_admin_password = "real-secret-value"
database_admin_password = "<from-secret-store>"
api_key = "REPLACE_ME"
''',
        "modules/key-vault/main.tf": '''
resource "azurerm_key_vault_secret" "this" {
  value = "actual-hardcoded-secret"
}
''',
    }
    findings = scan_secrets(files)
    assert len(findings) == 1
    assert findings[0].rule_id == "TG011"
    assert findings[0].file == "modules/key-vault/main.tf"


def test_disabled_rules_respect_non_disableable_controls() -> None:
    files = _secure_files()
    files["modules/storage/main.tf"] = files["modules/storage/main.tf"].replace(
        "public_network_access_enabled   = false",
        "public_network_access_enabled   = true",
    )
    result = evaluate_policy(
        files,
        environment="production",
        disabled_rules=["TG001", "TG010", "TG015"],
    )
    # TG001 is critical and must not be disableable; TG010/TG015 are disableable.
    assert any(finding.rule_id == "TG001" for finding in result.findings)
    assert not any(finding.rule_id in ("TG010", "TG015") for finding in result.findings)
