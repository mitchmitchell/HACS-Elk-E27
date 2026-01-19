"""
elke27_lib/handlers/log.py

Read-only handlers for the "log" domain.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from typing import Any

from elke27_lib.dispatcher import DispatchContext
from elke27_lib.events import (
    UNSET_AT,
    UNSET_CLASSIFICATION,
    UNSET_ROUTE,
    UNSET_SEQ,
    UNSET_SESSION_ID,
    ApiError,
    AuthorizationRequiredEvent,
    CsmSnapshotUpdated,
    TableCsmChanged,
)
from elke27_lib.states import PanelState, update_csm_snapshot

EmitFn = Callable[[object, DispatchContext], None]
NowFn = Callable[[], float]

LOG = logging.getLogger(__name__)


def make_log_get_trouble_handler(state: PanelState, emit: EmitFn, now: NowFn):
    """
    Handler for ("log","get_trouble") responses.
    """
    def handler_log_get_trouble(msg: Mapping[str, Any], ctx: DispatchContext) -> bool:
        log_obj = msg.get("log")
        if not isinstance(log_obj, Mapping):
            return False

        payload = log_obj.get("get_trouble")
        if not isinstance(payload, Mapping):
            return False
        if getattr(ctx, "classification", None) == "BROADCAST":
            LOG.warning("log.get_trouble broadcast received")

        if _handle_log_error(emit, ctx, payload, log_obj):
            return True

        _store_log_status(state, "get_trouble", payload, now())
        return True

    return handler_log_get_trouble


def make_log_get_index_handler(state: PanelState, emit: EmitFn, now: NowFn):
    """
    Handler for ("log","get_index") responses.
    """
    def handler_log_get_index(msg: Mapping[str, Any], ctx: DispatchContext) -> bool:
        log_obj = msg.get("log")
        if not isinstance(log_obj, Mapping):
            return False

        payload = log_obj.get("get_index")
        if not isinstance(payload, Mapping):
            return False

        if _handle_log_error(emit, ctx, payload, log_obj):
            return True

        _store_log_status(state, "get_index", payload, now())
        return True

    return handler_log_get_index


def make_log_get_table_info_handler(state: PanelState, emit: EmitFn, now: NowFn):
    """
    Handler for ("log","get_table_info") responses.
    """
    def handler_log_get_table_info(msg: Mapping[str, Any], ctx: DispatchContext) -> bool:
        log_obj = msg.get("log")
        if not isinstance(log_obj, Mapping):
            return False

        payload = log_obj.get("get_table_info")
        if not isinstance(payload, Mapping):
            return False

        if _handle_log_error(emit, ctx, payload, log_obj):
            return True

        state.table_info_by_domain["log"] = dict(payload)
        state.panel.last_message_at = now()
        table_csm = _extract_table_csm(payload, domain="log")
        if table_csm is not None:
            old = state.table_csm_by_domain.get("log")
            if old != table_csm:
                state.table_csm_by_domain["log"] = table_csm
                emit(
                    TableCsmChanged(
                        kind=TableCsmChanged.KIND,
                        at=UNSET_AT,
                        seq=UNSET_SEQ,
                        classification=UNSET_CLASSIFICATION,
                        route=UNSET_ROUTE,
                        session_id=UNSET_SESSION_ID,
                        domain="log",
                        old=old,
                        new=table_csm,
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
        return True

    return handler_log_get_table_info


def make_log_get_attribs_handler(state: PanelState, emit: EmitFn, now: NowFn):
    """
    Handler for ("log","get_attribs") responses.
    """
    def handler_log_get_attribs(msg: Mapping[str, Any], ctx: DispatchContext) -> bool:
        log_obj = msg.get("log")
        if not isinstance(log_obj, Mapping):
            return False

        payload = log_obj.get("get_attribs")
        if not isinstance(payload, Mapping):
            return False

        if _handle_log_error(emit, ctx, payload, log_obj):
            return True

        _store_log_status(state, "get_attribs", payload, now())
        return True

    return handler_log_get_attribs


def make_log_set_attribs_handler(state: PanelState, emit: EmitFn, now: NowFn):
    """
    Handler for ("log","set_attribs") responses.
    """
    def handler_log_set_attribs(msg: Mapping[str, Any], ctx: DispatchContext) -> bool:
        log_obj = msg.get("log")
        if not isinstance(log_obj, Mapping):
            return False

        payload = log_obj.get("set_attribs")
        if not isinstance(payload, Mapping):
            return False

        if _handle_log_error(emit, ctx, payload, log_obj):
            return True

        _store_log_status(state, "set_attribs", payload, now())
        return True

    return handler_log_set_attribs


def make_log_get_list_handler(state: PanelState, emit: EmitFn, now: NowFn):
    """
    Handler for ("log","get_list") responses.
    """
    def handler_log_get_list(msg: Mapping[str, Any], ctx: DispatchContext) -> bool:
        log_obj = msg.get("log")
        if not isinstance(log_obj, Mapping):
            return False

        payload = log_obj.get("get_list")
        if not isinstance(payload, Mapping):
            return False

        if _handle_log_error(emit, ctx, payload, log_obj):
            return True

        _store_log_status(state, "get_list", payload, now())
        return True

    return handler_log_get_list


def make_log_get_log_handler(state: PanelState, emit: EmitFn, now: NowFn):
    """
    Handler for ("log","get_log") responses.
    """
    def handler_log_get_log(msg: Mapping[str, Any], ctx: DispatchContext) -> bool:
        log_obj = msg.get("log")
        if not isinstance(log_obj, Mapping):
            return False

        payload = log_obj.get("get_log")
        if not isinstance(payload, Mapping):
            return False

        if _handle_log_error(emit, ctx, payload, log_obj):
            return True

        _store_log_status(state, "get_log", payload, now())
        return True

    return handler_log_get_log


def make_log_clear_handler(state: PanelState, emit: EmitFn, now: NowFn):
    """
    Handler for ("log","clear") responses.
    """
    def handler_log_clear(msg: Mapping[str, Any], ctx: DispatchContext) -> bool:
        log_obj = msg.get("log")
        if not isinstance(log_obj, Mapping):
            return False

        payload = log_obj.get("clear")
        if not isinstance(payload, Mapping):
            return False

        if _handle_log_error(emit, ctx, payload, log_obj):
            return True

        _store_log_status(state, "clear", payload, now())
        return True

    return handler_log_clear


def make_log_realloc_handler(state: PanelState, emit: EmitFn, now: NowFn):
    """
    Handler for ("log","realloc") responses.
    """
    def handler_log_realloc(msg: Mapping[str, Any], ctx: DispatchContext) -> bool:
        log_obj = msg.get("log")
        if not isinstance(log_obj, Mapping):
            return False

        payload = log_obj.get("realloc")
        if not isinstance(payload, Mapping):
            return False

        if _handle_log_error(emit, ctx, payload, log_obj):
            return True

        _store_log_status(state, "realloc", payload, now())
        return True

    return handler_log_realloc


def _store_log_status(state: PanelState, command: str, payload: Mapping[str, Any], now_value: float) -> None:
    state.log_status[command] = dict(payload)
    state.panel.last_message_at = now_value


def _handle_log_error(
    emit: EmitFn,
    ctx: DispatchContext,
    payload: Mapping[str, Any],
    log_obj: Mapping[str, Any],
) -> bool:
    error_code = payload.get("error_code", log_obj.get("error_code"))
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
                    scope="log",
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
                scope="log",
                entity_id=None,
                message=None,
            ),
            ctx=ctx,
        )
        return True
    return False


def _extract_table_csm(payload: Mapping[str, Any], *, domain: str) -> int | None:
    if "table_csm" not in payload:
        return None
    value = payload.get("table_csm")
    if isinstance(value, bool):
        LOG.warning("%s.get_table_info table_csm has invalid bool value.", domain)
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    LOG.warning("%s.get_table_info table_csm has non-int value %r.", domain, value)
    return None
