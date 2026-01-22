"""
elke27_lib/handlers/network_param.py

Read/observe-only handlers for the "network" domain.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, cast

from elke27_lib.dispatcher import DispatchContext
from elke27_lib.events import (
    UNSET_AT,
    UNSET_CLASSIFICATION,
    UNSET_ROUTE,
    UNSET_SEQ,
    UNSET_SESSION_ID,
    ApiError,
    AuthorizationRequiredEvent,
    Event,
    NetworkRssiUpdated,
    NetworkSsidResultsUpdated,
)
from elke27_lib.states import PanelState

EmitFn = Callable[[Event, DispatchContext], None]
NowFn = Callable[[], float]


def _as_mapping(obj: object) -> Mapping[str, Any] | None:
    if isinstance(obj, Mapping):
        return cast(Mapping[str, Any], obj)
    return None


def make_network_param_get_ssid_handler(state: PanelState, emit: EmitFn, now: NowFn):
    """
    Handler for ("network","get_ssid").
    """

    def handler_network_param_get_ssid(msg: Mapping[str, Any], ctx: DispatchContext) -> bool:
        net_obj = _as_mapping(msg.get("network"))
        if net_obj is None:
            return False

        error_code = net_obj.get("error_code")
        payload = _as_mapping(net_obj.get("get_ssid"))
        if payload is not None:
            error_code = payload.get("error_code", error_code)

        if isinstance(error_code, int) and error_code != 0:
            message = net_obj.get("error_message")
            _message_text = str(message) if message is not None else None
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
                    scope="network",
                    entity_id=None,
                    message=None,
                ),
                ctx,
            )
            return True

        results = _normalize_ssid_results(payload, net_obj)
        state.network.ssid_scan_results = results
        state.network.last_update_at = now()
        state.panel.last_message_at = state.network.last_update_at

        ssids: tuple[str, ...] = tuple(
            ssid for entry in results for ssid in [entry.get("ssid")] if isinstance(ssid, str)
        )
        emit(
            NetworkSsidResultsUpdated(
                kind=NetworkSsidResultsUpdated.KIND,
                at=UNSET_AT,
                seq=UNSET_SEQ,
                classification=UNSET_CLASSIFICATION,
                route=UNSET_ROUTE,
                session_id=UNSET_SESSION_ID,
                count=len(results),
                ssids=ssids,
            ),
            ctx,
        )
        return True

    return handler_network_param_get_ssid


def make_network_param_get_rssi_handler(state: PanelState, emit: EmitFn, now: NowFn):
    """
    Handler for ("network","get_rssi").
    """

    def handler_network_param_get_rssi(msg: Mapping[str, Any], ctx: DispatchContext) -> bool:
        net_obj = _as_mapping(msg.get("network"))
        if net_obj is None:
            return False

        payload = _as_mapping(net_obj.get("get_rssi"))

        error_code = net_obj.get("error_code")
        if payload is not None:
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
                    scope="network",
                    entity_id=None,
                    message=None,
                ),
                ctx,
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
            ctx,
        )
        return True

    return handler_network_param_get_rssi


def make_network_error_handler(_state: PanelState, emit: EmitFn, _now: NowFn):
    """
    Handler for ("network","error") domain-root errors.
    """

    def handler_network_error(msg: Mapping[str, Any], ctx: DispatchContext) -> bool:
        net_obj = _as_mapping(msg.get("network"))
        if net_obj is None:
            return False

        error_code = net_obj.get("error_code")
        if isinstance(error_code, str):
            try:
                error_code = int(error_code)
            except ValueError:
                error_code = None

        message = net_obj.get("error_message")
        message_text = str(message) if message is not None else None
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
                        message=message_text,
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
                    scope="network",
                    entity_id=None,
                    message=message_text,
                ),
                ctx,
            )
            return True

        return False

    return handler_network_error


def _normalize_ssid_results(payload: object, net_obj: Mapping[str, Any]) -> list[dict[str, object]]:
    if payload is None:
        return []

    if isinstance(payload, list):
        list_results: list[dict[str, object]] = []
        for item in cast(list[object], payload):
            entry = _normalize_ssid_entry(item)
            if entry is not None:
                list_results.append(entry)
        return list_results

    if isinstance(payload, str):
        return [{"ssid": payload}]

    if isinstance(payload, Mapping):
        payload_map = cast(Mapping[str, Any], payload)
        for key in ("ssids", "results", "list", "scan"):
            if key in payload_map:
                list_value = payload_map.get(key)
                if isinstance(list_value, list):
                    mapped_results: list[dict[str, object]] = []
                    for item in cast(list[object], list_value):
                        entry = _normalize_ssid_entry(item)
                        if entry is not None:
                            mapped_results.append(entry)
                    return mapped_results
        if "ssid" in payload_map:
            return [{"ssid": str(payload_map.get("ssid"))}]
        return [dict(payload_map)]

    list_value = net_obj.get("get_ssid")
    if isinstance(list_value, list):
        legacy_results: list[dict[str, object]] = []
        for item in cast(list[object], list_value):
            entry = _normalize_ssid_entry(item)
            if entry is not None:
                legacy_results.append(entry)
        return legacy_results

    return []


def _normalize_ssid_entry(item: Any) -> dict[str, object] | None:
    if isinstance(item, Mapping):
        return dict(cast(Mapping[str, object], item))
    if isinstance(item, str):
        return {"ssid": item}
    return None


def _extract_rssi(payload: Mapping[str, Any] | None, net_obj: Mapping[str, Any]) -> int | None:
    value = payload.get("rssi") if payload is not None else net_obj.get("rssi")

    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None
