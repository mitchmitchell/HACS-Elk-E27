"""
elke27_lib/handlers/system.py

Read-only handlers for the "system" domain.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping

from elke27_lib.dispatcher import DispatchContext
from elke27_lib.events import (
    ApiError,
    AuthorizationRequiredEvent,
    UNSET_AT,
    UNSET_CLASSIFICATION,
    UNSET_ROUTE,
    UNSET_SEQ,
    UNSET_SESSION_ID,
)
from elke27_lib.states import PanelState


EmitFn = Callable[[object, DispatchContext], None]
NowFn = Callable[[], float]


def _handle_system_command(
    state: PanelState,
    emit: EmitFn,
    now: NowFn,
    msg: Mapping[str, Any],
    ctx: DispatchContext,
    *,
    command: str,
    alt_command: str | None = None,
) -> bool:
    system_obj = msg.get("system")
    if not isinstance(system_obj, Mapping):
        return False

    payload = system_obj.get(command)
    if not isinstance(payload, Mapping) and alt_command is not None:
        payload = system_obj.get(alt_command)
    if not isinstance(payload, Mapping):
        return False

    error_code = payload.get("error_code", system_obj.get("error_code"))
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
                    scope="system",
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
                scope="system",
                entity_id=None,
                message=None,
            ),
            ctx=ctx,
        )
        return True

    state.system_status[command] = dict(payload)
    if command in {"get_trouble", "get_troubles"}:
        troubles = payload.get("troubles")
        if isinstance(troubles, list):
            state.system_status["troubles"] = list(troubles)
    state.panel.last_message_at = now()
    return True


def make_system_get_trouble_handler(state: PanelState, emit: EmitFn, now: NowFn):
    """
    Handler for ("system","get_trouble") responses.
    """
    def handler_system_get_trouble(msg: Mapping[str, Any], ctx: DispatchContext) -> bool:
        return _handle_system_command(
            state,
            emit,
            now,
            msg,
            ctx,
            command="get_trouble",
            alt_command="get_troubles",
        )

    return handler_system_get_trouble


def make_system_get_troubles_handler(state: PanelState, emit: EmitFn, now: NowFn):
    """
    Handler for ("system","get_troubles") responses.
    """
    def handler_system_get_troubles(msg: Mapping[str, Any], ctx: DispatchContext) -> bool:
        return _handle_system_command(
            state,
            emit,
            now,
            msg,
            ctx,
            command="get_troubles",
        )

    return handler_system_get_troubles


def make_system_get_table_info_handler(state: PanelState, emit: EmitFn, now: NowFn):
    """
    Handler for ("system","get_table_info") responses.
    """
    def handler_system_get_table_info(msg: Mapping[str, Any], ctx: DispatchContext) -> bool:
        return _handle_system_command(
            state,
            emit,
            now,
            msg,
            ctx,
            command="get_table_info",
        )

    return handler_system_get_table_info


def make_system_get_attribs_handler(state: PanelState, emit: EmitFn, now: NowFn):
    """
    Handler for ("system","get_attribs") responses.
    """
    def handler_system_get_attribs(msg: Mapping[str, Any], ctx: DispatchContext) -> bool:
        return _handle_system_command(
            state,
            emit,
            now,
            msg,
            ctx,
            command="get_attribs",
        )

    return handler_system_get_attribs


def make_system_set_attribs_handler(state: PanelState, emit: EmitFn, now: NowFn):
    """
    Handler for ("system","set_attribs") responses.
    """
    def handler_system_set_attribs(msg: Mapping[str, Any], ctx: DispatchContext) -> bool:
        return _handle_system_command(
            state,
            emit,
            now,
            msg,
            ctx,
            command="set_attribs",
        )

    return handler_system_set_attribs


def make_system_get_cutoffs_handler(state: PanelState, emit: EmitFn, now: NowFn):
    """
    Handler for ("system","get_cutoffs") responses.
    """
    def handler_system_get_cutoffs(msg: Mapping[str, Any], ctx: DispatchContext) -> bool:
        return _handle_system_command(
            state,
            emit,
            now,
            msg,
            ctx,
            command="get_cutoffs",
        )

    return handler_system_get_cutoffs


def make_system_set_cutoffs_handler(state: PanelState, emit: EmitFn, now: NowFn):
    """
    Handler for ("system","set_cutoffs") responses.
    """
    def handler_system_set_cutoffs(msg: Mapping[str, Any], ctx: DispatchContext) -> bool:
        return _handle_system_command(
            state,
            emit,
            now,
            msg,
            ctx,
            command="set_cutoffs",
        )

    return handler_system_set_cutoffs


def make_system_get_sounders_handler(state: PanelState, emit: EmitFn, now: NowFn):
    """
    Handler for ("system","get_sounders") responses.
    """
    def handler_system_get_sounders(msg: Mapping[str, Any], ctx: DispatchContext) -> bool:
        return _handle_system_command(
            state,
            emit,
            now,
            msg,
            ctx,
            command="get_sounders",
        )

    return handler_system_get_sounders


def make_system_get_system_time_handler(state: PanelState, emit: EmitFn, now: NowFn):
    """
    Handler for ("system","get_system_time") responses.
    """
    def handler_system_get_system_time(msg: Mapping[str, Any], ctx: DispatchContext) -> bool:
        return _handle_system_command(
            state,
            emit,
            now,
            msg,
            ctx,
            command="get_system_time",
        )

    return handler_system_get_system_time


def make_system_set_system_time_handler(state: PanelState, emit: EmitFn, now: NowFn):
    """
    Handler for ("system","set_system_time") responses.
    """
    def handler_system_set_system_time(msg: Mapping[str, Any], ctx: DispatchContext) -> bool:
        return _handle_system_command(
            state,
            emit,
            now,
            msg,
            ctx,
            command="set_system_time",
        )

    return handler_system_set_system_time


def make_system_set_system_key_handler(state: PanelState, emit: EmitFn, now: NowFn):
    """
    Handler for ("system","set_system_key") responses.
    """
    def handler_system_set_system_key(msg: Mapping[str, Any], ctx: DispatchContext) -> bool:
        return _handle_system_command(
            state,
            emit,
            now,
            msg,
            ctx,
            command="set_system_key",
        )

    return handler_system_set_system_key


def make_system_file_info_handler(state: PanelState, emit: EmitFn, now: NowFn):
    """
    Handler for ("system","file_info") responses.
    """
    def handler_system_file_info(msg: Mapping[str, Any], ctx: DispatchContext) -> bool:
        return _handle_system_command(
            state,
            emit,
            now,
            msg,
            ctx,
            command="file_info",
        )

    return handler_system_file_info


def make_system_get_debug_flags_handler(state: PanelState, emit: EmitFn, now: NowFn):
    """
    Handler for ("system","get_debug_flags") responses.
    """
    def handler_system_get_debug_flags(msg: Mapping[str, Any], ctx: DispatchContext) -> bool:
        return _handle_system_command(
            state,
            emit,
            now,
            msg,
            ctx,
            command="get_debug_flags",
        )

    return handler_system_get_debug_flags


def make_system_set_debug_flags_handler(state: PanelState, emit: EmitFn, now: NowFn):
    """
    Handler for ("system","set_debug_flags") responses.
    """
    def handler_system_set_debug_flags(msg: Mapping[str, Any], ctx: DispatchContext) -> bool:
        return _handle_system_command(
            state,
            emit,
            now,
            msg,
            ctx,
            command="set_debug_flags",
        )

    return handler_system_set_debug_flags


def make_system_get_debug_string_handler(state: PanelState, emit: EmitFn, now: NowFn):
    """
    Handler for ("system","get_debug_string") responses.
    """
    def handler_system_get_debug_string(msg: Mapping[str, Any], ctx: DispatchContext) -> bool:
        return _handle_system_command(
            state,
            emit,
            now,
            msg,
            ctx,
            command="get_debug_string",
        )

    return handler_system_get_debug_string


def make_system_r_u_alive_handler(state: PanelState, emit: EmitFn, now: NowFn):
    """
    Handler for ("system","r_u_alive") responses.
    """
    def handler_system_r_u_alive(msg: Mapping[str, Any], ctx: DispatchContext) -> bool:
        return _handle_system_command(
            state,
            emit,
            now,
            msg,
            ctx,
            command="r_u_alive",
        )

    return handler_system_r_u_alive


def make_system_reset_smokes_handler(state: PanelState, emit: EmitFn, now: NowFn):
    """
    Handler for ("system","reset_smokes") responses.
    """
    def handler_system_reset_smokes(msg: Mapping[str, Any], ctx: DispatchContext) -> bool:
        return _handle_system_command(
            state,
            emit,
            now,
            msg,
            ctx,
            command="reset_smokes",
        )

    return handler_system_reset_smokes


def make_system_set_run_handler(state: PanelState, emit: EmitFn, now: NowFn):
    """
    Handler for ("system","set_run") responses.
    """
    def handler_system_set_run(msg: Mapping[str, Any], ctx: DispatchContext) -> bool:
        return _handle_system_command(
            state,
            emit,
            now,
            msg,
            ctx,
            command="set_run",
        )

    return handler_system_set_run


def make_system_start_updt_handler(state: PanelState, emit: EmitFn, now: NowFn):
    """
    Handler for ("system","start_updt") responses.
    """
    def handler_system_start_updt(msg: Mapping[str, Any], ctx: DispatchContext) -> bool:
        return _handle_system_command(
            state,
            emit,
            now,
            msg,
            ctx,
            command="start_updt",
        )

    return handler_system_start_updt


def make_system_reconfig_handler(state: PanelState, emit: EmitFn, now: NowFn):
    """
    Handler for ("system","reconfig") responses.
    """
    def handler_system_reconfig(msg: Mapping[str, Any], ctx: DispatchContext) -> bool:
        return _handle_system_command(
            state,
            emit,
            now,
            msg,
            ctx,
            command="reconfig",
        )

    return handler_system_reconfig


def make_system_get_update_handler(state: PanelState, emit: EmitFn, now: NowFn):
    """
    Handler for ("system","get_update") responses.
    """
    def handler_system_get_update(msg: Mapping[str, Any], ctx: DispatchContext) -> bool:
        return _handle_system_command(
            state,
            emit,
            now,
            msg,
            ctx,
            command="get_update",
        )

    return handler_system_get_update
