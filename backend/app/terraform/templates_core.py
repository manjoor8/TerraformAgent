"""Static HCL module templates: resource group, networking, monitoring."""
from __future__ import annotations

RESOURCE_GROUP_MAIN = '''resource "azurerm_resource_group" "this" {
  name     = var.name
  location = var.location

  tags = var.tags
}
'''

RESOURCE_GROUP_VARIABLES = '''variable "name" {
  description = "Resource group name."
  type        = string
}

variable "location" {
  description = "Azure region."
  type        = string
}

variable "tags" {
  description = "Tags applied to every resource in the group."
  type        = map(string)
  default     = {}
}
'''

RESOURCE_GROUP_OUTPUTS = '''output "id" {
  description = "Resource group resource ID."
  value       = azurerm_resource_group.this.id
}

output "name" {
  description = "Resource group name."
  value       = azurerm_resource_group.this.name
}

output "location" {
  description = "Resource group location."
  value       = azurerm_resource_group.this.location
}
'''

RESOURCE_GROUP_README = '''# resource-group

Creates the resource group that owns every resource in this stack.

## Inputs

| Name | Type | Required | Description |
|---|---|---|---|
| `name` | `string` | yes | Resource group name |
| `location` | `string` | yes | Azure region |
| `tags` | `map(string)` | no | Tags applied to the group |

## Outputs

`id`, `name`, `location`.
'''

NETWORKING_MAIN = '''resource "azurerm_virtual_network" "this" {
  name                = var.name
  location            = var.location
  resource_group_name = var.resource_group_name
  address_space       = var.address_space

  tags = var.tags
}

resource "azurerm_subnet" "this" {
  for_each = var.subnets

  name                 = each.key
  resource_group_name  = var.resource_group_name
  virtual_network_name = azurerm_virtual_network.this.name
  address_prefixes     = [each.value.cidr]
  service_endpoints    = each.value.service_endpoints

  dynamic "delegation" {
    for_each = each.value.delegation_service == null ? [] : [each.value.delegation_service]

    content {
      name = "delegation"

      service_delegation {
        name    = delegation.value
        actions = ["Microsoft.Network/virtualNetworks/subnets/action"]
      }
    }
  }
}

resource "azurerm_network_security_group" "this" {
  for_each = var.nsg_rules

  name                = each.key
  location            = var.location
  resource_group_name = var.resource_group_name

  tags = var.tags

  dynamic "security_rule" {
    for_each = each.value

    content {
      name                       = security_rule.value.name
      priority                   = security_rule.value.priority
      direction                  = security_rule.value.direction
      access                     = security_rule.value.access
      protocol                   = security_rule.value.protocol
      source_port_range          = "*"
      destination_port_ranges    = security_rule.value.destination_port_ranges
      source_address_prefix      = one(security_rule.value.source_address_prefixes)
      destination_address_prefix = "*"
    }
  }
}

resource "azurerm_subnet_network_security_group_association" "this" {
  for_each = var.subnet_nsgs

  subnet_id                 = azurerm_subnet.this[each.key].id
  network_security_group_id = azurerm_network_security_group.this[each.value].id
}

resource "azurerm_public_ip" "nat" {
  count = var.enable_nat_gateway ? 1 : 0

  name                = "pip-nat-${var.name}"
  location            = var.location
  resource_group_name = var.resource_group_name
  allocation_method   = "Static"
  sku                 = "Standard"

  tags = var.tags
}

resource "azurerm_nat_gateway" "this" {
  count = var.enable_nat_gateway ? 1 : 0

  name                = "nat-${var.name}"
  location            = var.location
  resource_group_name = var.resource_group_name
  sku_name            = "Standard"

  tags = var.tags
}

resource "azurerm_nat_gateway_public_ip_association" "this" {
  count = var.enable_nat_gateway ? 1 : 0

  nat_gateway_id       = azurerm_nat_gateway.this[0].id
  public_ip_address_id = azurerm_public_ip.nat[0].id
}

resource "azurerm_subnet_nat_gateway_association" "this" {
  count = var.enable_nat_gateway ? 1 : 0

  subnet_id      = azurerm_subnet.this[var.nat_subnet_key].id
  nat_gateway_id = azurerm_nat_gateway.this[0].id
}

resource "azurerm_private_dns_zone" "this" {
  for_each = toset(var.private_dns_zones)

  name                = each.value
  resource_group_name = var.resource_group_name

  tags = var.tags
}

resource "azurerm_private_dns_zone_virtual_network_link" "this" {
  for_each = toset(var.private_dns_zones)

  name                  = "${replace(each.value, ".", "-")}-link"
  resource_group_name   = var.resource_group_name
  private_dns_zone_name = azurerm_private_dns_zone.this[each.value].name
  virtual_network_id    = azurerm_virtual_network.this.id
  registration_enabled  = false

  tags = var.tags
}
'''

