"""Authenticated secret envelope for at-rest configuration secrets.

Why not ``cryptography``? Its wheels are unavailable on some Windows ARM64 Python
builds, and this project must stay portable. This module implements an
encrypt-then-MAC construction from the standard library:

    keystream_i = HMAC-SHA256(enc_key, nonce || counter_i)
    ciphertext  = plaintext XOR keystream
    tag         = HMAC-SHA256(mac_key, "ta1" || nonce || ciphertext)

Keys are derived from ``SECRET_KEY`` with domain separation. The tag is verified in
constant time before decryption. Rotating ``SECRET_KEY`` intentionally makes old
values unreadable, so store LLM keys in Azure Key Vault in production if key
rotation is required.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os

from app.config import get_settings

PREFIX = "ta1"
NONCE_BYTES = 16
TAG_BYTES = 32


def _subkey(label: bytes) -> bytes:
    secret = get_settings().secret_key.encode("utf-8")
    return hmac.new(secret, b"terraformagent:secret-box:" + label, hashlib.sha256).digest()


def _keystream(key: bytes, nonce: bytes, length: int) -> bytes:
    blocks = []
    counter = 0
    while sum(len(block) for block in blocks) < length:
        blocks.append(
            hmac.new(key, nonce + counter.to_bytes(4, "big"), hashlib.sha256).digest()
        )
        counter += 1
    return b"".join(blocks)[:length]


def encrypt_secret(plaintext: str) -> str:
    if plaintext is None:
        raise ValueError("plaintext is required")
    data = plaintext.encode("utf-8")
    nonce = os.urandom(NONCE_BYTES)
    key = _subkey(b"enc")
    stream = _keystream(key, nonce, len(data))
    ciphertext = bytes(a ^ b for a, b in zip(data, stream))
    tag = hmac.new(_subkey(b"mac"), PREFIX.encode() + nonce + ciphertext, hashlib.sha256).digest()
    payload = PREFIX.encode() + b"." + nonce + ciphertext + tag
    return base64.urlsafe_b64encode(payload).decode("ascii")


def decrypt_secret(envelope: str) -> str:
    try:
        raw = base64.urlsafe_b64decode(envelope.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise ValueError("Invalid secret envelope") from exc
    if not raw.startswith(PREFIX.encode() + b"."):
        raise ValueError("Unsupported secret envelope version")
    body = raw[len(PREFIX) + 1 :]
    if len(body) < NONCE_BYTES + TAG_BYTES:
        raise ValueError("Invalid secret envelope length")
    nonce = body[:NONCE_BYTES]
    ciphertext = body[NONCE_BYTES:-TAG_BYTES]
    tag = body[-TAG_BYTES:]
    expected = hmac.new(
        _subkey(b"mac"), PREFIX.encode() + nonce + ciphertext, hashlib.sha256
    ).digest()
    if not hmac.compare_digest(tag, expected):
        raise ValueError("Secret envelope failed integrity verification")
    stream = _keystream(_subkey(b"enc"), nonce, len(ciphertext))
    plaintext = bytes(a ^ b for a, b in zip(ciphertext, stream))
    return plaintext.decode("utf-8")


def last_four(value: str) -> str:
    cleaned = value.strip()
    return cleaned[-4:] if len(cleaned) >= 4 else "••••"
