"""Cost estimation. Deterministic catalog + optional live Azure Retail Prices."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import httpx

from app.agents import catalog
from app.agents.schemas import ArchitectureSpec, CostEstimate, CostLine
from app.config import get_settings

RETAIL_API = "https://prices.azure.com/api/retail/prices"
CACHE_TTL_SECONDS = 24 * 3600

# (catalog key, serviceName filter, armSkuName or productName filter)
_PRICE_QUERIES: dict[str, dict[str, str]] = {
    "vm:Standard_D2s_v5": {"serviceName": "Virtual Machines", "armSkuName": "Standard_D2s_v5"},
    "vm:Standard_D4s_v5": {"serviceName": "Virtual Machines", "armSkuName": "Standard_D4s_v5"},
    "app_service:P1v3": {"serviceName": "Azure App Service", "armSkuName": "P1v3"},
    "sql:GP_Gen5_2": {"serviceName": "SQL Database", "armSkuName": "GP_Gen5_2"},
    "postgres:GP_Standard_D2ds_v5": {
        "serviceName": "Azure Database for PostgreSQL",
        "armSkuName": "GP_Standard_D2ds_v5",
    },
    "storage:Standard_LRS": {"serviceName": "Storage", "armSkuName": "Standard_LRS"},
    "keyvault:standard": {"serviceName": "Key Vault", "productName": "Key Vault"},
    "log_analytics:PayAsYouGo": {"serviceName": "Log Analytics", "productName": "Log Analytics"},
    "app_gateway:WAF_v2": {"serviceName": "Application Gateway", "armSkuName": "WAF_v2"},
    "public_ip:Standard": {"serviceName": "Virtual Network", "armSkuName": "Standard"},
}


class PricingCache:
    def __init__(self, path: str | None = None) -> None:
        root = Path(get_settings().artifact_root) / "cache"
        root.mkdir(parents=True, exist_ok=True)
        self.path = Path(path) if path else root / "azure-prices.json"
        self._data: dict[str, Any] = {}
        self._loaded = False

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if self.path.exists():
            try:
                self._data = json.loads(self.path.read_text("utf-8"))
            except (json.JSONDecodeError, OSError):
                self._data = {}

    def get(self, region: str, key: str) -> float | None:
        self._load()
        entry = self._data.get(region, {}).get(key)
        if not entry:
            return None
        if time.time() - entry.get("fetched_at", 0) > CACHE_TTL_SECONDS:
            return None
        return entry.get("hourly")

    def put(self, region: str, key: str, hourly: float) -> None:
        self._load()
        self._data.setdefault(region, {})[key] = {
            "hourly": hourly,
            "fetched_at": time.time(),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._data), "utf-8")


cache = PricingCache()


def fetch_live_price(region: str, key: str, timeout: float = 6.0) -> float | None:
    query = _PRICE_QUERIES.get(key)
    if not query:
        return None
    filters = [
        f"armRegionName eq '{region}'",
        "priceType eq 'Consumption'",
    ]
    for field, value in query.items():
        if field == "serviceName":
            filters.append(f"serviceName eq '{value}'")
        elif field == "armSkuName":
            filters.append(f"armSkuName eq '{value}'")
        elif field == "productName":
            filters.append(f"contains(productName, '{value}')")
    params = {"$filter": " and ".join(filters), "$top": "50"}
    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.get(RETAIL_API, params=params)
            response.raise_for_status()
            items = response.json().get("Items", [])
    except (httpx.HTTPError, ValueError):
        return None
    candidates = [
        item
        for item in items
        if item.get("retailPrice") and item.get("unitOfMeasure") in ("1 Hour", "1 GB/Month", "1/Month")
    ]
    if not candidates:
        return None
    best = min(candidates, key=lambda item: item["retailPrice"])
    price = float(best["retailPrice"])
    if best.get("unitOfMeasure") == "1 GB/Month":
        return price / 730.0
    return price


def refresh_region(region: str, keys: list[str] | None = None) -> dict[str, float]:
    refreshed: dict[str, float] = {}
    for key in keys or list(_PRICE_QUERIES):
        hourly = fetch_live_price(region, key)
        if hourly is not None:
            cache.put(region, key, hourly)
            refreshed[key] = hourly
    return refreshed


def _price(key: str, region: str, *, use_live: bool) -> tuple[dict[str, Any], str]:
    hourly = cache.get(region, key) if use_live else None
    source = "azure-retail-api"
    if hourly is None and use_live:
        hourly = fetch_live_price(region, key)
        if hourly is not None:
            cache.put(region, key, hourly)
    if hourly is None:
        return catalog.price_line(key), "catalog"
    catalog_line = catalog.price_line(key)
    catalog_line["hourly"] = round(hourly, 5)
    catalog_line["monthly"] = round(hourly * catalog.HOURS_PER_MONTH * catalog_line.get("quantity", 1), 2)
    return catalog_line, source


def estimate_cost(spec: ArchitectureSpec, *, use_live: bool = True) -> CostEstimate:
    region = spec.region
    lines: list[CostLine] = []
    private_endpoints = 0

    for resource in spec.resources:
        key: str | None = None
        quantity = 1.0
        if resource.type in ("azurerm_linux_virtual_machine", "azurerm_windows_virtual_machine"):
            key = f"vm:{resource.sku or 'Standard_D2s_v5'}"
            quantity = float(resource.config.get("count", 1))
        elif resource.type == "azurerm_service_plan":
            key = f"app_service:{resource.sku or 'P1v3'}"
        elif resource.type == "azurerm_application_gateway":
            key = "app_gateway:WAF_v2"
        elif resource.type == "azurerm_public_ip":
            key = "public_ip:Standard"
        elif resource.type == "azurerm_nat_gateway":
            key = "nat:Standard"
        elif resource.type == "azurerm_mssql_database":
            key = f"sql:{resource.sku or 'GP_Gen5_2'}"
        elif resource.type == "azurerm_postgresql_flexible_server":
            key = f"postgres:{resource.sku or 'GP_Standard_D2ds_v5'}"
        elif resource.type == "azurerm_mysql_flexible_server":
            key = f"mysql:{resource.sku or 'GP_Standard_D2ds_v4'}"
        elif resource.type == "azurerm_storage_account":
            key = "storage:Standard_LRS"
            quantity = 100.0
        elif resource.type == "azurerm_key_vault":
            key = "keyvault:standard"
        elif resource.type == "azurerm_log_analytics_workspace":
            key = "log_analytics:PayAsYouGo"
            quantity = 10.0
        elif resource.type == "azurerm_application_insights":
            key = "app_insights:standard"
            quantity = 5.0
        elif resource.type == "azurerm_kubernetes_cluster":
            key = "aks:Standard"
        elif resource.type == "azurerm_private_endpoint":
            private_endpoints += 1

        if key:
            line, source = _price(key, region, use_live=use_live)
            line["quantity"] = quantity
            if line.get("unit") == "month":
                line["monthly"] = round(
                    float(line.get("hourly", 0)) * catalog.HOURS_PER_MONTH * quantity, 2
                )
            elif line.get("unit") == "GB-month":
                # Catalog/retail prices for GB-month lines are per gigabyte.
                line["monthly"] = round(float(line.get("monthly", 0)) * quantity, 2)
            line["source"] = source
            lines.append(CostLine(**line))

    if private_endpoints:
        line, source = _price("private_endpoint:standard", region, use_live=use_live)
        line["quantity"] = private_endpoints
        line["monthly"] = round(float(line.get("hourly", 0.01)) * catalog.HOURS_PER_MONTH * private_endpoints, 2)
        line["source"] = source
        lines.append(CostLine(**line))

    total = round(sum(line.monthly for line in lines), 2)
    return CostEstimate(currency="USD", monthly_total=total, lines=lines)
