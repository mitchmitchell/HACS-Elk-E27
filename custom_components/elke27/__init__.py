"""Set up the Elke27 integration."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING, NoReturn

from elke27_lib import ArmMode, ZoneState
from elke27_lib.errors import E27Error, Elke27Error, Elke27PinRequiredError
import voluptuous as vol

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import callback
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
    HomeAssistantError,
    ServiceValidationError,
)
from homeassistant.helpers import config_validation as cv, entity_registry as er
from homeassistant.helpers.target import (
    TargetSelection,
    async_extract_referenced_entity_ids,
)

from .const import CONF_INTEGRATION_SERIAL, CONF_LINK_KEYS_JSON, CONF_PANEL, DOMAIN
from .coordinator import Elke27DataUpdateCoordinator
from .entity import unique_base
from .hub import (
    AUTH_ERRORS,
    PIN_REQUIRED_REASON,
    Elke27Hub,
    ZoneBypassFailedError,
    area_is_armed,
    is_already_armed_refusal,
    is_definitive_refusal,
    zone_bypass_label,
)
from .identity import async_get_integration_serial
from .models import Elke27ConfigEntry, Elke27RuntimeData

if TYPE_CHECKING:
    from collections.abc import Sequence

    from homeassistant.core import HomeAssistant, ServiceCall
    from homeassistant.helpers.typing import ConfigType

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

SERVICE_ALARM_ARM_AUTOMATIC = "alarm_arm_automatic"
EVENT_ARM_AUTOMATIC_FAILED = "elke27_arm_automatic_failed"
ATTR_MODE = "mode"
ATTR_CODE = "code"
ATTR_AREA_ID = "area_id"
ATTR_ZONE_ID = "zone_id"
ATTR_REASON = "reason"
ATTR_STAGE = "stage"
ATTR_BYPASSED_ZONE_IDS = "bypassed_zone_ids"
ATTR_ROLLED_BACK_ZONE_IDS = "rolled_back_zone_ids"
ATTR_STILL_BYPASSED_ZONE_IDS = "still_bypassed_zone_ids"
ATTR_OUTCOME = "outcome"
STAGE_ARM_UNCERTAIN = "arm_uncertain"
STAGE_CANCELLED = "cancelled"
OUTCOME_NOT_ARMED = "not_armed"
OUTCOME_UNKNOWN = "unknown"
ARM_NOT_SENT_REASON = "the arm command was not sent (the panel is not connected)"

def _numeric_service_code(value: str) -> str:
    """Validate a service user code is numeric."""
    code = cv.string(value).strip()
    if not code.isdigit():
        raise vol.Invalid("Code must be numeric")
    return code


SERVICE_ALARM_ARM_AUTOMATIC_SCHEMA = cv.make_entity_service_schema(
    {
        vol.Required(ATTR_MODE): vol.In(("away", "home")),
        vol.Required(ATTR_CODE): _numeric_service_code,
    }
)

SERVICE_ZONE_BYPASS = "zone_bypass"
ATTR_BYPASS = "bypass"

SERVICE_ZONE_BYPASS_SCHEMA = cv.make_entity_service_schema(
    {
        vol.Required(ATTR_CODE): _numeric_service_code,
        vol.Optional(ATTR_BYPASS, default=True): cv.boolean,
    }
)

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
    await _async_migrate_unique_ids(hass, entry, unique_base(hub, coordinator, entry))
    entry.runtime_data = Elke27RuntimeData(hub=hub, coordinator=coordinator)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: Elke27ConfigEntry) -> bool:
    """Unload an Elke27 config entry."""
    # The coordinator and client are stopped by the async_on_unload callbacks
    # registered in setup, which Home Assistant runs only when this succeeds.
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


def _panel_name_from_entry(panel: object | None) -> str | None:
    if isinstance(panel, dict):
        return panel.get("panel_name") or panel.get("name")
    return None


async def _async_migrate_unique_ids(
    hass: HomeAssistant, entry: ConfigEntry, base: str
) -> None:
    """Migrate legacy unique IDs to the <base>:<domain>:<id> format."""
    registry = er.async_get(hass)
    prefix = f"{base}_"
    for entity in registry.entities.values():
        if entity.platform != DOMAIN:
            continue
        if entity.config_entry_id != entry.entry_id:
            continue
        unique_id = entity.unique_id
        if not unique_id.startswith(prefix):
            continue
        rest = unique_id[len(prefix) :]
        if "_" not in rest:
            continue
        domain, numeric_id = rest.rsplit("_", 1)
        new_unique_id = f"{base}:{domain}:{numeric_id}"
        if registry.async_get_entity_id(entity.domain, DOMAIN, new_unique_id):
            _LOGGER.debug(
                "Unique ID migration skipped for %s; %s already exists",
                entity.entity_id,
                new_unique_id,
            )
            continue
        registry.async_update_entity(entity.entity_id, new_unique_id=new_unique_id)


async def _async_handle_alarm_arm_automatic(
    hass: HomeAssistant, call: ServiceCall
) -> None:
    """Handle the Elke27 automation arming service."""
    mode_name = call.data[ATTR_MODE]
    code = call.data[ATTR_CODE]

    entity_ids = _entity_ids_from_service_call(hass, call)
    if not entity_ids:
        msg = "No Elke27 alarm control panel target was provided"
        raise ServiceValidationError(msg)

    # Each area is handled on its own: a failure in one area is reported and
    # does not stop the others, and areas armed earlier stay armed. Refusals
    # are never retried; within a failed area, its own bypasses are undone.
    failures: list[HomeAssistantError] = []
    for entity_id in entity_ids:
        try:
            await _async_arm_automatic_entity(
                hass,
                entity_id,
                mode_name,
                code,
            )
        except HomeAssistantError as err:
            failures.append(err)
    if len(failures) == 1:
        raise failures[0]
    if failures:
        details = "; ".join(str(err) for err in failures)
        msg = (
            f"Automatic arming failed for {len(failures)} of {len(entity_ids)} "
            f"areas: {details}"
        )
        raise HomeAssistantError(msg) from failures[0]


def _entity_ids_from_service_call(hass: HomeAssistant, call: ServiceCall) -> list[str]:
    """Extract target entity IDs from a service call."""
    referenced = async_extract_referenced_entity_ids(hass, TargetSelection(call.data))
    return sorted(referenced.referenced | referenced.indirectly_referenced)


async def _async_arm_automatic_entity(
    hass: HomeAssistant,
    entity_id: str,
    mode_name: str,
    code: str,
) -> None:
    """Handle the automatic arming service for one entity."""
    entity_entry, runtime_data = _entity_runtime_data(
        hass, entity_id, Platform.ALARM_CONTROL_PANEL, "alarm control panel"
    )
    area_id = _area_id_from_unique_id(entity_entry.unique_id)
    hub = runtime_data.hub
    attempted: list[ZoneState] = []
    arm_sent: list[bool] = []

    async def _run() -> None:
        # Serialize the whole bypass, arm and rollback sequence for this area so
        # a concurrent call cannot roll back bypasses another call relies on.
        async with hub.area_arm_lock(area_id):
            await _async_arm_automatic_locked(
                hass,
                entity_entry,
                runtime_data,
                entity_id,
                area_id,
                mode_name,
                code,
                attempted=attempted,
                arm_sent=arm_sent,
            )

    # Run each area in its own task, tracked on the hub (from before the lock
    # is taken), so a disarm of this area can cancel it, queued or running,
    # without touching other areas in the same call.
    task = asyncio.create_task(_run(), name=f"elke27 arm automatic area {area_id}")
    hub.register_arm_automatic(area_id, task)
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        if not hub.cancelled_by_disarm(task):
            task.cancel()
            raise
        # Disarm wins: send nothing more (no rollback, no arm) and report.
        _async_report_arm_automatic_failure(
            hass,
            entity_id=entity_id,
            area_id=area_id,
            stage=STAGE_CANCELLED,
            reason="cancelled by a disarm of this area",
            zone=None,
            bypassed_zones=attempted,
            config_entry_id=entity_entry.config_entry_id,
            arm_sent=bool(arm_sent),
        )
        msg = (
            f"Area {area_id} automatic arming was cancelled by a disarm."
            f" {_cancelled_advice(attempted, arm_sent=bool(arm_sent))}"
        )
        raise HomeAssistantError(msg) from None
    finally:
        hub.unregister_arm_automatic(area_id, task)


async def _async_arm_automatic_locked(
    hass: HomeAssistant,
    entity_entry: er.RegistryEntry,
    runtime_data: Elke27RuntimeData,
    entity_id: str,
    area_id: int,
    mode_name: str,
    code: str,
    *,
    attempted: list[ZoneState],
    arm_sent: list[bool],
) -> None:
    """Bypass, arm and (on a definitive refusal) roll back one area."""
    hub = runtime_data.hub

    async def _gate() -> None:
        # Pause before every command while a disarm of this area is pending.
        await hub.async_wait_disarm_clear(area_id)

    async def _fail(
        stage: str,
        reason: str,
        *,
        zone: ZoneState | None = None,
        bypassed: Sequence[ZoneState] = (),
        cause: Exception | None,
    ) -> NoReturn:
        # Definitive refusal: undo this call's bypasses, one un-bypass per
        # zone, never retried.
        rolled_back, still_bypassed = await hub.async_rollback_bypasses(
            tuple(bypassed), code, gate=_gate
        )
        summary = _rollback_summary(rolled_back, still_bypassed)
        _async_report_arm_automatic_failure(
            hass,
            entity_id=entity_id,
            area_id=area_id,
            stage=stage,
            reason=reason,
            zone=zone,
            bypassed_zones=bypassed,
            rolled_back_zones=rolled_back,
            still_bypassed_zones=still_bypassed,
            config_entry_id=entity_entry.config_entry_id,
        )
        detail = f"{zone_bypass_label(zone)}: {reason}" if zone is not None else reason
        msg = f"Area {area_id} was not armed: {_sentence(detail)}"
        if summary:
            msg = f"{msg} {summary}"
        raise HomeAssistantError(msg) from cause

    def _uncertain(
        reason: str, bypassed: Sequence[ZoneState], cause: Exception
    ) -> NoReturn:
        # The panel may have armed: leave the bypasses in place and say so.
        _async_report_arm_automatic_failure(
            hass,
            entity_id=entity_id,
            area_id=area_id,
            stage=STAGE_ARM_UNCERTAIN,
            reason=reason,
            zone=None,
            bypassed_zones=bypassed,
            still_bypassed_zones=bypassed,
            config_entry_id=entity_entry.config_entry_id,
        )
        msg = (
            f"Area {area_id} arm result is unknown: {_sentence(reason)}"
            f" {_uncertain_advice(bypassed)}"
        )
        raise HomeAssistantError(msg) from cause

    # Read the live client snapshot inside the lock, not the debounced
    # coordinator copy, so an earlier call's bypasses and arming are seen.
    async def _already_armed(
        reason: str, bypassed: Sequence[ZoneState], cause: Exception
    ) -> None:
        # Re-read the area: armed means the goal is met (a no-op success);
        # otherwise the result is uncertain. No rollback either way.
        fresh = await hub.async_refresh_area_state(area_id)
        if area_is_armed(fresh, area_id):
            _LOGGER.info(
                "Area %s is already armed (panel said: %s); automatic arming skipped",
                area_id,
                reason,
            )
            return
        _uncertain(reason, bypassed, cause)

    # The client snapshot can lag the panel, so ask the panel for the area and
    # zone status first; fall back to the snapshot if that fails.
    snapshot = await hub.async_refresh_area_state(area_id)
    if snapshot is None:
        await _fail("arm", ARM_NOT_SENT_REASON, cause=None)
    if area_is_armed(snapshot, area_id):
        # Already armed (for example by a call queued just before this one):
        # nothing to do, and nothing is bypassed, armed or rolled back.
        _LOGGER.debug("Area %s is already armed; automatic arming skipped", area_id)
        return
    try:
        bypassed = await hub.async_bypass_faulted_zones(
            area_id, snapshot, code, attempted=attempted, gate=_gate
        )
    except ZoneBypassFailedError as err:
        if is_already_armed_refusal(err):
            # 11028: the area is armed after all (a stale snapshot). Never roll
            # back here: an armed area may rely on every bypass.
            await _already_armed(err.reason, err.bypassed_zones, err)
            return
        await _fail(
            "bypass", err.reason, zone=err.zone, bypassed=err.bypassed_zones, cause=err
        )

    await _gate()
    arm_sent.append(True)
    try:
        sent = await hub.async_arm_area(
            area_id,
            _service_mode_to_arm_mode(mode_name),
            code,
            auto_stay_cancel=True,
            exit_delay_cancel=True,
        )
    except Elke27PinRequiredError as err:
        await _fail("arm", PIN_REQUIRED_REASON, bypassed=bypassed, cause=err)
    except HomeAssistantError as err:
        # "Already armed" means the area may rely on these bypasses: keep them.
        if is_already_armed_refusal(err):
            await _already_armed(str(err), bypassed, err)
            return
        if not is_definitive_refusal(err):
            _uncertain(str(err), bypassed, err)
        await _fail("arm", str(err), bypassed=bypassed, cause=err)
    if not sent:
        # False means the command was never sent (no client), so it is definitive.
        await _fail("arm", ARM_NOT_SENT_REASON, bypassed=bypassed, cause=None)


def _cancelled_advice(bypassed: Sequence[ZoneState], *, arm_sent: bool) -> str:
    """Tell the user what a disarm-cancelled automatic arm left behind."""
    if not bypassed:
        return "No zones were bypassed."
    labels = _zone_labels(bypassed)
    if arm_sent:
        return (
            f"The arm had already been sent; the disarm clears the bypasses if the"
            f" area armed. If these zones are still bypassed, clear them with"
            f" elke27.zone_bypass and bypass: false: {labels}."
        )
    return (
        f"These zones were (or may have been) bypassed and were left as they are."
        f" If they are still bypassed, clear them with elke27.zone_bypass and"
        f" bypass: false: {labels}."
    )


def _uncertain_advice(bypassed: Sequence[ZoneState]) -> str:
    """Tell the user what to check after an arm with an unknown result."""
    advice = "Check the area's state on the panel."
    if bypassed:
        advice += (
            f" These zones were bypassed and were left bypassed:"
            f" {_zone_labels(bypassed)}. If the area is not armed, clear them with"
            " elke27.zone_bypass and bypass: false."
        )
    return advice


def _sentence(text: str) -> str:
    """Return text ending in exactly one period."""
    return text.rstrip().rstrip(".") + "."


def _zone_labels(zones: Sequence[ZoneState]) -> str:
    return ", ".join(zone_bypass_label(zone) for zone in zones)


def _rollback_summary(
    rolled_back: Sequence[ZoneState], still_bypassed: Sequence[ZoneState]
) -> str:
    """Describe what the rollback of this call's bypasses did."""
    parts: list[str] = []
    if rolled_back:
        parts.append(
            "The bypasses made before the failure were undone:"
            f" {_zone_labels(rolled_back)}."
        )
    if still_bypassed:
        parts.append(
            "These zones could not be un-bypassed and are still bypassed:"
            f" {_zone_labels(still_bypassed)}. Clear them with elke27.zone_bypass"
            " and bypass: false."
        )
    return " ".join(parts)


