"""Set up the Elke27 integration."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.const import CONF_HOST, Platform
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv, issue_registry as ir

from .const import CONF_INTEGRATION_SERIAL, DOMAIN, ISSUE_RECONNECT_FAILED
from .coordinator import (  # noqa: F401  # PRIMED_DOMAINS/SETUP_RETRY_ERRORS re-exported
    PRIMED_DOMAINS,
    SETUP_RETRY_ERRORS,
    Elke27DataUpdateCoordinator,
)
from .identity import async_get_integration_serial
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
    host = entry.data[CONF_HOST]
    integration_serial = entry.data.get(CONF_INTEGRATION_SERIAL)
    entry_data = dict(entry.data)
    pin_removed = entry_data.pop("pin", None)
    if not integration_serial:
        integration_serial = await async_get_integration_serial(hass, host)
        entry_data[CONF_INTEGRATION_SERIAL] = integration_serial
        hass.config_entries.async_update_entry(entry, data=entry_data)
    elif pin_removed is not None:
        hass.config_entries.async_update_entry(entry, data=entry_data)
    coordinator = Elke27DataUpdateCoordinator(hass, entry)
    # Runs on unload, and when setup fails after this point.
    entry.async_on_unload(coordinator.async_shutdown_client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = Elke27RuntimeData(coordinator=coordinator)
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
