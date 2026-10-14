"""Garage door covers for Elke27 barriers (open/close only)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components.cover import (
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import Elke27DataUpdateCoordinator
from .entity import (
    area_device_info,
    area_has_device,
    build_unique_id,
    device_info_for_entry,
    unique_base,
)

if TYPE_CHECKING:
    from elke27_lib import BarrierState, PanelSnapshot

    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.device_registry import DeviceInfo
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from .models import Elke27ConfigEntry

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0

_OPEN_STATUSES = frozenset({"OPEN"})
_CLOSED_STATUSES = frozenset({"CLOSE", "CLOSED"})


def barrier_is_closed(barrier: BarrierState) -> bool | None:
    """
    Map a barrier to HA is_closed: True, False, or None when unknown.

    Uses the library's normalised is_open when present; otherwise maps the raw
    status (OPEN, CLOSE/CLOSED). STOPPED and anything else are unknown.
    """
    is_open = getattr(barrier, "is_open", None)
    if isinstance(is_open, bool):
        return not is_open
    status = barrier.status
    if not isinstance(status, str):
        return None
    normalized = status.strip().upper()
    if normalized in _OPEN_STATUSES:
        return False
    if normalized in _CLOSED_STATUSES:
        return True
    return None


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: Elke27ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Elke27 garage door covers from a config entry."""
    coordinator = entry.runtime_data.coordinator
    known_ids: set[int] = set()

    def _async_add_barriers() -> None:
        snapshot = coordinator.data
        barriers = getattr(snapshot, "barriers", None) if snapshot else None
        if not barriers:
            return
        entities: list[Elke27BarrierCover] = []
        for barrier_id, barrier in barriers.items():
            if barrier_id in known_ids:
                continue
            known_ids.add(barrier_id)
            entities.append(Elke27BarrierCover(coordinator, entry, barrier_id, barrier))
        if entities:
            async_add_entities(entities)

    _async_add_barriers()
    entry.async_on_unload(coordinator.async_add_listener(_async_add_barriers))


def device_info_for_barrier(
    coordinator: Elke27DataUpdateCoordinator,
    entry: Elke27ConfigEntry,
    barrier: BarrierState,
) -> DeviceInfo:
    """Attach to the barrier's area device when it has one, else the panel."""
    snapshot = coordinator.data
    area_id = getattr(barrier, "area_id", None)
    if snapshot is not None and area_has_device(snapshot, area_id):
        return area_device_info(
            coordinator, entry, area_id, snapshot.areas.get(area_id)
        )
    return device_info_for_entry(coordinator, entry)


class Elke27BarrierCover(CoordinatorEntity[Elke27DataUpdateCoordinator], CoverEntity):
    """A garage door (barrier). No stop, no position, no travel states."""

    _attr_has_entity_name = True
    _attr_translation_key = "garage_door"
    _attr_device_class = CoverDeviceClass.GARAGE
    _attr_supported_features = CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE
    _attr_is_opening = False
    _attr_is_closing = False

    def __init__(
        self,
        coordinator: Elke27DataUpdateCoordinator,
        entry: Elke27ConfigEntry,
        barrier_id: int,
        barrier: BarrierState,
    ) -> None:
        """Initialize the cover."""
        super().__init__(coordinator)
        self._barrier_id = barrier_id
        self._attr_name = barrier.name or f"Garage door {barrier_id}"
        self._attr_unique_id = build_unique_id(
            unique_base(coordinator, entry), "barrier", barrier_id
        )
        self._attr_device_info = device_info_for_barrier(coordinator, entry, barrier)

    def _barrier(self) -> BarrierState | None:
        return _get_barrier(self.coordinator.data, self._barrier_id)

    @property
    def available(self) -> bool:
        """Return if the entity is available."""
        return (
            super().available
            and self.coordinator.is_ready
            and self._barrier() is not None
        )

    @property
    def is_closed(self) -> bool | None:
        """Return True when closed, False when open, None when unknown."""
        barrier = self._barrier()
        return None if barrier is None else barrier_is_closed(barrier)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the raw panel status (e.g. STOPPED) for troubleshooting."""
        barrier = self._barrier()
        return {"raw_status": barrier.status if barrier else None}

    async def async_open_cover(self, **_kwargs: Any) -> None:
        """Open the door (sent once, never retried)."""
        await self.coordinator.async_set_barrier(self._barrier_id, open_=True)

    async def async_close_cover(self, **_kwargs: Any) -> None:
        """Close the door (sent once, never retried)."""
        await self.coordinator.async_set_barrier(self._barrier_id, open_=False)


def _get_barrier(
    snapshot: PanelSnapshot | None, barrier_id: int
) -> BarrierState | None:
    barriers = getattr(snapshot, "barriers", None) if snapshot else None
    return barriers.get(barrier_id) if barriers else None
