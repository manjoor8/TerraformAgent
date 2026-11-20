"""Static HCL module templates: compute (VM/App Service/AKS + App Gateway), security."""
from __future__ import annotations

COMPUTE_MAIN = '''resource "azurerm_service_plan" "this" {
  count = var.kind == "app_service" ? 1 : 0

  name                = var.service_plan_name
  location            = var.location
  resource_group_name = var.resource_group_name
  os_type             = "Linux"
  sku_name            = var.app_service_sku

  tags = var.tags
}

resource "azurerm_linux_web_app" "this" {
  count = var.kind == "app_service" ? 1 : 0

  name                          = var.app_name
  location                      = var.location
  resource_group_name           = var.resource_group_name
  service_plan_id               = azurerm_service_plan.this[0].id
  https_only                    = true
  public_network_access_enabled = var.public_network_access_enabled

  identity {
    type         = "UserAssigned"
    identity_ids = [var.workload_identity_id]
  }

  site_config {
    always_on                         = true
    minimum_tls_version               = "1.2"
    ftps_state                        = "Disabled"
    http2_enabled                     = true
    health_check_path                 = "/health"
    health_check_eviction_time_in_min = 5
  }

  app_settings = merge(
    var.app_settings,
    {
      APPLICATIONINSIGHTS_CONNECTION_STRING = var.app_insights_connection_string
      KEY_VAULT_URI                         = var.key_vault_uri
    }
  )

  tags = var.tags
}

resource "azurerm_monitor_diagnostic_setting" "app" {
  count = var.kind == "app_service" ? 1 : 0

  name                       = "diag-${var.app_name}"
  target_resource_id         = azurerm_linux_web_app.this[0].id
  log_analytics_workspace_id = var.log_analytics_workspace_id

  enabled_log {
    category_group = "allLogs"
  }

  enabled_metric {
    category = "AllMetrics"
  }
}

resource "azurerm_private_endpoint" "app" {
  count = var.kind == "app_service" && var.enable_private_endpoint ? 1 : 0

  name                = "pe-${var.app_name}"
  location            = var.location
  resource_group_name = var.resource_group_name
  subnet_id           = var.private_endpoint_subnet_id

  private_service_connection {
    name                           = "psc-${var.app_name}"
    private_connection_resource_id = azurerm_linux_web_app.this[0].id
    subresource_names              = ["sites"]
    is_manual_connection           = false
  }

  private_dns_zone_group {
    name                 = "default"
    private_dns_zone_ids = [var.app_service_private_dns_zone_id]
  }

  tags = var.tags
}

resource "azurerm_network_interface" "this" {
  count = var.kind == "vm" ? var.vm_count : 0

  name                = "${var.name_prefix}-nic-${count.index + 1}"
  location            = var.location
  resource_group_name = var.resource_group_name

  ip_configuration {
    name                          = "internal"
    subnet_id                     = var.app_subnet_id
    private_ip_address_allocation = "Dynamic"
  }

  tags = var.tags
}

resource "azurerm_linux_virtual_machine" "this" {
  count = var.kind == "vm" && var.os_type == "linux" ? var.vm_count : 0

  name                  = "${var.name_prefix}-vm-${count.index + 1}"
  location              = var.location
  resource_group_name   = var.resource_group_name
  size                  = var.vm_size
  admin_username        = var.admin_username
  network_interface_ids = [azurerm_network_interface.this[count.index].id]
  zone                  = var.zone_redundant ? tostring((count.index % 3) + 1) : null

  admin_ssh_key {
    username   = var.admin_username
    public_key = var.admin_ssh_public_key
  }

  disable_password_authentication = true

  identity {
    type         = "UserAssigned"
    identity_ids = [var.workload_identity_id]
  }

  os_disk {
    caching              = "ReadWrite"
    storage_account_type = "Premium_LRS"
  }

  source_image_reference {
    publisher = "Canonical"
    offer     = "ubuntu-24_04-lts"
    sku       = "server"
    version   = "latest"
  }

  tags = var.tags
}

resource "azurerm_windows_virtual_machine" "this" {
  count = var.kind == "vm" && var.os_type == "windows" ? var.vm_count : 0

  name                  = "${var.name_prefix}-vm-${count.index + 1}"
  location              = var.location
  resource_group_name   = var.resource_group_name
  size                  = var.vm_size
  admin_username        = var.admin_username
  admin_password        = var.admin_password
  network_interface_ids = [azurerm_network_interface.this[count.index].id]
  zone                  = var.zone_redundant ? tostring((count.index % 3) + 1) : null

  identity {
    type         = "UserAssigned"
    identity_ids = [var.workload_identity_id]
  }

  os_disk {
    caching              = "ReadWrite"
    storage_account_type = "Premium_LRS"
  }

  source_image_reference {
    publisher = "MicrosoftWindowsServer"
    offer     = "WindowsServer"
    sku       = "2022-datacenter-azure-edition"
    version   = "latest"
  }

  tags = var.tags
}

resource "azurerm_kubernetes_cluster" "this" {
  count = var.kind == "aks" ? 1 : 0

  name                       = var.aks_name
  location                   = var.location
  resource_group_name        = var.resource_group_name
  dns_prefix                 = var.name_prefix
  private_cluster_enabled    = var.enable_private_endpoint

  identity {
    type         = "UserAssigned"
    identity_ids = [var.workload_identity_id]
  }

  default_node_pool {
    name           = "default"
    node_count     = var.vm_count
    vm_size        = var.vm_size
    vnet_subnet_id = var.app_subnet_id
    zones          = var.zone_redundant ? ["1", "2", "3"] : null
  }

  network_profile {
    network_plugin    = "azure"
    load_balancer_sku = "standard"
  }

  tags = var.tags
}

resource "azurerm_public_ip" "appgw" {
  count = var.enable_app_gateway ? 1 : 0

  name                = var.app_gateway_public_ip_name
  location            = var.location
  resource_group_name = var.resource_group_name
  allocation_method   = "Static"
  sku                 = "Standard"
  zones               = var.zone_redundant ? ["1", "2", "3"] : null

  tags = var.tags
}

resource "azurerm_web_application_firewall_policy" "this" {
  count = var.enable_app_gateway ? 1 : 0

  name                = "${var.app_gateway_name}-waf"
  resource_group_name = var.resource_group_name
  location            = var.location

  policy_settings {
    enabled                     = true
    mode                        = "Prevention"
    request_body_check          = true
    max_request_body_size_in_kb = 128
    file_upload_limit_in_mb     = 100
  }

  managed_rules {
    managed_rule_set {
      type    = "OWASP"
      version = "3.2"
    }
  }

  tags = var.tags
}

resource "azurerm_application_gateway" "this" {
  count = var.enable_app_gateway ? 1 : 0

  name                = var.app_gateway_name
  location            = var.location
  resource_group_name = var.resource_group_name
  firewall_policy_id  = azurerm_web_application_firewall_policy.this[0].id
  http2_enabled       = true
  zones               = var.zone_redundant ? ["1", "2", "3"] : null

  identity {
    type         = "UserAssigned"
    identity_ids = [var.gateway_identity_id]
  }

  sku {
    name = "WAF_v2"
    tier = "WAF_v2"
  }

  autoscale_configuration {
    min_capacity = 2
    max_capacity = 10
  }

  gateway_ip_configuration {
    name      = "gateway-ip"
    subnet_id = var.gateway_subnet_id
  }

  frontend_ip_configuration {
    name                 = "public-frontend"
    public_ip_address_id = azurerm_public_ip.appgw[0].id
  }

  frontend_port {
    name = "http"
    port = 80
  }

  frontend_port {
    name = "https"
    port = 443
  }

  ssl_certificate {
    name                = "appgw-cert"
    key_vault_secret_id = var.app_gateway_certificate_secret_id
  }

  ssl_policy {
    policy_type = "Predefined"
    policy_name = "AppGwSslPolicy20220101"
  }

  backend_address_pool {
    name         = "application-backend"
    ip_addresses = [for nic in azurerm_network_interface.this : nic.private_ip_address]
  }

  probe {
    name                                      = "https-probe"
    protocol                                  = "Http"
    path                                      = "/health"
    interval                                  = 30
    timeout                                   = 30
    unhealthy_threshold                       = 3
    pick_host_name_from_backend_http_settings = true

    match {
      status_code = ["200-399"]
    }
  }

  backend_http_settings {
    name                  = "application-http"
    cookie_based_affinity = "Disabled"
    path                  = "/"
    port                  = 80
    protocol              = "Http"
    request_timeout       = 60
    probe_name            = "https-probe"
  }

  http_listener {
    name                           = "https-listener"
    frontend_ip_configuration_name = "public-frontend"
    frontend_port_name             = "https"
    protocol                       = "Https"
    ssl_certificate_name           = "appgw-cert"
  }

  http_listener {
    name                           = "http-listener"
    frontend_ip_configuration_name = "public-frontend"
    frontend_port_name             = "http"
    protocol                       = "Http"
  }

  redirect_configuration {
    name                 = "http-to-https"
    redirect_type        = "Permanent"
    target_listener_name = "https-listener"
    include_path         = true
    include_query_string = true
  }

  request_routing_rule {
    name                        = "https-routing"
    priority                    = 100
    rule_type                   = "Basic"
    http_listener_name          = "https-listener"
    backend_address_pool_name   = "application-backend"
    backend_http_settings_name  = "application-http"
  }

  request_routing_rule {
    name                        = "http-redirect"
    priority                    = 200
    rule_type                   = "Basic"
    http_listener_name          = "http-listener"
    redirect_configuration_name = "http-to-https"
  }

  tags = var.tags
}
'''

