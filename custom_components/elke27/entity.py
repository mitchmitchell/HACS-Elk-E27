"""Shared entity helpers for the Elke27 integration."""

from __future__ import annotations

import inspect
import re
from typing import TYPE_CHECKING, Any, cast

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import (
    CONNECTION_NETWORK_MAC,
    DeviceInfo,
    format_mac,
)

from .const import CONF_PANEL_MAC, CONF_PANEL_MAC_FILLED, DOMAIN
from .errors import NOT_ACCEPTED_MESSAGE
from .identity import panel_serial_from_info

if TYPE_CHECKING:
    from elke27_lib import AreaState, PanelSnapshot, ZoneState

    from .coordinator import Elke27DataUpdateCoordinator
    from .models import Elke27ConfigEntry

# HA 2026.8+ accepts via_device_id; the pinned HA only has the identifier form.
SUPPORTS_VIA_DEVICE_ID = (
    "via_device_id"
    in inspect.signature(dr.DeviceRegistry.async_get_or_create).parameters
)

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


def stored_panel_mac(entry: Elke27ConfigEntry) -> str | None:
    """Return the stored discovery MAC (12 uppercase hex) or None."""
    value = entry.data.get(CONF_PANEL_MAC)
    return value if isinstance(value, str) and value else None


def device_info_for_entry(
    coordinator: Elke27DataUpdateCoordinator,
    entry: Elke27ConfigEntry,
) -> DeviceInfo:
    """Build device info for entities tied to a config entry."""
    snapshot = coordinator.data
    panel_name = (
        get_panel_field(snapshot, coordinator.panel_name, "name") or entry.title
    )
    mac = get_panel_field(snapshot, coordinator.panel_name, "mac")
    if not mac and (stored_mac := stored_panel_mac(entry)):
        mac = format_mac(stored_mac)
    panel_serial = get_panel_field(snapshot, coordinator.panel_name, "serial")
    model = get_panel_field(snapshot, coordinator.panel_name, "model")
    firmware = get_panel_field(snapshot, coordinator.panel_name, "firmware")
    identifiers = {(DOMAIN, entry_identity_key(coordinator, entry))}
    return DeviceInfo(
        connections={(CONNECTION_NETWORK_MAC, mac)} if mac else set(),
        identifiers=identifiers,
        name=panel_name,
        model=model,
        sw_version=firmware,
        serial_number=panel_serial,
    )


AREA_DEVICE_MODEL = "Area"


def area_device_identifier(key: str, area_id: int) -> str:
    """Return the area device identifier, same shape as the area unique_id."""
    return f"{key}:area:{area_id}"


def area_id_from_device_identifier(key: str, identifier: str) -> int | None:
    """Return N for an identifier `<key>:area:<N>` of this entry, else None."""
    prefix = f"{key}:area:"
    if not identifier.startswith(prefix):
        return None
    suffix = identifier[len(prefix) :]
    if not suffix.isascii() or not suffix.isdecimal():
        return None
    return int(suffix)


def area_has_device(snapshot: PanelSnapshot | None, area_id: int | None) -> bool:
    """
    Return True when this area gets its own device.

    E2 Q2 (Architect default, pending Mitch's OK): every area the library
    reports in snapshot.areas gets a device, with no zone-count filter. Change
    this one function to filter areas differently.
    """
    if snapshot is None or area_id is None or area_id == 0:
        return False
    return area_id in snapshot.areas


def area_device_name(area_id: int, area: AreaState | None) -> str:
    """Return the area device name: the panel's area name or "Area N"."""
    name = sanitize_name(area.name) if area is not None else None
    return name or f"Area {area_id}"


def zone_entity_name(zone_name: str, area_name: str | None) -> str:
    """
    Return the zone entity name shown under its device.

    E2 Q3 (Architect default, pending Mitch's OK): keep the panel's zone name
    verbatim; a leading area name is not stripped ("Garage Garage door").
    Change this one function to strip it.
    """
    del area_name
    return zone_name


