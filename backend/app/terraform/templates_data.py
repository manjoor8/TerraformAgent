"""Static HCL module templates: storage, key vault, database."""
from __future__ import annotations

STORAGE_MAIN = '''resource "azurerm_storage_account" "this" {
  name                          = var.name
  resource_group_name           = var.resource_group_name
  location                      = var.location
  account_tier                  = var.account_tier
  account_replication_type      = var.replication_type
  min_tls_version               = "TLS1_2"
  public_network_access_enabled = var.public_network_access_enabled
  allow_nested_items_to_be_public = false
  shared_access_key_enabled     = false

  blob_properties {
    versioning_enabled = var.enable_versioning

    delete_retention_policy {
      days = var.soft_delete_retention_days
    }

    container_delete_retention_policy {
      days = var.soft_delete_retention_days
    }
  }

  network_rules {
    default_action = var.public_network_access_enabled ? "Allow" : "Deny"
    bypass         = ["AzureServices"]
  }

  tags = var.tags
}

resource "azurerm_storage_container" "this" {
  for_each = toset(var.containers)

  name                  = each.value
  storage_account_id    = azurerm_storage_account.this.id
  container_access_type = "private"
}

resource "azurerm_private_endpoint" "blob" {
  count = var.enable_private_endpoint ? 1 : 0

  name                = "pe-${var.name}-blob"
  location            = var.location
  resource_group_name = var.resource_group_name
  subnet_id           = var.subnet_id

  private_service_connection {
    name                           = "psc-${var.name}-blob"
    private_connection_resource_id = azurerm_storage_account.this.id
    subresource_names              = ["blob"]
    is_manual_connection           = false
  }

  private_dns_zone_group {
    name                 = "default"
    private_dns_zone_ids = [var.private_dns_zone_id]
  }

  tags = var.tags
}
'''

STORAGE_VARIABLES = '''variable "name" {
  description = "Globally unique storage account name (3-24 lowercase alphanumeric)."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9]{3,24}$", var.name))
    error_message = "Storage account names must be 3-24 lowercase alphanumeric characters."
  }
}

variable "resource_group_name" {
  description = "Resource group name."
  type        = string
}

variable "location" {
  description = "Azure region."
  type        = string
}

variable "tags" {
  description = "Tags applied to storage resources."
  type        = map(string)
  default     = {}
}

variable "account_tier" {
  description = "Storage account tier."
  type        = string
  default     = "Standard"
}

variable "replication_type" {
  description = "Storage replication type (LRS, ZRS, GRS...)."
  type        = string
  default     = "LRS"

  validation {
    condition     = contains(["LRS", "ZRS", "GRS", "RAGRS", "GZRS", "RAGZRS"], var.replication_type)
    error_message = "Unsupported replication type."
  }
}

variable "public_network_access_enabled" {
  description = "Allow public network access (disable when private endpoints are used)."
  type        = bool
  default     = false
}

variable "containers" {
  description = "Blob container names."
  type        = list(string)
  default     = []
}

variable "enable_versioning" {
  description = "Enable blob versioning."
  type        = bool
  default     = true
}

variable "soft_delete_retention_days" {
  description = "Blob soft-delete retention in days."
  type        = number
  default     = 7
}

variable "enable_private_endpoint" {
  description = "Create a private endpoint for blob storage."
  type        = bool
  default     = false
}

variable "subnet_id" {
  description = "Private endpoint subnet ID."
  type        = string
  default     = null
}

variable "private_dns_zone_id" {
  description = "Private DNS zone ID for blob storage."
  type        = string
  default     = null
}
'''

STORAGE_OUTPUTS = '''output "id" {
  description = "Storage account resource ID."
  value       = azurerm_storage_account.this.id
}

output "name" {
  description = "Storage account name."
  value       = azurerm_storage_account.this.name
}

output "primary_blob_endpoint" {
  description = "Primary blob endpoint."
  value       = azurerm_storage_account.this.primary_blob_endpoint
}

output "container_names" {
  description = "Created container names."
  value       = [for container in azurerm_storage_container.this : container.name]
}
'''

STORAGE_README = '''# storage

Creates a hardened StorageV2 account:

- Shared key access disabled (identity-only authentication).
- Public blob access disabled.
- TLS 1.2 minimum.
- Blob versioning and soft delete enabled.
- Optional private endpoint with private DNS.

## Inputs

| Name | Type | Default |
|---|---|---|
| `name` | `string` | required |
| `replication_type` | `string` | `LRS` |
| `public_network_access_enabled` | `bool` | `false` |
| `enable_private_endpoint` | `bool` | `false` |

## Outputs

`id`, `name`, `primary_blob_endpoint`, `container_names`.
'''

