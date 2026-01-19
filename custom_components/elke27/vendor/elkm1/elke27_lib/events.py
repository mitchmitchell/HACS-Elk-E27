"""
elke27_lib/events.py

Event dataclasses v0 (clean model).

Rules:
- Handlers construct event objects directly, with placeholder header fields.
- kernel.emit() stamps the authoritative header fields from DispatchContext and enqueues.
- No payload helper functions (cleanest possible API surface).
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from elke27_lib.types import CsmSnapshot

RouteKey = tuple[str, str]


# -------------------------
# Common header (stamped by kernel.emit)
# -------------------------

@dataclass(frozen=True, slots=True)
class Event:
    # Common header fields (kernel.emit overwrites these unconditionally)
    kind: str
    at: float
    seq: int | None
    classification: str
    route: RouteKey
    session_id: int | None

    @property
    def domain(self) -> str:
        return self.route[0]


# Placeholder header values for handlers (optional convenience constants)
UNSET_ROUTE: RouteKey = ("__unset__", "__unset__")
UNSET_AT: float = 0.0
UNSET_SEQ: int | None = None
UNSET_CLASSIFICATION: str = "UNKNOWN"
UNSET_SESSION_ID: int | None = None


# -------------------------
# Connection lifecycle
# -------------------------

@dataclass(frozen=True, slots=True)
class ConnectionStateChanged(Event):
    KIND = "connection_state_changed"

    connected: bool
    reason: str | None = None
    error_type: str | None = None


# -------------------------
# Area events
# -------------------------

@dataclass(frozen=True, slots=True)
class AreaStatusUpdated(Event):
    KIND = "area_status_updated"

    area_id: int
    changed_fields: tuple[str, ...]  # sorted tuple for deterministic tests/logs


@dataclass(frozen=True, slots=True)
class AreaAttribsUpdated(Event):
    KIND = "area_attribs_updated"

    area_id: int
    changed_fields: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AreaConfiguredUpdated(Event):
    KIND = "area_configured_updated"

    configured_ids: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class AreaConfiguredInventoryReady(Event):
    KIND = "area_configured_inventory_ready"


# -------------------------
# Zone events
# -------------------------

@dataclass(frozen=True, slots=True)
class ZoneConfiguredUpdated(Event):
    KIND = "zone_configured_updated"

    configured_ids: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class ZoneConfiguredInventoryReady(Event):
    KIND = "zone_configured_inventory_ready"


@dataclass(frozen=True, slots=True)
class ZoneStatusUpdated(Event):
    KIND = "zone_status_updated"

    zone_id: int
    changed_fields: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ZonesStatusBulkUpdated(Event):
    KIND = "zones_status_bulk_updated"

    updated_count: int
    updated_ids: tuple[int, ...]


# -------------------------
# Zone definitions
# -------------------------

@dataclass(frozen=True, slots=True)
class ZoneDefsUpdated(Event):
    KIND = "zone_defs_updated"

    count: int
    updated_ids: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class ZoneDefFlagsUpdated(Event):
    KIND = "zone_def_flags_updated"

    count: int


# -------------------------
# Zone attribs
# -------------------------

@dataclass(frozen=True, slots=True)
class ZoneAttribsUpdated(Event):
    KIND = "zone_attribs_updated"

    zone_id: int
    changed_fields: tuple[str, ...]


# -------------------------
# Area troubles
# -------------------------

@dataclass(frozen=True, slots=True)
class AreaTroublesUpdated(Event):
    KIND = "area_troubles_updated"

    area_id: int | None
    troubles: tuple[str, ...]


# -------------------------
# Output events
# -------------------------

@dataclass(frozen=True, slots=True)
class OutputConfiguredUpdated(Event):
    KIND = "output_configured_updated"

    configured_ids: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class OutputConfiguredInventoryReady(Event):
    KIND = "output_configured_inventory_ready"


@dataclass(frozen=True, slots=True)
class UserConfiguredInventoryReady(Event):
    KIND = "user_configured_inventory_ready"


@dataclass(frozen=True, slots=True)
class KeypadConfiguredInventoryReady(Event):
    KIND = "keypad_configured_inventory_ready"


@dataclass(frozen=True, slots=True)
class OutputStatusUpdated(Event):
    KIND = "output_status_updated"

    output_id: int
    status: str | None
    on: bool | None


@dataclass(frozen=True, slots=True)
class OutputsStatusBulkUpdated(Event):
    KIND = "outputs_status_bulk_updated"

    updated_count: int
    updated_ids: tuple[int, ...]


# -------------------------
# Tstat events
# -------------------------

@dataclass(frozen=True, slots=True)
class TstatStatusUpdated(Event):
    KIND = "tstat_status_updated"

    tstat_id: int
    mode: str | None
    fan_mode: str | None
    temperature: int | None


# -------------------------
# Network events
# -------------------------

@dataclass(frozen=True, slots=True)
class NetworkSsidResultsUpdated(Event):
    KIND = "network_ssid_results_updated"

    count: int
    ssids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class NetworkRssiUpdated(Event):
    KIND = "network_rssi_updated"

    rssi: int | None


# -------------------------
# Table info events
# -------------------------

@dataclass(frozen=True, slots=True)
class AreaTableInfoUpdated(Event):
    KIND = "area_table_info_updated"

    table_elements: int | None
    increment_size: int | None
    table_csm: int | None


@dataclass(frozen=True, slots=True)
class ZoneTableInfoUpdated(Event):
    KIND = "zone_table_info_updated"

    table_elements: int | None
    increment_size: int | None
    table_csm: int | None


@dataclass(frozen=True, slots=True)
class OutputTableInfoUpdated(Event):
    KIND = "output_table_info_updated"

    table_elements: int | None
    increment_size: int | None
    table_csm: int | None


@dataclass(frozen=True, slots=True)
class TstatTableInfoUpdated(Event):
    KIND = "tstat_table_info_updated"

    table_elements: int | None
    increment_size: int | None
    table_csm: int | None


# -------------------------
# CSM events
# -------------------------

@dataclass(frozen=True, slots=True)
class CsmSnapshotUpdated(Event):
    KIND = "csm_snapshot_updated"

    snapshot: CsmSnapshot


@dataclass(frozen=True, slots=True)
class DomainCsmChanged(Event):
    KIND = "domain_csm_changed"

    domain: str = ""
    old: int | None = None
    new: int = 0


@dataclass(frozen=True, slots=True)
class TableCsmChanged(Event):
    KIND = "table_csm_changed"

    domain: str = ""
    old: int | None = None
    new: int = 0


# -------------------------
# Trouble / diagnostics
# -------------------------

@dataclass(frozen=True, slots=True)
class TroubleStatusUpdated(Event):
    KIND = "trouble_status_updated"

    active: bool | None
    changed_fields: tuple[str, ...]


# -------------------------
# Panel version info
# -------------------------

@dataclass(frozen=True, slots=True)
class PanelVersionInfoUpdated(Event):
    KIND = "panel_version_info_updated"

    changed_fields: tuple[str, ...]


# -------------------------
# API / protocol errors
# -------------------------

@dataclass(frozen=True, slots=True)
class ApiError(Event):
    KIND = "api_error"

    error_code: int
    scope: str | None = None
    entity_id: int | None = None
    message: str | None = None


@dataclass(frozen=True, slots=True)
class AuthorizationRequiredEvent(Event):
    KIND = "authorization_required"

    error_code: int
    scope: str | None = None
    entity_id: int | None = None
    message: str | None = None


@dataclass(frozen=True, slots=True)
class AuthenticateResult(Event):
    KIND = "authenticate_result"

    success: bool
    error_code: int | None = None


@dataclass(frozen=True, slots=True)
class BootstrapCountsReady(Event):
    KIND = "bootstrap_counts_ready"


@dataclass(frozen=True, slots=True)
class DispatchRoutingError(Event):
    KIND = "dispatch_routing_error"

    code: str
    message: str
    keys: tuple[str, ...]
    severity: str  # "debug"|"info"|"warning"|"error"


# -------------------------
# Unknown/unhandled
# -------------------------

@dataclass(frozen=True, slots=True)
class UnknownMessage(Event):
    KIND = "unknown_message"

    unhandled_route: RouteKey
    keys: tuple[str, ...]


# -------------------------
# Stamping helper (used by kernel.emit)
# -------------------------

def stamp_event(
    evt: Event,
    *,
    at: float,
    seq: int | None,
    classification: str,
    route: RouteKey,
    session_id: int | None,
) -> Event:
    """
    Replace the common header fields on an event.
    kernel.emit() uses this to make headers authoritative and consistent.
    """
    return replace(
        evt,
        at=at,
        seq=seq,
        classification=classification,
        route=route,
        session_id=session_id,
    )