COMPUTE_VARIABLES = '''variable "kind" {
  description = "Compute platform: vm, app_service or aks."
  type        = string

  validation {
    condition     = contains(["vm", "app_service", "aks"], var.kind)
    error_message = "kind must be vm, app_service or aks."
  }
}

variable "name_prefix" {
  description = "Naming prefix for compute resources."
  type        = string
}

variable "app_name" {
  description = "App Service name."
  type        = string
  default     = ""
}

variable "service_plan_name" {
  description = "App Service plan name."
  type        = string
  default     = ""
}

variable "aks_name" {
  description = "AKS cluster name."
  type        = string
  default     = ""
}

variable "location" {
  description = "Azure region."
  type        = string
}

variable "resource_group_name" {
  description = "Resource group name."
  type        = string
}

variable "tags" {
  description = "Tags applied to compute resources."
  type        = map(string)
  default     = {}
}

variable "app_subnet_id" {
  description = "Application subnet ID."
  type        = string
}

variable "gateway_subnet_id" {
  description = "Application Gateway subnet ID."
  type        = string
  default     = null
}

variable "private_endpoint_subnet_id" {
  description = "Private endpoint subnet ID."
  type        = string
  default     = null
}

variable "app_service_private_dns_zone_id" {
  description = "Private DNS zone ID for App Service."
  type        = string
  default     = null
}

variable "workload_identity_id" {
  description = "User-assigned managed identity for the workload."
  type        = string
}

variable "gateway_identity_id" {
  description = "User-assigned managed identity for Application Gateway."
  type        = string
  default     = null
}

variable "log_analytics_workspace_id" {
  description = "Log Analytics workspace for diagnostics."
  type        = string
}

variable "app_insights_connection_string" {
  description = "Application Insights connection string."
  type        = string
  default     = ""
}

variable "key_vault_uri" {
  description = "Key Vault URI used by the application at runtime."
  type        = string
  default     = ""
}

variable "app_settings" {
  description = "Additional App Service application settings."
  type        = map(string)
  default     = {}
}

variable "app_service_sku" {
  description = "App Service plan SKU."
  type        = string
  default     = "P1v3"
}

variable "vm_size" {
  description = "Virtual machine size."
  type        = string
  default     = "Standard_D2s_v5"
}

variable "vm_count" {
  description = "Number of virtual machines or AKS nodes."
  type        = number
  default     = 2

  validation {
    condition     = var.vm_count >= 1 && var.vm_count <= 20
    error_message = "vm_count must be between 1 and 20."
  }
}

variable "os_type" {
  description = "VM operating system: linux or windows."
  type        = string
  default     = "linux"

  validation {
    condition     = contains(["linux", "windows"], var.os_type)
    error_message = "os_type must be linux or windows."
  }
}

variable "admin_username" {
  description = "VM administrator username."
  type        = string
  default     = "azureadmin"
}

variable "admin_ssh_public_key" {
  description = "SSH public key for Linux VMs."
  type        = string
  default     = ""
}

variable "admin_password" {
  description = "Administrator password for Windows VMs."
  type        = string
  default     = ""
  sensitive   = true
}

variable "zone_redundant" {
  description = "Spread instances across availability zones."
  type        = bool
  default     = false
}

variable "public_network_access_enabled" {
  description = "Allow public network access to the App Service."
  type        = bool
  default     = false
}

variable "enable_private_endpoint" {
  description = "Create a private endpoint for the App Service or AKS."
  type        = bool
  default     = false
}

variable "enable_app_gateway" {
  description = "Deploy an Application Gateway with WAF_v2 in front of the VMs."
  type        = bool
  default     = false
}

variable "app_gateway_name" {
  description = "Application Gateway name."
  type        = string
  default     = "appgw"
}

variable "app_gateway_public_ip_name" {
  description = "Application Gateway public IP name."
  type        = string
  default     = "pip-appgw"
}

variable "app_gateway_certificate_secret_id" {
  description = "Key Vault secret ID holding the TLS certificate (PFX)."
  type        = string
  default     = ""
}
'''

