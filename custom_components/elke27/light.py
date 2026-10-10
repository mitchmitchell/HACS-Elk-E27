"""Lights for Elke27 lights."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, ClassVar

from elke27_lib.errors import Elke27PinRequiredError

from homeassistant.components.light import ATTR_BRIGHTNESS, ColorMode, LightEntity
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import Elke27DataUpdateCoordinator
from .entity import (
    build_unique_id,
    device_info_for_entry,
    raise_if_not_sent,
    sanitize_name,
    unique_base,
)

if TYPE_CHECKING:
    from elke27_lib import LightState, PanelSnapshot

    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from .hub import Elke27Hub
    from .models import Elke27ConfigEntry

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0
_ELK_MAX_DIM_LEVEL = 99


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: Elke27ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Elke27 lights from a config entry."""
    data = entry.runtime_data
    hub = data.hub
    coordinator = data.coordinator
    known_ids: set[int] = set()

    def _async_add_lights() -> None:
        snapshot = coordinator.data
        if snapshot is None:
            _LOGGER.debug("Light entities skipped because snapshot is unavailable")
            return
        entities: list[Elke27Light] = []
        if not snapshot.lights:
            _LOGGER.debug("No lights available for entity creation")
            return
        for light_id, light in snapshot.lights.items():
            if light_id in known_ids:
                continue
            known_ids.add(light_id)
            entities.append(Elke27Light(coordinator, hub, entry, light_id, light))
        if entities:
            async_add_entities(entities)

    _async_add_lights()
    entry.async_on_unload(coordinator.async_add_listener(_async_add_lights))


class Elke27Light(CoordinatorEntity[Elke27DataUpdateCoordinator], LightEntity):
    """Representation of an Elke27 light."""

    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_supported_color_modes: ClassVar[set[ColorMode]] = {ColorMode.BRIGHTNESS}
    _attr_has_entity_name = True
    _attr_translation_key = "light"

    def __init__(
        self,
        coordinator: Elke27DataUpdateCoordinator,
        hub: Elke27Hub,
        entry: Elke27ConfigEntry,
        light_id: int,
        light: LightState,
    ) -> None:
        """Initialize the light entity."""
        super().__init__(coordinator)
        self._hub = hub
        self._entry = entry
        self._light_id = light_id
        self._attr_name = sanitize_name(light.name) or f"Light {light_id}"
        self._attr_unique_id = build_unique_id(
            unique_base(hub, coordinator, entry),
            "light",
            light_id,
        )
        self._attr_device_info = device_info_for_entry(hub, coordinator, entry)
        self._missing_logged = False

    @property
    def is_on(self) -> bool | None:
        """Return if the light is on."""
        light = _get_light(self.coordinator.data, self._light_id)
        if light is None:
            self._log_missing()
            return None
        if light.state is not None:
            return light.state
        if light.level is not None:
            return light.level > 0
        return None

    @property
    def brightness(self) -> int | None:
        """Return the current brightness value (0-255)."""
        light = _get_light(self.coordinator.data, self._light_id)
        if light is None:
            return None
        level = light.level
        if level is None:
            return None
        bounded = max(0, min(_ELK_MAX_DIM_LEVEL, level))
        return round(bounded * 255 / _ELK_MAX_DIM_LEVEL)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the light on if supported by the client."""
        try:
            if ATTR_BRIGHTNESS in kwargs:
                level = _level_from_kwargs(kwargs)
                sent = await self._hub.async_set_light(
                    self._light_id, state=True, level=level
                )
                raise_if_not_sent(sent=sent, hub=self._hub)
            else:
                sent = await self._hub.async_set_light(
                    self._light_id, state=True, level=_ELK_MAX_DIM_LEVEL
                )
                raise_if_not_sent(sent=sent, hub=self._hub)
        except Elke27PinRequiredError as err:
            msg = "PIN required to perform this action."
            raise HomeAssistantError(msg) from err

    async def async_turn_off(self, **_kwargs: Any) -> None:
        """Turn the light off if supported by the client."""
        try:
            sent = await self._hub.async_set_light(self._light_id, state=False, level=0)
            raise_if_not_sent(sent=sent, hub=self._hub)
        except Elke27PinRequiredError as err:
            msg = "PIN required to perform this action."
            raise HomeAssistantError(msg) from err

    @property
    def available(self) -> bool:
        """Return if the entity is available."""
        return (
            super().available
            and self._hub.is_ready
            and _get_light(self.coordinator.data, self._light_id) is not None
        )

    def _log_missing(self) -> None:
        """Log when the light snapshot is missing."""
        if self._missing_logged:
            return
        self._missing_logged = True
        _LOGGER.debug("Light %s missing from snapshot", self._light_id)


def _level_from_kwargs(kwargs: dict[str, Any]) -> int:
    """Map Home Assistant brightness kwargs to Elk level (0-100)."""
    brightness = kwargs.get(ATTR_BRIGHTNESS)
    if isinstance(brightness, int):
        bounded = max(0, min(255, brightness))
        # Keep minimum 1 for ON requests with brightness set.
        return max(1, round(bounded * _ELK_MAX_DIM_LEVEL / 255))
    return _ELK_MAX_DIM_LEVEL


def _get_light(snapshot: PanelSnapshot | None, light_id: int) -> LightState | None:
    if snapshot is None:
        return None
    return snapshot.lights.get(light_id)
