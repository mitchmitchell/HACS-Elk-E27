"""Shared entity helpers for the Elke27 integration."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import (
    CONNECTION_NETWORK_MAC,
    DeviceInfo,
    format_mac,
)

from .const import DOMAIN
from .hub import NOT_ACCEPTED_MESSAGE
from .identity import panel_serial_from_info

if TYPE_CHECKING:
    from elke27_lib import PanelSnapshot

    from .coordinator import Elke27DataUpdateCoordinator
    from .hub import Elke27Hub
    from .models import Elke27ConfigEntry

_NAME_SAFE_RE = re.compile(r"[^A-Za-z0-9 _-]")


def sanitize_name(name: str | None) -> str | None:
    """Normalize entity names to Home Assistant-safe characters."""
    if name is None:
        return None
    return name


_PANEL_FIELDS = {
    "name": "panel_name",
    "mac": "mac",
    "serial": "serial",
    "model": "model",
    "firmware": "firmware",
}


def get_panel_field(
    snapshot: PanelSnapshot | None, panel_name: str | None, field: str
) -> Any:
    """Return a field from the current panel snapshot."""
    if field == "name" and panel_name:
        return sanitize_name(panel_name)
    if snapshot is None:
        return None
    value = getattr(snapshot.panel, _PANEL_FIELDS[field])
    return sanitize_name(value) if field == "name" else value


def device_info_for_entry(
    hub: Elke27Hub,
    coordinator: Elke27DataUpdateCoordinator,
    entry: Elke27ConfigEntry,
) -> DeviceInfo:
    """Build device info for entities tied to a config entry."""
    snapshot = coordinator.data
    panel_name = get_panel_field(snapshot, hub.panel_name, "name") or entry.title
    mac = get_panel_field(snapshot, hub.panel_name, "mac")
    panel_serial = get_panel_field(snapshot, hub.panel_name, "serial")
    model = get_panel_field(snapshot, hub.panel_name, "model")
    firmware = get_panel_field(snapshot, hub.panel_name, "firmware")
    identifiers = {(DOMAIN, entry_identity_key(hub, coordinator, entry))}
    return DeviceInfo(
        connections={(CONNECTION_NETWORK_MAC, mac)} if mac else set(),
        identifiers=identifiers,
        name=panel_name,
        model=model,
        sw_version=firmware,
        serial_number=panel_serial,
    )


def entry_identity_key(
    hub: Elke27Hub,
    coordinator: Elke27DataUpdateCoordinator,
    entry: Elke27ConfigEntry,
) -> str:
    """Return identity: formatted MAC, panel serial, or entry id if both absent."""
    snapshot = coordinator.data
    mac = get_panel_field(snapshot, hub.panel_name, "mac")
    if mac:
        return format_mac(str(mac))
    serial = get_panel_field(snapshot, hub.panel_name, "serial")
    if serial is not None:
        panel_serial = panel_serial_from_info({"serial": serial})
        if panel_serial:
            return panel_serial
    return entry.entry_id


def unique_base(
    hub: Elke27Hub,
    coordinator: Elke27DataUpdateCoordinator,
    entry: Elke27ConfigEntry,
) -> str:
    """Return the stable unique ID base for this config entry."""
    return entry_identity_key(hub, coordinator, entry)


def build_unique_id(base: str, domain: str, numeric_id: int | str) -> str:
    """Build a stable unique ID in <mac>:<domain>:<id> format."""
    return f"{base}:{domain}:{numeric_id}"


NOT_CONNECTED_MESSAGE = "The panel is not connected; the command was not sent."


def raise_if_not_sent(*, sent: bool, hub: Elke27Hub) -> None:
    """
    Raise when the hub reports a command did not go through.

    "Not connected" only when there is no client; otherwise the panel answered
    without accepting it.
    """
    if sent:
        return
    if hub.client is None:
        raise HomeAssistantError(NOT_CONNECTED_MESSAGE)
    raise HomeAssistantError(NOT_ACCEPTED_MESSAGE)
