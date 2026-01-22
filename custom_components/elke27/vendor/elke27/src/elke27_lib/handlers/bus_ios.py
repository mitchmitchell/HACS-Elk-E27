"""
elke27_lib/handlers/bus_ios.py

Read-only handlers for the "bus_io_dev" domain.
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
    AuthorizationRequiredEvent,
    Event,
)
from elke27_lib.states import PanelState

EmitFn = Callable[[Event, DispatchContext], None]
NowFn = Callable[[], float]

LOG = logging.getLogger(__name__)


def _as_mapping(obj: object) -> Mapping[str, Any] | None:
    if isinstance(obj, Mapping):
        return cast(Mapping[str, Any], obj)
    return None


def make_bus_ios_get_trouble_handler(state: PanelState, emit: EmitFn, now: NowFn):
    """
    Handler for ("bus_io_dev","get_trouble") responses.
    """

    def handler_bus_ios_get_trouble(msg: Mapping[str, Any], ctx: DispatchContext) -> bool:
        bus_obj = _as_mapping(msg.get("bus_io_dev"))
        if bus_obj is None:
            bus_obj = _as_mapping(msg.get("bus_ios"))
        if bus_obj is None:
            return False

        payload = _as_mapping(bus_obj.get("get_trouble"))
        if payload is None:
            return False
        if getattr(ctx, "classification", None) == "BROADCAST":
            LOG.warning("bus_io_dev.get_trouble broadcast received")

        error_code = payload.get("error_code", bus_obj.get("error_code"))
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
                        scope="bus_io_dev",
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
                    scope="bus_io_dev",
                    entity_id=None,
                    message=None,
                ),
                ctx,
            )
            return True

        state.bus_io_status["get_trouble"] = dict(payload)
        state.panel.last_message_at = now()
        return True

    return handler_bus_ios_get_trouble
