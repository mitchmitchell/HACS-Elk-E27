"""
elke27_lib/handlers/area.py

Read/observe-only handlers for the "area" domain.

Colocation policy:
- Message-specific reconcile helpers live in the same module as their handlers.
- Reconcile helpers are module-private (prefixed with _).
- Reconcile helpers are PURE:
    - no dispatcher context
    - no event emission
    - no I/O / logging

Policy:
- We do NOT send any writes to the panel in this phase.
- We DO process inbound status messages, including "set_status", as ingest-only updates.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from elke27_lib.dispatcher import (  # adjust import to your dispatcher module location
    DispatchContext,
    PagedBlock,
)
from elke27_lib.events import (
    UNSET_AT,
    UNSET_CLASSIFICATION,
    UNSET_ROUTE,
    UNSET_SEQ,
    UNSET_SESSION_ID,
    ApiError,
    AreaAttribsUpdated,
    AreaConfiguredInventoryReady,
    AreaConfiguredUpdated,
    AreaStatusUpdated,
    AreaTableInfoUpdated,
    AreaTroublesUpdated,
    AuthorizationRequiredEvent,
    BootstrapCountsReady,
    CsmSnapshotUpdated,
    DispatchRoutingError,
    TableCsmChanged,
    UnknownMessage,
)
from elke27_lib.states import InventoryState, PanelState, update_csm_snapshot

EmitFn = Callable[[object, DispatchContext], None]
NowFn = Callable[[], float]

LOG = logging.getLogger(__name__)


# -------------------------
# Module-private reconcile
# -------------------------

@dataclass(frozen=True, slots=True)
class _AreaOutcome:
    area_id: int
    changed_fields: tuple[str, ...]
    error_code: int | None
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _ConfiguredOutcome:
    configured_ids: tuple[int, ...]
    warnings: tuple[str, ...]
    completed_now: bool


_EXPECTED_TYPES: dict[str, type | tuple[type, ...]] = {
    # strings
    "arm_state": str,
    "armed_state": str,
    "alarm_state": str,
    "alarm_event": str,
    "ready_status": str,

    # bools
    "ready": bool,
    "stay": bool,
    "away": bool,
    "bypass": bool,
    "chime": bool,
    "Chime": bool,
    "entry_delay_active": bool,
    "exit_delay_active": bool,
    "trouble": bool,

    # ints
    "num_not_ready_zones": int,
    "num_bypassed_zones": int,
    "zones_bypassed": int,
    "error_code": int,
}

_FIELD_MAP: dict[str, str] = {
    "arm_state": "arm_state",
    "armed_state": "armed_state",
    "alarm_state": "alarm_state",
    "alarm_event": "alarm_event",

    "ready_status": "ready_status",
    "ready": "ready",
    "stay": "stay",
    "away": "away",
    "bypass": "bypass",
    "chime": "chime",
    "Chime": "chime",
    "entry_delay_active": "entry_delay_active",
    "exit_delay_active": "exit_delay_active",
    "trouble": "trouble",

    "num_not_ready_zones": "num_not_ready_zones",
    "num_bypassed_zones": "num_bypassed_zones",
    "zones_bypassed": "num_bypassed_zones",

    # response field; stored on state as last_error_code
    "error_code": "last_error_code",
}


def _reconcile_area_state(state: PanelState, payload: Mapping[str, Any], *, now: float, source: str) -> _AreaOutcome:
    """
    Pure reconcile for area.* payloads.

    v0 semantics:
    - Requires payload["area_id"] (int >= 1); otherwise returns warnings and no state changes.
    - Patch-style: only fields present in payload are applied; absent fields are not cleared.
    - Strict typing: if a field type mismatches, ignore it and add a warning.
    - Always updates timestamps when area_id is valid:
        - state.panel.last_message_at
        - area.last_update_at
    """
    warnings: list[str] = []
    changed: set[str] = set()

    area_id_val = payload.get("area_id")
    if not isinstance(area_id_val, int) or area_id_val < 1:
        warnings.append("missing/invalid area_id (expected int >= 1)")
        return _AreaOutcome(
            area_id=-1,
            changed_fields=(),
            error_code=_extract_error_code(payload),
            warnings=tuple(warnings),
        )

    area = state.get_or_create_area(area_id_val)

    for key, attr in _FIELD_MAP.items():
        if key not in payload:
            continue

        value = payload.get(key)
        expected = _EXPECTED_TYPES.get(key)
        if expected is not None and not isinstance(value, expected):
            warnings.append(
                f"field '{key}' wrong type (expected {_type_name(expected)}, got {type(value).__name__})"
            )
            continue

        old = getattr(area, attr)
        if old != value:
            setattr(area, attr, value)
            changed.add(attr)

    # timestamps (monotonic)
    area.last_update_at = now
    state.panel.last_message_at = now

    return _AreaOutcome(
        area_id=area_id_val,
        changed_fields=tuple(sorted(changed)),
        error_code=_extract_error_code(payload),
        warnings=tuple(warnings),
    )


def _extract_error_code(payload: Mapping[str, Any]) -> int | None:
    v = payload.get("error_code")
    return v if isinstance(v, int) else None


def _type_name(t: type | tuple[type, ...]) -> str:
    if isinstance(t, tuple):
        return " | ".join(x.__name__ for x in t)
    return t.__name__


# -------------------------
# Handler factories
# -------------------------

def make_area_get_status_handler(state: PanelState, emit: EmitFn, now: NowFn):
    """
    Handler for ("area","get_status") where payload is msg["area"]["get_status"].
    """
    def handler_area_get_status(msg: Mapping[str, Any], ctx: DispatchContext) -> bool:
        area_obj = msg.get("area")
        if not isinstance(area_obj, Mapping):
            return False

        payload = area_obj.get("get_status")
        if not isinstance(payload, Mapping):
            return False

        error_code = _extract_error_code(payload)
        if error_code is not None and error_code != 0:
            area_id = payload.get("area_id")
            emit(
                ApiError(
                    kind=ApiError.KIND,
                    at=UNSET_AT,
                    seq=UNSET_SEQ,
                    classification=UNSET_CLASSIFICATION,
                    route=UNSET_ROUTE,
                    session_id=UNSET_SESSION_ID,
                    error_code=error_code,
                    scope="area",
                    entity_id=area_id if isinstance(area_id, int) else None,
                    message=None,
                ),
                ctx=ctx,
            )
            return True

        outcome = _reconcile_area_state(state, payload, now=now(), source="snapshot")
        if state.debug_last_raw_by_route_enabled:
            state.debug_last_raw_by_route["area.get_status"] = dict(payload)

        if outcome.area_id < 1:
            emit(
                DispatchRoutingError(
                    kind=DispatchRoutingError.KIND,
                    at=UNSET_AT,
                    seq=UNSET_SEQ,
                    classification=UNSET_CLASSIFICATION,
                    route=UNSET_ROUTE,
                    session_id=UNSET_SESSION_ID,
                    code="schema_invalid_area_id",
                    message="area.get_status missing/invalid area_id; ignoring payload.",
                    keys=tuple(payload.keys()),
                    severity="warning",
                ),
                ctx=ctx,
            )
            return False

        if outcome.changed_fields:
            LOG.debug("area.get_status changed_fields=%s area_id=%s", outcome.changed_fields, outcome.area_id)
        else:
            LOG.warning("area.get_status no changes; area_id=%s", outcome.area_id)
        evt = AreaStatusUpdated(
            kind=AreaStatusUpdated.KIND,
            at=UNSET_AT,
            seq=UNSET_SEQ,
            classification=UNSET_CLASSIFICATION,
            route=UNSET_ROUTE,
            session_id=UNSET_SESSION_ID,
            area_id=outcome.area_id,
            changed_fields=outcome.changed_fields,
        )
        try:
            emit(evt, ctx=ctx)
            LOG.debug("area.get_status emitted AreaStatusUpdated")
        except Exception as e:
            LOG.error("area.get_status emit failed: %s", e, exc_info=True)

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
                    message="area.get_status payload contained type/schema warnings.",
                    keys=outcome.warnings,
                    severity="info",
                ),
                ctx=ctx,
            )

        return True

    return handler_area_get_status


def make_area_get_attribs_handler(state: PanelState, emit: EmitFn, now: NowFn):
    """
    Handler for ("area","get_attribs").
    """
    def handler_area_get_attribs(msg: Mapping[str, Any], ctx: DispatchContext) -> bool:
        area_obj = msg.get("area")
        if not isinstance(area_obj, Mapping):
            return False

        payload = area_obj.get("get_attribs")
        if not isinstance(payload, Mapping):
            return False

        error_code = payload.get("error_code", area_obj.get("error_code"))
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
                        scope="area",
                        entity_id=payload.get("area_id") if isinstance(payload.get("area_id"), int) else None,
                        message=None,
                    ),
                    ctx=ctx,
                )
                return True
            if error_code == 11006:
                area_id = payload.get("area_id")
                if isinstance(area_id, int) and area_id >= 1:
                    _record_invalid_attrib_id(state.inventory, area_id, domain="area")
            emit(
                ApiError(
                    kind=ApiError.KIND,
                    at=UNSET_AT,
                    seq=UNSET_SEQ,
                    classification=UNSET_CLASSIFICATION,
                    route=UNSET_ROUTE,
                    session_id=UNSET_SESSION_ID,
                    error_code=error_code,
                    scope="area",
                    entity_id=payload.get("area_id") if isinstance(payload.get("area_id"), int) else None,
                    message=None,
                ),
                ctx=ctx,
            )
            return True

        area_id = payload.get("area_id")
        if not isinstance(area_id, int) or area_id < 1:
            return False

        area = state.get_or_create_area(area_id)
        changed: set[str] = set()
        _apply_area_attribs(area, payload, changed)
        area.last_update_at = now()
        state.panel.last_message_at = area.last_update_at

        if changed:
            emit(
                AreaAttribsUpdated(
                    kind=AreaAttribsUpdated.KIND,
                    at=UNSET_AT,
                    seq=UNSET_SEQ,
                    classification=UNSET_CLASSIFICATION,
                    route=UNSET_ROUTE,
                    session_id=UNSET_SESSION_ID,
                    area_id=area_id,
                    changed_fields=tuple(sorted(changed)),
                ),
                ctx=ctx,
            )

        return True

    return handler_area_get_attribs


def _record_invalid_attrib_id(inv: InventoryState, entity_id: int, *, domain: str) -> None:
    if domain == "area":
        last = inv.area_last_invalid_id
        if last is not None and entity_id == last + 1:
            inv.area_invalid_streak += 1
        else:
            inv.area_invalid_streak = 1
        inv.area_last_invalid_id = entity_id
        if inv.area_invalid_streak >= inv.invalid_id_streak_threshold:
            max_id = max(entity_id - inv.area_invalid_streak, 0)
            if inv.area_discovery_max_id is None or max_id < inv.area_discovery_max_id:
                inv.area_discovery_max_id = max_id
                if inv.configured_areas:
                    inv.configured_areas = {i for i in inv.configured_areas if i <= max_id}
                if inv.area_attribs_requested:
                    inv.area_attribs_requested = {i for i in inv.area_attribs_requested if i <= max_id}
                LOG.debug(
                    "area.get_attribs discovery max_id=%s (invalid streak=%s)",
                    max_id,
                    inv.area_invalid_streak,
                )


def make_area_get_configured_handler(
    state: PanelState,
    emit: EmitFn,
    now: NowFn,
):
    """
    Handler for ("area","get_configured").
    """
    def handler_area_get_configured(msg: Mapping[str, Any], ctx: DispatchContext) -> bool:
        area_obj = msg.get("area")
        if not isinstance(area_obj, Mapping):
            return False

        payload = area_obj.get("get_configured")
        if not isinstance(payload, Mapping):
            return False
        LOG.debug("area.get_configured response keys=%s", sorted(payload))

        error_code = payload.get("error_code", area_obj.get("error_code"))
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
                        scope="area",
                        entity_id=None,
                        message=None,
                    ),
                    ctx=ctx,
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
                    scope="area",
                    entity_id=None,
                    message=None,
                ),
                ctx=ctx,
            )
            return True

        if "block_id" in payload or "block_count" in payload:
            block_id = payload.get("block_id")
            block_count = payload.get("block_count")
            if not isinstance(block_id, int) or block_id < 1:
                LOG.warning("area.get_configured missing/invalid block_id; skipping inventory update.")
                return True
            if not isinstance(block_count, int) or block_count < 1:
                LOG.warning("area.get_configured missing/invalid block_count; skipping inventory update.")
                return True

        outcome = _reconcile_configured_areas(state, payload, now=now())
        if LOG.isEnabledFor(logging.DEBUG):
            LOG.debug(
                "area.get_configured parsed: block_id=%s block_count=%s ids=%s warnings=%s",
                payload.get("block_id"),
                payload.get("block_count"),
                len(outcome.configured_ids),
                outcome.warnings,
            )
        if outcome.configured_ids:
            emit(
                AreaConfiguredUpdated(
                    kind=AreaConfiguredUpdated.KIND,
                    at=UNSET_AT,
                    seq=UNSET_SEQ,
                    classification=UNSET_CLASSIFICATION,
                    route=UNSET_ROUTE,
                    session_id=UNSET_SESSION_ID,
                    configured_ids=outcome.configured_ids,
                ),
                ctx=ctx,
            )

        if outcome.completed_now:
            if LOG.isEnabledFor(logging.DEBUG):
                area_names = [
                    (area_id, getattr(state.areas.get(area_id), "name", None))
                    for area_id in sorted(state.inventory.configured_areas)
                ]
                LOG.debug(
                    "area.get_configured complete: ids=%s names=%s",
                    len(state.inventory.configured_areas),
                    area_names,
                )
            emit(
                AreaConfiguredInventoryReady(
                    kind=AreaConfiguredInventoryReady.KIND,
                    at=UNSET_AT,
                    seq=UNSET_SEQ,
                    classification=UNSET_CLASSIFICATION,
                    route=UNSET_ROUTE,
                    session_id=UNSET_SESSION_ID,
                ),
                ctx=ctx,
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
                    message="area.get_configured payload contained type/schema warnings.",
                    keys=outcome.warnings,
                    severity="info",
                ),
                ctx=ctx,
            )

        return True

    return handler_area_get_configured


def make_area_configured_merge(state: PanelState) -> Callable[[list[PagedBlock], int], Mapping[str, Any]]:
    """
    Merge paged get_configured blocks into a single payload (ADR-0013).
    """
    def _merge(blocks: list[PagedBlock], block_count: int) -> Mapping[str, Any]:
        warnings: list[str] = []
        merged_ids: list[int] = []
        for block in blocks:
            ids = _extract_configured_area_ids(block.payload, warnings)
            block_size = _configured_block_size(block.payload, state, block_count, domain="area")
            ids = _apply_configured_block_offset(ids, block_id=block.block_id, block_size=block_size)
            merged_ids.extend(ids)
        merged = _dedupe_sorted(merged_ids)
        block_id = blocks[-1].block_id if blocks else None
        return {"areas": merged, "block_id": block_id, "block_count": block_count}

    return _merge


def make_area_set_status_handler(state: PanelState, emit: EmitFn, now: NowFn):
    """
    Handler for ("area","set_status") ingest-only status reconcile.
    Even though the name implies a write, in this phase we do not send writes;
    we simply consume inbound messages of this name if they appear.
    """
    def handler_area_set_status(msg: Mapping[str, Any], ctx: DispatchContext) -> bool:
        area_obj = msg.get("area")
        if not isinstance(area_obj, Mapping):
            return False

        payload = area_obj.get("set_status")
        if not isinstance(payload, Mapping):
            return False

        outcome = _reconcile_area_state(state, payload, now=now(), source="delta")

        if outcome.area_id < 1:
            emit(
                DispatchRoutingError(
                    kind=DispatchRoutingError.KIND,
                    at=UNSET_AT,
                    seq=UNSET_SEQ,
                    classification=UNSET_CLASSIFICATION,
                    route=UNSET_ROUTE,
                    session_id=UNSET_SESSION_ID,
                    code="schema_invalid_area_id",
                    message="area.set_status missing/invalid area_id; ignoring payload.",
                    keys=tuple(payload.keys()),
                    severity="warning",
                ),
                ctx=ctx,
            )
            return False

        if outcome.changed_fields:
            emit(
                AreaStatusUpdated(
                    kind=AreaStatusUpdated.KIND,
                    at=UNSET_AT,
                    seq=UNSET_SEQ,
                    classification=UNSET_CLASSIFICATION,
                    route=UNSET_ROUTE,
                    session_id=UNSET_SESSION_ID,
                    area_id=outcome.area_id,
                    changed_fields=outcome.changed_fields,
                ),
                ctx=ctx,
            )

        if outcome.error_code is not None and outcome.error_code != 0:
            emit(
                ApiError(
                    kind=ApiError.KIND,
                    at=UNSET_AT,
                    seq=UNSET_SEQ,
                    classification=UNSET_CLASSIFICATION,
                    route=UNSET_ROUTE,
                    session_id=UNSET_SESSION_ID,
                    error_code=outcome.error_code,
                    scope="area",
                    entity_id=outcome.area_id,
                    message=None,
                ),
                ctx=ctx,
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
                    message="area.set_status payload contained type/schema warnings.",
                    keys=outcome.warnings,
                    severity="info",
                ),
                ctx=ctx,
            )

        return True

    return handler_area_set_status


def make_area_get_troubles_handler(state: PanelState, emit: EmitFn, now: NowFn):
    """
    Handler for ("area","get_troubles").
    """
    def handler_area_get_troubles(msg: Mapping[str, Any], ctx: DispatchContext) -> bool:
        area_obj = msg.get("area")
        if not isinstance(area_obj, Mapping):
            return False

        payload = area_obj.get("get_troubles")
        if payload is None:
            payload = area_obj.get("get_trouble")
            if payload is not None:
                LOG.info("area.get_trouble payload received; treating as get_troubles")
        if not isinstance(payload, Mapping):
            return False
        # Defensive: some panels broadcast get_troubles without a request.
        if getattr(ctx, "classification", None) == "BROADCAST":
            area_id = payload.get("area_id")
            LOG.info("area.get_troubles broadcast received (area_id=%s)", area_id)

        error_code = _extract_error_code(payload)
        if error_code is not None and error_code != 0:
            area_id = payload.get("area_id")
            emit(
                ApiError(
                    kind=ApiError.KIND,
                    at=UNSET_AT,
                    seq=UNSET_SEQ,
                    classification=UNSET_CLASSIFICATION,
                    route=UNSET_ROUTE,
                    session_id=UNSET_SESSION_ID,
                    error_code=error_code,
                    scope="area",
                    entity_id=area_id if isinstance(area_id, int) else None,
                    message=None,
                ),
                ctx=ctx,
            )
            return True

        area_id = payload.get("area_id")
        if not isinstance(area_id, int) or area_id < 1:
            return False

        troubles = _extract_troubles_list(payload)
        area = state.get_or_create_area(area_id)
        area.troubles = troubles
        area.last_update_at = now()
        state.panel.last_message_at = area.last_update_at

        emit(
            AreaTroublesUpdated(
                kind=AreaTroublesUpdated.KIND,
                at=UNSET_AT,
                seq=UNSET_SEQ,
                classification=UNSET_CLASSIFICATION,
                route=UNSET_ROUTE,
                session_id=UNSET_SESSION_ID,
                area_id=area_id,
                troubles=tuple(troubles or []),
            ),
            ctx=ctx,
        )
        return True

    return handler_area_get_troubles


def make_area_get_table_info_handler(state: PanelState, emit: EmitFn, now: NowFn):
    """
    Handler for ("area","get_table_info").
    """
    def handler_area_get_table_info(msg: Mapping[str, Any], ctx: DispatchContext) -> bool:
        area_obj = msg.get("area")
        if not isinstance(area_obj, Mapping):
            return False

        payload = area_obj.get("get_table_info")
        if payload is None:
            payload = area_obj.get("table_info")
        if not isinstance(payload, Mapping):
            return False
        LOG.debug("area.get_table_info response keys=%s", sorted(payload))

        error_code = _extract_error_code(payload)
        if error_code is not None and error_code != 0:
            emit(
                ApiError(
                    kind=ApiError.KIND,
                    at=UNSET_AT,
                    seq=UNSET_SEQ,
                    classification=UNSET_CLASSIFICATION,
                    route=UNSET_ROUTE,
                    session_id=UNSET_SESSION_ID,
                    error_code=error_code,
                    scope="area",
                    entity_id=None,
                    message=None,
                ),
                ctx=ctx,
            )
            return True

        table_info = dict(payload)
        state.table_info_by_domain["area"] = table_info
        state.panel.last_message_at = now()
        table_elements = _extract_int(payload, "table_elements")
        table_csm = _extract_table_csm(payload, domain="area")
        if table_csm is not None:
            old = state.table_csm_by_domain.get("area")
            if old != table_csm:
                state.table_csm_by_domain["area"] = table_csm
                emit(
                    TableCsmChanged(
                        kind=TableCsmChanged.KIND,
                        at=UNSET_AT,
                        seq=UNSET_SEQ,
                        classification=UNSET_CLASSIFICATION,
                        route=UNSET_ROUTE,
                        session_id=UNSET_SESSION_ID,
                        domain="area",
                        old=old,
                        new=table_csm,
                    ),
                    ctx=ctx,
                )
        if table_elements is not None:
            state.table_info_known.add("area")
            if state.inventory.configured_areas:
                before = state.inventory.configured_areas
                filtered = {area_id for area_id in before if area_id <= table_elements}
                if filtered != before:
                    state.inventory.configured_areas = filtered
                    if state.inventory.area_attribs_requested:
                        state.inventory.area_attribs_requested = {
                            area_id for area_id in state.inventory.area_attribs_requested if area_id <= table_elements
                        }
                    if LOG.isEnabledFor(logging.DEBUG):
                        LOG.debug(
                            "area.get_table_info pruned configured_areas to table_elements=%s (before=%s after=%s)",
                            table_elements,
                            len(before),
                            len(filtered),
                        )
                    emit(
                        AreaConfiguredUpdated(
                            kind=AreaConfiguredUpdated.KIND,
                            at=UNSET_AT,
                            seq=UNSET_SEQ,
                            classification=UNSET_CLASSIFICATION,
                            route=UNSET_ROUTE,
                            session_id=UNSET_SESSION_ID,
                            configured_ids=tuple(sorted(filtered)),
                        ),
                        ctx=ctx,
                    )

        emit(
            AreaTableInfoUpdated(
                kind=AreaTableInfoUpdated.KIND,
                at=UNSET_AT,
                seq=UNSET_SEQ,
                classification=UNSET_CLASSIFICATION,
                route=UNSET_ROUTE,
                session_id=UNSET_SESSION_ID,
                table_elements=table_elements,
                increment_size=_extract_int(payload, "increment_size"),
                table_csm=table_csm,
            ),
            ctx=ctx,
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
                ctx=ctx,
            )

        if (
            not state.bootstrap_counts_ready
            and {"area", "zone", "output", "tstat"}.issubset(state.table_info_known)
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
                ctx=ctx,
            )
        return True

    return handler_area_get_table_info


def make_area___root___handler(state: PanelState, emit: EmitFn, now: NowFn):
    """
    Handler for ("area","__root__") to catch multi-key/ambiguous area payloads.
    This is intentionally conservative: it does not attempt to interpret unknown shapes.
    """
    def handler_area___root__(msg: Mapping[str, Any], ctx: DispatchContext) -> bool:
        area_obj = msg.get("area")
        if not isinstance(area_obj, Mapping):
            return False

        if state.debug_last_raw_by_route_enabled:
            state.debug_last_raw_by_route["area.__root__"] = dict(area_obj)

        emit(
            UnknownMessage(
                kind=UnknownMessage.KIND,
                at=UNSET_AT,
                seq=UNSET_SEQ,
                classification=UNSET_CLASSIFICATION,
                route=UNSET_ROUTE,
                session_id=UNSET_SESSION_ID,
                unhandled_route=("area", "__root__"),
                keys=tuple(area_obj.keys()),
            ),
            ctx=ctx,
        )
        return True

    return handler_area___root__


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


def _reconcile_configured_areas(state: PanelState, payload: Mapping[str, Any], *, now: float) -> _ConfiguredOutcome:
    warnings: list[str] = []
    inv = state.inventory
    ids = _extract_configured_area_ids(payload, warnings)
    table_info = state.table_info_by_domain.get("area")
    if isinstance(table_info, Mapping):
        max_id = table_info.get("table_elements")
        if isinstance(max_id, int) and max_id >= 1:
            filtered = [area_id for area_id in ids if area_id <= max_id]
            if len(filtered) != len(ids):
                if LOG.isEnabledFor(logging.DEBUG):
                    LOG.debug(
                        "area.get_configured filtered ids above table_elements=%s (before=%s after=%s)",
                        max_id,
                        len(ids),
                        len(filtered),
                    )
                ids = filtered
    if ids or _has_configured_area_ids(payload):
        inv.configured_areas = set(ids)
        state.panel.last_message_at = now
        for area_id in ids:
            area = state.get_or_create_area(area_id)
            area.last_update_at = now
    completed_now = False
    block_count = payload.get("block_count")
    block_count_value = int(block_count) if _is_intish(block_count) else None
    if block_count_value is not None and block_count_value >= 1:
        inv.configured_area_block_count = block_count_value
        inv.configured_area_blocks_seen = set(range(1, block_count_value + 1))
        inv.configured_area_blocks_remaining = 0
    if not inv.configured_areas_complete:
        inv.configured_areas_complete = True
        completed_now = True
    return _ConfiguredOutcome(
        configured_ids=tuple(ids),
        warnings=tuple(warnings),
        completed_now=completed_now,
    )


def _configured_block_size(
    payload: Mapping[str, Any],
    state: PanelState,
    block_count: int,
    *,
    domain: str,
) -> int | None:
    for key in ("block_size", "block_elements", "block_length", "block_len"):
        value = payload.get(key)
        if isinstance(value, int) and value > 0:
            return value
    table_info = state.table_info_by_domain.get(domain)
    if isinstance(table_info, Mapping):
        total = table_info.get("table_elements")
        if isinstance(total, int) and total > 0:
            return int((total + block_count - 1) / block_count)
    return None


def _apply_configured_block_offset(
    ids: list[int],
    *,
    block_id: int,
    block_size: int | None,
) -> list[int]:
    if not ids or block_id <= 1:
        return ids
    if block_size is None or block_size <= 0:
        return ids
    max_id = max(ids)
    if max_id > block_size:
        return ids
    offset = (block_id - 1) * block_size
    return [area_id + offset for area_id in ids]


def _extract_configured_area_ids(payload: Mapping[str, Any], warnings: list[str]) -> list[int]:
    candidates: list[Any] = []
    has_blocks = _is_intish(payload.get("block_id")) or _is_intish(payload.get("block_count"))

    for key in ("configured_area_ids", "configured_areas", "area_ids", "areas", "configured"):
        if key in payload:
            candidates.append(payload.get(key))

    if not has_blocks:
        for key in ("bitmask", "bitmap", "mask", "area_mask"):
            if key in payload:
                candidates.append(payload.get(key))
    elif any(key in payload for key in ("bitmask", "bitmap", "mask", "area_mask")):
        warnings.append("bitmask ignored for paged configured areas")

    for value in candidates:
        ids = _parse_area_id_container(value, warnings)
        if ids:
            return ids

    warnings.append("no configured area ids found")
    return []


def _is_intish(value: Any) -> bool:
    if isinstance(value, int):
        return True
    return bool(isinstance(value, str) and value.isdigit())


def _has_configured_area_ids(payload: Mapping[str, Any]) -> bool:
    return any(
        key in payload
        for key in (
            "configured_area_ids",
            "configured_areas",
            "area_ids",
            "areas",
            "configured",
            "bitmask",
            "bitmap",
            "mask",
            "area_mask",
        )
    )


def _parse_area_id_container(value: Any, warnings: list[str]) -> list[int]:
    if value is None:
        return []

    if isinstance(value, list):
        ids: list[int] = []
        for item in value:
            area_id = _coerce_area_id(item)
            if area_id is not None:
                ids.append(area_id)
        return _dedupe_sorted(ids)

    if isinstance(value, dict):
        ids: list[int] = []
        for k, v in value.items():
            area_id = _coerce_area_id(k)
            if area_id is not None:
                if isinstance(v, bool) and not v:
                    continue
                ids.append(area_id)
            elif isinstance(v, Mapping):
                area_id = _coerce_area_id(v.get("area_id") or v.get("id"))
                if area_id is not None:
                    ids.append(area_id)
        return _dedupe_sorted(ids)

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

    warnings.append(f"unsupported configured area ids type: {type(value).__name__}")
    return []


def _ids_from_bitmask(mask: int) -> list[int]:
    ids: list[int] = []
    bit = 1
    area_id = 1
    while bit <= mask:
        if mask & bit:
            ids.append(area_id)
        bit <<= 1
        area_id += 1
    return ids


def _coerce_area_id(value: Any) -> int | None:
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            parsed = int(text, 10)
        except ValueError:
            return None
        if parsed > 0:
            return parsed
    return None


def _dedupe_sorted(items: list[int]) -> list[int]:
    return sorted(set(items))


def _extract_troubles_list(payload: Mapping[str, Any]) -> list[str]:
    for key in ("troubles", "trouble", "items", "list"):
        if key in payload:
            value = payload.get(key)
            if isinstance(value, list):
                return [str(v) for v in value if v is not None]
            if isinstance(value, str):
                text = value.strip()
                if not text:
                    return []
                return [text]
    return [] 


def _normalize_name(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def _apply_area_attribs(area, payload: Mapping[str, Any], changed: set[str]) -> None:
    if "name" in payload:
        name = _normalize_name(payload.get("name"))
        if area.name != name:
            area.name = name
            changed.add("name")


def _extract_int(payload: Mapping[str, Any], key: str) -> int | None:
    value = payload.get(key)
    return value if isinstance(value, int) else None
