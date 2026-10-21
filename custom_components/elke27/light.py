"""Lights for Elke27 lights."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from elke27_lib.errors import Elke27PinRequiredError

from homeassistant.components.light import ATTR_BRIGHTNESS, LightEntity
from homeassistant.components.light.const import ColorMode
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import Elke27DataUpdateCoordinator
from .entity import (
    build_unique_id,
    device_info_for_entry,
    raise_if_not_sent,
    unique_base,
)
from .errors import PinAction, pin_required_error

if TYPE_CHECKING:
    from elke27_lib import LightState, PanelSnapshot

    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from .models import Elke27ConfigEntry

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0
# Panel light levels (confirmed by Elk, #99): a command level is 1-99, where
# 99 is full brightness; a command of 100 means "on at the last level". The
# panel reports full as 100, so readback stays on the 0-100 scale.
_ELK_MAX_DIM_LEVEL = 100
_ELK_MAX_COMMAND_LEVEL = 99
_ELK_RESTORE_LAST_LEVEL = 100


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: Elke27ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Elke27 lights from a config entry."""
    data = entry.runtime_data
    coordinator = data.coordinator
    known_ids: set[int] = set()

    def _async_add_lights() -> None:
        snapshot = coordinator.data
        entities: list[Elke27Light] = []
        if not snapshot.lights:
            _LOGGER.debug("No lights available for entity creation")
            return
        for light_id, light in snapshot.lights.items():
            if light_id in known_ids:
                continue
            known_ids.add(light_id)
            entities.append(Elke27Light(coordinator, entry, light_id, light))
        if entities:
            async_add_entities(entities)

    _async_add_lights()
    entry.async_on_unload(coordinator.async_add_listener(_async_add_lights))


class Elke27Light(CoordinatorEntity[Elke27DataUpdateCoordinator], LightEntity):
    """Representation of an Elke27 light."""

    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}  # noqa: RUF012
    _attr_has_entity_name = True
    _attr_translation_key = "light"

    def __init__(
        self,
        coordinator: Elke27DataUpdateCoordinator,
        entry: Elke27ConfigEntry,
        light_id: int,
        light: LightState,
    ) -> None:
        """Initialize the light entity."""
        super().__init__(coordinator)
        self._entry = entry
        self._light_id = light_id
        self._attr_name = light.name or f"Light {light_id}"
        self._attr_unique_id = build_unique_id(
            unique_base(coordinator, entry),
            light_id,
        )
        self._attr_device_info = device_info_for_entry(coordinator, entry)
        self._missing_logged = False

    @property
    def is_on(self) -> bool | None:
        """Return if the light is on."""
        light = _get_light(self.coordinator.data, self._light_id)
        if light is None:
            self._log_missing()
            return None
        if light.state is not None:
            return bool(light.state)
        level = light.level
        # elke27_lib has no py.typed yet; narrow Any snapshot fields until typed.
        if isinstance(level, int | float):
            return level > 0
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
        # elke27_lib has no py.typed yet; narrow Any snapshot fields until typed.
        level_int = int(level) if not isinstance(level, int) else level
        return _brightness_from_level(level_int)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the light on if supported by the client."""
        try:
            if ATTR_BRIGHTNESS in kwargs:
                level = _level_from_kwargs(kwargs)
                sent = await self.coordinator.async_set_light(
                    self._light_id, state=True, level=level
                )
                raise_if_not_sent(sent=sent, coordinator=self.coordinator)
            else:
                sent = await self.coordinator.async_set_light(
                    self._light_id, state=True, level=_ELK_RESTORE_LAST_LEVEL
                )
                raise_if_not_sent(sent=sent, coordinator=self.coordinator)
        except Elke27PinRequiredError as err:
            action: PinAction = "control"
            raise pin_required_error(action) from err

    async def async_turn_off(self, **_kwargs: Any) -> None:
        """Turn the light off if supported by the client."""
        try:
            sent = await self.coordinator.async_set_light(
                self._light_id, state=False, level=0
            )
            raise_if_not_sent(sent=sent, coordinator=self.coordinator)
        except Elke27PinRequiredError as err:
            action: PinAction = "control"
            raise pin_required_error(action) from err

    @property
    def available(self) -> bool:
        """Return if the entity is available."""
        return (
            super().available
            and self.coordinator.is_ready
            and _get_light(self.coordinator.data, self._light_id) is not None
        )

    def _log_missing(self) -> None:
        """Log when the light snapshot is missing."""
        if self._missing_logged:
            return
        self._missing_logged = True
        _LOGGER.debug("Light %s missing from snapshot", self._light_id)


def _brightness_from_level(level: int) -> int:
    """Map an Elk level (0-100) to Home Assistant brightness (0-255), half up."""
    bounded = max(0, min(_ELK_MAX_DIM_LEVEL, level))
    return (bounded * 255 + _ELK_MAX_DIM_LEVEL // 2) // _ELK_MAX_DIM_LEVEL


def _level_from_brightness(brightness: int) -> int:
    """
    Map Home Assistant brightness (0-255) to an Elk command level (0-99), half up.

    The 0-100 scale is kept so levels 1-99 round-trip with
    _brightness_from_level; only the top is capped at 99 (full), because a
    command of 100 means "on at the last level" to the panel (#99).
    """
    bounded = max(0, min(255, brightness))
    level = (bounded * _ELK_MAX_DIM_LEVEL + 127) // 255
    return min(_ELK_MAX_COMMAND_LEVEL, level)


def _level_from_kwargs(kwargs: dict[str, Any]) -> int:
    """Map Home Assistant brightness kwargs to an Elk command level (1-99)."""
    brightness = kwargs.get(ATTR_BRIGHTNESS)
    if isinstance(brightness, int):
        # Keep minimum 1 for ON requests with brightness set.
        return max(1, _level_from_brightness(brightness))
    return _ELK_MAX_COMMAND_LEVEL


def _get_light(snapshot: PanelSnapshot, light_id: int) -> LightState | None:
    return snapshot.lights.get(light_id)
