"""Elke27 service schemas and handlers."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING, NoReturn

from elke27_lib import ArmMode, PanelSnapshot, ZoneState
from elke27_lib.errors import Elke27PinRequiredError
import voluptuous as vol

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv, entity_registry as er
from homeassistant.helpers.target import (
    TargetSelection,
    async_extract_referenced_entity_ids,
)

from .arm_automatic_errors import (
    ARM_NOT_SENT_REASON,
    arm_automatic_cancelled_error,
    arm_automatic_multi_failed_error,
    arm_automatic_not_armed_error,
    arm_automatic_uncertain_error,
    zone_bypass_failed_pin_required,
)
from .arming import (
    PIN_REQUIRED_REASON,
    ZoneBypassFailedError,
    is_already_armed_refusal,
    is_definitive_refusal,
    zone_bypass_label,
)
from .const import DOMAIN
from .errors import (
    arm_mode_not_supported_validation,
    code_rejected_error,
    homeassistant_error,
    service_validation_error,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from homeassistant.core import HomeAssistant, ServiceCall

    from .models import Elke27RuntimeData

_LOGGER = logging.getLogger(__package__)

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


def _numeric_service_code(value: str) -> str:
    """Validate a service user code is numeric and not all zeros."""
    code = cv.string(value).strip()
    if not (code.isascii() and code.isdigit()):
        msg = "Code must be numeric"
        raise vol.Invalid(msg)
    if not code.strip("0"):
        msg = "Code must not be all zeros"
        raise vol.Invalid(msg)
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


async def _async_handle_alarm_arm_automatic(
    hass: HomeAssistant, call: ServiceCall
) -> None:
    """Handle the Elke27 automation arming service."""
    mode_name = call.data[ATTR_MODE]
    code = call.data[ATTR_CODE]

    entity_ids = _entity_ids_from_service_call(hass, call, Platform.ALARM_CONTROL_PANEL)
    if not entity_ids:
        msg = "no_alarm_panel_target"
        raise service_validation_error(
            msg,
            "No Elk E27 alarm area was selected. Select an alarm area and try again.",
        )

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
        except (HomeAssistantError, ServiceValidationError) as err:
            failures.append(err)
    if len(failures) == 1 or (
        failures and all(code_rejected_error(err) is err for err in failures)
    ):
        raise failures[0]
    if failures:
        raise arm_automatic_multi_failed_error(
            len(failures), len(entity_ids)
        ) from failures[0]


def _config_entry_id(entity_entry: er.RegistryEntry) -> str:
    """Return the config entry id for an entity registry entry."""
    config_entry_id = entity_entry.config_entry_id
    if config_entry_id is None:
        msg = "entity_not_linked"
        raise service_validation_error(
            msg,
            "This entity isn't connected to an Elk E27 panel. Pick a different one.",
        )
    return config_entry_id


def _entity_ids_from_service_call(
    hass: HomeAssistant, call: ServiceCall, platform: Platform
) -> list[str]:
    """
    Extract target entity IDs from a service call.

    Entities named directly are all kept (a wrong one is reported). Entities
    reached through a device or area target are kept only for this platform:
    since E2 an area device holds both its alarm panel and its zones.
    """
    referenced = async_extract_referenced_entity_ids(hass, TargetSelection(call.data))
    registry = er.async_get(hass)
    indirect = {
        entity_id
        for entity_id in referenced.indirectly_referenced
        if (entity_entry := registry.async_get(entity_id)) is not None
        and entity_entry.platform == DOMAIN
        and entity_entry.domain == platform
    }
    return sorted(referenced.referenced | indirect)


async def _async_arm_automatic_entity(
    hass: HomeAssistant,
    entity_id: str,
    mode_name: str,
    code: str,
) -> None:
    """Handle the automatic arming service for one entity."""
    entity_entry, runtime_data = _entity_runtime_data(
        hass, entity_id, Platform.ALARM_CONTROL_PANEL, "alarm area"
    )
    area_id = _area_id_from_unique_id(entity_entry.unique_id)
    coordinator = runtime_data.coordinator
    attempted: list[ZoneState] = []
    arm_sent: list[bool] = []

    async def _run() -> None:
        # Serialize the whole bypass, arm and rollback sequence for this area so
        # a concurrent call cannot roll back bypasses another call relies on.
        area_lock = coordinator.area_arm_lock(area_id)
        lock_was_contended = area_lock.locked()
        # No await may be added between locked() and async with below.
        async with area_lock:
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
                lock_was_contended=lock_was_contended,
            )

    # Run each area in its own task, tracked on the coordinator (from before the lock
    # is taken), so a disarm of this area can cancel it, queued or running,
    # without touching other areas in the same call.
    task = asyncio.create_task(_run(), name=f"elke27 arm automatic area {area_id}")
    coordinator.register_arm_automatic(area_id, task)
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        if not coordinator.cancelled_by_disarm(task):
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
            config_entry_id=_config_entry_id(entity_entry),
            arm_sent=bool(arm_sent),
        )
        labels = [zone_bypass_label(zone) for zone in attempted]
        raise arm_automatic_cancelled_error(
            area_id, bypassed_labels=labels, arm_sent=bool(arm_sent)
        ) from None
    finally:
        coordinator.unregister_arm_automatic(area_id, task)


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
    lock_was_contended: bool,
) -> None:
    """Bypass, arm and (on a definitive refusal) roll back one area."""
    coordinator = runtime_data.coordinator

    async def _gate() -> None:
        # Pause before every command while a disarm of this area is pending.
        await coordinator.async_wait_disarm_clear(area_id)

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
        rolled_back, still_bypassed = await coordinator.async_rollback_bypasses(
            tuple(bypassed), code, gate=_gate
        )
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
            config_entry_id=_config_entry_id(entity_entry),
        )
        rejected = code_rejected_error(cause) if cause is not None else None
        if rejected is not None:
            # A rejected code is a user error: reported above, raised as is.
            if still_bypassed:
                _LOGGER.warning(
                    "Code rejected; zones still bypassed after rollback: %s",
                    ", ".join(str(item.zone_id) for item in still_bypassed),
                )
            raise rejected
        zone_label = zone_bypass_label(zone) if zone is not None else None
        pin_required = reason == PIN_REQUIRED_REASON
        if isinstance(cause, ZoneBypassFailedError):
            pin_required = pin_required or zone_bypass_failed_pin_required(cause)
        rolled_back_labels = [zone_bypass_label(item) for item in rolled_back]
        still_labels = [zone_bypass_label(item) for item in still_bypassed]
        err = arm_automatic_not_armed_error(
            area_id,
            reason=reason,
            zone_label=zone_label,
            rolled_back_labels=rolled_back_labels,
            still_bypassed_labels=still_labels,
            pin_required=pin_required,
        )
        raise err from cause

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
            config_entry_id=_config_entry_id(entity_entry),
        )
        labels = [zone_bypass_label(item) for item in bypassed]
        raise arm_automatic_uncertain_error(area_id, bypassed_labels=labels) from cause

    # Read the live client snapshot inside the lock, not the debounced
    # coordinator copy, so an earlier call's bypasses and arming are seen.
    async def _panel_reports_armed() -> tuple[PanelSnapshot | None, bool | None]:
        if lock_was_contended:
            _snap, armed = await coordinator.async_poll_until_area_armed(
                area_id, gate=_gate
            )
            if armed:
                return _snap, True
            return await coordinator.async_refresh_area_state(area_id)
        return await coordinator.async_refresh_area_state(area_id)

    async def _already_armed(
        reason: str, bypassed: Sequence[ZoneState], cause: Exception
    ) -> None:
        # Poll until the panel reports armed; never trust the cache on a failed read.
        _fresh, armed = await coordinator.async_poll_until_area_armed(
            area_id, gate=_gate
        )
        if armed:
            _LOGGER.info(
                "Area %s is already armed (panel said: %s); automatic arming skipped",
                area_id,
                reason,
            )
            return
        _uncertain(reason, bypassed, cause)

    # area_get_status can lag after a queued call armed this area; poll only then.
    snapshot, skip_reply = await _panel_reports_armed()
    if skip_reply is True:
        _LOGGER.debug(
            "Area %s is already armed or arming; automatic arming skipped", area_id
        )
        return
    if snapshot is None:
        await _fail("arm", ARM_NOT_SENT_REASON, cause=None)
    try:
        bypassed = await coordinator.async_bypass_faulted_zones(
            area_id,
            snapshot,
            code,
            attempted=attempted,
            gate=_gate,
            raise_code_rejected=False,
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
        sent = await coordinator.async_arm_area(
            area_id,
            _service_mode_to_arm_mode(mode_name),
            code,
            auto_stay_cancel=True,
            exit_delay_cancel=True,
        )
    except Elke27PinRequiredError as err:
        await _fail("arm", PIN_REQUIRED_REASON, bypassed=bypassed, cause=err)
    except HomeAssistantError as err:
        rejected = code_rejected_error(err)
        if rejected is not None:
            # A rejected code is a user error: undo, report, then raise it as is.
            await _fail("arm", str(rejected), bypassed=bypassed, cause=err)
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
            f" area armed. If these zones are still bypassed, clear them with the"
            f" zone bypass service: {labels}."
        )
    return (
        f"These zones were (or may have been) bypassed and were left as they are."
        f" If they are still bypassed, clear them with the zone bypass service:"
        f" {labels}."
    )


def _uncertain_advice(bypassed: Sequence[ZoneState]) -> str:
    """Tell the user what to check after an arm with an unknown result."""
    advice = "Check the area's state on the panel."
    if bypassed:
        advice += (
            f" These zones were bypassed and were left bypassed:"
            f" {_zone_labels(bypassed)}. If the area is not armed, clear them with"
            " the zone bypass service."
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
        parts.append(f"Bypasses were cleared: {_zone_labels(rolled_back)}.")
    if still_bypassed:
        parts.append(
            f"These zones are still bypassed: {_zone_labels(still_bypassed)}."
            " Clear them with the zone bypass service."
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
        msg = "entity_invalid"
        raise service_validation_error(
            msg,
            f"This isn't an Elk E27 {label}. Pick a different one.",
            translation_placeholders={"entity_kind": label},
        )

    config_entry = hass.config_entries.async_get_entry(_config_entry_id(entity_entry))
    if config_entry is None:
        msg = "entity_not_linked"
        raise service_validation_error(
            msg,
            "This entity isn't connected to an Elk E27 panel. Pick a different one.",
        )
    if config_entry.state is not ConfigEntryState.LOADED:
        msg = "config_entry_not_loaded"
        raise service_validation_error(
            msg,
            "The panel integration is not ready yet. Try again in a moment.",
        )

    return entity_entry, config_entry.runtime_data


async def _async_handle_zone_bypass(hass: HomeAssistant, call: ServiceCall) -> None:
    """Bypass or un-bypass the targeted Elke27 zones."""
    code = call.data[ATTR_CODE]
    bypass = call.data[ATTR_BYPASS]

    entity_ids = _entity_ids_from_service_call(hass, call, Platform.BINARY_SENSOR)
    if not entity_ids:
        msg = "no_zone_target"
        raise service_validation_error(
            msg,
            "No Elk E27 zone was selected. Select a zone and try again.",
        )

    for entity_id in entity_ids:
        entity_entry, runtime_data = _entity_runtime_data(
            hass, entity_id, Platform.BINARY_SENSOR, "zone"
        )
        zone_id = _numeric_id_from_unique_id(entity_entry.unique_id, "zone")
        coordinator = runtime_data.coordinator
        snapshot = coordinator.get_snapshot()
        zone = snapshot.zones.get(zone_id) if snapshot is not None else None
        area_id = zone.area_id if zone is not None else None
        # Share the area lock with automatic arming so a rollback cannot race
        # a manual bypass. Zones with no known area are not serialized.
        lock = (
            coordinator.area_arm_lock(area_id) if area_id else contextlib.nullcontext()
        )
        async with lock:
            acknowledged = await coordinator.async_set_zone_bypass(
                zone_id, bypassed=bypass, pin=code
            )
        if not acknowledged:
            msg = "zone_bypass_not_acknowledged"
            raise homeassistant_error(
                msg,
                "The panel didn't confirm the bypass. Check the zone's status, "
                "then try again.",
            )


def _service_mode_to_arm_mode(mode_name: str) -> ArmMode:
    """Map service mode names to library arm modes."""
    if mode_name == "away":
        return ArmMode.ARMED_AWAY
    if mode_name == "home":
        return ArmMode.ARMED_STAY
    raise arm_mode_not_supported_validation()


def _area_id_from_unique_id(unique_id: str) -> int:
    """Extract an area ID from an Elke27 alarm entity unique ID."""
    return _numeric_id_from_unique_id(unique_id, "area")


def _numeric_id_from_unique_id(unique_id: str, domain: str) -> int:
    """
    Extract the numeric ID from an Elke27 ``<base>:<id>`` unique ID.

    The caller has already checked the entity's platform, so the kind
    (area or zone) is known; ``domain`` only names it in errors.
    """
    if ":" not in unique_id:
        msg = "entity_invalid"
        raise service_validation_error(
            msg,
            f"This isn't an Elk E27 {domain}. Pick a different one.",
            translation_placeholders={"entity_kind": domain},
        )
    id_str = unique_id.rsplit(":", 1)[1]
    try:
        return int(id_str)
    except ValueError as err:
        msg = "entity_invalid"
        raise service_validation_error(
            msg,
            f"This isn't an Elk E27 {domain}. Pick a different one.",
            translation_placeholders={"entity_kind": domain},
        ) from err
