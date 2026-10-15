"""Set up the Elke27 integration."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.const import Platform
from homeassistant.core import callback
from homeassistant.exceptions import ConfigEntryError
from homeassistant.helpers import (
    config_validation as cv,
    device_registry as dr,
    issue_registry as ir,
)

from .const import CONF_INTEGRATION_ID, DOMAIN, ISSUE_RECONNECT_FAILED
from .coordinator import (  # noqa: F401  # PRIMED_DOMAINS/SETUP_RETRY_ERRORS re-exported
    PRIMED_DOMAINS,
    SETUP_RETRY_ERRORS,
    Elke27DataUpdateCoordinator,
)
from .entity import (
    area_has_device,
    area_id_from_device_identifier,
    device_info_for_entry,
    entry_identity_key,
)
from .identity import is_valid_integration_id
from .models import Elke27ConfigEntry, Elke27RuntimeData
from .panel_mac import mismatch_issue_id
from .services import (  # noqa: F401  # re-exported; tests and callers import these from the package
    ATTR_AREA_ID,
    EVENT_ARM_AUTOMATIC_FAILED,
    SERVICE_ALARM_ARM_AUTOMATIC,
    SERVICE_ALARM_ARM_AUTOMATIC_SCHEMA,
    SERVICE_ZONE_BYPASS,
    SERVICE_ZONE_BYPASS_SCHEMA,
    _area_id_from_unique_id,
    _async_handle_alarm_arm_automatic,
    _async_handle_zone_bypass,
    _numeric_id_from_unique_id,
    _service_mode_to_arm_mode,
)

if TYPE_CHECKING:
    from elke27_lib import PanelSnapshot

    from homeassistant.core import HomeAssistant, ServiceCall
    from homeassistant.helpers.typing import ConfigType

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


PLATFORMS: list[Platform] = [
    Platform.ALARM_CONTROL_PANEL,
    Platform.BINARY_SENSOR,
    Platform.CLIMATE,
    Platform.LIGHT,
    Platform.LOCK,
    Platform.SENSOR,
    Platform.SWITCH,
]


async def async_setup(hass: HomeAssistant, _config: ConfigType) -> bool:
    """Set up the Elke27 domain services."""
    if not hass.services.has_service(DOMAIN, SERVICE_ALARM_ARM_AUTOMATIC):

        async def _handle_alarm_arm_automatic(call: ServiceCall) -> None:
            await _async_handle_alarm_arm_automatic(hass, call)

        hass.services.async_register(
            DOMAIN,
            SERVICE_ALARM_ARM_AUTOMATIC,
            _handle_alarm_arm_automatic,
            schema=SERVICE_ALARM_ARM_AUTOMATIC_SCHEMA,
        )
    if not hass.services.has_service(DOMAIN, SERVICE_ZONE_BYPASS):

        async def _handle_zone_bypass(call: ServiceCall) -> None:
            await _async_handle_zone_bypass(hass, call)

        hass.services.async_register(
            DOMAIN,
            SERVICE_ZONE_BYPASS,
            _handle_zone_bypass,
            schema=SERVICE_ZONE_BYPASS_SCHEMA,
        )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: Elke27ConfigEntry) -> bool:
    """Set up Elke27 from a config entry."""
    # E1: the client sn is the flow-generated integration ID. Entries made
    # before E1 have none (the panel bound their link to the old sn), so they
    # must be deleted and added again; there is no migration or backfill.
    if not is_valid_integration_id(entry.data.get(CONF_INTEGRATION_ID)):
        raise ConfigEntryError(
            translation_domain=DOMAIN, translation_key="integration_id_invalid"
        )
    coordinator = Elke27DataUpdateCoordinator(hass, entry)
    # Runs on unload, and when setup fails after this point.
    entry.async_on_unload(coordinator.async_shutdown_client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = Elke27RuntimeData(coordinator=coordinator)
    # E2: register the panel device first so area devices can link to it.
    # Area entities link to it by device id (via_device_id) where HA supports it.
    panel_device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, **device_info_for_entry(coordinator, entry)
    )
    coordinator.panel_device_id = panel_device.id
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: Elke27ConfigEntry) -> bool:
    """Unload an Elke27 config entry."""
    # The coordinator and client are stopped by the async_on_unload callbacks
    # registered in setup, which Home Assistant runs only when this succeeds.
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: Elke27ConfigEntry) -> None:
    """Clean up integration state when a config entry is removed."""
    _async_delete_entry_issues(hass, entry)


def _entry_area_ids(
    entry: Elke27ConfigEntry,
    coordinator: Elke27DataUpdateCoordinator,
    device_entry: dr.DeviceEntry,
) -> set[int]:
    """Return the area numbers this device stands for in this entry."""
    key = entry_identity_key(coordinator, entry)
    return {
        area_id
        for domain, identifier in device_entry.identifiers
        if domain == DOMAIN
        and (area_id := area_id_from_device_identifier(key, identifier)) is not None
    }


def _full_snapshot(snapshot: PanelSnapshot | None) -> bool:
    """Return True when the snapshot can be trusted to list every area."""
    return snapshot is not None and not snapshot.stale and bool(snapshot.areas)


async def async_remove_config_entry_device(
    hass: HomeAssistant,  # noqa: ARG001  # Home Assistant signature
    entry: Elke27ConfigEntry,
    device_entry: dr.DeviceEntry,
) -> bool:
    """Allow deleting an area device the panel no longer reports (E2)."""
    coordinator = entry.runtime_data.coordinator
    area_ids = _entry_area_ids(entry, coordinator, device_entry)
    snapshot = coordinator.data
    if not area_ids or not _full_snapshot(snapshot):
        return False
    return not any(area_has_device(snapshot, area_id) for area_id in area_ids)


def _duplicate_unique_id_issue_id(entry_id: str) -> str:
    return f"duplicate_unique_id_{entry_id}"


def _reconnect_failed_issue_id(entry_id: str) -> str:
    return f"{ISSUE_RECONNECT_FAILED}_{entry_id}"


@callback
def _async_delete_entry_issues(hass: HomeAssistant, entry: Elke27ConfigEntry) -> None:
    """Remove repairs issues scoped to one config entry."""
    ir.async_delete_issue(hass, DOMAIN, _duplicate_unique_id_issue_id(entry.entry_id))
    ir.async_delete_issue(hass, DOMAIN, _reconnect_failed_issue_id(entry.entry_id))
    ir.async_delete_issue(hass, DOMAIN, mismatch_issue_id(entry.entry_id))