def _arm_automatic_notification_id(config_entry_id: str, area_id: int) -> str:
    """Return a stable notification ID for automatic arming failures."""
    return f"elke27_arm_automatic_{config_entry_id}_{area_id}"


@callback
def _async_report_arm_automatic_failure(
    hass: HomeAssistant,
    *,
    entity_id: str,
    area_id: int,
    stage: str,
    reason: str,
    zone: ZoneState | None,
    bypassed_zones: Sequence[ZoneState],
    rolled_back_zones: Sequence[ZoneState] = (),
    still_bypassed_zones: Sequence[ZoneState] = (),
    config_entry_id: str,
    arm_sent: bool = False,
) -> None:
    """Notify the user and fire an event when automatic arming fails for an area."""
    uncertain = stage == STAGE_ARM_UNCERTAIN
    if stage == STAGE_CANCELLED:
        message = (
            f"Area {area_id} ({entity_id}): automatic arming was cancelled by a"
            f" disarm. {_cancelled_advice(bypassed_zones, arm_sent=arm_sent)}"
        )
    elif uncertain:
        message = (
            f"Area {area_id} ({entity_id}): the arm result is unknown."
            f" {_sentence(reason)} {_uncertain_advice(bypassed_zones)}"
        )
    else:
        if zone is not None:
            detail = f"{zone_bypass_label(zone)} could not be bypassed: {reason}"
        else:
            detail = f"The panel did not arm the area: {reason}"
        message = f"Area {area_id} ({entity_id}) was not armed. {_sentence(detail)}"
        summary = _rollback_summary(rolled_back_zones, still_bypassed_zones)
        if summary:
            message = f"{message} {summary}"
    persistent_notification.async_create(
        hass,
        title="Elk E27 automatic arming failed",
        message=message,
        notification_id=_arm_automatic_notification_id(config_entry_id, area_id),
    )
    hass.bus.async_fire(
        EVENT_ARM_AUTOMATIC_FAILED,
        {
            "entity_id": entity_id,
            ATTR_AREA_ID: area_id,
            ATTR_STAGE: stage,
            ATTR_OUTCOME: OUTCOME_UNKNOWN if uncertain else OUTCOME_NOT_ARMED,
            ATTR_ZONE_ID: zone.zone_id if zone is not None else None,
            ATTR_REASON: reason,
            ATTR_BYPASSED_ZONE_IDS: [item.zone_id for item in bypassed_zones],
            ATTR_ROLLED_BACK_ZONE_IDS: [item.zone_id for item in rolled_back_zones],
            ATTR_STILL_BYPASSED_ZONE_IDS: [
                item.zone_id for item in still_bypassed_zones
            ],
        },
    )


