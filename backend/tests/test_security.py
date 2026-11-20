from __future__ import annotations

import os

from app.core.security import (
    create_access_token,
    decode_access_token,
    hash_json,
    hash_password,
    redact,
    redact_obj,
    verify_password,
)
from app.terraform.executor import build_environment


def test_password_hashing_roundtrip() -> None:
    encoded = hash_password("CorrectHorseBatteryStaple")
    assert encoded.startswith("scrypt$")
    assert verify_password("CorrectHorseBatteryStaple", encoded)
    assert not verify_password("wrong-password", encoded)


def test_jwt_roundtrip() -> None:
    token = create_access_token("user-1", "user@example.test")
    payload = decode_access_token(token)
    assert payload["sub"] == "user-1"
    assert payload["email"] == "user@example.test"


def test_redaction_masks_credentials() -> None:
    text = (
        "client_secret=abc123 password=hunter2 "
        "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.signature "
        "AccountKey=abc123=="
    )
    cleaned = redact(text)
    assert "abc123" not in cleaned
    assert "hunter2" not in cleaned
    assert "signature" not in cleaned


def test_redact_object_masks_sensitive_keys() -> None:
    payload = {"subscription_id": "sub", "client_secret": "value", "nested": {"password": "x"}}
    cleaned = redact_obj(payload)
    assert cleaned["subscription_id"] == "sub"
    assert cleaned["client_secret"] == "[REDACTED]"
    assert cleaned["nested"]["password"] == "[REDACTED]"


def test_hash_json_is_order_independent() -> None:
    assert hash_json({"a": 1, "b": 2}) == hash_json({"b": 2, "a": 1})
    assert hash_json({"a": 1}) != hash_json({"a": 2})


def test_execution_environment_allowlist() -> None:
    os.environ["UNRELATED_APPLICATION_SECRET"] = "must-not-leak"
    os.environ["ARM_CLIENT_SECRET"] = "terraform-needs-this"
    env = build_environment()
    assert "UNRELATED_APPLICATION_SECRET" not in env
    assert env["ARM_CLIENT_SECRET"] == "terraform-needs-this"
    assert env["TF_INPUT"] == "0"
    assert env["TF_IN_AUTOMATION"] == "1"


def test_repair_agent_does_nothing_without_llm() -> None:
    from app.agents.runtime import terraform_engineer

    assert (
        terraform_engineer.repair(
            {"main.tf": 'resource "null_resource" "x" {}'},
            errors="terraform_validate failed",
            environment="dev",
        )
        is None
    )


def test_secret_box_roundtrip_and_tamper_detection() -> None:
    from app.core.secret_box import decrypt_secret, encrypt_secret

    secret = "sk-deepseek-abcdef123456"
    envelope = encrypt_secret(secret)
    assert secret not in envelope
    assert decrypt_secret(envelope) == secret

    tampered = envelope[:-6] + ("A" if envelope[-6] != "A" else "B") + envelope[-5:]
    try:
        decrypt_secret(tampered)
    except ValueError:
        pass
    else:  # pragma: no cover - integrity must fail
        raise AssertionError("tampered envelope was accepted")


def test_secret_box_uses_random_nonces() -> None:
    from app.core.secret_box import encrypt_secret

    assert encrypt_secret("same-value") != encrypt_secret("same-value")
