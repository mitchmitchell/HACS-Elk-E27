"""
elke27_lib/handlers/zone.py

Read/observe-only handlers for the "zone" domain.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any, cast

from elke27_lib.dispatcher import DispatchContext, PagedBlock
from elke27_lib.events import (
    UNSET_AT,
    UNSET_CLASSIFICATION,
    UNSET_ROUTE,
    UNSET_SEQ,
    UNSET_SESSION_ID,
    ApiError,
    AuthorizationRequiredEvent,
    BootstrapCountsReady,
    CsmSnapshotUpdated,
    DispatchRoutingError,
    Event,
    TableCsmChanged,
    ZoneAttribsUpdated,
    ZoneConfiguredInventoryReady,
    ZoneConfiguredUpdated,
    ZoneDefFlagsUpdated,
    ZoneDefsUpdated,
    ZonesStatusBulkUpdated,
    ZoneStatusUpdated,
    ZoneTableInfoUpdated,
)
from elke27_lib.states import InventoryState, PanelState, ZoneState, update_csm_snapshot

EmitFn = Callable[[Event, DispatchContext], None]
NowFn = Callable[[], float]

LOG = logging.getLogger(__name__)


def _as_mapping(obj: object) -> Mapping[str, Any] | None:
    if isinstance(obj, Mapping):
        return cast(Mapping[str, Any], obj)
    return None


def _coerce_int(value: object) -> int | None:
    return value if isinstance(value, int) else None


@dataclass(frozen=True, slots=True)
class _ConfiguredOutcome:
    configured_ids: tuple[int, ...]
    warnings: tuple[str, ...]
    completed_now: bool


@dataclass(frozen=True, slots=True)
class _BulkStatusOutcome:
    updated_ids: tuple[int, ...]
    warnings: tuple[str, ...]


_ZONE_STATUS_FIELDS: dict[str, str] = {
    "BYPASSED": "bypassed",
    "name": "name",
    "area_id": "area_id",
    "enabled": "enabled",
    "bypassed": "bypassed",
    "violated": "violated",
    "trouble": "trouble",
    "tamper": "tamper",
    "alarm": "alarm",
    "low_batt": "low_battery",
    "low_battery": "low_battery",
}

_ZONE_STATUS_TYPES: dict[str, type] = {
    "BYPASSED": bool,
    "name": str,
    "area_id": int,
    "enabled": bool,
    "bypassed": bool,
    "violated": bool,
    "trouble": bool,
    "tamper": bool,
    "alarm": bool,
    "low_batt": bool,
    "low_battery": bool,
}


def make_zone_get_configured_handler(
    state: PanelState,
    emit: EmitFn,
    now: NowFn,
):
    """
    Handler for ("zone","get_configured").
    """

    def handler_zone_get_configured(msg: Mapping[str, Any], ctx: DispatchContext) -> bool:
        zone_obj = _as_mapping(msg.get("zone"))
        if zone_obj is None:
            return False

        payload = _as_mapping(zone_obj.get("get_configured"))
        if payload is None:
            return False
        LOG.debug("zone.get_configured response keys=%s", sorted(payload))

        error_code = payload.get("error_code", zone_obj.get("error_code"))
        if isinstance(error_code, int) and error_code != 0:
            if error_code == 11008:
                emit(
                    AuthorizationRequiredEvent(
                        kind=AuthorizationRequiredEvent.KIND,
                        at=UNSET_AT,
                        seq=UNSET_SEQ,
                        classification=UNSET_CLASSIFICATION,
                        route=UNSET_ROUTE,
                        session_id=UNSET_SESSION_ID,
                        error_code=error_code,
                        scope="zone",
                        entity_id=None,
                        message=None,
                    ),
                    ctx,
                )
                return True
            emit(
                ApiError(
                    kind=ApiError.KIND,
                    at=UNSET_AT,
                    seq=UNSET_SEQ,
                    classification=UNSET_CLASSIFICATION,
                    route=UNSET_ROUTE,
                    session_id=UNSET_SESSION_ID,
                    error_code=error_code,
                    scope="zone",
                    entity_id=None,
                    message=None,
                ),
                ctx,
            )
            return True

        outcome = _reconcile_configured_zones(state, payload, now=now())
        if outcome.configured_ids:
            emit(
                ZoneConfiguredUpdated(
                    kind=ZoneConfiguredUpdated.KIND,
                    at=UNSET_AT,
                    seq=UNSET_SEQ,
                    classification=UNSET_CLASSIFICATION,
                    route=UNSET_ROUTE,
                    session_id=UNSET_SESSION_ID,
                    configured_ids=outcome.configured_ids,
                ),
                ctx,
            )

        if outcome.completed_now:
            if LOG.isEnabledFor(logging.DEBUG):
                zone_names = [
                    (zone_id, getattr(state.zones.get(zone_id), "name", None))
                    for zone_id in sorted(state.inventory.configured_zones)
                ]
                LOG.debug(
                    "zone.get_configured complete: ids=%s names=%s",
                    len(state.inventory.configured_zones),
                    zone_names,
                )
            emit(
                ZoneConfiguredInventoryReady(
                    kind=ZoneConfiguredInventoryReady.KIND,
                    at=UNSET_AT,
                    seq=UNSET_SEQ,
                    classification=UNSET_CLASSIFICATION,
                    route=UNSET_ROUTE,
                    session_id=UNSET_SESSION_ID,
                ),
                ctx,
            )

        if outcome.warnings:
            emit(
                DispatchRoutingError(
                    kind=DispatchRoutingError.KIND,
                    at=UNSET_AT,
                    seq=UNSET_SEQ,
                    classification=UNSET_CLASSIFICATION,
                    route=UNSET_ROUTE,
                    session_id=UNSET_SESSION_ID,
                    code="schema_warnings",
                    message="zone.get_configured payload contained type/schema warnings.",
                    keys=outcome.warnings,
                    severity="info",
                ),
                ctx,
            )

        return True

    return handler_zone_get_configured


def make_zone_configured_merge(
    _state: PanelState,
) -> Callable[[list[PagedBlock], int], Mapping[str, Any]]:
    """
    Merge paged get_configured blocks into a single payload (ADR-0013).
    """

    def _merge(blocks: list[PagedBlock], block_count: int) -> Mapping[str, Any]:
        warnings: list[str] = []
        merged_ids: list[int] = []
        for block in blocks:
            ids = _extract_configured_zone_ids(block.payload, warnings)
            merged_ids.extend(ids)
        merged = _dedupe_sorted(merged_ids)
        return {"zones": merged, "block_count": block_count}

    return _merge


def make_zone_get_attribs_handler(state: PanelState, emit: EmitFn, now: NowFn):
    """
    Handler for ("zone","get_attribs").
    """

    def handler_zone_get_attribs(msg: Mapping[str, Any], ctx: DispatchContext) -> bool:
        zone_obj = _as_mapping(msg.get("zone"))
        if zone_obj is None:
            return False

        payload = _as_mapping(zone_obj.get("get_attribs"))
        if payload is None:
            return False

        error_code = payload.get("error_code", zone_obj.get("error_code"))
        if isinstance(error_code, int) and error_code != 0:
            if error_code == 11008:
                emit(
                    AuthorizationRequiredEvent(
                        kind=AuthorizationRequiredEvent.KIND,
                        at=UNSET_AT,
                        seq=UNSET_SEQ,
                        classification=UNSET_CLASSIFICATION,
                        route=UNSET_ROUTE,
                        session_id=UNSET_SESSION_ID,
                        error_code=error_code,
                        scope="zone",
                        entity_id=_coerce_int(payload.get("zone_id")),
                        message=None,
                    ),
                    ctx,
                )
                return True
            if error_code == 11006:
                zone_id = payload.get("zone_id")
                if isinstance(zone_id, int) and zone_id >= 1:
                    _record_invalid_attrib_id(state.inventory, zone_id, domain="zone")
            emit(
                ApiError(
                    kind=ApiError.KIND,
                    at=UNSET_AT,
                    seq=UNSET_SEQ,
                    classification=UNSET_CLASSIFICATION,
                    route=UNSET_ROUTE,
                    session_id=UNSET_SESSION_ID,
                    error_code=error_code,
                    scope="zone",
                    entity_id=_coerce_int(payload.get("zone_id")),
                    message=None,
                ),
                ctx,
            )
            return True

        zone_id = payload.get("zone_id")
        if not isinstance(zone_id, int) or zone_id < 1:
            return False

        zone = state.get_or_create_zone(zone_id)
        changed: set[str] = set()
        _apply_zone_attribs(zone, payload, changed)
        zone.last_update_at = now()
        state.panel.last_message_at = zone.last_update_at

        if changed:
            emit(
                ZoneAttribsUpdated(
                    kind=ZoneAttribsUpdated.KIND,
                    at=UNSET_AT,
                    seq=UNSET_SEQ,
                    classification=UNSET_CLASSIFICATION,
                    route=UNSET_ROUTE,
                    session_id=UNSET_SESSION_ID,
                    zone_id=zone_id,
                    changed_fields=tuple(sorted(changed)),
                ),
                ctx,
            )

        return True

    return handler_zone_get_attribs


def _record_invalid_attrib_id(inv: InventoryState, entity_id: int, *, domain: str) -> None:
    if domain == "zone":
        last = inv.zone_last_invalid_id
        if last is not None and entity_id == last + 1:
            inv.zone_invalid_streak += 1
        else:
            inv.zone_invalid_streak = 1
        inv.zone_last_invalid_id = entity_id
        if inv.zone_invalid_streak >= inv.invalid_id_streak_threshold:
            max_id = max(entity_id - inv.zone_invalid_streak, 0)
            if inv.zone_discovery_max_id is None or max_id < inv.zone_discovery_max_id:
                inv.zone_discovery_max_id = max_id
                if inv.configured_zones:
                    inv.configured_zones = {i for i in inv.configured_zones if i <= max_id}
                if inv.zone_attribs_requested:
                    inv.zone_attribs_requested = {
                        i for i in inv.zone_attribs_requested if i <= max_id
                    }
                LOG.debug(
                    "zone.get_attribs discovery max_id=%s (invalid streak=%s)",
                    max_id,
                    inv.zone_invalid_streak,
                )


def make_zone_get_all_zones_status_handler(state: PanelState, emit: EmitFn, now: NowFn):
    """
    Handler for ("zone","get_all_zones_status").
    """

    def handler_zone_get_all_zones_status(msg: Mapping[str, Any], ctx: DispatchContext) -> bool:
        zone_obj = _as_mapping(msg.get("zone"))
        if zone_obj is None:
            return False

        payload = _as_mapping(zone_obj.get("get_all_zones_status"))
        if payload is None:
            return False

        error_code = payload.get("error_code")
        if isinstance(error_code, int) and error_code != 0:
            emit(
                ApiError(
                    kind=ApiError.KIND,
                    at=UNSET_AT,
                    seq=UNSET_SEQ,
                    classification=UNSET_CLASSIFICATION,
                    route=UNSET_ROUTE,
                    session_id=UNSET_SESSION_ID,
                    error_code=error_code,
                    scope="zone",
                    entity_id=None,
                    message=None,
                ),
                ctx,
            )
            return True

        outcome = _reconcile_bulk_zone_status(state, payload, now=now())
        if outcome.updated_ids:
            emit(
                ZonesStatusBulkUpdated(
                    kind=ZonesStatusBulkUpdated.KIND,
                    at=UNSET_AT,
                    seq=UNSET_SEQ,
                    classification=UNSET_CLASSIFICATION,
                    route=UNSET_ROUTE,
                    session_id=UNSET_SESSION_ID,
                    updated_count=len(outcome.updated_ids),
                    updated_ids=outcome.updated_ids,
                ),
                ctx,
            )

        if outcome.warnings:
            emit(
                DispatchRoutingError(
                    kind=DispatchRoutingError.KIND,
                    at=UNSET_AT,
                    seq=UNSET_SEQ,
                    classification=UNSET_CLASSIFICATION,
                    route=UNSET_ROUTE,
                    session_id=UNSET_SESSION_ID,
                    code="schema_warnings",
                    message="zone.get_all_zones_status payload contained type/schema warnings.",
                    keys=outcome.warnings,
                    severity="info",
                ),
                ctx,
            )

        return True

    return handler_zone_get_all_zones_status


def make_zone_get_status_handler(state: PanelState, emit: EmitFn, now: NowFn):
    """
    Handler for ("zone","get_status").
    """

    def handler_zone_get_status(msg: Mapping[str, Any], ctx: DispatchContext) -> bool:
        zone_obj = _as_mapping(msg.get("zone"))
        if zone_obj is None:
            return False

        payload = _as_mapping(zone_obj.get("get_status"))
        if payload is None:
            return False

        error_code = payload.get("error_code", zone_obj.get("error_code"))
        if isinstance(error_code, int) and error_code != 0:
            if error_code == 11008:
                emit(
                    AuthorizationRequiredEvent(
                        kind=AuthorizationRequiredEvent.KIND,
                        at=UNSET_AT,
                        seq=UNSET_SEQ,
                        classification=UNSET_CLASSIFICATION,
                        route=UNSET_ROUTE,
                        session_id=UNSET_SESSION_ID,
                        error_code=error_code,
                        scope="zone",
                        entity_id=_coerce_int(payload.get("zone_id")),
                        message=None,
                    ),
                    ctx,
                )
                return True
            emit(
                ApiError(
                    kind=ApiError.KIND,
                    at=UNSET_AT,
                    seq=UNSET_SEQ,
                    classification=UNSET_CLASSIFICATION,
                    route=UNSET_ROUTE,
                    session_id=UNSET_SESSION_ID,
                    error_code=error_code,
                    scope="zone",
                    entity_id=_coerce_int(payload.get("zone_id")),
                    message=None,
                ),
                ctx,
            )
            return True

        zone_id = _coerce_zone_id(payload.get("zone_id"))
        if zone_id is None:
            return False

        zone = state.get_or_create_zone(zone_id)
        changed: set[str] = set()
        warnings: list[str] = []
        _apply_zone_status_payload(zone, payload, changed, warnings)
        zone.last_update_at = now()
        state.panel.last_message_at = zone.last_update_at
        if LOG.isEnabledFor(logging.DEBUG) and "BYPASSED" in payload:
            LOG.debug(
                "zone.get_status bypassed=%s zone_id=%s changed=%s",
                payload.get("BYPASSED"),
                zone_id,
                sorted(changed),
            )
        if changed:
            emit(
                ZoneStatusUpdated(
                    kind=ZoneStatusUpdated.KIND,
                    at=UNSET_AT,
                    seq=UNSET_SEQ,
                    classification=UNSET_CLASSIFICATION,
                    route=UNSET_ROUTE,
                    session_id=UNSET_SESSION_ID,
                    zone_id=zone_id,
                    changed_fields=tuple(sorted(changed)),
                ),
                ctx,
            )
        return True

    return handler_zone_get_status


def make_zone_set_status_handler(state: PanelState, emit: EmitFn, now: NowFn):
    """
    Handler for ("zone","set_status") ingest-only status reconcile.
    """

    def handler_zone_set_status(msg: Mapping[str, Any], ctx: DispatchContext) -> bool:
        zone_obj = _as_mapping(msg.get("zone"))
        if zone_obj is None:
            return False

        payload = _as_mapping(zone_obj.get("set_status"))
        if payload is None:
            return False

        error_code = payload.get("error_code", zone_obj.get("error_code"))
        zone_id = _coerce_zone_id(payload.get("zone_id"))
        if isinstance(error_code, int) and error_code != 0:
            emit(
                ApiError(
                    kind=ApiError.KIND,
                    at=UNSET_AT,
                    seq=UNSET_SEQ,
                    classification=UNSET_CLASSIFICATION,
                    route=UNSET_ROUTE,
                    session_id=UNSET_SESSION_ID,
                    error_code=error_code,
                    scope="zone",
                    entity_id=zone_id,
                    message=None,
                ),
                ctx,
            )
            return True

        if zone_id is None:
            return False

        zone = state.get_or_create_zone(zone_id)
        changed: set[str] = set()
        warnings: list[str] = []
        _apply_zone_status_payload(zone, payload, changed, warnings)
        zone.last_update_at = now()
        state.panel.last_message_at = zone.last_update_at
        if LOG.isEnabledFor(logging.DEBUG) and "BYPASSED" in payload:
            LOG.debug(
                "zone.set_status bypassed=%s zone_id=%s changed=%s",
                payload.get("BYPASSED"),
                zone_id,
                sorted(changed),
            )
        if changed:
            emit(
                ZoneStatusUpdated(
                    kind=ZoneStatusUpdated.KIND,
                    at=UNSET_AT,
                    seq=UNSET_SEQ,
                    classification=UNSET_CLASSIFICATION,
                    route=UNSET_ROUTE,
                    session_id=UNSET_SESSION_ID,
                    zone_id=zone_id,
                    changed_fields=tuple(sorted(changed)),
                ),
                ctx,
            )
        return True

    return handler_zone_set_status


def make_zone_get_table_info_handler(state: PanelState, emit: EmitFn, now: NowFn):
    """
    Handler for ("zone","get_table_info").
    """

    def handler_zone_get_table_info(msg: Mapping[str, Any], ctx: DispatchContext) -> bool:
        zone_obj = _as_mapping(msg.get("zone"))
        if zone_obj is None:
            return False

        payload = _as_mapping(zone_obj.get("get_table_info"))
        if payload is None:
            payload = _as_mapping(zone_obj.get("table_info"))
        if payload is None:
            return False
        LOG.debug("zone.get_table_info response keys=%s", sorted(payload))

        error_code = payload.get("error_code")
        if isinstance(error_code, int) and error_code != 0:
            emit(
                ApiError(
                    kind=ApiError.KIND,
                    at=UNSET_AT,
                    seq=UNSET_SEQ,
                    classification=UNSET_CLASSIFICATION,
                    route=UNSET_ROUTE,
                    session_id=UNSET_SESSION_ID,
                    error_code=error_code,
                    scope="zone",
                    entity_id=None,
                    message=None,
                ),
                ctx,
            )
            return True

        table_info = dict(payload)
        state.table_info_by_domain["zone"] = table_info
        state.panel.last_message_at = now()
        table_elements = _extract_int(payload, "table_elements")
        table_csm = _extract_table_csm(payload, domain="zone")
        if table_csm is not None:
            old = state.table_csm_by_domain.get("zone")
            if old != table_csm:
                state.table_csm_by_domain["zone"] = table_csm
                emit(
                    TableCsmChanged(
                        kind=TableCsmChanged.KIND,
                        at=UNSET_AT,
                        seq=UNSET_SEQ,
                        classification=UNSET_CLASSIFICATION,
                        route=UNSET_ROUTE,
                        session_id=UNSET_SESSION_ID,
                        csm_domain="zone",
                        old=old,
                        new=table_csm,
                    ),
                    ctx,
                )
        if table_elements is not None:
            state.table_info_known.add("zone")
            if state.inventory.configured_zones:
                before = state.inventory.configured_zones
                filtered = {zone_id for zone_id in before if zone_id <= table_elements}
                if filtered != before:
                    state.inventory.configured_zones = filtered
                    if state.inventory.zone_attribs_requested:
                        state.inventory.zone_attribs_requested = {
                            zone_id
                            for zone_id in state.inventory.zone_attribs_requested
                            if zone_id <= table_elements
                        }
                    if LOG.isEnabledFor(logging.DEBUG):
                        LOG.debug(
                            "zone.get_table_info pruned configured_zones to table_elements=%s (before=%s after=%s)",
                            table_elements,
                            len(before),
                            len(filtered),
                        )
                    emit(
                        ZoneConfiguredUpdated(
                            kind=ZoneConfiguredUpdated.KIND,
                            at=UNSET_AT,
                            seq=UNSET_SEQ,
                            classification=UNSET_CLASSIFICATION,
                            route=UNSET_ROUTE,
                            session_id=UNSET_SESSION_ID,
                            configured_ids=tuple(sorted(filtered)),
                        ),
                        ctx,
                    )

        emit(
            ZoneTableInfoUpdated(
                kind=ZoneTableInfoUpdated.KIND,
                at=UNSET_AT,
                seq=UNSET_SEQ,
                classification=UNSET_CLASSIFICATION,
                route=UNSET_ROUTE,
                session_id=UNSET_SESSION_ID,
                table_elements=table_elements,
                increment_size=_extract_int(payload, "increment_size"),
                table_csm=table_csm,
            ),
            ctx,
        )

        snapshot = update_csm_snapshot(state)
        if snapshot is not None:
            emit(
                CsmSnapshotUpdated(
                    kind=CsmSnapshotUpdated.KIND,
                    at=UNSET_AT,
                    seq=UNSET_SEQ,
                    classification=UNSET_CLASSIFICATION,
                    route=UNSET_ROUTE,
                    session_id=UNSET_SESSION_ID,
                    snapshot=snapshot,
                ),
                ctx,
            )

        if not state.bootstrap_counts_ready and {"area", "zone", "output", "tstat"}.issubset(
            state.table_info_known
        ):
            state.bootstrap_counts_ready = True
            emit(
                BootstrapCountsReady(
                    kind=BootstrapCountsReady.KIND,
                    at=UNSET_AT,
                    seq=UNSET_SEQ,
                    classification=UNSET_CLASSIFICATION,
                    route=UNSET_ROUTE,
                    session_id=UNSET_SESSION_ID,
                ),
                ctx,
            )
        return True

    return handler_zone_get_table_info


def _extract_table_csm(payload: Mapping[str, Any], *, domain: str) -> int | None:
    if "table_csm" not in payload:
        return None
    value = payload.get("table_csm")
    if isinstance(value, bool):
        LOG.warning("%s.get_table_info table_csm has invalid bool value.", domain)
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if text.isdigit():
            return int(text)
    LOG.warning("%s.get_table_info table_csm has non-int value %r.", domain, value)
    return None


def make_zone_get_defs_handler(state: PanelState, emit: EmitFn, now: NowFn):
    """
    Handler for ("zone","get_defs").
    """

    def handler_zone_get_defs(msg: Mapping[str, Any], ctx: DispatchContext) -> bool:
        zone_obj = _as_mapping(msg.get("zone"))
        if zone_obj is None:
            return False

        payload = _as_mapping(zone_obj.get("get_defs"))
        if payload is None:
            return False

        error_code = payload.get("error_code")
        if isinstance(error_code, int) and error_code != 0:
            emit(
                ApiError(
                    kind=ApiError.KIND,
                    at=UNSET_AT,
                    seq=UNSET_SEQ,
                    classification=UNSET_CLASSIFICATION,
                    route=UNSET_ROUTE,
                    session_id=UNSET_SESSION_ID,
                    error_code=error_code,
                    scope="zone",
                    entity_id=None,
                    message=None,
                ),
                ctx,
            )
            return True

        defs = payload.get("definitions")
        if not isinstance(defs, list):
            return False
        defs_list = cast(list[object], defs)

        block_id = payload.get("block_id")
        # Preserve API meaning: block_id is 1-based; offset by block size we observed.
        base_index = (
            1 + (block_id - 1) * len(defs_list)
            if isinstance(block_id, int) and block_id >= 1 and defs_list
            else 1
        )

        updated: list[int] = []
        for idx, name in enumerate(defs_list):
            if name is None:
                continue
            def_id = base_index + idx
            state.zone_defs_by_id[def_id] = {"definition": str(name)}
            updated.append(def_id)

        if updated:
            state.panel.last_message_at = now()

        emit(
            ZoneDefsUpdated(
                kind=ZoneDefsUpdated.KIND,
                at=UNSET_AT,
                seq=UNSET_SEQ,
                classification=UNSET_CLASSIFICATION,
                route=UNSET_ROUTE,
                session_id=UNSET_SESSION_ID,
                count=len(updated),
                updated_ids=tuple(updated),
            ),
            ctx,
        )
        return True

    return handler_zone_get_defs


def make_zone_get_def_flags_handler(state: PanelState, emit: EmitFn, now: NowFn):
    """
    Handler for ("zone","get_def_flags").
    """

    def handler_zone_get_def_flags(msg: Mapping[str, Any], ctx: DispatchContext) -> bool:
        zone_obj = _as_mapping(msg.get("zone"))
        if zone_obj is None:
            return False

        payload = _as_mapping(zone_obj.get("get_def_flags"))
        if payload is None:
            return False

        error_code = payload.get("error_code")
        if isinstance(error_code, int) and error_code != 0:
            emit(
                ApiError(
                    kind=ApiError.KIND,
                    at=UNSET_AT,
                    seq=UNSET_SEQ,
                    classification=UNSET_CLASSIFICATION,
                    route=UNSET_ROUTE,
                    session_id=UNSET_SESSION_ID,
                    error_code=error_code,
                    scope="zone",
                    entity_id=None,
                    message=None,
                ),
                ctx,
            )
            return True

        definition = payload.get("definition")
        flags = payload.get("flags")
        if definition is None or flags is None:
            return False

        entry: dict[str, object] = {"definition": str(definition), "flags": flags}
        state.zone_def_flags_by_name[str(definition)] = entry

        def_id = _resolve_zone_def_id(state, str(definition))
        if def_id is not None:
            state.zone_def_flags_by_id[def_id] = entry

        state.panel.last_message_at = now()

        emit(
            ZoneDefFlagsUpdated(
                kind=ZoneDefFlagsUpdated.KIND,
                at=UNSET_AT,
                seq=UNSET_SEQ,
                classification=UNSET_CLASSIFICATION,
                route=UNSET_ROUTE,
                session_id=UNSET_SESSION_ID,
                count=1,
            ),
            ctx,
        )
        return True

    return handler_zone_get_def_flags


def _reconcile_configured_zones(
    state: PanelState, payload: Mapping[str, Any], *, now: float
) -> _ConfiguredOutcome:
    warnings: list[str] = []
    inv = state.inventory
    ids = _extract_configured_zone_ids(payload, warnings)
    table_info = _as_mapping(state.table_info_by_domain.get("zone"))
    if table_info is not None:
        max_id = table_info.get("table_elements")
        if isinstance(max_id, int) and max_id >= 1:
            filtered = [zone_id for zone_id in ids if zone_id <= max_id]
            if len(filtered) != len(ids):
                if LOG.isEnabledFor(logging.DEBUG):
                    LOG.debug(
                        "zone.get_configured filtered ids above table_elements=%s (before=%s after=%s)",
                        max_id,
                        len(ids),
                        len(filtered),
                    )
                ids = filtered
    if ids or _has_configured_zone_ids(payload):
        inv.configured_zones = set(ids)
        state.panel.last_message_at = now
        for zone_id in ids:
            zone = state.get_or_create_zone(zone_id)
            zone.last_update_at = now
    completed_now = False
    block_count_value = _coerce_intish(payload.get("block_count"))
    if block_count_value is not None and block_count_value >= 1:
        inv.configured_zone_block_count = block_count_value
        inv.configured_zone_blocks_seen = set(range(1, block_count_value + 1))
        inv.configured_zone_blocks_remaining = 0
    if not inv.configured_zones_complete:
        inv.configured_zones_complete = True
        completed_now = True
    return _ConfiguredOutcome(
        configured_ids=tuple(ids),
        warnings=tuple(warnings),
        completed_now=completed_now,
    )


def _extract_configured_zone_ids(payload: Mapping[str, Any], warnings: list[str]) -> list[int]:
    candidates: list[Any] = []
    has_blocks = (
        _coerce_intish(payload.get("block_id")) is not None
        or _coerce_intish(payload.get("block_count")) is not None
    )

    for key in ("configured_zone_ids", "configured_zones", "zone_ids", "zones", "configured"):
        if key in payload:
            candidates.append(payload.get(key))

    if not has_blocks:
        for key in ("bitmask", "bitmap", "mask", "zone_mask"):
            if key in payload:
                candidates.append(payload.get(key))
    elif any(key in payload for key in ("bitmask", "bitmap", "mask", "zone_mask")):
        warnings.append("bitmask ignored for paged configured zones")

    for value in candidates:
        ids = _parse_zone_id_container(value, warnings)
        if ids:
            return ids

    warnings.append("no configured zone ids found")
    return []


def _has_configured_zone_ids(payload: Mapping[str, Any]) -> bool:
    return any(
        key in payload
        for key in (
            "configured_zone_ids",
            "configured_zones",
            "zone_ids",
            "zones",
            "configured",
            "bitmask",
            "bitmap",
            "mask",
            "zone_mask",
        )
    )


def _parse_zone_id_container(value: Any, warnings: list[str]) -> list[int]:
    if value is None:
        return []

    if isinstance(value, list):
        ids_list: list[int] = []
        items = cast(list[object], value)
        for item in items:
            zone_id = _coerce_zone_id(item)
            if zone_id is not None:
                ids_list.append(zone_id)
        return _dedupe_sorted(ids_list)

    if isinstance(value, dict):
        mapping = cast(Mapping[str, Any], value)
        ids_map: list[int] = []
        for k, v in mapping.items():
            zone_id = _coerce_zone_id(k)
            if zone_id is not None:
                if isinstance(v, bool) and not v:
                    continue
                ids_map.append(zone_id)
            else:
                v_mapping = _as_mapping(v)
                if v_mapping is None:
                    continue
                zone_id = _coerce_zone_id(v_mapping.get("zone_id") or v_mapping.get("id"))
                if zone_id is not None:
                    ids_map.append(zone_id)
        return _dedupe_sorted(ids_map)

    if isinstance(value, int):
        return _ids_from_bitmask(value)

    if isinstance(value, str):
        text = value.strip().lower()
        if text.startswith("0x"):
            text = text[2:]
        try:
            mask = int(text, 16)
        except ValueError:
            warnings.append(f"configured ids string not hex: {value!r}")
            return []
        return _ids_from_bitmask(mask)

    warnings.append(f"unsupported configured zone ids type: {type(value).__name__}")
    return []


def _coerce_intish(value: object) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _ids_from_bitmask(mask: int) -> list[int]:
    ids: list[int] = []
    bit = 1
    zone_id = 1
    while bit <= mask:
        if mask & bit:
            ids.append(zone_id)
        bit <<= 1
        zone_id += 1
    return ids


def _reconcile_bulk_zone_status(
    state: PanelState, payload: Mapping[str, Any], *, now: float
) -> _BulkStatusOutcome:
    warnings: list[str] = []
    updated: list[int] = []
    status_text = payload.get("status")
    if isinstance(status_text, str):
        compact = "".join(status_text.split()).upper()
        for idx, ch in enumerate(compact):
            zone_id = idx + 1
            if not _should_apply_bulk_zone(state, zone_id):
                continue
            zone = state.zones.get(zone_id)
            if zone is None:
                continue
            if _apply_zone_status_char(zone, ch, warnings):
                zone.last_update_at = now
                updated.append(zone_id)
        if updated:
            state.panel.last_message_at = now
        return _BulkStatusOutcome(
            updated_ids=tuple(_dedupe_sorted(updated)), warnings=tuple(warnings)
        )

    items = _extract_zone_status_items(payload, warnings)
    updated = []

    if not items:
        warnings.append("no zone status items found")
        return _BulkStatusOutcome(updated_ids=(), warnings=tuple(warnings))

    for item in items:
        zone_id = _coerce_zone_id(item.get("zone_id") or item.get("id") or item.get("zone"))
        if zone_id is None:
            warnings.append("zone status item missing zone_id")
            continue
        if not _should_apply_bulk_zone(state, zone_id):
            continue
        zone = state.zones.get(zone_id)
        if zone is None:
            continue
        _apply_zone_fields(zone, item, warnings)
        zone.last_update_at = now
        updated.append(zone_id)

    if updated:
        state.panel.last_message_at = now

    return _BulkStatusOutcome(updated_ids=tuple(_dedupe_sorted(updated)), warnings=tuple(warnings))


def _should_apply_bulk_zone(state: PanelState, zone_id: int) -> bool:
    inv = state.inventory
    if inv.configured_zones:
        return zone_id in inv.configured_zones
    if inv.zone_discovery_max_id is not None:
        return zone_id <= inv.zone_discovery_max_id
    return True


def _coerce_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"true", "yes", "on", "1"}:
            return True
        if text in {"false", "no", "off", "0"}:
            return False
    return None


def _update_zone_bool(zone: ZoneState, field: str, value: bool | None, changed: set[str]) -> None:
    if value is None:
        return
    if getattr(zone, field) != value:
        setattr(zone, field, value)
        changed.add(field)


def _apply_zone_status_payload(
    zone: ZoneState,
    payload: Mapping[str, Any],
    changed: set[str],
    warnings: list[str],
) -> None:
    bypassed = _coerce_bool(payload.get("BYPASSED", payload.get("bypassed")))
    _update_zone_bool(zone, "bypassed", bypassed, changed)

    low_battery = _coerce_bool(payload.get("low_batt", payload.get("low_battery")))
    _update_zone_bool(zone, "low_battery", low_battery, changed)

    for key in ("trouble", "tamper", "alarm", "violated", "enabled"):
        value = _coerce_bool(payload.get(key))
        _update_zone_bool(zone, key, value, changed)

    secure_state = payload.get("secure_state")
    if isinstance(secure_state, str):
        state = secure_state.strip().upper()
        if state in {"NORMAL", "SECURE", "RESTORE"}:
            _update_zone_bool(zone, "violated", False, changed)
            _update_zone_bool(zone, "trouble", False, changed)
            _update_zone_bool(zone, "tamper", False, changed)
            _update_zone_bool(zone, "alarm", False, changed)
        elif "VIOLATED" in state or state == "OPEN":
            _update_zone_bool(zone, "violated", True, changed)
        elif "TROUBLE" in state:
            _update_zone_bool(zone, "trouble", True, changed)
        elif "TAMPER" in state:
            _update_zone_bool(zone, "tamper", True, changed)
        elif "ALARM" in state:
            _update_zone_bool(zone, "alarm", True, changed)
        elif "BYPASS" in state:
            _update_zone_bool(zone, "bypassed", True, changed)
        else:
            warnings.append(f"unknown secure_state: {secure_state!r}")


def _apply_zone_status_char(zone: ZoneState, ch: str, warnings: list[str]) -> bool:
    disabled = {"0", "4", "8", "C"}
    normal = {"1", "2", "3"}
    trouble = {"5", "6", "7"}
    violated = {"9", "A", "B"}
    bypassed = {"D", "E", "F"}

    if ch not in disabled | normal | trouble | violated | bypassed:
        warnings.append(f"unknown zone status char: {ch!r}")
        return False

    zone.status_code = ch
    if ch in disabled:
        zone.enabled = False
        zone.trouble = False
        zone.violated = False
        zone.bypassed = False
    elif ch in normal:
        zone.enabled = True
        zone.trouble = False
        zone.violated = False
        zone.bypassed = False
    elif ch in trouble:
        zone.enabled = True
        zone.trouble = True
        zone.violated = False
        zone.bypassed = False
    elif ch in violated:
        zone.enabled = True
        zone.trouble = False
        zone.violated = True
        zone.bypassed = False
    else:
        zone.enabled = True
        zone.trouble = False
        zone.violated = False
        zone.bypassed = True
    return True


def _extract_zone_status_items(
    payload: Mapping[str, Any], warnings: list[str]
) -> list[Mapping[str, Any]]:
    for key in ("zones", "zone_statuses", "zone_status", "status"):
        if key in payload:
            value = payload.get(key)
            items = _coerce_zone_items(value, warnings)
            if items:
                return items
    return []


def _coerce_zone_items(value: Any, warnings: list[str]) -> list[Mapping[str, Any]]:
    if value is None:
        return []
    if isinstance(value, list):
        items: list[Mapping[str, Any]] = []
        items_list = cast(list[object], value)
        for item in items_list:
            if isinstance(item, Mapping):
                items.append(cast(Mapping[str, Any], item))
        return items
    if isinstance(value, dict):
        value_map = cast(dict[str, Any], value)
        values = list(value_map.values())
        # If values are mappings, treat them as items; else treat dict as a single item.
        if all(isinstance(v, Mapping) for v in values):
            items = []
            for item in values:
                if isinstance(item, Mapping):
                    items.append(cast(Mapping[str, Any], item))
            return items
        return [cast(Mapping[str, Any], value)]
    warnings.append(f"unsupported zone status container type: {type(value).__name__}")
    return []


def _apply_zone_fields(zone: ZoneState, item: Mapping[str, Any], warnings: list[str]) -> None:
    for key, attr in _ZONE_STATUS_FIELDS.items():
        if key not in item:
            continue
        value = item.get(key)
        expected = _ZONE_STATUS_TYPES.get(key)
        if expected is not None and not isinstance(value, expected):
            warnings.append(
                f"field '{key}' wrong type (expected {expected.__name__}, got {type(value).__name__})"
            )
            continue
        setattr(zone, attr, value)


def _apply_zone_attribs(zone: ZoneState, payload: Mapping[str, Any], changed: set[str]) -> None:
    for key, attr in (
        ("name", "name"),
        ("area_id", "area_id"),
        ("definition", "definition"),
        ("flags", "flags"),
    ):
        if key in payload:
            value = payload.get(key)
            if key == "name":
                value = _normalize_name(value)
            if getattr(zone, attr) != value:
                setattr(zone, attr, value)
                changed.add(attr)

    for key, value in payload.items():
        if key in {"zone_id", "error_code", "name", "area_id", "definition", "flags"}:
            continue
        if zone.attribs.get(key) != value:
            zone.attribs[key] = value
            changed.add(key)


def _coerce_zone_id(value: Any) -> int | None:
    if isinstance(value, int):
        return value if value >= 1 else None
    if isinstance(value, str):
        try:
            num = int(value)
        except ValueError:
            return None
        return num if num >= 1 else None
    if isinstance(value, Mapping):
        mapping = cast(Mapping[str, Any], value)
        inner = mapping.get("zone_id") or mapping.get("id")
        return _coerce_zone_id(inner)
    return None


def _normalize_name(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def _dedupe_sorted(ids: Iterable[int]) -> list[int]:
    return sorted({i for i in ids if i >= 1})


def _extract_int(payload: Mapping[str, Any], key: str) -> int | None:
    value = payload.get(key)
    return value if isinstance(value, int) else None


def _resolve_zone_def_id(state: PanelState, definition: str) -> int | None:
    for def_id, entry in state.zone_defs_by_id.items():
        if entry.get("definition") == definition:
            return def_id
    return None
