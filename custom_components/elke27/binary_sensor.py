"""Binary sensors for Elke27 zones."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import Elke27DataUpdateCoordinator
from .entity import build_unique_id, device_info_for_entry, unique_base

if TYPE_CHECKING:
    from elke27_lib import PanelSnapshot, ZoneDefinition, ZoneState

    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from .hub import Elke27Hub
    from .models import Elke27ConfigEntry

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0

_ZONE_ICON_BY_DEFINITION = {
    "UNDEFINED": "mdi:help-circle-outline",
    "BURG EE DELAY": "mdi:door-closed-lock",
    "BURG PERIM INST": "mdi:window-closed",
    "BURG INTERIOR": "mdi:motion-sensor",
    "BURG 24HR": "mdi:shield-alert",
    "BURG BOX TAMPER": "mdi:shield-off-outline",
    "FIRE": "mdi:fire",
    "CARBON MONOXIDE": "mdi:molecule-co",
    "PANIC": "mdi:alert-octagon",
    "MEDICAL": "mdi:medical-bag",
    "AUTOMATION": "mdi:home-automation",
    "POWER SUPERVISION": "mdi:power",
    "WATER": "mdi:water",
    "HILO TEMP": "mdi:thermometer",
}
_ZONE_OPEN_ICON_BY_DEFINITION = {
    "BURG EE DELAY": "mdi:door-open",
    "BURG PERIM INST": "mdi:window-open",
    "BURG INTERIOR": "mdi:motion-sensor",
    "BURG 24HR": "mdi:shield-alert",
    "BURG BOX TAMPER": "mdi:shield-off-outline",
    "FIRE": "mdi:fire-alert",
    "CARBON MONOXIDE": "mdi:molecule-co",
    "PANIC": "mdi:alert-octagon",
    "MEDICAL": "mdi:medical-bag",
    "AUTOMATION": "mdi:home-automation",
    "POWER SUPERVISION": "mdi:power-alert",
    "WATER": "mdi:water-alert",
    "HILO TEMP": "mdi:thermometer-alert",
}


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: Elke27ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Elke27 zone binary sensors from a config entry."""
    data = entry.runtime_data
    hub = data.hub
    coordinator = data.coordinator
    known_ids: set[int] = set()
    skipped_zone_ids: set[int] = set()

    def _async_add_zones() -> None:
        snapshot = coordinator.data
        entities: list[Elke27ZoneBinarySensor] = []
        if not snapshot.zones:
            _LOGGER.debug("No zones available for entity creation")
            return
        added = 0
        skipped = 0
        for zone_id, zone in snapshot.zones.items():
            zone_definition = snapshot.zone_definitions.get(zone_id)
            definition = _zone_definition_value(zone_definition)
            if definition == "UNDEFINED":
                if zone_id not in skipped_zone_ids:
                    _LOGGER.debug(
                        "Skipping zone entity %s (%s): definition=UNDEFINED",
                        zone_id,
                        zone.name,
                    )
                    skipped_zone_ids.add(zone_id)
                skipped += 1
                continue
            skipped_zone_ids.discard(zone_id)
            if zone_id in known_ids:
                continue
            known_ids.add(zone_id)
            entities.append(
                Elke27ZoneBinarySensor(
                    coordinator, hub, entry, zone_id, zone, zone_definition
                )
            )
            added += 1
        _LOGGER.debug(
            "Adding %s zone entities (skipped %s UNDEFINED)",
            added,
            skipped,
        )
        if entities:
            async_add_entities(entities)

    _async_add_zones()
    entry.async_on_unload(coordinator.async_add_listener(_async_add_zones))


