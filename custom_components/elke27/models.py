"""Models for the Elke27 integration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry

if TYPE_CHECKING:
    from .coordinator import Elke27DataUpdateCoordinator
    from .hub import Elke27Hub

type Elke27ConfigEntry = ConfigEntry[Elke27RuntimeData]


@dataclass(slots=True)
class Elke27RuntimeData:
    """Runtime data stored on the config entry."""

    hub: Elke27Hub
    coordinator: Elke27DataUpdateCoordinator