NETWORKING_VARIABLES = '''variable "name" {
  description = "Virtual network name."
  type        = string
}

variable "location" {
  description = "Azure region."
  type        = string
}

variable "resource_group_name" {
  description = "Resource group that owns the network."
  type        = string
}

variable "tags" {
  description = "Tags applied to network resources."
  type        = map(string)
  default     = {}
}

variable "address_space" {
  description = "VNet address spaces (non-overlapping CIDRs)."
  type        = list(string)

  validation {
    condition     = length(var.address_space) > 0
    error_message = "At least one address space is required."
  }
}

variable "subnets" {
  description = "Subnet map keyed by subnet name."
  type = map(object({
    cidr               = string
    delegation_service = optional(string)
    service_endpoints  = optional(list(string), [])
  }))
}

variable "subnet_nsgs" {
  description = "Map of subnet name to network security group name."
  type        = map(string)
  default     = {}
}

variable "nsg_rules" {
  description = "Network security group rules keyed by NSG name."
  type = map(list(object({
    name                    = string
    priority                = number
    direction               = string
    access                  = string
    protocol                = string
    destination_port_ranges = list(string)
    source_address_prefixes = list(string)
  })))
  default = {}
}

variable "enable_nat_gateway" {
  description = "Create a NAT gateway for deterministic outbound egress."
  type        = bool
  default     = false
}

variable "nat_subnet_key" {
  description = "Subnet that egresses through the NAT gateway."
  type        = string
  default     = "snet-app"
}

variable "private_dns_zones" {
  description = "Private DNS zones to create and link to the VNet."
  type        = list(string)
  default     = []
}
'''

NETWORKING_OUTPUTS = '''output "vnet_id" {
  description = "Virtual network resource ID."
  value       = azurerm_virtual_network.this.id
}

output "vnet_name" {
  description = "Virtual network name."
  value       = azurerm_virtual_network.this.name
}

output "subnet_ids" {
  description = "Subnet IDs keyed by subnet name."
  value       = { for key, subnet in azurerm_subnet.this : key => subnet.id }
}

output "nsg_ids" {
  description = "Network security group IDs keyed by name."
  value       = { for key, nsg in azurerm_network_security_group.this : key => nsg.id }
}

output "private_dns_zone_ids" {
  description = "Private DNS zone IDs keyed by zone name."
  value       = { for key, zone in azurerm_private_dns_zone.this : key => zone.id }
}
'''

NETWORKING_README = '''# networking

Creates the virtual network, subnets, network security groups, NAT egress and
private DNS zones.

- Subnets are declared as a map so environments can change CIDRs without editing HCL.
- NSG rules are data-driven and rendered with `dynamic` blocks.
- Private DNS zones are linked to the VNet so private endpoints resolve.

## Inputs

| Name | Type | Default |
|---|---|---|
| `name` | `string` | required |
| `address_space` | `list(string)` | required |
| `subnets` | `map(object)` | required |
| `subnet_nsgs` | `map(string)` | `{}` |
| `nsg_rules` | `map(list(object))` | `{}` |
| `enable_nat_gateway` | `bool` | `false` |
| `private_dns_zones` | `list(string)` | `[]` |

## Outputs

`vnet_id`, `vnet_name`, `subnet_ids`, `nsg_ids`, `private_dns_zone_ids`.
'''