KEY_VAULT_MAIN = '''resource "azurerm_key_vault" "this" {
  name                          = var.name
  location                      = var.location
  resource_group_name           = var.resource_group_name
  tenant_id                     = var.tenant_id
  sku_name                      = var.sku_name
  rbac_authorization_enabled    = true
  purge_protection_enabled      = var.purge_protection_enabled
  soft_delete_retention_days    = var.soft_delete_retention_days
  public_network_access_enabled = var.public_network_access_enabled

  network_acls {
    default_action = var.public_network_access_enabled ? "Allow" : "Deny"
    bypass         = "AzureServices"
  }

  tags = var.tags
}

resource "azurerm_key_vault_secret" "this" {
  for_each = var.secrets

  name         = each.key
  value        = each.value
  content_type = "text/plain"
  key_vault_id = azurerm_key_vault.this.id

  tags = var.tags
}

resource "azurerm_role_assignment" "this" {
  for_each = var.role_assignments

  scope                = azurerm_key_vault.this.id
  role_definition_name = each.value.role_definition_name
  principal_id         = each.value.principal_id
}

resource "azurerm_private_endpoint" "this" {
  count = var.enable_private_endpoint ? 1 : 0

  name                = "pe-${var.name}"
  location            = var.location
  resource_group_name = var.resource_group_name
  subnet_id           = var.subnet_id

  private_service_connection {
    name                           = "psc-${var.name}"
    private_connection_resource_id = azurerm_key_vault.this.id
    subresource_names              = ["vault"]
    is_manual_connection           = false
  }

  private_dns_zone_group {
    name                 = "default"
    private_dns_zone_ids = [var.private_dns_zone_id]
  }

  tags = var.tags
}
'''

KEY_VAULT_VARIABLES = '''variable "name" {
  description = "Key Vault name (3-24 alphanumeric and hyphens)."
  type        = string

  validation {
    condition     = can(regex("^[a-zA-Z][a-zA-Z0-9-]{2,22}[a-zA-Z0-9]$", var.name))
    error_message = "Key Vault names must be 3-24 characters, start with a letter and end alphanumeric."
  }
}

variable "resource_group_name" {
  description = "Resource group name."
  type        = string
}

variable "location" {
  description = "Azure region."
  type        = string
}

variable "tenant_id" {
  description = "Azure AD tenant ID."
  type        = string
}

variable "tags" {
  description = "Tags applied to Key Vault resources."
  type        = map(string)
  default     = {}
}

variable "sku_name" {
  description = "Key Vault SKU."
  type        = string
  default     = "standard"

  validation {
    condition     = contains(["standard", "premium"], var.sku_name)
    error_message = "Key Vault SKU must be standard or premium."
  }
}

variable "purge_protection_enabled" {
  description = "Enable purge protection (recommended for production)."
  type        = bool
  default     = true
}

variable "soft_delete_retention_days" {
  description = "Soft delete retention in days."
  type        = number
  default     = 90
}

variable "public_network_access_enabled" {
  description = "Allow public network access."
  type        = bool
  default     = false
}

variable "enable_private_endpoint" {
  description = "Create a private endpoint for Key Vault."
  type        = bool
  default     = false
}

variable "subnet_id" {
  description = "Private endpoint subnet ID."
  type        = string
  default     = null
}

variable "private_dns_zone_id" {
  description = "Private DNS zone ID for Key Vault."
  type        = string
  default     = null
}

variable "secrets" {
  description = "Secrets to create in the vault. Supply values from a secret store, never source control. Keys must be non-sensitive; values are marked sensitive by the provider."
  type        = map(string)
  default     = {}
}

variable "role_assignments" {
  description = "Key Vault RBAC assignments keyed by logical name."
  type = map(object({
    principal_id         = string
    role_definition_name = string
  }))
  default = {}
}
'''

KEY_VAULT_OUTPUTS = '''output "id" {
  description = "Key Vault resource ID."
  value       = azurerm_key_vault.this.id
}

output "name" {
  description = "Key Vault name."
  value       = azurerm_key_vault.this.name
}

output "uri" {
  description = "Key Vault DNS URI."
  value       = azurerm_key_vault.this.vault_uri
}

output "secret_ids" {
  description = "Secret IDs keyed by secret name."
  value       = { for key, secret in azurerm_key_vault_secret.this : key => secret.versionless_id }
}
'''

