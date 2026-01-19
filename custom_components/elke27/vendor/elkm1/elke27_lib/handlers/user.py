"""
elke27_lib/handlers/user.py

Read-only handlers for the "user" domain.
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
    UserConfiguredInventoryReady,
)
from elke27_lib.states import PanelState, UserState

EmitFn = Callable[[object, DispatchContext], None]
NowFn = Callable[[], float]


def make_user_get_configured_handler(state: PanelState, emit: EmitFn, now: NowFn):
    """
    Handler for ("user","get_configured").
    """
    def handler_user_get_configured(msg: Mapping[str, Any], ctx: DispatchContext) -> bool:
        user_obj = msg.get("user")
        if not isinstance(user_obj, Mapping):
            return False

        payload = user_obj.get("get_configured")
        if not isinstance(payload, Mapping):
            return False

        error_code = payload.get("error_code", user_obj.get("error_code"))
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
                        scope="user",
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
                    scope="user",
                    entity_id=None,
                    message=None,
                ),
                ctx=ctx,
            )
            return True

        users = _extract_configured_ids(payload)
        if users:
            state.inventory.configured_users.update(users)

        block_id = payload.get("block_id")
        block_count = payload.get("block_count")
        if (
            isinstance(block_id, int)
            and isinstance(block_count, int)
            and block_count >= 1
            and block_id >= block_count
        ):
            state.inventory.configured_users_complete = True
            emit(
                UserConfiguredInventoryReady(
                    kind=UserConfiguredInventoryReady.KIND,
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

    return handler_user_get_configured


def make_user_get_attribs_handler(state: PanelState, emit: EmitFn, now: NowFn):
    """
    Handler for ("user","get_attribs").
    """
    def handler_user_get_attribs(msg: Mapping[str, Any], ctx: DispatchContext) -> bool:
        user_obj = msg.get("user")
        if not isinstance(user_obj, Mapping):
            return False

        payload = user_obj.get("get_attribs")
        if not isinstance(payload, Mapping):
            return False

        error_code = payload.get("error_code", user_obj.get("error_code"))
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
                        scope="user",
                        entity_id=payload.get("user_id") if isinstance(payload.get("user_id"), int) else None,
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
                    scope="user",
                    entity_id=payload.get("user_id") if isinstance(payload.get("user_id"), int) else None,
                    message=None,
                ),
                ctx=ctx,
            )
            return True

        user_id = payload.get("user_id")
        if not isinstance(user_id, int) or user_id < 1:
            return False

        user = state.get_or_create_user(user_id)
        changed: set[str] = set()
        _apply_user_attribs(user, payload, changed)
        user.last_update_at = now()
        state.panel.last_message_at = user.last_update_at

        return True

    return handler_user_get_attribs


def _extract_configured_ids(payload: Mapping[str, Any]) -> set[int]:
    for key in ("users", "user_ids", "configured_users", "configured_user_ids"):
        value = payload.get(key)
        if isinstance(value, list):
            return {item for item in value if isinstance(item, int) and item >= 1}
    return set()


def _normalize_name(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def _apply_user_attribs(user: UserState, payload: Mapping[str, Any], changed: set[str]) -> None:
    if "name" in payload:
        name = _normalize_name(payload.get("name"))
        if user.name != name:
            user.name = name
            changed.add("name")
    if "group_id" in payload:
        group_id = payload.get("group_id")
        if isinstance(group_id, int) and user.group_id != group_id:
            user.group_id = group_id
            changed.add("group_id")
    if "enabled" in payload:
        enabled = payload.get("enabled")
        if isinstance(enabled, bool) and user.enabled != enabled:
            user.enabled = enabled
            changed.add("enabled")
    if "pin" in payload:
        pin = payload.get("pin")
        if isinstance(pin, int) and user.pin != pin:
            user.pin = pin
            changed.add("pin")
    if "flags" in payload:
        flags = payload.get("flags")
        if isinstance(flags, list) and user.flags != flags:
            user.flags = flags
            changed.add("flags")

    for key, value in payload.items():
        if key in {"user_id", "error_code", "name", "group_id", "enabled", "pin", "flags"}:
            continue
        if user.fields.get(key) != value:
            user.fields[key] = value
            changed.add(key)
