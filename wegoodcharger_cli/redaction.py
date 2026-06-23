from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


REDACTED = "[REDACTED]"
SENSITIVE_KEY_PARTS = (
    "authorization",
    "bearer",
    "password",
    "passwd",
    "secret",
    "token",
    "access_token",
    "refresh_token",
)


def is_sensitive_key(key: object) -> bool:
    text = str(key).lower()
    return any(part in text for part in SENSITIVE_KEY_PARTS)


def redact(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            key: REDACTED if is_sensitive_key(key) else redact(item)
            for key, item in value.items()
        }

    if isinstance(value, tuple):
        return tuple(redact(item) for item in value)

    if isinstance(value, list):
        return [redact(item) for item in value]

    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [redact(item) for item in value]

    return value
