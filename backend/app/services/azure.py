"""Read-only Azure Resource Manager client for subscription verification and checks.

Write operations are deliberately absent: Terraform is the only mutation path.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.errors import AppError, UpstreamError

ARM_SCOPE = "https://management.azure.com/.default"
ARM_RESOURCE = "https://management.azure.com"
IMDS_ENDPOINT = "http://169.254.169.254/metadata/identity/oauth2/token"
CLI_TIMEOUT = 30


class AzureAuthError(AppError):
    status_code = 400
    code = "azure_auth_error"


@dataclass
class TokenBundle:
    token: str
    method: str
    expires_at: float


_token_cache: dict[str, TokenBundle] = {}


def _cached(key: str) -> str | None:
    bundle = _token_cache.get(key)
    if bundle and bundle.expires_at > time.time() + 60:
        return bundle.token
    return None


def _store(key: str, token: str, method: str, expires_in: int) -> str:
    _token_cache[key] = TokenBundle(token=token, method=method, expires_at=time.time() + expires_in)
    return token


def _client_credentials_token() -> tuple[str, str] | None:
    client_id = os.environ.get("ARM_CLIENT_ID")
    tenant_id = os.environ.get("ARM_TENANT_ID")
    if not (client_id and tenant_id):
        return None
    token_url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
    oidc = os.environ.get("ARM_OIDC_TOKEN")
    secret = os.environ.get("ARM_CLIENT_SECRET")
    data: dict[str, str] = {
        "client_id": client_id,
        "scope": ARM_SCOPE,
        "grant_type": "client_credentials",
    }
    if oidc:
        data["client_assertion_type"] = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
        data["client_assertion"] = oidc
    elif secret:
        data["client_secret"] = secret
    else:
        return None
    try:
        with httpx.Client(timeout=20) as client:
            response = client.post(token_url, data=data)
            response.raise_for_status()
            payload = response.json()
    except httpx.HTTPError as exc:
        raise AzureAuthError(f"Azure client credential authentication failed: {exc}") from exc
    return payload["access_token"], "oidc" if oidc else "service_principal"


def _managed_identity_token() -> tuple[str, str] | None:
    # Only attempt IMDS when explicitly enabled or when running in Azure.
    if os.environ.get("ARM_USE_MSI", "").lower() not in ("1", "true") and not os.environ.get(
        "IDENTITY_ENDPOINT"
    ):
        return None
    endpoint = os.environ.get("IDENTITY_ENDPOINT", IMDS_ENDPOINT)
    params = {"resource": ARM_RESOURCE, "api-version": "2018-02-01"}
    headers = {"Metadata": "true"}
    if os.environ.get("IDENTITY_HEADER"):
        headers["X-IDENTITY-HEADER"] = os.environ["IDENTITY_HEADER"]
    try:
        with httpx.Client(timeout=5) as client:
            response = client.get(endpoint, params=params, headers=headers)
            response.raise_for_status()
            payload = response.json()
    except httpx.HTTPError:
        return None
    return payload.get("access_token"), "managed_identity"


def _cli_token() -> tuple[str, str] | None:
    az = shutil.which("az")
    if not az:
        return None
    try:
        result = subprocess.run(
            [
                az,
                "account",
                "get-access-token",
                "--resource",
                ARM_RESOURCE,
                "--query",
                "{token:accessToken,expires:expiresOn}",
                "-o",
                "json",
            ],
            capture_output=True,
            text=True,
            timeout=CLI_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    return payload.get("token"), "azure_cli"


def get_arm_token(force: bool = False) -> tuple[str, str]:
    cache_key = "arm"
    if not force:
        cached = _cached(cache_key)
        if cached:
            return cached, _token_cache[cache_key].method
    for strategy in (_managed_identity_token, _client_credentials_token, _cli_token):
        found = strategy()
        if found and found[0]:
            token, method = found
            _store(cache_key, token, method, 3000)
            return token, method
    raise AzureAuthError(
        "No Azure credentials available. Configure a service principal (ARM_CLIENT_ID, "
        "ARM_CLIENT_SECRET, ARM_TENANT_ID), OIDC, managed identity, or run `az login`."
    )


class AzureClient:
    def __init__(self, subscription_id: str, tenant_id: str | None = None) -> None:
        if not subscription_id:
            raise AzureAuthError("No Azure subscription ID configured")
        self.subscription_id = subscription_id
        self.tenant_id = tenant_id

    def _headers(self) -> dict[str, str]:
        token, _ = get_arm_token()
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    def _get(self, path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{ARM_RESOURCE}{path}"
        try:
            with httpx.Client(timeout=30) as client:
                response = client.get(url, headers=self._headers(), params=params)
                if response.status_code == 401:
                    token, _ = get_arm_token(force=True)
                    response = client.get(
                        url,
                        headers={"Authorization": f"Bearer {token}"},
                        params=params,
                    )
                response.raise_for_status()
                return response.json()
        except httpx.HTTPStatusError as exc:
            raise UpstreamError(
                f"Azure ARM request failed ({exc.response.status_code}): {exc.response.text[:400]}"
            ) from exc
        except httpx.HTTPError as exc:
            raise UpstreamError(f"Azure ARM request failed: {exc}") from exc

    # -- read-only operations --------------------------------------------
    def subscription(self) -> dict[str, Any]:
        return self._get(f"/subscriptions/{self.subscription_id}", params={"api-version": "2022-12-01"})

    def permissions(self) -> dict[str, Any]:
        return self._get(
            f"/subscriptions/{self.subscription_id}/providers/Microsoft.Authorization/permissions",
            params={"api-version": "2022-04-01"},
        )

    def resource_groups(self) -> list[dict[str, Any]]:
        data = self._get(
            f"/subscriptions/{self.subscription_id}/resourcegroups",
            params={"api-version": "2021-04-01"},
        )
        return data.get("value", [])

    def list_resources(self, *, tag_name: str | None = None, tag_value: str | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"api-version": "2021-04-01"}
        if tag_name and tag_value:
            params["$filter"] = f"tagName eq '{tag_name}' and tagValue eq '{tag_value}'"
        data = self._get(f"/subscriptions/{self.subscription_id}/resources", params=params)
        return data.get("value", [])

    def get_resource_by_id(self, resource_id: str) -> dict[str, Any]:
        if not resource_id.startswith("/subscriptions/"):
            raise AppError("Invalid Azure resource ID")
        return self._get(resource_id, params={"api-version": "2021-04-01"})

    def verify_connection(self) -> dict[str, Any]:
        subscription = self.subscription()
        permissions = self.permissions()
        actions = sorted(
            {
                action
                for assignment in permissions.get("value", [])
                for action in assignment.get("actions", [])
                if action and not action.startswith("Microsoft.Authorization/*/read")
            }
        )
        write_capable = any(
            action in ("*", "*/write", "Microsoft.Resources/deployments/write") or action.endswith("/write")
            for action in actions
        )
        return {
            "subscription_id": subscription.get("subscriptionId", self.subscription_id),
            "subscription_name": subscription.get("displayName", ""),
            "tenant_id": subscription.get("tenantId", self.tenant_id or ""),
            "state": subscription.get("state", ""),
            "roles_or_actions": actions[:40],
            "can_deploy": write_capable,
        }


def resolve_subscription_id(project_subscription: Any, fallback: str | None = None) -> str | None:
    if project_subscription is not None and getattr(project_subscription, "subscription_id", None):
        return project_subscription.subscription_id
    return fallback or os.environ.get("ARM_SUBSCRIPTION_ID")