COMPUTE_OUTPUTS = '''output "app_service_id" {
  description = "App Service resource ID."
  value       = var.kind == "app_service" ? azurerm_linux_web_app.this[0].id : null
}

output "app_service_default_hostname" {
  description = "App Service default hostname."
  value       = var.kind == "app_service" ? azurerm_linux_web_app.this[0].default_hostname : null
}

output "aks_id" {
  description = "AKS cluster ID."
  value       = var.kind == "aks" ? azurerm_kubernetes_cluster.this[0].id : null
}

output "vm_ids" {
  description = "Virtual machine resource IDs."
  value       = concat(azurerm_linux_virtual_machine.this[*].id, azurerm_windows_virtual_machine.this[*].id)
}

output "vm_private_ips" {
  description = "Private IP addresses of the application NICs."
  value       = [for nic in azurerm_network_interface.this : nic.private_ip_address]
}

output "app_gateway_public_ip" {
  description = "Application Gateway public IP address."
  value       = var.enable_app_gateway ? azurerm_public_ip.appgw[0].ip_address : null
}

output "application_url" {
  description = "Public application URL when an ingress is configured."
  value = (
    var.enable_app_gateway
    ? "https://${azurerm_public_ip.appgw[0].ip_address}"
    : (var.kind == "app_service" ? "https://${azurerm_linux_web_app.this[0].default_hostname}" : null)
  )
}
'''

