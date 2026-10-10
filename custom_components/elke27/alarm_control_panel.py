"""Alarm control panel platform for Elke27 areas."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from elke27_lib import AreaState, ArmMode, PanelSnapshot, ZoneState
from elke27_lib.errors import Elke27PinRequiredError

from homeassistant.components.alarm_control_panel import (
    AlarmControlPanelEntity,
    AlarmControlPanelEntityFeature,
    AlarmControlPanelState,
    CodeFormat,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import Elke27DataUpdateCoordinator
from .entity import (
    build_unique_id,
    device_info_for_entry,
    raise_if_not_sent,
    sanitize_name,
    unique_base,
)
from .hub import ZoneBypassFailedError, area_faulted_zones, is_definitive_refusal

if TYPE_CHECKING:
    from collections.abc import Mapping

    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from .hub import Elke27Hub
    from .models import Elke27ConfigEntry

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: Elke27ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Elke27 area alarm control panels from a config entry."""
    data = entry.runtime_data
    hub = data.hub
    coordinator = data.coordinator
    known_ids: set[int] = set()

    def _async_add_areas() -> None:
        snapshot = coordinator.data
        if snapshot is None:
            _LOGGER.debug("Area entities skipped because snapshot is unavailable")
            return
        entities: list[Elke27AreaAlarmControlPanel] = []
        if not snapshot.areas:
            _LOGGER.debug("No areas available for entity creation")
            return
        for area_id, area in snapshot.areas.items():
            if area_id in known_ids:
                continue
            known_ids.add(area_id)
            entities.append(
                Elke27AreaAlarmControlPanel(coordinator, hub, entry, area_id, area)
            )
        if entities:
            _LOGGER.debug("Adding %s area entities", len(entities))
            async_add_entities(entities)

    _async_add_areas()
    entry.async_on_unload(coordinator.async_add_listener(_async_add_areas))


