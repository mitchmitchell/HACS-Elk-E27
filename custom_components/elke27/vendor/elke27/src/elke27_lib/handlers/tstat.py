"""
elke27_lib/handlers/tstat.py

Read/observe-only handlers for the "tstat" domain.
"""

from __future__ import annotations

import logging
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
    BootstrapCountsReady,
    CsmSnapshotUpdated,
    Event,
    TableCsmChanged,
    TstatStatusUpdated,
    TstatTableInfoUpdated,
)
from elke27_lib.states import PanelState, TstatState, update_csm_snapshot

EmitFn = Callable[[Event, DispatchContext], None]
NowFn = Callable[[], float]

LOG = logging.getLogger(__name__)


def _as_mapping(obj: object) -> Mapping[str, Any] | None:
    if isinstance(obj, Mapping):
        return cast(Mapping[str, Any], obj)
    return None


def _coerce_int(value: object) -> int | None:
    return value if isinstance(value, int) else None


def make_tstat_get_status_handler(state: PanelState, emit: EmitFn, now: NowFn):
    """
    Handler for ("tstat","get_status").
    """

    def handler_tstat_get_status(msg: Mapping[str, Any], ctx: DispatchContext) -> bool:
        tstat_obj = _as_mapping(msg.get("tstat"))
        if tstat_obj is None:
            return False

        payload = _as_mapping(tstat_obj.get("get_status"))
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
                    scope="tstat",
                    entity_id=_coerce_int(payload.get("tstat_id")),
                    message=None,
                ),
                ctx,
            )
            return True

        tstat_id = payload.get("tstat_id")
        if not isinstance(tstat_id, int) or tstat_id < 1:
            return False

        tstat = state.get_or_create_tstat(tstat_id)
        changed: set[str] = set()
        _apply_tstat_status_fields(tstat, payload, changed)
        tstat.last_update_at = now()
        state.panel.last_message_at = tstat.last_update_at

        emit(
            TstatStatusUpdated(
                kind=TstatStatusUpdated.KIND,
                at=UNSET_AT,
                seq=UNSET_SEQ,
                classification=UNSET_CLASSIFICATION,
                route=UNSET_ROUTE,
                session_id=UNSET_SESSION_ID,
                tstat_id=tstat_id,
                mode=tstat.mode,
                fan_mode=tstat.fan_mode,
                temperature=tstat.temperature,
            ),
            ctx,
        )
        return True

    return handler_tstat_get_status


def make_tstat_get_table_info_handler(state: PanelState, emit: EmitFn, now: NowFn):
    """
    Handler for ("tstat","get_table_info").
    """

    def handler_tstat_get_table_info(msg: Mapping[str, Any], ctx: DispatchContext) -> bool:
        tstat_obj = _as_mapping(msg.get("tstat"))
        if tstat_obj is None:
            return False

        payload = _as_mapping(tstat_obj.get("get_table_info"))
        if payload is None:
            payload = _as_mapping(tstat_obj.get("table_info"))
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
                    scope="tstat",
                    entity_id=None,
                    message=None,
                ),
                ctx,
            )
            return True

        table_info = dict(payload)
        state.table_info_by_domain["tstat"] = table_info
        state.panel.last_message_at = now()
        table_elements = _extract_int(payload, "table_elements")
        table_csm = _extract_table_csm(payload, domain="tstat")
        if table_csm is not None:
            old = state.table_csm_by_domain.get("tstat")
            if old != table_csm:
                state.table_csm_by_domain["tstat"] = table_csm
                emit(
                    TableCsmChanged(
                        kind=TableCsmChanged.KIND,
                        at=UNSET_AT,
                        seq=UNSET_SEQ,
                        classification=UNSET_CLASSIFICATION,
                        route=UNSET_ROUTE,
                        session_id=UNSET_SESSION_ID,
                        csm_domain="tstat",
                        old=old,
                        new=table_csm,
                    ),
                    ctx,
                )
        if table_elements is not None:
            state.table_info_known.add("tstat")

        emit(
            TstatTableInfoUpdated(
                kind=TstatTableInfoUpdated.KIND,
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

    return handler_tstat_get_table_info


def _apply_tstat_status_fields(
    tstat: TstatState, payload: Mapping[str, Any], changed: set[str]
) -> None:
    _maybe_set(tstat, "temperature", payload.get("temperature"), changed)
    _maybe_set(tstat, "cool_setpoint", payload.get("cool_setpoint"), changed)
    _maybe_set(tstat, "heat_setpoint", payload.get("heat_setpoint"), changed)
    _maybe_set(tstat, "mode", payload.get("mode"), changed)
    _maybe_set(tstat, "fan_mode", payload.get("fan_mode"), changed)
    _maybe_set(tstat, "humidity", payload.get("humidity"), changed)
    _maybe_set(tstat, "rssi", payload.get("rssi"), changed)

    battery = payload.get("battery level")
    if battery is None:
        battery = payload.get("battery_level")
    _maybe_set(tstat, "battery_level", battery, changed)

    prec = payload.get("prec")
    if isinstance(prec, list):
        prec_items = cast(list[object], prec)
        prec_values: list[int] = []
        all_ints = True
        for item in prec_items:
            if not isinstance(item, int):
                all_ints = False
                break
            prec_values.append(item)
        if all_ints:
            _maybe_set(tstat, "prec", prec_values, changed)

    for key, value in payload.items():
        if key in {
            "tstat_id",
            "error_code",
            "temperature",
            "cool_setpoint",
            "heat_setpoint",
            "mode",
            "fan_mode",
            "humidity",
            "rssi",
            "battery level",
            "battery_level",
            "prec",
        }:
            continue
        if tstat.fields.get(key) != value:
            tstat.fields[key] = value
            changed.add(key)


def _maybe_set(tstat: TstatState, attr: str, value: Any, changed: set[str]) -> None:
    if value is None:
        return
    if getattr(tstat, attr) != value:
        setattr(tstat, attr, value)
        changed.add(attr)


def _extract_int(payload: Mapping[str, Any], key: str) -> int | None:
    value = payload.get(key)
    return value if isinstance(value, int) else None


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