class Elke27ZoneBinarySensor(
    CoordinatorEntity[Elke27DataUpdateCoordinator], BinarySensorEntity
):
    """Representation of an Elke27 zone."""

    _attr_device_class = BinarySensorDeviceClass.OPENING
    _attr_has_entity_name = True
    _attr_translation_key = "zone"

    def __init__(
        self,
        coordinator: Elke27DataUpdateCoordinator,
        hub: Elke27Hub,
        entry: Elke27ConfigEntry,
        zone_id: int,
        zone: ZoneState,
        zone_definition: ZoneDefinition | None,
    ) -> None:
        """Initialize the zone entity."""
        super().__init__(coordinator)
        self._hub = hub
        self._entry = entry
        self._zone_id = zone_id
        self._attr_name = _zone_name(zone, zone_definition) or f"Zone {zone_id}"
        self._attr_unique_id = build_unique_id(
            unique_base(hub, coordinator, entry),
            "zone",
            zone_id,
        )
        self._attr_device_info = device_info_for_entry(hub, coordinator, entry)
        self._missing_logged = False
        self._attr_device_class = _zone_device_class(zone_definition)

    @property
    def is_on(self) -> bool | None:
        """Return if the zone is open."""
        zone = _get_zone(self.coordinator.data, self._zone_id)
        if zone is None:
            self._log_missing()
            return None
        open_state = zone.open
        if open_state is None:
            return None
        return bool(open_state)

    @property
    def icon(self) -> str | None:
        """Return the icon based on zone definition and state."""
        zone = _get_zone(self.coordinator.data, self._zone_id)
        if zone is None:
            return None
        definition = _zone_definition_value(
            _zone_definition_entry(self.coordinator.data, self._zone_id)
        )
        if not definition:
            return None
        open_state = zone.open
        if open_state is None:
            return None
        if open_state:
            return _ZONE_OPEN_ICON_BY_DEFINITION.get(
                definition
            ) or _ZONE_ICON_BY_DEFINITION.get(definition)
        return _ZONE_ICON_BY_DEFINITION.get(definition)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        zone = _get_zone(self.coordinator.data, self._zone_id)
        if zone is None:
            return {}
        return {
            "definition": _zone_definition_value(
                _zone_definition_entry(self.coordinator.data, self._zone_id)
            ),
            "bypassed": zone.bypassed,
            "trouble": zone.trouble,
        }

    @property
    def available(self) -> bool:
        """Return if the entity is available."""
        return (
            super().available
            and self._hub.is_ready
            and _get_zone(self.coordinator.data, self._zone_id) is not None
        )

    def _log_missing(self) -> None:
        """Log when the zone snapshot is missing."""
        if self._missing_logged:
            return
        self._missing_logged = True
        _LOGGER.debug("Zone %s missing from snapshot", self._zone_id)


def _get_zone(snapshot: PanelSnapshot, zone_id: int) -> ZoneState | None:
    return snapshot.zones.get(zone_id)


def _zone_definition_entry(
    snapshot: PanelSnapshot, zone_id: int
) -> ZoneDefinition | None:
    return snapshot.zone_definitions.get(zone_id)


def _zone_definition_value(zone_definition: ZoneDefinition | None) -> str | None:
    if zone_definition is None or not zone_definition.definition:
        return None
    return str(zone_definition.definition)


def _zone_name(zone: ZoneState, zone_definition: ZoneDefinition | None) -> str | None:
    if zone_definition is not None and zone_definition.name:
        return str(zone_definition.name)
    if zone.name:
        return str(zone.name)
    return None


def _zone_device_class(
    zone_definition: ZoneDefinition | None,
) -> BinarySensorDeviceClass:
    zone_type = None
    if zone_definition is not None:
        zone_type = zone_definition.zone_type or zone_definition.kind
    # Temporary until elke27_lib ships py.typed (strict mypy treats fields as Any).
    if isinstance(zone_type, str):
        normalized = zone_type.lower()
        if "motion" in normalized:
            return BinarySensorDeviceClass.MOTION
        if "window" in normalized:
            return BinarySensorDeviceClass.WINDOW
        if "door" in normalized:
            return BinarySensorDeviceClass.DOOR
    return BinarySensorDeviceClass.OPENING