class Elke27AreaAlarmControlPanel(
    CoordinatorEntity[Elke27DataUpdateCoordinator], AlarmControlPanelEntity
):
    """Representation of an Elke27 area."""

    _attr_has_entity_name = True
    _attr_code_format = CodeFormat.NUMBER
    _attr_code_arm_required = True
    _attr_supported_features = (
        AlarmControlPanelEntityFeature.ARM_AWAY
        | AlarmControlPanelEntityFeature.ARM_HOME
        | AlarmControlPanelEntityFeature.ARM_CUSTOM_BYPASS
    )

    def __init__(
        self,
        coordinator: Elke27DataUpdateCoordinator,
        hub: Elke27Hub,
        entry: Elke27ConfigEntry,
        area_id: int,
        area: AreaState,
    ) -> None:
        """Initialize the area entity."""
        super().__init__(coordinator)
        self._hub = hub
        self._entry = entry
        self._area_id = area_id
        self._attr_name = sanitize_name(area.name) or f"Area {area_id}"
        self._attr_unique_id = build_unique_id(
            unique_base(hub, coordinator, entry),
            "area",
            area_id,
        )
        self._attr_device_info = device_info_for_entry(hub, coordinator, entry)
        self._missing_logged = False

    @property
    def alarm_state(self) -> AlarmControlPanelState | None:
        """Return the current alarm state."""
        area = _get_area(self.coordinator.data, self._area_id)
        if area is None:
            self._log_missing()
            return None
        return _area_state_to_ha(area)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        area = _get_area(self.coordinator.data, self._area_id)
        if area is None:
            return {
                "ready": None,
                "ready_status": None,
                "faulted_zone_ids": None,
                "faulted_zones": None,
            }
        faulted_zones = area_faulted_zones(self.coordinator.data, self._area_id)
        definitions = self.coordinator.data.zone_definitions
        return {
            "ready": area.ready,
            # AreaState.ready_status is new in elke27 0.3.10 (RDY_AWAY, RDY_STAY,
            # RDY_NOT); older versions do not have it.
            "ready_status": getattr(area, "ready_status", None),
            "faulted_zone_ids": [zone.zone_id for zone in faulted_zones],
            "faulted_zones": [
                _zone_display_name(zone, definitions) for zone in faulted_zones
            ],
        }

    @property
    def available(self) -> bool:
        """Return if the entity is available."""
        return (
            super().available
            and self._hub.is_ready
            and _get_area(self.coordinator.data, self._area_id) is not None
        )

    async def async_alarm_arm_away(self, code: str | None = None) -> None:
        """Arm the area in away mode."""
        async with self._hub.area_arm_lock(self._area_id):
            await self._async_arm(ArmMode.ARMED_AWAY, code)

    async def async_alarm_arm_home(self, code: str | None = None) -> None:
        """Arm the area in home mode."""
        async with self._hub.area_arm_lock(self._area_id):
            await self._async_arm(ArmMode.ARMED_STAY, code)

    async def async_alarm_arm_custom_bypass(self, code: str | None = None) -> None:
        """Arm the area with a custom bypass."""
        code = _normalize_code(code)
        # Same per-area lock as alarm_arm_automatic and zone_bypass.
        async with self._hub.area_arm_lock(self._area_id):
            snapshot = self._hub.get_snapshot() or self.coordinator.data
            try:
                await self._hub.async_bypass_faulted_zones(
                    self._area_id, snapshot, code
                )
            except ZoneBypassFailedError as err:
                if not err.pin_required:
                    raise
                msg = "PIN required to perform this action."
                raise HomeAssistantError(msg) from err
            await self._async_arm(ArmMode.ARMED_AWAY, code)

    async def async_alarm_disarm(self, code: str | None = None) -> None:
        """Disarm the area."""
        # Validate the code first: an invalid or missing code changes nothing
        # and never touches automatic arming.
        code = _normalize_code(code)
        if code is None:
            msg = "PIN required to perform this action."
            raise HomeAssistantError(msg)
        # Disarm wins without waiting on the area lock. While the disarm is
        # pending, automatic arming of this area pauses before its next
        # command, so it cannot send an arm (or bypass) that lands after the
        # disarm. Once a disarm has been sent, automatic arming is cancelled on
        # any outcome except a definitive refusal (e.g. wrong code): after a
        # timeout or transport error the panel may have disarmed, and resuming
        # could re-arm with someone inside. A disarm that was never sent (no
        # connection, missing code) or was definitively refused lets it resume.
        hub = self._hub
        hub.begin_disarm(self._area_id)
        try:
            try:
                accepted = await hub.async_disarm_area(self._area_id, code)
            except Elke27PinRequiredError as err:
                msg = "PIN required to perform this action."
                raise HomeAssistantError(msg) from err
            except asyncio.CancelledError:
                hub.cancel_arm_automatic(self._area_id)
                raise
            except Exception as err:
                if not is_definitive_refusal(err):
                    hub.cancel_arm_automatic(self._area_id)
                raise
            # False: never sent (no connection), so arming may resume.
            raise_if_not_sent(sent=accepted, hub=hub)
            hub.cancel_arm_automatic(self._area_id)
        finally:
            hub.end_disarm(self._area_id)

    async def _async_arm(self, mode: ArmMode, code: str | None) -> None:
        """Arm the area; the caller holds the area lock."""
        code = _normalize_code(code)
        try:
            sent = await self._hub.async_arm_area(self._area_id, mode, code)
            raise_if_not_sent(sent=sent, hub=self._hub)
        except Elke27PinRequiredError as err:
            msg = "PIN required to perform this action."
            raise HomeAssistantError(msg) from err

    def _log_missing(self) -> None:
        """Log when the area snapshot is missing."""
        if self._missing_logged:
            return
        self._missing_logged = True
        _LOGGER.debug("Area %s missing from snapshot", self._area_id)


def _get_area(snapshot: PanelSnapshot | None, area_id: int) -> AreaState | None:
    if snapshot is None:
        return None
    return snapshot.areas.get(area_id)


def _area_state_to_ha(area: AreaState) -> AlarmControlPanelState | None:
    if area.alarm_active:
        return AlarmControlPanelState.TRIGGERED
    if area.arming:
        return AlarmControlPanelState.ARMING
    if area.arm_mode is None or area.arm_mode is ArmMode.DISARMED:
        return AlarmControlPanelState.DISARMED
    if area.arm_mode is ArmMode.ARMED_STAY:
        return AlarmControlPanelState.ARMED_HOME
    if area.arm_mode is ArmMode.ARMED_NIGHT:
        return AlarmControlPanelState.ARMED_NIGHT
    if area.arm_mode is ArmMode.ARMED_AWAY:
        return AlarmControlPanelState.ARMED_AWAY
    return None


def _zone_display_name(zone: ZoneState, definitions: Mapping[int, Any]) -> str:
    definition = definitions.get(zone.zone_id)
    if definition is not None and definition.name:
        return sanitize_name(definition.name) or f"Zone {zone.zone_id}"
    return sanitize_name(zone.name) or f"Zone {zone.zone_id}"


def _normalize_code(code: str | None) -> str | None:
    if code is None:
        return None
    normalized = code.strip()
    if not normalized.isdigit():
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="code_not_numeric"
        )
    return normalized
