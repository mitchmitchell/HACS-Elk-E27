"""
elke27_lib/handlers/keypad.py

Read-only handlers for the "keypad" domain.
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
    KeypadConfiguredInventoryReady,
    TableCsmChanged,
)
from elke27_lib.states import KeypadState, PanelState, update_csm_snapshot

EmitFn = Callable[[object, DispatchContext], None]
NowFn = Callable[[], float]

LOG = logging.getLogger(__name__)


def make_keypad_get_configured_handler(state: PanelState, emit: EmitFn, now: NowFn):
    """
    Handler for ("keypad","get_configured").
    """
    def handler_keypad_get_configured(msg: Mapping[str, Any], ctx: DispatchContext) -> bool:
        keypad_obj = msg.get("keypad")
        if not isinstance(keypad_obj, Mapping):
            return False

        payload = keypad_obj.get("get_configured")
        if not isinstance(payload, Mapping):
            return False

        error_code = payload.get("error_code", keypad_obj.get("error_code"))
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
                        scope="keypad",
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
                    scope="keypad",
                    entity_id=None,
                    message=None,
                ),
                ctx=ctx,
            )
            return True

        keypads = _extract_configured_ids(payload)
        if keypads:
            state.inventory.configured_keypads.update(keypads)
            for keypad_id in keypads:
                state.get_or_create_keypad(keypad_id)

        block_id = payload.get("block_id")
        block_count = payload.get("block_count")
        if (
            isinstance(block_id, int)
            and isinstance(block_count, int)
            and block_count >= 1
            and block_id >= block_count
        ):
            state.inventory.configured_keypads_complete = True
            emit(
                KeypadConfiguredInventoryReady(
                    kind=KeypadConfiguredInventoryReady.KIND,
                    at=UNSET_AT,
                    seq=UNSET_SEQ,
                    classification=UNSET_CLASSIFICATION,
                    route=UNSET_ROUTE,
                    session_id=UNSET_SESSION_ID,
                ),
                ctx=ctx,
            )

        state.panel.last_message_at = now()
        return True

    return handler_keypad_get_configured


def make_keypad_get_attribs_handler(state: PanelState, emit: EmitFn, now: NowFn):
    """
    Handler for ("keypad","get_attribs").
    """
    def handler_keypad_get_attribs(msg: Mapping[str, Any], ctx: DispatchContext) -> bool:
        keypad_obj = msg.get("keypad")
        if not isinstance(keypad_obj, Mapping):
            return False

        payload = keypad_obj.get("get_attribs")
        if not isinstance(payload, Mapping):
            return False

        error_code = payload.get("error_code", keypad_obj.get("error_code"))
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
                        scope="keypad",
                        entity_id=payload.get("keypad_id") if isinstance(payload.get("keypad_id"), int) else None,
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
                    scope="keypad",
                    entity_id=payload.get("keypad_id") if isinstance(payload.get("keypad_id"), int) else None,
                    message=None,
                ),
                ctx=ctx,
            )
            return True

        keypad_id = payload.get("keypad_id")
        if not isinstance(keypad_id, int) or keypad_id < 1:
            return False

        keypad = state.get_or_create_keypad(keypad_id)
        changed: set[str] = set()
        _apply_keypad_attribs(keypad, payload, changed)
        keypad.last_update_at = now()
        state.panel.last_message_at = keypad.last_update_at
        return True

    return handler_keypad_get_attribs


def make_keypad_get_table_info_handler(state: PanelState, emit: EmitFn, now: NowFn):
    """
    Handler for ("keypad","get_table_info").
    """
    def handler_keypad_get_table_info(msg: Mapping[str, Any], ctx: DispatchContext) -> bool:
        keypad_obj = msg.get("keypad")
        if not isinstance(keypad_obj, Mapping):
            return False

        payload = keypad_obj.get("get_table_info")
        if payload is None:
            payload = keypad_obj.get("table_info")
        if not isinstance(payload, Mapping):
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
                    scope="keypad",
                    entity_id=None,
                    message=None,
                ),
                ctx=ctx,
            )
            return True

        state.table_info_by_domain["keypad"] = dict(payload)
        state.panel.last_message_at = now()
        table_csm = _extract_table_csm(payload, domain="keypad")
        if table_csm is not None:
            old = state.table_csm_by_domain.get("keypad")
            if old != table_csm:
                state.table_csm_by_domain["keypad"] = table_csm
                emit(
                    TableCsmChanged(
                        kind=TableCsmChanged.KIND,
                        at=UNSET_AT,
                        seq=UNSET_SEQ,
                        classification=UNSET_CLASSIFICATION,
                        route=UNSET_ROUTE,
                        session_id=UNSET_SESSION_ID,
                        domain="keypad",
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

    return handler_keypad_get_table_info


def _extract_configured_ids(payload: Mapping[str, Any]) -> set[int]:
    for key in ("keypads", "keypad_ids", "configured_keypads", "configured_keypad_ids"):
        value = payload.get(key)
        if isinstance(value, list):
            return {item for item in value if isinstance(item, int) and item >= 1}
    return set()


def _normalize_name(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def _apply_keypad_attribs(keypad: KeypadState, payload: Mapping[str, Any], changed: set[str]) -> None:
    if "name" in payload:
        name = _normalize_name(payload.get("name"))
        if keypad.name != name:
            keypad.name = name
            changed.add("name")
    if "area" in payload:
        area = payload.get("area")
        if isinstance(area, int) and keypad.area != area:
            keypad.area = area
            changed.add("area")
    if "zone_id" in payload:
        zone_id = payload.get("zone_id")
        if isinstance(zone_id, int) and keypad.zone_id != zone_id:
            keypad.zone_id = zone_id
            changed.add("zone_id")
    if "source_id" in payload:
        source_id = payload.get("source_id")
        if isinstance(source_id, int) and keypad.source_id != source_id:
            keypad.source_id = source_id
            changed.add("source_id")
    if "device_id" in payload:
        device_id = payload.get("device_id")
        if isinstance(device_id, str) and keypad.device_id != device_id:
            keypad.device_id = device_id
            changed.add("device_id")
    if "flags" in payload:
        flags = payload.get("flags")
        if isinstance(flags, list) and keypad.flags != flags:
            keypad.flags = flags
            changed.add("flags")

    for key, value in payload.items():
        if key in {"keypad_id", "error_code", "name", "area", "zone_id", "source_id", "device_id", "flags"}:
            continue
        if keypad.fields.get(key) != value:
            keypad.fields[key] = value
            changed.add(key)


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
