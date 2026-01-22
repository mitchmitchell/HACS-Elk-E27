"""
elke27_lib/handlers/output.py

Read/observe-only handlers for the "output" domain.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
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
    OutputConfiguredInventoryReady,
    OutputConfiguredUpdated,
    OutputsStatusBulkUpdated,
    OutputStatusUpdated,
    OutputTableInfoUpdated,
    TableCsmChanged,
)
from elke27_lib.states import OutputState, PanelState, update_csm_snapshot

EmitFn = Callable[[Event, DispatchContext], None]
NowFn = Callable[[], float]

LOG = logging.getLogger(__name__)


def _as_mapping(obj: object) -> Mapping[str, Any] | None:
    if isinstance(obj, Mapping):
        return cast(Mapping[str, Any], obj)
    return None


def _coerce_int(value: object) -> int | None:
    return value if isinstance(value, int) else None


def make_output_get_status_handler(state: PanelState, emit: EmitFn, now: NowFn):
    """
    Handler for ("output","get_status").
    """

    def handler_output_get_status(msg: Mapping[str, Any], ctx: DispatchContext) -> bool:
        output_obj = _as_mapping(msg.get("output"))
        if output_obj is None:
            return False

        payload = _as_mapping(output_obj.get("get_status"))
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
                    scope="output",
                    entity_id=_coerce_int(payload.get("output_id")),
                    message=None,
                ),
                ctx,
            )
            return True

        output_id = payload.get("output_id")
        if not isinstance(output_id, int) or output_id < 1:
            return False

        output = state.get_or_create_output(output_id)
        changed: set[str] = set()
        _apply_output_status_fields(output, payload, changed)
        output.last_update_at = now()
        state.panel.last_message_at = output.last_update_at

        emit(
            OutputStatusUpdated(
                kind=OutputStatusUpdated.KIND,
                at=UNSET_AT,
                seq=UNSET_SEQ,
                classification=UNSET_CLASSIFICATION,
                route=UNSET_ROUTE,
                session_id=UNSET_SESSION_ID,
                output_id=output_id,
                status=output.status,
                on=output.on,
            ),
            ctx,
        )
        return True

    return handler_output_get_status


def make_output_get_configured_handler(state: PanelState, emit: EmitFn, now: NowFn):
    """
    Handler for ("output","get_configured").
    """

    def handler_output_get_configured(msg: Mapping[str, Any], ctx: DispatchContext) -> bool:
        output_obj = _as_mapping(msg.get("output"))
        if output_obj is None:
            return False

        payload = _as_mapping(output_obj.get("get_configured"))
        if payload is None:
            return False

        error_code = payload.get("error_code", output_obj.get("error_code"))
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
                        scope="output",
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
                    scope="output",
                    entity_id=None,
                    message=None,
                ),
                ctx,
            )
            return True

        inv = state.inventory
        ids = _extract_configured_output_ids(payload)
        table_info = _as_mapping(state.table_info_by_domain.get("output"))
        if table_info is not None:
            max_id = table_info.get("table_elements")
            if isinstance(max_id, int) and max_id >= 1:
                ids = [output_id for output_id in ids if output_id <= max_id]

        inv.configured_outputs = set(ids)
        inv.configured_outputs_complete = True
        state.panel.last_message_at = now()

        emit(
            OutputConfiguredUpdated(
                kind=OutputConfiguredUpdated.KIND,
                at=UNSET_AT,
                seq=UNSET_SEQ,
                classification=UNSET_CLASSIFICATION,
                route=UNSET_ROUTE,
                session_id=UNSET_SESSION_ID,
                configured_ids=tuple(sorted(ids)),
            ),
            ctx,
        )
        emit(
            OutputConfiguredInventoryReady(
                kind=OutputConfiguredInventoryReady.KIND,
                at=UNSET_AT,
                seq=UNSET_SEQ,
                classification=UNSET_CLASSIFICATION,
                route=UNSET_ROUTE,
                session_id=UNSET_SESSION_ID,
            ),
            ctx,
        )
        return True

    return handler_output_get_configured


def make_output_get_all_outputs_status_handler(state: PanelState, emit: EmitFn, now: NowFn):
    """
    Handler for ("output","get_all_outputs_status").
    """

    def handler_output_get_all_outputs_status(msg: Mapping[str, Any], ctx: DispatchContext) -> bool:
        output_obj = _as_mapping(msg.get("output"))
        if output_obj is None:
            return False

        payload = _as_mapping(output_obj.get("get_all_outputs_status"))
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
                    scope="output",
                    entity_id=None,
                    message=None,
                ),
                ctx,
            )
            return True

        status_text = payload.get("status")
        if not isinstance(status_text, str):
            emit(
                DispatchRoutingError(
                    kind=DispatchRoutingError.KIND,
                    at=UNSET_AT,
                    seq=UNSET_SEQ,
                    classification=UNSET_CLASSIFICATION,
                    route=UNSET_ROUTE,
                    session_id=UNSET_SESSION_ID,
                    code="schema_warnings",
                    message="output.get_all_outputs_status missing status string.",
                    keys=tuple(payload.keys()),
                    severity="info",
                ),
                ctx,
            )
            return True

        compact = "".join(status_text.split()).upper()
        updated: list[int] = []
        for idx, ch in enumerate(compact):
            output_id = idx + 1
            output = state.get_or_create_output(output_id)
            if _apply_output_status_char(output, ch):
                output.last_update_at = now()
                updated.append(output_id)

        if updated:
            state.panel.last_message_at = now()

        emit(
            OutputsStatusBulkUpdated(
                kind=OutputsStatusBulkUpdated.KIND,
                at=UNSET_AT,
                seq=UNSET_SEQ,
                classification=UNSET_CLASSIFICATION,
                route=UNSET_ROUTE,
                session_id=UNSET_SESSION_ID,
                updated_count=len(updated),
                updated_ids=tuple(updated),
            ),
            ctx,
        )
        return True

    return handler_output_get_all_outputs_status


def make_output_configured_merge(_state: PanelState):
    """
    Merge paged get_configured blocks into a single payload (ADR-0013).
    """

    def _merge(blocks: list[PagedBlock], block_count: int) -> Mapping[str, Any]:
        merged: set[int] = set()
        for block in blocks:
            for output_id in _extract_configured_output_ids(block.payload):
                merged.add(output_id)
        return {"outputs": sorted(merged), "block_count": block_count}

    return _merge


def make_output_get_attribs_handler(state: PanelState, emit: EmitFn, now: NowFn):
    """
    Handler for ("output","get_attribs").
    """

    def handler_output_get_attribs(msg: Mapping[str, Any], ctx: DispatchContext) -> bool:
        output_obj = _as_mapping(msg.get("output"))
        if output_obj is None:
            return False

        payload = _as_mapping(output_obj.get("get_attribs"))
        if payload is None:
            return False

        error_code = payload.get("error_code", output_obj.get("error_code"))
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
                        scope="output",
                        entity_id=_coerce_int(payload.get("output_id")),
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
                    scope="output",
                    entity_id=_coerce_int(payload.get("output_id")),
                    message=None,
                ),
                ctx,
            )
            return True

        output_id = payload.get("output_id")
        if not isinstance(output_id, int) or output_id < 1:
            return False

        output = state.get_or_create_output(output_id)
        changed: set[str] = set()
        _apply_output_attribs(output, payload, changed)
        output.last_update_at = now()
        state.panel.last_message_at = output.last_update_at
        return True

    return handler_output_get_attribs


def make_output_get_table_info_handler(state: PanelState, emit: EmitFn, now: NowFn):
    """
    Handler for ("output","get_table_info").
    """

    def handler_output_get_table_info(msg: Mapping[str, Any], ctx: DispatchContext) -> bool:
        output_obj = _as_mapping(msg.get("output"))
        if output_obj is None:
            return False

        payload = _as_mapping(output_obj.get("get_table_info"))
        if payload is None:
            payload = _as_mapping(output_obj.get("table_info"))
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
                    scope="output",
                    entity_id=None,
                    message=None,
                ),
                ctx,
            )
            return True

        table_info = dict(payload)
        state.table_info_by_domain["output"] = table_info
        state.panel.last_message_at = now()
        table_elements = _extract_int(payload, "table_elements")
        table_csm = _extract_table_csm(payload, domain="output")
        if table_csm is not None:
            old = state.table_csm_by_domain.get("output")
            if old != table_csm:
                state.table_csm_by_domain["output"] = table_csm
                emit(
                    TableCsmChanged(
                        kind=TableCsmChanged.KIND,
                        at=UNSET_AT,
                        seq=UNSET_SEQ,
                        classification=UNSET_CLASSIFICATION,
                        route=UNSET_ROUTE,
                        session_id=UNSET_SESSION_ID,
                        csm_domain="output",
                        old=old,
                        new=table_csm,
                    ),
                    ctx,
                )
        if table_elements is not None:
            state.table_info_known.add("output")

        emit(
            OutputTableInfoUpdated(
                kind=OutputTableInfoUpdated.KIND,
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

    return handler_output_get_table_info


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


def _apply_output_status_fields(
    output: OutputState, payload: Mapping[str, Any], changed: set[str]
) -> None:
    status = payload.get("status")
    if isinstance(status, str):
        norm = status.strip().upper()
        if output.status != norm:
            output.status = norm
            changed.add("status")
        on = norm == "ON"
        if output.on != on:
            output.on = on
            changed.add("on")

    for key, value in payload.items():
        if key in {"output_id", "error_code", "status"}:
            continue
        if output.fields.get(key) != value:
            output.fields[key] = value
            changed.add(key)


def _extract_configured_output_ids(payload: Mapping[str, Any]) -> list[int]:
    keys = ("outputs", "output_ids", "configured_outputs", "configured_output_ids")
    for key in keys:
        value = payload.get(key)
        if isinstance(value, list):
            ids = [
                item for item in cast(list[object], value) if isinstance(item, int) and item >= 1
            ]
            return sorted(set(ids))
    return []


def _apply_output_status_char(output: OutputState, ch: str) -> bool:
    if ch not in {"0", "1"}:
        return False
    output.status_code = ch
    output.on = ch == "1"
    output.status = "ON" if output.on else "OFF"
    return True


def _normalize_name(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def _apply_output_attribs(
    output: OutputState, payload: Mapping[str, Any], changed: set[str]
) -> None:
    if "name" in payload:
        name = _normalize_name(payload.get("name"))
        if output.name != name:
            output.name = name
            changed.add("name")


def _extract_int(payload: Mapping[str, Any], key: str) -> int | None:
    value = payload.get(key)
    return value if isinstance(value, int) else None
