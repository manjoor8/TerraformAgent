"""Static Azure service catalog: naming, SKUs, indicative prices, region mapping."""
from __future__ import annotations

from typing import Any

REGION_ALIASES = {
    "uae north": "uaenorth",
    "uae": "uaenorth",
    "dubai": "uaenorth",
    "east us": "eastus",
    "eastus": "eastus",
    "west us": "westus",
    "west us 2": "westus2",
    "west us 3": "westus3",
    "central us": "centralus",
    "west europe": "westeurope",
    "north europe": "northeurope",
    "uk south": "uksouth",
    "southeast asia": "southeastasia",
    "south east asia": "southeastasia",
    "australia east": "australiaeast",
    "japan east": "japaneast",
    "india central": "centralindia",
    "qatar": "qatarcentral",
    "south africa north": "southafricanorth",
}

REGION_LABELS = {
    "uaenorth": "UAE North",
    "eastus": "East US",
    "eastus2": "East US 2",
    "westus": "West US",
    "westus2": "West US 2",
    "westus3": "West US 3",
    "centralus": "Central US",
    "westeurope": "West Europe",
    "northeurope": "North Europe",
    "uksouth": "UK South",
    "southeastasia": "Southeast Asia",
    "australiaeast": "Australia East",
    "japaneast": "Japan East",
    "centralindia": "Central India",
    "qatarcentral": "Qatar Central",
    "southafricanorth": "South Africa North",
}

# Monthly USD, pay-as-you-go, single instance. Indicative; refreshed by the
# Azure Retail Prices API when the network and API allow.
PRICE_CATALOG: dict[str, dict[str, Any]] = {
    "vm:Standard_B2s": {"hourly": 0.0416, "label": "Standard_B2s"},
    "vm:Standard_D2s_v5": {"hourly": 0.096, "label": "Standard_D2s_v5"},
    "vm:Standard_D4s_v5": {"hourly": 0.192, "label": "Standard_D4s_v5"},
    "vm:Standard_D8s_v5": {"hourly": 0.384, "label": "Standard_D8s_v5"},
    "app_service:P1v3": {"hourly": 0.10, "label": "App Service P1v3 Linux"},
    "app_service:P2v3": {"hourly": 0.20, "label": "App Service P2v3 Linux"},
    "app_service:B1": {"hourly": 0.018, "label": "App Service B1 Linux"},
    "sql:GP_Gen5_2": {"hourly": 0.5041, "label": "Azure SQL GP Gen5 2 vCores"},
    "sql:GP_Gen5_4": {"hourly": 1.0082, "label": "Azure SQL GP Gen5 4 vCores"},
    "sql:BC_Gen5_2": {"hourly": 1.0082, "label": "Azure SQL Business Critical 2 vCores"},
    "sql:S2": {"hourly": 0.2055, "label": "Azure SQL Standard S2"},
    "postgres:GP_Standard_D2ds_v5": {"hourly": 0.2246, "label": "PostgreSQL Flexible D2ds_v5"},
    "postgres:GP_Standard_D4ds_v5": {"hourly": 0.4492, "label": "PostgreSQL Flexible D4ds_v5"},
    "mysql:GP_Standard_D2ds_v4": {"hourly": 0.2088, "label": "MySQL Flexible D2ds_v4"},
    "storage:Standard_LRS": {"gigabyte": 0.018, "label": "Blob Storage Hot LRS"},
    "keyvault:standard": {"hourly": 0.0055, "label": "Key Vault Standard (operations incl.)"},
    "log_analytics:PayAsYouGo": {"gigabyte": 2.76, "label": "Log Analytics ingestion"},
    "app_insights:standard": {"gigabyte": 2.30, "label": "Application Insights ingestion"},
    "app_gateway:WAF_v2": {"hourly": 0.4434, "label": "Application Gateway WAF_v2"},
    "public_ip:Standard": {"hourly": 0.005, "label": "Standard public IP"},
    "nat:Standard": {"hourly": 0.045, "label": "NAT Gateway"},
    "private_endpoint:standard": {"hourly": 0.01, "label": "Private endpoint"},
    "aks:Standard": {"hourly": 0.10, "label": "AKS cluster management"},
}

HOURS_PER_MONTH = 730

DEFAULT_SKU = {
    "app_service": "P1v3",
    "vm": "Standard_D2s_v5",
    "sql": "GP_Gen5_2",
    "postgres": "GP_Standard_D2ds_v5",
    "mysql": "GP_Standard_D2ds_v4",
    "storage": "Standard_LRS",
    "key_vault": "standard",
    "app_gateway": "WAF_v2",
}