COMPUTE_README = '''# compute

Deploys one of three compute platforms, selected by `kind`:

- `vm` — Linux or Windows VMs behind an optional Application Gateway WAF_v2.
- `app_service` — Linux App Service with HTTPS-only, TLS 1.2, managed identity and optional private endpoint.
- `aks` — AKS cluster with a standard load balancer and zone-aware node pool.

The Application Gateway terminates TLS using a certificate stored in Key Vault.
Set `app_gateway_certificate_secret_id` to the Key Vault secret ID (the gateway's
managed identity is granted `Key Vault Secrets User` by the root module).

## Inputs (selection)

| Name | Type | Default |
|---|---|---|
| `kind` | `string` | required |
| `vm_count` | `number` | `2` |
| `vm_size` | `string` | `Standard_D2s_v5` |
| `app_service_sku` | `string` | `P1v3` |
| `enable_app_gateway` | `bool` | `false` |
| `enable_private_endpoint` | `bool` | `false` |

## Outputs

`app_service_id`, `app_service_default_hostname`, `vm_ids`, `vm_private_ips`,
`app_gateway_public_ip`, `application_url`.
'''

SECURITY_MAIN = '''resource "azurerm_role_assignment" "this" {
  for_each = var.role_assignments

  scope                = each.value.scope
  role_definition_name = each.value.role_definition_name
  principal_id         = each.value.principal_id
}

resource "azurerm_management_lock" "this" {
  count = var.enable_resource_lock ? 1 : 0

  name       = var.lock_name
  scope      = var.resource_group_id
  lock_level = "CanNotDelete"
  notes      = "Managed by TerraformAgent. Remove intentionally before destruction."
}
'''

SECURITY_VARIABLES = '''variable "resource_group_id" {
  description = "Resource group ID to protect with a lock."
  type        = string
}

variable "enable_resource_lock" {
  description = "Add a CanNotDelete lock to the resource group."
  type        = bool
  default     = false
}

variable "lock_name" {
  description = "Management lock name."
  type        = string
  default = "lock-cannot-delete"
}

variable "role_assignments" {
  description = "Additional role assignments keyed by logical name."
  type = map(object({
    scope                = string
    principal_id         = string
    role_definition_name = string
  }))
  default = {}
}
'''

SECURITY_OUTPUTS = '''output "role_assignment_ids" {
  description = "Role assignment IDs keyed by logical name."
  value       = { for key, assignment in azurerm_role_assignment.this : key => assignment.id }
}

output "lock_id" {
  description = "Management lock ID when enabled."
  value       = var.enable_resource_lock ? azurerm_management_lock.this[0].id : null
}
'''

SECURITY_README = '''# security

Cross-cutting RBAC assignments and an optional `CanNotDelete` resource group
lock. Key Vault-scoped roles live in the key-vault module because the vault is
created there.

## Inputs

| Name | Type | Default |
|---|---|---|
| `resource_group_id` | `string` | required |
| `enable_resource_lock` | `bool` | `false` |
| `role_assignments` | `map(object)` | `{}` |

## Outputs

`role_assignment_ids`, `lock_id`.
'''
