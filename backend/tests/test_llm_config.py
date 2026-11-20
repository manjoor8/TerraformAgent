from __future__ import annotations

from fastapi.testclient import TestClient


def test_non_admin_cannot_read_or_change_llm_config(
    client: TestClient, auth: dict[str, str]
) -> None:
    assert client.get("/api/v1/system/llm", headers=auth).status_code == 403
    response = client.put(
        "/api/v1/system/llm",
        headers=auth,
        json={"provider": "offline", "model": "", "base_url": "", "api_key": ""},
    )
    assert response.status_code == 403


def test_admin_can_configure_offline_and_see_effective_state(
    client: TestClient, admin_auth: dict[str, str]
) -> None:
    response = client.put(
        "/api/v1/system/llm",
        headers=admin_auth,
        json={
            "provider": "offline",
            "model": "deterministic-heuristics",
            "base_url": "",
            "api_key": "",
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["provider"] == "offline"
    assert payload["configured"] is True
    assert payload["effective"]["available"] is False

    fetched = client.get("/api/v1/system/llm", headers=admin_auth)
    assert fetched.status_code == 200
    assert fetched.json()["source"] == "runtime"

    test = client.post(
        "/api/v1/system/llm/test", headers=admin_auth, json={"provider": "offline"}
    )
    assert test.status_code == 200
    assert test.json()["ok"] is True


def test_deepseek_key_is_stored_but_never_returned(
    client: TestClient, admin_auth: dict[str, str]
) -> None:
    secret = "sk-deepseek-test-secret-1234"
    response = client.put(
        "/api/v1/system/llm",
        headers=admin_auth,
        json={
            "provider": "deepseek",
            "model": "deepseek-chat",
            "base_url": "https://api.deepseek.com/v1",
            "api_key": secret,
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    serialized = response.text
    assert secret not in serialized
    assert payload["has_api_key"] is True
    assert payload["api_key_last4"] == "1234"
    assert payload["effective"]["active_provider"] == "deepseek"

    fetched = client.get("/api/v1/system/llm", headers=admin_auth)
    assert secret not in fetched.text
    assert fetched.json()["api_key_last4"] == "1234"

    # Removing the runtime row falls back to the environment (offline in tests).
    reset = client.delete("/api/v1/system/llm", headers=admin_auth)
    assert reset.status_code == 200
    assert reset.json()["configured"] is False
    assert secret not in reset.text
    assert reset.json()["effective"]["available"] is False


def test_invalid_base_url_is_rejected(client: TestClient, admin_auth: dict[str, str]) -> None:
    response = client.put(
        "/api/v1/system/llm",
        headers=admin_auth,
        json={
            "provider": "openai_compatible",
            "model": "gpt-4o-mini",
            "base_url": "http://evil.example.com/v1",
            "api_key": "sk-test-1234",
        },
    )
    assert response.status_code == 400
    assert "https" in response.json()["detail"].lower() or "http" in response.json()["detail"].lower()