# Azure service names for the inventory table.
SERVICE_LABELS = {
    "azurerm_resource_group": "Resource Group",
    "azurerm_virtual_network": "Virtual Network",
    "azurerm_subnet": "Subnet",
    "azurerm_network_security_group": "Network Security Group",
    "azurerm_nat_gateway": "NAT Gateway",
    "azurerm_public_ip": "Public IP",
    "azurerm_application_gateway": "Application Gateway",
    "azurerm_linux_virtual_machine": "Linux Virtual Machine",
    "azurerm_windows_virtual_machine": "Windows Virtual Machine",
    "azurerm_network_interface": "Network Interface",
    "azurerm_user_assigned_identity": "Managed Identity",
    "azurerm_service_plan": "App Service Plan",
    "azurerm_linux_web_app": "Azure App Service (Linux)",
    "azurerm_windows_web_app": "Azure App Service (Windows)",
    "azurerm_kubernetes_cluster": "Azure Kubernetes Service",
    "azurerm_mssql_server": "Azure SQL Server",
    "azurerm_mssql_database": "Azure SQL Database",
    "azurerm_postgresql_flexible_server": "PostgreSQL Flexible Server",
    "azurerm_mysql_flexible_server": "MySQL Flexible Server",
    "azurerm_storage_account": "Storage Account",
    "azurerm_storage_container": "Blob Container",
    "azurerm_key_vault": "Key Vault",
    "azurerm_key_vault_secret": "Key Vault Secret",
    "azurerm_log_analytics_workspace": "Log Analytics Workspace",
    "azurerm_application_insights": "Application Insights",
    "azurerm_private_endpoint": "Private Endpoint",
    "azurerm_private_dns_zone": "Private DNS Zone",
    "azurerm_monitor_diagnostic_setting": "Diagnostic Setting",
    "azurerm_role_assignment": "RBAC Role Assignment",
    "azurerm_management_lock": "Resource Lock",
}

TF_TYPE_TO_MODULE = {
    "azurerm_resource_group": "resource-group",
    "azurerm_virtual_network": "networking",
    "azurerm_subnet": "networking",
    "azurerm_network_security_group": "networking",
    "azurerm_nat_gateway": "networking",
    "azurerm_public_ip": "networking",
    "azurerm_application_gateway": "compute",
    "azurerm_linux_virtual_machine": "compute",
    "azurerm_windows_virtual_machine": "compute",
    "azurerm_network_interface": "compute",
    "azurerm_user_assigned_identity": "compute",
    "azurerm_service_plan": "compute",
    "azurerm_linux_web_app": "compute",
    "azurerm_windows_web_app": "compute",
    "azurerm_kubernetes_cluster": "compute",
    "azurerm_mssql_server": "database",
    "azurerm_mssql_database": "database",
    "azurerm_postgresql_flexible_server": "database",
    "azurerm_mysql_flexible_server": "database",
    "azurerm_storage_account": "storage",
    "azurerm_storage_container": "storage",
    "azurerm_key_vault": "key-vault",
    "azurerm_key_vault_secret": "key-vault",
    "azurerm_log_analytics_workspace": "monitoring",
    "azurerm_application_insights": "monitoring",
    "azurerm_private_endpoint": "networking",
    "azurerm_monitor_diagnostic_setting": "monitoring",
    "azurerm_role_assignment": "security",
    "azurerm_management_lock": "security",
}

PRIVATE_DNS_ZONES = {
    "mssql": "privatelink.database.windows.net",
    "sql": "privatelink.database.windows.net",
    "postgres": "privatelink.postgres.database.azure.com",
    "mysql": "privatelink.mysql.database.azure.com",
    "storage": "privatelink.blob.core.windows.net",
    "key_vault": "privatelink.vaultcore.azure.net",
    "app_service": "privatelink.azurewebsites.net",
    "monitoring": "privatelink.monitor.azure.com",
}

VALID_REGIONS = set(REGION_LABELS)


def normalize_region(text: str | None, default: str = "eastus") -> str:
    if not text:
        return default
    value = text.strip().lower()
    if value in REGION_ALIASES:
        return REGION_ALIASES[value]
    if value in VALID_REGIONS:
        return value
    compact = value.replace(" ", "")
    if compact in VALID_REGIONS:
        return compact
    return default


def region_label(region: str) -> str:
    return REGION_LABELS.get(region, region)


def price_line(key: str, quantity: float = 1, hours: float = HOURS_PER_MONTH) -> dict[str, Any]:
    entry = PRICE_CATALOG.get(key)
    if not entry:
        return {
            "service": key.split(":")[0],
            "sku": key,
            "quantity": quantity,
            "unit": "month",
            "hourly": 0.0,
            "monthly": 0.0,
            "source": "not-priced",
        }
    if "hourly" in entry:
        hourly = entry["hourly"]
        return {
            "service": key.split(":")[0],
            "sku": entry["label"],
            "quantity": quantity,
            "unit": "month",
            "hourly": round(hourly, 5),
            "monthly": round(hourly * hours * quantity, 2),
            "source": "catalog",
        }
    gigabyte = entry.get("gigabyte", 0)
    return {
        "service": key.split(":")[0],
        "sku": entry["label"],
        "quantity": quantity,
        "unit": "GB-month",
        "hourly": 0.0,
        "monthly": round(gigabyte * quantity, 2),
        "source": "catalog",
    }
