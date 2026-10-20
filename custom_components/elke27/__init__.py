"""Set up the Elke27 integration."""

from __future__ import annotations

from contextlib import contextmanager
import logging
from typing import TYPE_CHECKING

from homeassistant.const import Platform
from homeassistant.core import callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryError
from homeassistant.helpers import (
    config_validation as cv,
    device_registry as dr,
    issue_registry as ir,
)

from .const import (
    CONF_INTEGRATION_ID,
    DOMAIN,
    ISSUE_LEGACY_MIGRATION_RECORD_MALFORMED,
    ISSUE_RECONNECT_FAILED,
)
from .coordinator import PRIMED_DOMAINS, SETUP_RETRY_ERRORS, Elke27DataUpdateCoordinator  # noqa: F401  # PRIMED_DOMAINS/SETUP_RETRY_ERRORS re-exported
from .entity import (
    area_has_device,
    area_id_from_device_identifier,
    device_info_for_entry,
    entry_identity_key,
    snapshot_is_full,
)
from .identity import is_valid_integration_id
from .migration import (
    LegacyMigrationRecordError,
    async_apply_legacy_migration,
    async_forget_blocked_key,
    async_migrate_legacy_entry,
    async_refresh_duplicates_issue,
    blocked_issue_id,
    duplicates_issue_id,
    legacy_migration_pending,
    record_issue_id,
    validate_legacy_migration_record,
)
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
    from collections.abc import Iterator

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


@contextmanager
def _record_errors_fail_setup(
    hass: HomeAssistant, entry: Elke27ConfigEntry
) -> Iterator[None]:
    """
    Turn LegacyMigrationRecordError into a Repairs issue and ConfigEntryError.

    The issue offers the rebuild flow (repairs.py). The field name goes to
    the log only, never a value; the user text names no field.
    """
    try:
        yield
    except LegacyMigrationRecordError as err:
        ir.async_create_issue(
            hass,
            DOMAIN,
            record_issue_id(entry.entry_id),
            is_fixable=True,
            data={"entry_id": entry.entry_id, "flow": "rebuild"},
            severity=ir.IssueSeverity.ERROR,
            translation_key=ISSUE_LEGACY_MIGRATION_RECORD_MALFORMED,
        )
        _LOGGER.error(  # noqa: TRY400  # a traceback would carry values
            "Elke27 upgrade notes unreadable (field %s); see Settings > Repairs",
            err.field,
        )
        # The record stays as stored and the entry stays unloaded until the
        # Repairs flow rebuilds it: a plain retry can't fix it.
        raise ConfigEntryError(
            translation_domain=DOMAIN,
            translation_key=ISSUE_LEGACY_MIGRATION_RECORD_MALFORMED,
        ) from None
    ir.async_delete_issue(hass, DOMAIN, record_issue_id(entry.entry_id))


async def async_setup_entry(hass: HomeAssistant, entry: Elke27ConfigEntry) -> bool:
    """Set up Elke27 from a config entry."""
    # A malformed 0.1.x upgrade record is never run, skipped or marked done.
    with _record_errors_fail_setup(hass, entry):
        validate_legacy_migration_record(entry)
    # E1: the client sn is the flow-generated integration ID. A 0.1.x entry
    # (migration pending) has none: the re-link (reauth) draws one and links
    # again. A 0.2.x entry without a valid one can't occur; it stays an error.
    if not is_valid_integration_id(entry.data.get(CONF_INTEGRATION_ID)):
        if legacy_migration_pending(entry):
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN, translation_key="legacy_relink_required"
            )
        raise ConfigEntryError(
            translation_domain=DOMAIN, translation_key="integration_id_invalid"
        )
    coordinator = Elke27DataUpdateCoordinator(hass, entry)
    # Runs on unload, and when setup fails after this point.
    entry.async_on_unload(coordinator.async_shutdown_client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = Elke27RuntimeData(coordinator=coordinator)
    # 0.1.x upgrade, Phase B: before the panel device is created and before the
    # platforms register entities, or the new unique_ids would collide.
    if legacy_migration_pending(entry):
        async_apply_legacy_migration(
            hass, entry, entry_identity_key(coordinator, entry)
        )
    async_refresh_duplicates_issue(hass, entry)
    # E2: register the panel device first so area devices can link to it.
    # Area entities link to it by device id (via_device_id) where HA supports it.
    panel_device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, **device_info_for_entry(coordinator, entry)
    )
    coordinator.panel_device_id = panel_device.id
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: Elke27ConfigEntry) -> bool:
    """Migrate a config entry to VERSION 2 (0.1.x upgrade in place, Phase A)."""
    if entry.version > 2:  # noqa: PLR2004  # VERSION in config_flow
        # Written by a newer release: refuse rather than mis-read it.
        return False
    if entry.version == 1:
        return await async_migrate_legacy_entry(hass, entry)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: Elke27ConfigEntry) -> bool:
    """Unload an Elke27 config entry."""
    # The coordinator and client are stopped by the async_on_unload callbacks
    # registered in setup, which Home Assistant runs only when this succeeds.
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: Elke27ConfigEntry) -> None:
    """Clean up integration state when a config entry is removed."""
    _async_delete_entry_issues(hass, entry)
    async_forget_blocked_key(hass, entry.entry_id)


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


async def async_remove_config_entry_device(
    hass: HomeAssistant,  # noqa: ARG001  # Home Assistant signature
    entry: Elke27ConfigEntry,
    device_entry: dr.DeviceEntry,
) -> bool:
    """Allow deleting an area device the panel no longer reports (E2)."""
    coordinator = entry.runtime_data.coordinator
    area_ids = _entry_area_ids(entry, coordinator, device_entry)
    snapshot = coordinator.data
    if not area_ids or not snapshot_is_full(snapshot):
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
    ir.async_delete_issue(hass, DOMAIN, blocked_issue_id(entry.entry_id))
    ir.async_delete_issue(hass, DOMAIN, duplicates_issue_id(entry.entry_id))
    ir.async_delete_issue(hass, DOMAIN, record_issue_id(entry.entry_id))