def area_device_info(
    coordinator: Elke27DataUpdateCoordinator,
    entry: Elke27ConfigEntry,
    area_id: int,
    area: AreaState | None,
) -> DeviceInfo:
    """
    Build device info for an area device; entity registration creates it.

    The panel device is registered once in async_setup_entry and its id kept on
    the coordinator. Newer Home Assistant gets a ChildDeviceInfo (the area is a
    logical part of the panel), else DeviceInfo with via_device_id. The pinned
    Home Assistant has neither, so it gets the identifier form of via_device
    (deprecated, removed in HA 2027.8, but the only option there).
    """
    key = entry_identity_key(coordinator, entry)
    identifiers = {(DOMAIN, area_device_identifier(key, area_id))}
    name = area_device_name(area_id, area)
    # Set in async_setup_entry before the platforms load.
    panel_device_id = cast("str", coordinator.panel_device_id)
    child_info = getattr(dr, "ChildDeviceInfo", None)
    if child_info is not None:
        return child_info(  # type: ignore[no-any-return]
            identifiers=identifiers, name=name, parent_device_id=panel_device_id
        )
    if SUPPORTS_VIA_DEVICE_ID:
        return DeviceInfo(  # type: ignore[typeddict-unknown-key]
            identifiers=identifiers,
            name=name,
            model=AREA_DEVICE_MODEL,
            via_device_id=panel_device_id,
        )
    return DeviceInfo(
        identifiers=identifiers,
        name=name,
        model=AREA_DEVICE_MODEL,
        via_device=(DOMAIN, key),
    )


def device_info_for_zone(
    coordinator: Elke27DataUpdateCoordinator,
    entry: Elke27ConfigEntry,
    zone: ZoneState,
) -> DeviceInfo:
    """Return the zone's area device, or the panel device when it has none."""
    snapshot = coordinator.data
    area_id = zone.area_id
    if (
        snapshot is not None
        and area_id is not None
        and area_has_device(snapshot, area_id)
    ):
        return area_device_info(
            coordinator, entry, area_id, snapshot.areas.get(area_id)
        )
    return device_info_for_entry(coordinator, entry)


def entry_identity_key(
    coordinator: Elke27DataUpdateCoordinator,
    entry: Elke27ConfigEntry,
) -> str:
    """
    Return identity: formatted MAC, panel serial, or entry id if both absent.

    Tier 1 is the discovery MAC stored at entry creation (CONF_PANEL_MAC). A MAC
    filled in later (CONF_PANEL_MAC_FILLED) is ignored here so an existing
    entry's entity and device identity never changes in place.
    """
    stored_mac = stored_panel_mac(entry)
    if stored_mac and not entry.data.get(CONF_PANEL_MAC_FILLED):
        return format_mac(stored_mac)
    snapshot = coordinator.data
    mac = get_panel_field(snapshot, coordinator.panel_name, "mac")
    if mac:
        return format_mac(str(mac))
    serial = get_panel_field(snapshot, coordinator.panel_name, "serial")
    if serial is not None:
        panel_serial = panel_serial_from_info({"serial": serial})
        if panel_serial:
            return panel_serial
    return entry.entry_id


def unique_base(
    coordinator: Elke27DataUpdateCoordinator,
    entry: Elke27ConfigEntry,
) -> str:
    """Return the stable unique ID base for this config entry."""
    return entry_identity_key(coordinator, entry)


def build_unique_id(base: str, numeric_id: int | str) -> str:
    """
    Build a stable unique ID in <base>:<id> format (0.2.0, ADR-0126 decision).

    No platform segment: HA scopes unique IDs per entity platform, and each
    platform here has one kind of entity. Device identifiers keep their own
    `<key>:area:<N>` shape.
    """
    return f"{base}:{numeric_id}"


def panel_display_name(
    snapshot: PanelSnapshot,
    coordinator: Elke27DataUpdateCoordinator,
    entry: Elke27ConfigEntry,
) -> str | None:
    """Return the panel name from the coordinator, snapshot, or entry title."""
    return get_panel_field(snapshot, coordinator.panel_name, "name") or entry.title


NOT_CONNECTED_MESSAGE = "The panel is not connected; the command was not sent."


def raise_if_not_sent(*, sent: bool, coordinator: Elke27DataUpdateCoordinator) -> None:
    """
    Raise when the coordinator reports a command did not go through.

    "Not connected" only when there is no client; otherwise the panel answered
    without accepting it.
    """
    if sent:
        return
    if coordinator.client is None:
        raise HomeAssistantError(NOT_CONNECTED_MESSAGE)
    raise HomeAssistantError(NOT_ACCEPTED_MESSAGE)
