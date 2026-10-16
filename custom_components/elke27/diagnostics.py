"""Diagnostics support for Elke27."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from datetime import date, datetime
import enum
from importlib.metadata import PackageNotFoundError, version as package_version
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from elke27_lib import redact_for_diagnostics

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.loader import async_get_integration

from .const import (
    CONF_INTEGRATION_ID,
    CONF_LINK_KEYS_JSON,
    CONF_PANEL_MAC,
    CONF_PANEL_MAC_FILLED,
    DOMAIN,
    MANUFACTURER_NUMBER,
)
from .entity import area_has_device

# Host, MAC, panel serial (unique_id may be a serial or MAC), integration ID and
# link keys never leave the box unredacted. Link keys are not emitted at all.
TO_REDACT = {
    CONF_HOST,
    CONF_PANEL_MAC,
    CONF_INTEGRATION_ID,
    CONF_LINK_KEYS_JSON,
    "unique_id",
    "serial",
    "panel_serial",
    "mac",
}

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .models import Elke27ConfigEntry


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: Elke27ConfigEntry
) -> dict[str, Any]:
    """
    Return diagnostics for a config entry.

    Reads coordinator and supervisor properties only; never calls the client.
    An entry that is not loaded (e.g. setup_error after an upgrade) has no
    runtime data, so only the redacted entry fields are returned.
    """
    integration = await async_get_integration(hass, DOMAIN)
    data: dict[str, Any] = {
        "entry_id": entry.entry_id,
        "entry_state": entry.state.value,
        "unique_id": entry.unique_id,
        CONF_PANEL_MAC: entry.data.get(CONF_PANEL_MAC),
        "panel_mac_filled": entry.data.get(CONF_PANEL_MAC_FILLED, False),
        CONF_HOST: entry.data.get(CONF_HOST),
        "port": entry.data.get(CONF_PORT),
        CONF_INTEGRATION_ID: entry.data.get(CONF_INTEGRATION_ID),
        "manufacturer_number": MANUFACTURER_NUMBER,
        "link_keys_present": CONF_LINK_KEYS_JSON in entry.data,
        "integration_version": str(integration.version),
        "elke27_version": _elke27_version(),
    }
    if entry.state is not ConfigEntryState.LOADED:
        return async_redact_data(data, TO_REDACT)

    coordinator = entry.runtime_data.coordinator
    supervisor = coordinator.reconnect
    snapshot = coordinator.data
    snapshot_dict = _to_jsonable(snapshot)
    redacted_snapshot = redact_for_diagnostics(snapshot_dict)

    snapshot_meta = _to_jsonable(
        {
            "version": snapshot.version,
            "updated_at": snapshot.updated_at,
        }
    )
    data |= {
        # A client object exists (connect done, not torn down). This is not
        # link state; client_ready is the library's ready signal for that.
        "client_present": coordinator.client is not None,
        "client_ready": coordinator.is_ready,
        "last_update_success": coordinator.last_update_success,
        "reconnect_attempts": supervisor.attempts,
        "repairs_issue_active": supervisor.issue_active,
        "reauth_requested": supervisor.reauth_requested,
        "snapshot_stale": coordinator.snapshot_stale,
        "stale_area_ids": sorted(coordinator.stale_area_ids),
        "stale_zone_ids": sorted(coordinator.stale_zone_ids),
        "fallback_refresh_count": coordinator.fallback_refresh_count,
        "last_fallback_at": _to_jsonable(coordinator.last_fallback_at),
        # Soak aid: Architect default, pending Mitch's OK.
        "stale_auto_refresh_count": coordinator.stale_auto_refresh_count,
        "area_device_count": sum(
            1 for area_id in snapshot.areas if area_has_device(snapshot, area_id)
        ),
        "snapshot_meta": snapshot_meta,
        "snapshot": redacted_snapshot,
    }
    return async_redact_data(data, TO_REDACT)


def _elke27_version() -> str | None:
    """Return the installed elke27 library version."""
    try:
        return package_version("elke27")
    except PackageNotFoundError:
        return None


def _to_jsonable(value: Any) -> Any:
    """Normalize snapshots to JSON-safe types."""
    if value is None:
        return None
    if is_dataclass(value):
        return {
            field.name: _to_jsonable(getattr(value, field.name))
            for field in fields(value)
        }
    if isinstance(value, MappingProxyType):
        return {str(key): _to_jsonable(val) for key, val in dict(value).items()}
    if isinstance(value, Mapping):
        return {str(key): _to_jsonable(val) for key, val in value.items()}
    if isinstance(value, list | tuple):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, set | frozenset):
        return sorted([_to_jsonable(item) for item in value], key=str)
    if isinstance(value, bytes | bytearray):
        return value.hex()
    if isinstance(value, enum.Enum):
        return value.name
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)
