"""Shared entity helpers for the Elke27 integration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.const import CONF_HOST
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import (
    CONNECTION_NETWORK_MAC,
    DeviceInfo,
    format_mac,
)

from .const import CONF_INTEGRATION_SERIAL, DOMAIN, MANUFACTURER_NUMBER
from .hub import NOT_ACCEPTED_MESSAGE

if TYPE_CHECKING:
    from elke27_lib import PanelSnapshot

    from .coordinator import Elke27DataUpdateCoordinator
    from .hub import Elke27Hub
    from .models import Elke27ConfigEntry


def device_info_for_entry(
    hub: Elke27Hub,
    coordinator: Elke27DataUpdateCoordinator,
    entry: Elke27ConfigEntry,
) -> DeviceInfo:
    """Build device info for entities tied to a config entry."""
    snapshot = coordinator.data
    panel = snapshot.panel
    panel_name = hub.panel_name or panel.panel_name or entry.title
    integration_serial = entry.data.get(CONF_INTEGRATION_SERIAL)
    identifier = (
        f"{MANUFACTURER_NUMBER}-{integration_serial}"
        if integration_serial
        else entry.entry_id
    )
    identifiers = {(DOMAIN, identifier)}
    return DeviceInfo(
        connections={(CONNECTION_NETWORK_MAC, panel.mac)} if panel.mac else set(),
        identifiers=identifiers,
        name=panel_name,
        model=panel.model,
        sw_version=panel.firmware,
        serial_number=panel.serial,
    )


def unique_base(
    _hub: Elke27Hub,
    coordinator: Elke27DataUpdateCoordinator,
    entry: Elke27ConfigEntry,
) -> str:
    """Return the stable unique ID base for this config entry."""
    mac = coordinator.data.panel.mac
    if mac:
        return format_mac(str(mac))
    integration_serial = entry.data.get(CONF_INTEGRATION_SERIAL)
    if integration_serial:
        return str(integration_serial)
    if entry.unique_id:
        return entry.unique_id
    return str(entry.data[CONF_HOST])


def build_unique_id(base: str, domain: str, numeric_id: int | str) -> str:
    """Build a stable unique ID in <mac>:<domain>:<id> format."""
    return f"{base}:{domain}:{numeric_id}"


def panel_display_name(
    snapshot: PanelSnapshot, hub: Elke27Hub, entry: Elke27ConfigEntry
) -> str | None:
    """Return the panel name from the hub, snapshot, or entry title."""
    if hub.panel_name:
        return hub.panel_name
    panel_name = snapshot.panel.panel_name
    # Temporary until elke27_lib ships py.typed (strict mypy treats fields as Any).
    if isinstance(panel_name, str) and panel_name:
        return panel_name
    return entry.title


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