KEY_VAULT_README = '''# key-vault

Creates a Key Vault with RBAC authorization, purge protection, private access,
optional secrets and least-privilege role assignments for managed identities.

## Inputs

| Name | Type | Default |
|---|---|---|
| `name` | `string` | required |
| `sku_name` | `string` | `standard` |
| `purge_protection_enabled` | `bool` | `true` |
| `public_network_access_enabled` | `bool` | `false` |
| `enable_private_endpoint` | `bool` | `false` |
| `secrets` | `map(string)` | `{}` |
| `role_assignments` | `map(object)` | `{}` |

## Outputs

`id`, `name`, `uri`, `secret_ids`.
'''

DATABASE_MAIN = '''resource "azurerm_mssql_server" "this" {
  count = var.engine == "mssql" ? 1 : 0

  name                          = var.server_name
  resource_group_name           = var.resource_group_name
  location                      = var.location
  version                       = "12.0"
  minimum_tls_version           = "1.2"
  public_network_access_enabled = var.public_network_access_enabled
  administrator_login           = var.admin_login
  administrator_login_password  = var.admin_password

  tags = var.tags
}

resource "azurerm_mssql_database" "this" {
  count = var.engine == "mssql" ? 1 : 0

  name           = var.database_name
  server_id      = azurerm_mssql_server.this[0].id
  sku_name       = var.sku_name
  max_size_gb    = var.max_size_gb
  zone_redundant = var.zone_redundant

  short_term_retention_policy {
    retention_days = var.backup_retention_days
  }

  tags = var.tags
}

resource "azurerm_private_endpoint" "sql" {
  count = var.engine == "mssql" && var.enable_private_endpoint ? 1 : 0

  name                = "pe-${var.server_name}"
  location            = var.location
  resource_group_name = var.resource_group_name
  subnet_id           = var.subnet_id

  private_service_connection {
    name                           = "psc-${var.server_name}"
    private_connection_resource_id = azurerm_mssql_server.this[0].id
    subresource_names              = ["sqlServer"]
    is_manual_connection           = false
  }

  private_dns_zone_group {
    name                 = "default"
    private_dns_zone_ids = [var.private_dns_zone_id]
  }

  tags = var.tags
}

resource "azurerm_postgresql_flexible_server" "this" {
  count = var.engine == "postgres" ? 1 : 0

  name                          = var.server_name
  resource_group_name           = var.resource_group_name
  location                      = var.location
  version                       = var.postgres_version
  sku_name                      = var.sku_name
  storage_mb                    = var.storage_mb
  backup_retention_days         = var.backup_retention_days
  geo_redundant_backup_enabled  = var.geo_redundant_backup
  public_network_access_enabled = var.public_network_access_enabled
  delegated_subnet_id           = var.subnet_id
  private_dns_zone_id           = var.private_dns_zone_id
  administrator_login           = var.admin_login
  administrator_password        = var.admin_password

  high_availability {
    mode = var.zone_redundant ? "ZoneRedundant" : "Disabled"
  }

  tags = var.tags

  lifecycle {
    ignore_changes = [zone]
  }
}

resource "azurerm_postgresql_flexible_server_database" "this" {
  count = var.engine == "postgres" ? 1 : 0

  name      = var.database_name
  server_id = azurerm_postgresql_flexible_server.this[0].id
  charset   = "UTF8"
  collation = "en_US.utf8"
}

resource "azurerm_mysql_flexible_server" "this" {
  count = var.engine == "mysql" ? 1 : 0

  name                          = var.server_name
  resource_group_name           = var.resource_group_name
  location                      = var.location
  version                       = var.mysql_version
  sku_name                      = var.sku_name
  backup_retention_days         = var.backup_retention_days
  geo_redundant_backup_enabled  = var.geo_redundant_backup
  delegated_subnet_id           = var.subnet_id
  private_dns_zone_id           = var.private_dns_zone_id
  administrator_login           = var.admin_login
  administrator_password        = var.admin_password

  storage {
    size_gb = var.max_size_gb
  }

  high_availability {
    mode = var.zone_redundant ? "ZoneRedundant" : "Disabled"
  }

  tags = var.tags
}

resource "azurerm_mysql_flexible_database" "this" {
  count = var.engine == "mysql" ? 1 : 0

  name                = var.database_name
  resource_group_name = var.resource_group_name
  server_name         = azurerm_mysql_flexible_server.this[0].name
  charset             = "utf8mb4"
  collation           = "utf8mb4_unicode_ci"
}
'''

