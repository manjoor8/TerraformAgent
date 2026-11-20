export const REGIONS = [
  { value: "uaenorth", label: "UAE North (Dubai)" },
  { value: "eastus", label: "East US" },
  { value: "eastus2", label: "East US 2" },
  { value: "westus2", label: "West US 2" },
  { value: "westus3", label: "West US 3" },
  { value: "centralus", label: "Central US" },
  { value: "westeurope", label: "West Europe" },
  { value: "northeurope", label: "North Europe" },
  { value: "uksouth", label: "UK South" },
  { value: "southeastasia", label: "Southeast Asia" },
  { value: "australiaeast", label: "Australia East" },
  { value: "japaneast", label: "Japan East" },
  { value: "centralindia", label: "Central India" },
  { value: "qatarcentral", label: "Qatar Central" },
  { value: "southafricanorth", label: "South Africa North" },
];

export const ENVIRONMENTS = [
  { value: "dev", label: "Development" },
  { value: "staging", label: "Staging" },
  { value: "production", label: "Production" },
];

export const AUTH_METHODS = [
  { value: "service_principal", label: "Service principal" },
  { value: "oidc", label: "Workload identity (OIDC)" },
  { value: "managed_identity", label: "Managed identity" },
  { value: "azure_cli", label: "Azure CLI (local dev)" },
];

export function regionLabel(value: string): string {
  return REGIONS.find((region) => region.value === value)?.label ?? value;
}