def _entity_runtime_data(
    hass: HomeAssistant, entity_id: str, platform: Platform, label: str
) -> tuple[er.RegistryEntry, Elke27RuntimeData]:
    """Return the registry entry and loaded runtime data for an Elke27 entity."""
    entity_entry = er.async_get(hass).async_get(entity_id)
    if (
        entity_entry is None
        or entity_entry.platform != DOMAIN
        or entity_entry.domain != platform
    ):
        msg = f"Entity {entity_id} is not an Elke27 {label}"
        raise ServiceValidationError(msg)

    config_entry = hass.config_entries.async_get_entry(entity_entry.config_entry_id)
    if config_entry is None:
        msg = f"Config entry for {entity_id} was not found"
        raise ServiceValidationError(msg)
    if config_entry.state is not ConfigEntryState.LOADED:
        msg = f"Config entry for {entity_id} is not loaded"
        raise ServiceValidationError(msg)

    return entity_entry, config_entry.runtime_data


async def _async_handle_zone_bypass(hass: HomeAssistant, call: ServiceCall) -> None:
    """Bypass or un-bypass the targeted Elke27 zones."""
    code = call.data[ATTR_CODE]
    bypass = call.data[ATTR_BYPASS]

    entity_ids = _entity_ids_from_service_call(hass, call)
    if not entity_ids:
        msg = "No Elke27 zone target was provided"
        raise ServiceValidationError(msg)

    for entity_id in entity_ids:
        entity_entry, runtime_data = _entity_runtime_data(
            hass, entity_id, Platform.BINARY_SENSOR, "zone"
        )
        zone_id = _numeric_id_from_unique_id(entity_entry.unique_id, "zone")
        hub = runtime_data.hub
        snapshot = hub.get_snapshot()
        zone = snapshot.zones.get(zone_id) if snapshot is not None else None
        area_id = zone.area_id if zone is not None else None
        # Share the area lock with automatic arming so a rollback cannot race
        # a manual bypass. Zones with no known area are not serialized.
        lock = hub.area_arm_lock(area_id) if area_id else contextlib.nullcontext()
        async with lock:
            acknowledged = await hub.async_set_zone_bypass(
                zone_id, bypassed=bypass, pin=code
            )
        if not acknowledged:
            msg = f"Zone {zone_id} bypass was not acknowledged; is the panel connected?"
            raise HomeAssistantError(msg)


def _service_mode_to_arm_mode(mode_name: str) -> ArmMode:
    """Map service mode names to library arm modes."""
    if mode_name == "away":
        return ArmMode.ARMED_AWAY
    if mode_name == "home":
        return ArmMode.ARMED_STAY
    msg = f"Unsupported arming mode: {mode_name}"
    raise ServiceValidationError(msg)


def _area_id_from_unique_id(unique_id: str) -> int:
    """Extract an area ID from an Elke27 alarm entity unique ID."""
    return _numeric_id_from_unique_id(unique_id, "area")


def _numeric_id_from_unique_id(unique_id: str, domain: str) -> int:
    """Extract the numeric ID from an Elke27 ``<base>:<domain>:<id>`` unique ID."""
    prefix = f":{domain}:"
    if prefix not in unique_id:
        msg = f"Entity unique ID is not a recognized Elke27 {domain} identifier"
        raise ServiceValidationError(msg)
    id_str = unique_id.rsplit(prefix, 1)[1]
    try:
        return int(id_str)
    except ValueError as err:
        msg = f"Invalid Elke27 {domain} ID in unique ID: {unique_id}"
        raise ServiceValidationError(msg) from err