DATABASE_VARIABLES = '''variable "engine" {
  description = "Database engine: mssql, postgres or mysql."
  type        = string

  validation {
    condition     = contains(["mssql", "postgres", "mysql"], var.engine)
    error_message = "Engine must be mssql, postgres or mysql."
  }
}

variable "server_name" {
  description = "Database server name."
  type        = string
}

variable "database_name" {
  description = "Database name."
  type        = string
}

variable "resource_group_name" {
  description = "Resource group name."
  type        = string
}

variable "location" {
  description = "Azure region."
  type        = string
}

variable "tags" {
  description = "Tags applied to database resources."
  type        = map(string)
  default     = {}
}

variable "sku_name" {
  description = "Database SKU (for example GP_Gen5_2 or GP_Standard_D2ds_v5)."
  type        = string
}

variable "max_size_gb" {
  description = "Maximum database size in GB."
  type        = number
  default     = 32
}

variable "postgres_version" {
  description = "PostgreSQL major version."
  type        = string
  default     = "16"
}

variable "mysql_version" {
  description = "MySQL major version."
  type        = string
  default     = "8.0.21"
}

variable "storage_mb" {
  description = "PostgreSQL storage in MB."
  type        = number
  default     = 32768
}

variable "admin_login" {
  description = "Administrator login (Entra admin for mssql, local admin for postgres/mysql)."
  type        = string
}

variable "admin_password" {
  description = "Administrator password. Supply from a secret store, never source control."
  type        = string
  default     = ""
  sensitive   = true
}

variable "zone_redundant" {
  description = "Enable zone redundancy / high availability."
  type        = bool
  default     = false
}

variable "backup_retention_days" {
  description = "Backup retention in days."
  type        = number
  default     = 7
}

variable "geo_redundant_backup" {
  description = "Enable geo-redundant backups."
  type        = bool
  default     = false
}

variable "public_network_access_enabled" {
  description = "Allow public network access."
  type        = bool
  default     = false
}

variable "enable_private_endpoint" {
  description = "Create a private endpoint (mssql only)."
  type        = bool
  default     = false
}

variable "subnet_id" {
  description = "Private endpoint subnet (mssql) or delegated subnet (postgres/mysql)."
  type        = string
  default     = null
}

variable "private_dns_zone_id" {
  description = "Private DNS zone ID for the database."
  type        = string
  default     = null
}
'''

DATABASE_OUTPUTS = '''output "server_id" {
  description = "Database server resource ID."
  value = (
    var.engine == "mssql"
    ? azurerm_mssql_server.this[0].id
    : (var.engine == "postgres" ? azurerm_postgresql_flexible_server.this[0].id : azurerm_mysql_flexible_server.this[0].id)
  )
}

output "server_fqdn" {
  description = "Database server FQDN."
  value = (
    var.engine == "mssql"
    ? azurerm_mssql_server.this[0].fully_qualified_domain_name
    : (var.engine == "postgres" ? azurerm_postgresql_flexible_server.this[0].fqdn : azurerm_mysql_flexible_server.this[0].fqdn)
  )
}

output "database_name" {
  description = "Application database name."
  value = (
    var.engine == "mssql"
    ? azurerm_mssql_database.this[0].name
    : (var.engine == "postgres" ? azurerm_postgresql_flexible_server_database.this[0].name : azurerm_mysql_flexible_database.this[0].name)
  )
}
'''

DATABASE_README = '''# database

Deploys a managed relational database with TLS 1.2, backup retention and private
connectivity.

Supported engines:

| Engine | Server resource | Private connectivity |
|---|---|---|
| `mssql` | `azurerm_mssql_server` | Private endpoint + private DNS |
| `postgres` | `azurerm_postgresql_flexible_server` | Delegated subnet + private DNS |
| `mysql` | `azurerm_mysql_flexible_server` | Delegated subnet + private DNS |

Azure SQL uses Entra-only authentication, so no SQL password exists. PostgreSQL
and MySQL receive their administrator password from a secret store via
`admin_password`.

## Inputs

| Name | Type | Default |
|---|---|---|
| `engine` | `string` | required |
| `server_name` | `string` | required |
| `sku_name` | `string` | required |
| `zone_redundant` | `bool` | `false` |
| `public_network_access_enabled` | `bool` | `false` |

## Outputs

`server_id`, `server_fqdn`, `database_name`.
'''
