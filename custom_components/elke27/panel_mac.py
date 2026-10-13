"""Panel MAC from UDP discovery: normalize, verify on connect, fill old entries."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from elke27_lib import async_discover_host

from homeassistant.core import callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.device_registry import format_mac

from .const import (
    CONF_PANEL_MAC,
    CONF_PANEL_MAC_FILLED,
    DISCOVER_HOST_TIMEOUT,
    DOMAIN,
    ISSUE_PANEL_MAC_MISMATCH,
)
from .identity import is_placeholder_serial

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

_MAC_HEX_LEN = 12
_HEX = frozenset("0123456789ABCDEF")


def normalize_panel_mac(value: Any) -> str | None:
    """Return a MAC as 12 uppercase hex with no separators, or None if invalid."""
    if not isinstance(value, str):
        return None
    cleaned = "".join(ch for ch in value.strip() if ch not in ":-. ").upper()
    if (
        len(cleaned) != _MAC_HEX_LEN
        or not set(cleaned) <= _HEX
        or not cleaned.strip("0")
    ):
        return None
    return cleaned


def panel_mac_from_discovered(panel: Any) -> str | None:
    """Return the normalized MAC from a discovery result object or dict."""
    if panel is None:
        return None
    if isinstance(panel, dict):
        raw = panel.get("panel_mac") or panel.get("mac")
    else:
        raw = getattr(panel, "panel_mac", None) or getattr(panel, "mac", None)
    return normalize_panel_mac(raw)


async def async_discover_panel_mac(host: str) -> str | None:
    """Unicast-discover ``host`` and return its normalized MAC, or None."""
    try:
        panel = await async_discover_host(host, timeout=DISCOVER_HOST_TIMEOUT)
    except Exception as err:  # noqa: BLE001  # discovery must never break setup
        _LOGGER.debug("Unicast discovery of %s failed: %s", host, err)
        return None
    return panel_mac_from_discovered(panel)


def mismatch_issue_id(entry_id: str) -> str:
    """Return the Repairs issue id for a panel MAC mismatch."""
    return f"{ISSUE_PANEL_MAC_MISMATCH}_{entry_id}"


def duplicate_unique_id_issue_id(entry_id: str) -> str:
    """Return the Repairs issue id for a unique_id already owned elsewhere."""
    return f"duplicate_unique_id_{entry_id}"


@callback
def async_apply_discovered_mac(
    hass: HomeAssistant, entry: ConfigEntry, discovered: str | None
) -> None:
    """
    Compare a discovered MAC with the entry and fill, flag or accept it.

    - No reply: keep the stored MAC (if any); nothing changes.
    - Stored MAC differs: WARNING + Repairs issue; identity is kept.
    - No stored MAC (0.1.8 entries): fill CONF_PANEL_MAC and the entry
      unique_id in place, unless another entry already owns that unique_id
      (Repairs issue, no fill). This is a data fill, not a version migration;
      undo by removing CONF_PANEL_MAC / CONF_PANEL_MAC_FILLED and clearing the
      unique_id.
    """
    if discovered is None:
        return
    stored = normalize_panel_mac(entry.data.get(CONF_PANEL_MAC))
    host = entry.data.get("host", "")
    if stored is not None:
        issue_id = mismatch_issue_id(entry.entry_id)
        if stored == discovered:
            ir.async_delete_issue(hass, DOMAIN, issue_id)
            return
        _LOGGER.warning(
            "Panel at %s reports MAC %s but this entry is for %s; keeping the "
            "existing identity",
            host,
            format_mac(discovered),
            format_mac(stored),
        )
        ir.async_create_issue(
            hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key=ISSUE_PANEL_MAC_MISMATCH,
            translation_placeholders={
                "host": str(host),
                "stored": format_mac(stored),
                "discovered": format_mac(discovered),
            },
        )
        return

    candidate = format_mac(discovered)
    dup_issue = duplicate_unique_id_issue_id(entry.entry_id)
    existing = hass.config_entries.async_entry_for_domain_unique_id(DOMAIN, candidate)
    if existing is not None and existing.entry_id != entry.entry_id:
        _LOGGER.warning(
            "Cannot fill panel MAC %s on Elke27 entry %s: already used by %s",
            candidate,
            entry.entry_id,
            existing.entry_id,
        )
        ir.async_create_issue(
            hass,
            DOMAIN,
            dup_issue,
            is_fixable=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key="duplicate_unique_id",
            translation_placeholders={
                "candidate": candidate,
                "other_entry": existing.title or existing.entry_id,
            },
        )
        return
    data = {**entry.data, CONF_PANEL_MAC: discovered, CONF_PANEL_MAC_FILLED: True}
    if (
        entry.unique_id is None
        or entry.unique_id == candidate
        or is_placeholder_serial(entry.unique_id)
    ):
        hass.config_entries.async_update_entry(entry, data=data, unique_id=candidate)
    else:
        # A real (non-placeholder) panel serial identifies this entry; keep it.
        hass.config_entries.async_update_entry(entry, data=data)
    ir.async_delete_issue(hass, DOMAIN, dup_issue)
    _LOGGER.info("Stored panel MAC %s for Elke27 entry %s", candidate, entry.entry_id)


async def async_verify_panel_mac(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Unicast-discover the entry's host and apply the result."""
    host = entry.data.get("host")
    if not host:
        return
    discovered = await async_discover_panel_mac(str(host))
    async_apply_discovered_mac(hass, entry, discovered)
