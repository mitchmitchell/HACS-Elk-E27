"""Public v2 types for Elke27 (HA-first surface)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Mapping, Optional


@dataclass(frozen=True, slots=True)
class ClientConfig:
    """
    Immutable client configuration for the v2 public API.

    This config is intended to be provided once at construction time and
    treated as read-only thereafter.
    """

    event_queue_maxlen: int = 0
    event_queue_size: int = 256
    request_timeout_s: float = 5.0
    outbound_min_interval_s: float = 0.05
    outbound_max_burst: int = 1
    logger_name: Optional[str] = None


@dataclass(frozen=True, slots=True)
class DiscoveredPanel:
    """
    Immutable discovery result for a single panel.
    """

    host: str
    port: int
    tls_port: Optional[int] = None
    panel_name: Optional[str] = None
    panel_serial: Optional[str] = None
    panel_mac: Optional[str] = None


@dataclass(frozen=True, slots=True)
class LinkKeys:
    """
    Immutable link keys used for subsequent connections.
    """

    tempkey_hex: str
    linkkey_hex: str
    linkhmac_hex: str

    def to_json(self) -> dict[str, str]:
        """Return a JSON-serializable representation (no redaction applied)."""
        return {
            "tempkey_hex": self.tempkey_hex,
            "linkkey_hex": self.linkkey_hex,
            "linkhmac_hex": self.linkhmac_hex,
        }

    @classmethod
    def from_json(cls, data: dict[str, str]) -> "LinkKeys":
        """Create LinkKeys from a JSON-serializable representation."""
        return cls(
            tempkey_hex=str(data.get("tempkey_hex", "")),
            linkkey_hex=str(data.get("linkkey_hex", "")),
            linkhmac_hex=str(data.get("linkhmac_hex", "")),
        )


class EventType(str, Enum):
    """High-level event categories for the v2 public API."""

    READY = "ready"
    DISCONNECTED = "disconnected"
    CONNECTION = "connection"
    PANEL = "panel"
    AREA = "area"
    ZONE = "zone"
    OUTPUT = "output"
    SYSTEM = "system"


@dataclass(frozen=True, slots=True)
class Elke27Event:
    """
    Typed event emitted by the v2 public API.

    Events are immutable. Consumers should treat each event as a point-in-time
    observation rather than a mutable object that can be updated in place.
    """

    event_type: EventType
    data: Mapping[str, object]
    seq: int
    timestamp: datetime
    raw_type: Optional[str] = None


class ArmMode(str, Enum):
    """Arm/disarm modes for areas."""

    DISARMED = "disarmed"
    ARMED_STAY = "armed_stay"
    ARMED_AWAY = "armed_away"
    ARMED_NIGHT = "armed_night"


@dataclass(frozen=True, slots=True)
class PanelInfo:
    """
    Immutable panel information snapshot.
    """

    mac: Optional[str] = None
    model: Optional[str] = None
    firmware: Optional[str] = None
    serial: Optional[str] = None


@dataclass(frozen=True, slots=True)
class TableInfo:
    """
    Immutable snapshot of table metadata.
    """

    areas: Optional[int] = None
    zones: Optional[int] = None
    outputs: Optional[int] = None
    tstats: Optional[int] = None


@dataclass(frozen=True, slots=True)
class AreaState:
    """
    Immutable area state snapshot.
    """

    area_id: int
    name: Optional[str] = None
    arm_mode: Optional[ArmMode] = None
    ready: Optional[bool] = None
    alarm_active: Optional[bool] = None
    chime: Optional[bool] = None


@dataclass(frozen=True, slots=True)
class ZoneState:
    """
    Immutable zone state snapshot.
    """

    zone_id: int
    name: Optional[str] = None
    open: Optional[bool] = None
    bypassed: Optional[bool] = None
    trouble: Optional[bool] = None
    alarm: Optional[bool] = None
    tamper: Optional[bool] = None
    low_battery: Optional[bool] = None


@dataclass(frozen=True, slots=True)
class OutputState:
    """
    Immutable output state snapshot.
    """

    output_id: int
    name: Optional[str] = None
    state: Optional[bool] = None


@dataclass(frozen=True, slots=True)
class PanelSnapshot:
    """
    Immutable, atomic snapshot of panel state.

    The snapshot is replaced wholesale when new data is available. Consumers
    should treat the entire snapshot as immutable and replace references when
    updated rather than mutating fields in place.
    """

    panel: PanelInfo
    table_info: TableInfo
    areas: Mapping[int, AreaState]
    zones: Mapping[int, ZoneState]
    outputs: Mapping[int, OutputState]
    version: int
    updated_at: datetime

    @classmethod
    def empty(cls) -> "PanelSnapshot":
        """Return an empty snapshot placeholder."""
        return cls(
            panel=PanelInfo(),
            table_info=TableInfo(),
            areas={},
            zones={},
            outputs={},
            version=0,
            updated_at=datetime.min.replace(tzinfo=timezone.utc),
        )