MONITORING_MAIN = '''resource "azurerm_log_analytics_workspace" "this" {
  name                = var.workspace_name
  location            = var.location
  resource_group_name = var.resource_group_name
  sku                 = "PerGB2018"
  retention_in_days   = var.retention_days
  daily_quota_gb      = var.daily_quota_gb

  tags = var.tags
}

resource "azurerm_application_insights" "this" {
  count = var.enable_app_insights ? 1 : 0

  name                = var.app_insights_name
  location            = var.location
  resource_group_name = var.resource_group_name
  application_type    = "web"
  workspace_id        = azurerm_log_analytics_workspace.this.id

  tags = var.tags
}

resource "azurerm_monitor_diagnostic_setting" "this" {
  for_each = var.diagnostic_targets

  name                       = substr("diag-${each.key}", 0, 260)
  target_resource_id         = each.value
  log_analytics_workspace_id = azurerm_log_analytics_workspace.this.id

  enabled_log {
    category_group = "allLogs"
  }

  enabled_metric {
    category = "AllMetrics"
  }
}
'''

MONITORING_VARIABLES = '''variable "workspace_name" {
  description = "Log Analytics workspace name."
  type        = string
}

variable "app_insights_name" {
  description = "Application Insights name."
  type        = string
  default     = ""
}

variable "location" {
  description = "Azure region."
  type        = string
}

variable "resource_group_name" {
  description = "Resource group that owns monitoring resources."
  type        = string
}

variable "tags" {
  description = "Tags applied to monitoring resources."
  type        = map(string)
  default     = {}
}

variable "retention_days" {
  description = "Log retention in days."
  type        = number
  default     = 30

  validation {
    condition     = var.retention_days >= 30 && var.retention_days <= 730
    error_message = "Retention must be between 30 and 730 days."
  }
}

variable "daily_quota_gb" {
  description = "Daily ingestion cap in GB (-1 for unlimited)."
  type        = number
  default     = -1
}

variable "enable_app_insights" {
  description = "Deploy Application Insights."
  type        = bool
  default     = false
}

variable "diagnostic_targets" {
  description = "Map of logical name to resource ID that receives diagnostic settings."
  type        = map(string)
  default     = {}
}
'''

MONITORING_OUTPUTS = '''output "workspace_id" {
  description = "Log Analytics workspace ID."
  value       = azurerm_log_analytics_workspace.this.id
}

output "workspace_name" {
  description = "Log Analytics workspace name."
  value       = azurerm_log_analytics_workspace.this.name
}

output "app_insights_id" {
  description = "Application Insights resource ID (null when disabled)."
  value       = var.enable_app_insights ? azurerm_application_insights.this[0].id : null
}

output "app_insights_connection_string" {
  description = "Application Insights connection string."
  value       = var.enable_app_insights ? azurerm_application_insights.this[0].connection_string : ""
  sensitive   = true
}
'''

MONITORING_README = '''# monitoring

Centralized observability: a Log Analytics workspace, optional Application
Insights, and diagnostic settings for every supplied target.

`diagnostic_targets` is a map of logical name to resource ID. The module uses
the `allLogs` category group so it works across Azure service types.

## Inputs

| Name | Type | Default |
|---|---|---|
| `workspace_name` | `string` | required |
| `retention_days` | `number` | `30` |
| `enable_app_insights` | `bool` | `false` |
| `diagnostic_targets` | `map(string)` | `{}` |

## Outputs

`workspace_id`, `workspace_name`, `app_insights_id`, `app_insights_connection_string`.
'''
