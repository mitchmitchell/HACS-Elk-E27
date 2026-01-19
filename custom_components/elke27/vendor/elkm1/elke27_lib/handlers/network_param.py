"""
elke27_lib/handlers/network_param.py

Read/observe-only handlers for the "network" domain.
"""

from __future__ import annotations

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
    NetworkRssiUpdated,
    NetworkSsidResultsUpdated,
)
from elke27_lib.states import PanelState

EmitFn = Callable[[object, DispatchContext], None]
NowFn = Callable[[], float]


def make_network_param_get_ssid_handler(state: PanelState, emit: EmitFn, now: NowFn):
    """
    Handler for ("network","get_ssid").
    """
    def handler_network_param_get_ssid(msg: Mapping[str, Any], ctx: DispatchContext) -> bool:
        net_obj = msg.get("network")
        if not isinstance(net_obj, Mapping):
            return False

        error_code = net_obj.get("error_code")
        payload = net_obj.get("get_ssid")
        if isinstance(payload, Mapping):
            error_code = payload.get("error_code", error_code)

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
                        scope="network",
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
                    scope="network",
                    entity_id=None,
                    message=None,
                ),
                ctx=ctx,
            )
            return True

        results = _normalize_ssid_results(payload, net_obj)
        state.network.ssid_scan_results = results
        state.network.last_update_at = now()
        state.panel.last_message_at = state.network.last_update_at

        emit(
            NetworkSsidResultsUpdated(
                kind=NetworkSsidResultsUpdated.KIND,
                at=UNSET_AT,
                seq=UNSET_SEQ,
                classification=UNSET_CLASSIFICATION,
                route=UNSET_ROUTE,
                session_id=UNSET_SESSION_ID,
                count=len(results),
                ssids=tuple(r.get("ssid", "") for r in results if isinstance(r.get("ssid"), str)),
            ),
            ctx=ctx,
        )
        return True

    return handler_network_param_get_ssid


def make_network_param_get_rssi_handler(state: PanelState, emit: EmitFn, now: NowFn):
    """
    Handler for ("network","get_rssi").
    """
    def handler_network_param_get_rssi(msg: Mapping[str, Any], ctx: DispatchContext) -> bool:
        net_obj = msg.get("network")
        if not isinstance(net_obj, Mapping):
            return False

        payload = net_obj.get("get_rssi")
        if payload is not None and not isinstance(payload, Mapping):
            return False

        error_code = net_obj.get("error_code")
        if isinstance(payload, Mapping):
            error_code = payload.get("error_code", error_code)

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
                        scope="network",
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
                    scope="network",
                    entity_id=None,
                    message=None,
                ),
                ctx=ctx,
            )
            return True

        rssi = _extract_rssi(payload, net_obj)
        state.network.rssi = rssi
        state.network.last_update_at = now()
        state.panel.last_message_at = state.network.last_update_at

        emit(
            NetworkRssiUpdated(
                kind=NetworkRssiUpdated.KIND,
                at=UNSET_AT,
                seq=UNSET_SEQ,
                classification=UNSET_CLASSIFICATION,
                route=UNSET_ROUTE,
                session_id=UNSET_SESSION_ID,
                rssi=rssi,
            ),
            ctx=ctx,
        )
        return True

    return handler_network_param_get_rssi


def make_network_error_handler(state: PanelState, emit: EmitFn, now: NowFn):
    """
    Handler for ("network","error") domain-root errors.
    """
    def handler_network_error(msg: Mapping[str, Any], ctx: DispatchContext) -> bool:
        net_obj = msg.get("network")
        if not isinstance(net_obj, Mapping):
            return False

        error_code = net_obj.get("error_code")
        if isinstance(error_code, str):
            try:
                error_code = int(error_code)
            except ValueError:
                error_code = None

        if isinstance(error_code, int):
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
                        scope="network",
                        entity_id=None,
                        message=net_obj.get("error_message"),
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
                    scope="network",
                    entity_id=None,
                    message=net_obj.get("error_message"),
                ),
                ctx=ctx,
            )
            return True

        return False

    return handler_network_error


def _normalize_ssid_results(payload: Any, net_obj: Mapping[str, Any]) -> list[dict]:
    if payload is None:
        return []

    if isinstance(payload, list):
        return [_normalize_ssid_entry(item) for item in payload if _normalize_ssid_entry(item) is not None]

    if isinstance(payload, str):
        return [{"ssid": payload}]

    if isinstance(payload, Mapping):
        for key in ("ssids", "results", "list", "scan"):
            if key in payload and isinstance(payload.get(key), list):
                return [
                    _normalize_ssid_entry(item)
                    for item in payload.get(key, [])
                    if _normalize_ssid_entry(item) is not None
                ]
        if "ssid" in payload:
            return [{"ssid": str(payload.get("ssid"))}]
        return [dict(payload)]

    if isinstance(net_obj.get("get_ssid"), list):
        return [_normalize_ssid_entry(item) for item in net_obj.get("get_ssid") if _normalize_ssid_entry(item)]

    return []


def _normalize_ssid_entry(item: Any) -> dict | None:
    if isinstance(item, Mapping):
        return dict(item)
    if isinstance(item, str):
        return {"ssid": item}
    return None


def _extract_rssi(payload: Mapping[str, Any] | None, net_obj: Mapping[str, Any]) -> int | None:
    value = payload.get("rssi") if isinstance(payload, Mapping) else net_obj.get("rssi")

    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None
