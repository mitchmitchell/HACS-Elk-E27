"""
elke27_lib/handlers/rule.py

Read-only handlers for the "rule" domain.
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
)
from elke27_lib.states import PanelState

EmitFn = Callable[[Event, DispatchContext], None]
NowFn = Callable[[], float]


def _as_mapping(obj: object) -> Mapping[str, Any] | None:
    if isinstance(obj, Mapping):
        return cast(Mapping[str, Any], obj)
    return None


def make_rule_get_rules_handler(state: PanelState, emit: EmitFn, now: NowFn):
    """
    Handler for ("rule","get_rules").
    """

    def handler_rule_get_rules(msg: Mapping[str, Any], ctx: DispatchContext) -> bool:
        rule_obj = _as_mapping(msg.get("rule"))
        if rule_obj is None:
            return False

        payload = _as_mapping(rule_obj.get("get_rules"))
        if payload is None:
            return False

        error_code = payload.get("error_code", rule_obj.get("error_code"))
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
                        scope="rule",
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
                    scope="rule",
                    entity_id=None,
                    message=None,
                ),
                ctx,
            )
            return True

        block_id = payload.get("block_id")
        if not isinstance(block_id, int) or block_id < 0:
            return False

        block_count = payload.get("block_count")
        if isinstance(block_count, int):
            state.rules_block_count = block_count

        if block_id == 0:
            state.rules = {}
        data = payload.get("data")
        if isinstance(data, str) and block_id >= 1:
            state.rules[block_id] = {"block_id": block_id, "data": data}

        state.panel.last_message_at = now()
        return True

    return handler_rule_get_rules
