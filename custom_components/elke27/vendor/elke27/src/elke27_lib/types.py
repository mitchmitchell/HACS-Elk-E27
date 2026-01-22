"""Public v2 types for Elke27 (HA-first surface)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum


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
    logger_name: str | None = None
    session_wire_log: bool = False


@dataclass(frozen=True, slots=True)
class DiscoveredPanel:
    """
    Immutable discovery result for a single panel.
    """

    host: str
    port: int
    tls_port: int | None = None
    panel_name: str | None = None
    panel_serial: str | None = None
    panel_mac: str | None = None


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
    def from_json(cls, data: dict[str, str]) -> LinkKeys:
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
    raw_type: str | None = None


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

    mac: str | None = None
    model: str | None = None
    firmware: str | None = None
    serial: str | None = None


@dataclass(frozen=True, slots=True)
class TableInfo:
    """
    Immutable snapshot of table metadata.
    """

    areas: int | None = None
    zones: int | None = None
    outputs: int | None = None
    tstats: int | None = None


@dataclass(frozen=True, slots=True)
class ZoneDefinition:
    """
    Immutable zone definition snapshot.
    """

    zone_id: int
    name: str | None = None
    definition: str | None = None
    zone_type: str | None = None
    kind: str | None = None


@dataclass(frozen=True, slots=True)
class OutputDefinition:
    """
    Immutable output definition snapshot.
    """

    output_id: int
    name: str | None = None


@dataclass(frozen=True, slots=True)
class CsmSnapshot:
    """
    Snapshot of CSM values (treat as immutable).

    domain_csms: normalized per-domain CSMs (e.g., "zone": 123)
    table_csms: normalized per-domain table CSMs (e.g., "zone": 456)
    """

    domain_csms: Mapping[str, int]
    table_csms: Mapping[str, int]
    version: int
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class CsmDiff:
    """
    Optional diff summary for CSM updates (e.g., in tests).
    """

    changed_domain_csms: set[str]
    changed_table_csms: set[str]


@dataclass(frozen=True, slots=True)
class AreaState:
    """
    Immutable area state snapshot.
    """

    area_id: int
    name: str | None = None
    arm_mode: ArmMode | None = None
    ready: bool | None = None
    alarm_active: bool | None = None
    chime: bool | None = None


@dataclass(frozen=True, slots=True)
class ZoneState:
    """
    Immutable zone state snapshot.
    """

    zone_id: int
    name: str | None = None
    open: bool | None = None
    bypassed: bool | None = None
    trouble: bool | None = None
    alarm: bool | None = None
    tamper: bool | None = None
    low_battery: bool | None = None


@dataclass(frozen=True, slots=True)
class OutputState:
    """
    Immutable output state snapshot.
    """

    output_id: int
    name: str | None = None
    state: bool | None = None


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
    zone_definitions: Mapping[int, ZoneDefinition]
    outputs: Mapping[int, OutputState]
    output_definitions: Mapping[int, OutputDefinition]
    version: int
    updated_at: datetime

    @classmethod
    def empty(cls) -> PanelSnapshot:
        """Return an empty snapshot placeholder."""
        return cls(
            panel=PanelInfo(),
            table_info=TableInfo(),
            areas={},
            zones={},
            zone_definitions={},
            outputs={},
            output_definitions={},
            version=0,
            updated_at=datetime.min.replace(tzinfo=UTC),
        )
