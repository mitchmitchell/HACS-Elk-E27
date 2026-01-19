"""
elke27_lib/states.py

PanelState v0 (minimal, handler-friendly, HA-friendly).

Principles:
- IDs are 1-based; store by integer id without renumbering.
- Patch-style updates: handlers update only fields present in payloads.
- Timestamps use monotonic time (provided by the kernel).
- No I/O, no logging, no protocol knowledge here—this is pure state storage.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from elke27_lib.types import CsmSnapshot

# -------------------------
# Panel-level state
# -------------------------

@dataclass(slots=True)
class PanelMetaState:
    """
    Small "panel header" state owned by the kernel and updated by handlers.
    """
    session_id: int | None = None
    connected: bool = False

    # Monotonic timestamp of last message seen/processed.
    last_message_at: float | None = None

    # Optional panel/version info (filled when handlers decode device/version payloads)
    model: str | None = None
    firmware: str | None = None
    serial: str | None = None


# -------------------------
# Area state
# -------------------------

@dataclass(slots=True)
class AreaState:
    area_id: int

    # Identity/config
    name: str | None = None

    # Core status (strings depend on API enums; keep as str for now)
    armed_state: str | None = None
    alarm_state: str | None = None
    alarm_event: str | None = None
    arm_state: str | None = None
    ready_status: str | None = None

    # Common flags
    ready: bool | None = None
    stay: bool | None = None
    away: bool | None = None
    bypass: bool | None = None
    chime: bool | None = None
    entry_delay_active: bool | None = None
    exit_delay_active: bool | None = None
    trouble: bool | None = None

    # Common counts (if reported)
    num_not_ready_zones: int | None = None
    num_bypassed_zones: int | None = None

    # Response/error tracking
    last_error_code: int | None = None

    # Troubles list for area.get_troubles
    troubles: list[str] | None = None

    # Monotonic timestamp of last update to this area
    last_update_at: float | None = None


# -------------------------
# Zone state (stub v0)
# -------------------------

@dataclass(slots=True)
class ZoneState:
    zone_id: int

    name: str | None = None
    area_id: int | None = None
    definition: str | None = None
    flags: list[dict] | None = None

    enabled: bool | None = None
    bypassed: bool | None = None
    violated: bool | None = None
    trouble: bool | None = None
    tamper: bool | None = None
    alarm: bool | None = None
    low_battery: bool | None = None

    # Bulk status code (see zone.get_all_zones_status)
    status_code: str | None = None
    attribs: dict[str, object] = field(default_factory=dict)

    last_update_at: float | None = None


# -------------------------
# User state (stub v0)
# -------------------------

@dataclass(slots=True)
class UserState:
    user_id: int

    name: str | None = None
    group_id: int | None = None
    enabled: bool | None = None
    flags: list[dict] | None = None
    pin: int | None = None
    fields: dict[str, object] = field(default_factory=dict)
    last_update_at: float | None = None


# -------------------------
# Keypad state (stub v0)
# -------------------------

@dataclass(slots=True)
class KeypadState:
    keypad_id: int

    name: str | None = None
    area: int | None = None
    zone_id: int | None = None
    source_id: int | None = None
    device_id: str | None = None
    flags: list[dict] | None = None
    fields: dict[str, object] = field(default_factory=dict)
    last_update_at: float | None = None


# -------------------------
# Trouble state (stub v0)
# -------------------------

@dataclass(slots=True)
class TroubleState:
    active: bool | None = None
    last_update_at: float | None = None

    # Optional future expansion: named trouble bits, raw snapshots, etc.
    # bits: Dict[str, bool] = field(default_factory=dict)


@dataclass(slots=True)
class NetworkState:
    ssid_scan_results: list[dict[str, object]] = field(default_factory=list)
    rssi: int | None = None
    last_update_at: float | None = None


# -------------------------
# Output/Tstat state
# -------------------------

@dataclass(slots=True)
class OutputState:
    output_id: int

    name: str | None = None
    status: str | None = None
    on: bool | None = None
    status_code: str | None = None
    fields: dict[str, object] = field(default_factory=dict)
    last_update_at: float | None = None


@dataclass(slots=True)
class TstatState:
    tstat_id: int

    name: str | None = None
    temperature: int | None = None
    cool_setpoint: int | None = None
    heat_setpoint: int | None = None
    mode: str | None = None
    fan_mode: str | None = None
    humidity: int | None = None
    rssi: int | None = None
    battery_level: int | None = None
    prec: list[int] | None = None
    fields: dict[str, object] = field(default_factory=dict)
    last_update_at: float | None = None


# -------------------------
# Inventory state
# -------------------------

@dataclass(slots=True)
class InventoryState:
    configured_areas: set[int] = field(default_factory=set)
    configured_zones: set[int] = field(default_factory=set)
    configured_outputs: set[int] = field(default_factory=set)
    configured_users: set[int] = field(default_factory=set)
    configured_keypads: set[int] = field(default_factory=set)

    configured_area_blocks_seen: set[int] = field(default_factory=set)
    configured_zone_blocks_seen: set[int] = field(default_factory=set)
    configured_area_blocks_requested: set[int] = field(default_factory=set)
    configured_zone_blocks_requested: set[int] = field(default_factory=set)
    area_attribs_requested: set[int] = field(default_factory=set)
    zone_attribs_requested: set[int] = field(default_factory=set)
    output_attribs_requested: set[int] = field(default_factory=set)
    user_attribs_requested: set[int] = field(default_factory=set)
    keypad_attribs_requested: set[int] = field(default_factory=set)

    configured_area_block_count: int | None = None
    configured_zone_block_count: int | None = None
    configured_area_blocks_remaining: int | None = None
    configured_zone_blocks_remaining: int | None = None

    configured_areas_complete: bool = False
    configured_zones_complete: bool = False
    configured_outputs_complete: bool = False
    configured_users_complete: bool = False
    configured_keypads_complete: bool = False
    area_names_logged: bool = False
    zone_names_logged: bool = False
    invalid_id_streak_threshold: int = 3
    area_invalid_streak: int = 0
    zone_invalid_streak: int = 0
    area_last_invalid_id: int | None = None
    zone_last_invalid_id: int | None = None
    area_discovery_max_id: int | None = None
    zone_discovery_max_id: int | None = None


# -------------------------
# Root state container
# -------------------------

@dataclass(slots=True)
class PanelState:
    panel: PanelMetaState = field(default_factory=PanelMetaState)

    # Domain containers keyed by id
    areas: dict[int, AreaState] = field(default_factory=dict)
    zones: dict[int, ZoneState] = field(default_factory=dict)
    inventory: InventoryState = field(default_factory=InventoryState)
    zone_defs_by_id: dict[int, dict[str, object]] = field(default_factory=dict)
    zone_def_flags_by_id: dict[int, dict[str, object]] = field(default_factory=dict)
    zone_def_flags_by_name: dict[str, dict[str, object]] = field(default_factory=dict)
    outputs: dict[int, OutputState] = field(default_factory=dict)
    tstats: dict[int, TstatState] = field(default_factory=dict)
    users: dict[int, UserState] = field(default_factory=dict)
    keypads: dict[int, KeypadState] = field(default_factory=dict)

    troubles: TroubleState = field(default_factory=TroubleState)
    system_status: dict[str, object] = field(default_factory=dict)
    control_status: dict[str, object] = field(default_factory=dict)
    log_status: dict[str, object] = field(default_factory=dict)
    bus_io_status: dict[str, object] = field(default_factory=dict)
    network: NetworkState = field(default_factory=NetworkState)
    table_info_by_domain: dict[str, dict[str, object]] = field(default_factory=dict)
    table_info_known: set[str] = field(default_factory=set)
    domain_csm_by_name: dict[str, int] = field(default_factory=dict)
    table_csm_by_domain: dict[str, int] = field(default_factory=dict)
    csm_snapshot_version: int = 0
    csm_snapshot: CsmSnapshot | None = None
    bootstrap_counts_ready: bool = False
    rules: dict[int, dict[str, object]] = field(default_factory=dict)
    rules_block_count: int | None = None

    # Debug storage (off by default; kernel/handlers can choose to fill)
    debug_last_raw_by_route_enabled: bool = False
    debug_last_raw_by_route: dict[str, dict] = field(default_factory=dict)

    def get_or_create_area(self, area_id: int) -> AreaState:
        """
        Retrieve an AreaState by id; create if missing.
        """
        area = self.areas.get(area_id)
        if area is None:
            area = AreaState(area_id=area_id)
            self.areas[area_id] = area
        return area

    def get_or_create_zone(self, zone_id: int) -> ZoneState:
        """
        Retrieve a ZoneState by id; create if missing.
        """
        zone = self.zones.get(zone_id)
        if zone is None:
            zone = ZoneState(zone_id=zone_id)
            self.zones[zone_id] = zone
        return zone

    def get_or_create_output(self, output_id: int) -> OutputState:
        output = self.outputs.get(output_id)
        if output is None:
            output = OutputState(output_id=output_id)
            self.outputs[output_id] = output
        return output

    def get_or_create_user(self, user_id: int) -> UserState:
        user = self.users.get(user_id)
        if user is None:
            user = UserState(user_id=user_id)
            self.users[user_id] = user
        return user

    def get_or_create_keypad(self, keypad_id: int) -> KeypadState:
        keypad = self.keypads.get(keypad_id)
        if keypad is None:
            keypad = KeypadState(keypad_id=keypad_id)
            self.keypads[keypad_id] = keypad
        return keypad
    def get_or_create_tstat(self, tstat_id: int) -> TstatState:
        tstat = self.tstats.get(tstat_id)
        if tstat is None:
            tstat = TstatState(tstat_id=tstat_id)
            self.tstats[tstat_id] = tstat
        return tstat


def update_csm_snapshot(state: PanelState, *, updated_at: datetime | None = None) -> CsmSnapshot | None:
    """
    Build a normalized CSM snapshot and store it on state if changed.
    """
    if updated_at is None:
        updated_at = datetime.now(UTC)
    domain_csms = dict(state.domain_csm_by_name)
    table_csms = dict(state.table_csm_by_domain)

    existing = state.csm_snapshot
    if (
        existing is not None
        and dict(existing.domain_csms) == domain_csms
        and dict(existing.table_csms) == table_csms
    ):
        return None

    state.csm_snapshot_version += 1
    snapshot = CsmSnapshot(
        domain_csms=dict(domain_csms),
        table_csms=dict(table_csms),
        version=state.csm_snapshot_version,
        updated_at=updated_at,
    )
    state.csm_snapshot = snapshot
    return snapshot
