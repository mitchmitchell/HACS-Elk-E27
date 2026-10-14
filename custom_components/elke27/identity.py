"""Identity helpers for the Elke27 integration."""

from __future__ import annotations

import secrets
from typing import Any, TypeGuard

from homeassistant.helpers.device_registry import format_mac

from .const import INTEGRATION_ID_LENGTH, MANUFACTURER_NUMBER


def panel_mac_from_info(panel_info: dict[str, Any]) -> str | None:
    """Return MAC from panel_info dict keys used by the config flow."""
    return panel_info.get("mac") or panel_info.get("panel_mac")


def panel_serial_from_info(panel_info: dict[str, Any]) -> str | None:
    """Return the panel hardware serial from panel_info dict keys."""
    serial = panel_info.get("serial") or panel_info.get("panel_serial")
    if serial is None:
        return None
    value = str(serial).strip()
    return value or None


def is_placeholder_serial(value: str | None) -> bool:
    """
    Return True for serials that are not unique per panel (e.g. 00000001).

    Mirrors elke27_lib's rule: empty, all zeros, all F, or a value of 0/1 with
    any zero padding.
    """
    text = (value or "").strip().upper()
    if not text or set(text) <= {"0"} or set(text) <= {"F"}:
        return True
    return text.lstrip("0") == "1"


def config_entry_unique_id(panel_info: dict[str, Any]) -> str | None:
    """Return config entry unique_id: MAC, panel serial, or None (dedupe host:port)."""
    mac = panel_mac_from_info(panel_info)
    if mac:
        return format_mac(str(mac))
    return panel_serial_tier(panel_info)


def panel_serial_tier(panel_info: dict[str, Any]) -> str | None:
    """Panel serial identity tier (drop this helper to remove the serial tier)."""
    return panel_serial_from_info(panel_info)


def panel_identity_matches(
    live_panel_info: dict[str, Any],
    stored_panel_info: dict[str, Any],
) -> bool:
    """
    Return True when comparable panel identity fields agree.

    Only fields present on both snapshots are compared (for example, stored
    panel_info without a MAC does not fail when the live panel reports one).
    """
    live_mac = panel_mac_from_info(live_panel_info)
    stored_mac = panel_mac_from_info(stored_panel_info)
    if (
        live_mac
        and stored_mac
        and format_mac(str(live_mac)) != format_mac(str(stored_mac))
    ):
        return False
    live_serial = panel_serial_tier(live_panel_info)
    stored_serial = panel_serial_tier(stored_panel_info)
    return not (live_serial and stored_serial and live_serial != stored_serial)


def generate_integration_id() -> str:
    """Return a new random integration ID: 12 decimal digits, no leading zero."""
    return str(secrets.randbelow(9 * 10**11) + 10**11)


def is_valid_integration_id(value: object) -> TypeGuard[str]:
    """Return True when value is exactly 12 ASCII digits with no leading zero."""
    return (
        isinstance(value, str)
        and len(value) == INTEGRATION_ID_LENGTH
        and value.isascii()
        and value.isdigit()
        and value[0] != "0"
    )


def build_client_identity(integration_id: str) -> dict[str, str]:
    """Return the client identity mapping for provisioning and session setup."""
    return {
        "mn": str(MANUFACTURER_NUMBER),
        "sn": integration_id,
    }
