"""Redaction helpers for diagnostics-safe logging."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, is_dataclass
from enum import Enum
from typing import Any

from .const import REDACT_DIAGNOSTICS

_SECRET_KEYS = {
    "passphrase",
    "access_code",
    "accesscode",
    "pin",
    "linkkey",
    "link_keys",
    "linkkey_hex",
    "linkhmac",
    "linkhmac_hex",
    "tempkey",
    "tempkey_hex",
    "session_key",
    "session_keys",
    "token",
    "hmac",
    "secret",
    "key",
    "keys",
}


def _should_redact(key: str) -> bool:
    key_l = key.lower()
    if key_l in _SECRET_KEYS:
        return True
    if "pass" in key_l or "token" in key_l or "secret" in key_l:
        return True
    return bool("key" in key_l and "monkey" not in key_l)


def _normalize_mapping(data: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in data.items():
        out[str(key)] = _normalize_for_diagnostics(value)
    return out


def _redact_mapping(data: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in data.items():
        key_str = str(key)
        if _should_redact(key_str):
            out[key_str] = "***"
        else:
            out[key_str] = redact_for_diagnostics(value)
    return out


def _normalize_for_diagnostics(obj: Any) -> Any:
    if obj is None:
        return None
    if is_dataclass(obj):
        return _normalize_mapping(asdict(obj))
    if isinstance(obj, Mapping):
        return _normalize_mapping(obj)
    if isinstance(obj, (list, tuple, set)):
        return [_normalize_for_diagnostics(item) for item in obj]
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, (bytes, bytearray)):
        return f"<bytes:{len(obj)}>"
    if isinstance(obj, (int, float, str, bool)):
        return obj
    return repr(obj)


def redact_for_diagnostics(obj: Any) -> Any:
    """
    Return a JSON-serializable structure with likely secrets redacted.

    This is intentionally conservative and may redact additional fields to
    ensure diagnostics are safe for logs.
    """

    if not REDACT_DIAGNOSTICS:
        return _normalize_for_diagnostics(obj)
    if obj is None:
        return None
    if is_dataclass(obj):
        return _redact_mapping(asdict(obj))
    if isinstance(obj, Mapping):
        return _redact_mapping(obj)
    if isinstance(obj, (list, tuple, set)):
        return [redact_for_diagnostics(item) for item in obj]
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, (bytes, bytearray)):
        return f"<bytes:{len(obj)}>"
    if isinstance(obj, (int, float, str, bool)):
        return obj
    return repr(obj)
