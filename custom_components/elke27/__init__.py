"""Set up the Elke27 integration."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from elke27_lib.errors import E27Error, Elke27Error

from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import callback
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
    HomeAssistantError,
)
from homeassistant.helpers import config_validation as cv, issue_registry as ir

from .const import (
    CONF_INTEGRATION_SERIAL,
    CONF_LINK_KEYS_JSON,
    CONF_PANEL,
    DOMAIN,
    ISSUE_RECONNECT_FAILED,
)
from .coordinator import Elke27DataUpdateCoordinator
from .errors import AUTH_ERRORS
from .hub import Elke27Hub
from .identity import async_get_integration_serial
from .models import Elke27ConfigEntry, Elke27RuntimeData
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


# Errors that mean the stored link is no longer accepted: start reauth.
# Any other library or network failure during setup: let Home Assistant retry.
SETUP_RETRY_ERRORS: tuple[type[Exception], ...] = (
    Elke27Error,
    E27Error,
    TimeoutError,
    OSError,
    ConfigEntryNotReady,
    HomeAssistantError,
)
PRIMED_DOMAINS = ("light", "lock", "tstat")

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
    # TEST-ONLY (E1-8): probe whether the panel accepts a given client sn.
    if not hass.services.has_service(DOMAIN, "debug_probe_client_sn"):
        import voluptuous as vol
        from elke27_lib import ClientConfig, Elke27Client, LinkKeys
        from homeassistant.core import SupportsResponse

        from .identity import build_client_identity

        async def _probe(call: ServiceCall) -> dict:
            entry = hass.config_entries.async_get_entry(call.data["entry_id"])
            if entry is None:
                return {"ok": False, "stage": "entry", "error": "entry not found"}
            sn = call.data["sn"]
            client = Elke27Client(ClientConfig())
            stage = "identity"
            try:
                client.set_client_identity(build_client_identity(sn))
                stage = "connect"
                await client.async_connect(
                    entry.data[CONF_HOST],
                    entry.data.get(CONF_PORT, 2101),
                    LinkKeys.from_json(entry.data[CONF_LINK_KEYS_JSON]),
                )
                stage = "ready"
                ready = await client.wait_ready(timeout_s=20)
                return {"ok": bool(ready), "stage": stage, "sn": sn, "sn_len": len(sn)}
            except Exception as err:  # noqa: BLE001
                return {"ok": False, "stage": stage, "sn": sn, "sn_len": len(sn),
                        "error": f"{type(err).__name__}: {err}"}
            finally:
                try:
                    await client.async_disconnect()
                except Exception:  # noqa: BLE001
                    pass

        hass.services.async_register(
            DOMAIN,
            "debug_probe_client_sn",
            _probe,
            schema=vol.Schema({vol.Required("entry_id"): str, vol.Required("sn"): str}),
            supports_response=SupportsResponse.ONLY,
        )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: Elke27ConfigEntry) -> bool:
    """Set up Elke27 from a config entry."""
    host = entry.data[CONF_HOST]
    port = entry.data[CONF_PORT]
    link_keys_json = entry.data.get(CONF_LINK_KEYS_JSON)
    panel_name = _panel_name_from_entry(entry.data.get(CONF_PANEL))
    if not link_keys_json:
        raise ConfigEntryAuthFailed(
            translation_domain=DOMAIN, translation_key="auth_failed"
        )
    integration_serial = entry.data.get(CONF_INTEGRATION_SERIAL)
    entry_data = dict(entry.data)
    pin_removed = entry_data.pop("pin", None)
    if not integration_serial:
        integration_serial = await async_get_integration_serial(hass, host)
        entry_data[CONF_INTEGRATION_SERIAL] = integration_serial
        hass.config_entries.async_update_entry(entry, data=entry_data)
    elif pin_removed is not None:
        hass.config_entries.async_update_entry(entry, data=entry_data)
    hub = Elke27Hub(
        hass,
        host,
        port,
        link_keys_json,
        integration_serial,
        panel_name,
        entry=entry,
    )
    # Cleanup runs (last registered first) only after every platform unloaded,
    # or when setup fails after this point.
    entry.async_on_unload(hub.async_disconnect)
    coordinator = Elke27DataUpdateCoordinator(hass, hub, entry)
    entry.async_on_unload(coordinator.async_stop)
    try:
        await hub.async_connect()
        await coordinator.async_start()
        await coordinator.async_refresh_now()
        await coordinator.async_refresh_domains(PRIMED_DOMAINS)
    except AUTH_ERRORS as err:
        raise ConfigEntryAuthFailed(
            translation_domain=DOMAIN, translation_key="auth_failed"
        ) from err
    except SETUP_RETRY_ERRORS as err:
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="cannot_connect",
            translation_placeholders={"host": host, "port": str(port)},
        ) from err

    coordinator.async_set_updated_data(hub.get_snapshot())
    entry.runtime_data = Elke27RuntimeData(hub=hub, coordinator=coordinator)
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


def _panel_name_from_entry(panel: object | None) -> str | None:
    if isinstance(panel, dict):
        return panel.get("panel_name") or panel.get("name")
    return None


def _duplicate_unique_id_issue_id(entry_id: str) -> str:
    return f"duplicate_unique_id_{entry_id}"


def _reconnect_failed_issue_id(entry_id: str) -> str:
    return f"{ISSUE_RECONNECT_FAILED}_{entry_id}"


@callback
def _async_delete_entry_issues(hass: HomeAssistant, entry: Elke27ConfigEntry) -> None:
    """Remove repairs issues scoped to one config entry."""
    ir.async_delete_issue(hass, DOMAIN, _duplicate_unique_id_issue_id(entry.entry_id))
    ir.async_delete_issue(hass, DOMAIN, _reconnect_failed_issue_id(entry.entry_id))
