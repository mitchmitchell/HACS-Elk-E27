"""
elke27_lib/handlers/control.py

Read-only handlers for the "control" domain.

Colocation policy:
- Message-specific reconcile helpers live in the same module as their handlers.
- Reconcile helpers are module-private (prefixed with _).
- Reconcile helpers are PURE:
    - no dispatcher context
    - no event emission
    - no I/O / logging
- Handlers:
    - extract payload from msg
    - call reconcile helper(s)
    - emit events

Focus: ("control","get_version_info")
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, cast

from elke27_lib.dispatcher import (
    DispatchContext,  # adjust import to your dispatcher module location
)
from elke27_lib.events import (
    UNSET_AT,
    UNSET_CLASSIFICATION,
    UNSET_ROUTE,
    UNSET_SEQ,
    UNSET_SESSION_ID,
    ApiError,
    AuthenticateResult,
    AuthorizationRequiredEvent,
    CsmSnapshotUpdated,
    DispatchRoutingError,
    DomainCsmChanged,
    Event,
    PanelVersionInfoUpdated,
)
from elke27_lib.states import PanelState, update_csm_snapshot

EmitFn = Callable[[Event, DispatchContext], None]
NowFn = Callable[[], float]

LOG = logging.getLogger(__name__)


def _as_mapping(obj: object) -> Mapping[str, Any] | None:
    if isinstance(obj, Mapping):
        return cast(Mapping[str, Any], obj)
    return None


# -------------------------
# Module-private reconcile
# -------------------------


@dataclass(frozen=True, slots=True)
class _VersionInfoOutcome:
    changed_fields: tuple[str, ...]
    error_code: int | None
    warnings: tuple[str, ...]


def _reconcile_control_get_version_info(
    state: PanelState,
    payload: Mapping[str, Any],
    *,
    now: float,
) -> _VersionInfoOutcome:
    """
    Pure reconcile for control.get_version_info.

    v0 rules (conservative):
    - Patch-style only (never clears absent fields)
    - Strict typing for stored panel meta fields (strings only)
    - Always updates state.panel.last_message_at
    """
    warnings: list[str] = []
    changed: set[str] = set()

    # Always update panel freshness
    state.panel.last_message_at = now

    # Optional response field
    error_code = payload.get("error_code")
    if error_code is not None and not isinstance(error_code, int):
        warnings.append(
            f"field 'error_code' wrong type (expected int, got {type(error_code).__name__})"
        )
        error_code = None

    # Canonical stored fields (expand cautiously once live payload confirms keys)
    # Accept a couple common variants to reduce friction for first live test.
    model = _first_present(payload, ("model", "panel_model", "hw", "hwver"))
    firmware = _first_present(payload, ("firmware", "fw", "firmware_version", "sw_version", "SSP"))
    serial = _first_present(payload, ("serial", "serial_number", "sn"))

    if model is not None:
        if isinstance(model, int):
            model = str(model)
        if isinstance(model, str):
            if state.panel.model != model:
                state.panel.model = model
                changed.add("model")
        else:
            warnings.append(
                f"field 'model' wrong type (expected str/int, got {type(model).__name__})"
            )

    if firmware is not None:
        if isinstance(firmware, str):
            if state.panel.firmware != firmware:
                state.panel.firmware = firmware
                changed.add("firmware")
        else:
            warnings.append(
                f"field 'firmware' wrong type (expected str, got {type(firmware).__name__})"
            )

    if serial is not None:
        if isinstance(serial, str):
            if state.panel.serial != serial:
                state.panel.serial = serial
                changed.add("serial")
        else:
            warnings.append(
                f"field 'serial' wrong type (expected str, got {type(serial).__name__})"
            )

    return _VersionInfoOutcome(
        changed_fields=tuple(sorted(changed)),
        error_code=error_code,
        warnings=tuple(warnings),
    )


def _first_present(payload: Mapping[str, Any], keys: tuple[str, ...]) -> Any:
    for k in keys:
        if k in payload:
            return payload.get(k)
    return None


# -------------------------
# Handler factory
# -------------------------


def make_control_authenticate_handler(state: PanelState, emit: EmitFn, now: NowFn):
    """
    Handler for ("control","authenticate") where payload is msg["control"]["authenticate"].
    """

    def handler_control_authenticate(msg: Mapping[str, Any], ctx: DispatchContext) -> bool:
        _ = now
        control_obj = _as_mapping(msg.get("control"))
        if control_obj is None:
            return False

        payload = _as_mapping(control_obj.get("authenticate"))
        if payload is None:
            return False

        error_code = payload.get("error_code")
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
                        scope="control",
                        entity_id=None,
                        message=None,
                    ),
                    ctx,
                )
            else:
                emit(
                    ApiError(
                        kind=ApiError.KIND,
                        at=UNSET_AT,
                        seq=UNSET_SEQ,
                        classification=UNSET_CLASSIFICATION,
                        route=UNSET_ROUTE,
                        session_id=UNSET_SESSION_ID,
                        error_code=error_code,
                        scope="control",
                        entity_id=None,
                        message=None,
                    ),
                    ctx,
                )
            emit(
                AuthenticateResult(
                    kind=AuthenticateResult.KIND,
                    at=UNSET_AT,
                    seq=UNSET_SEQ,
                    classification=UNSET_CLASSIFICATION,
                    route=UNSET_ROUTE,
                    session_id=UNSET_SESSION_ID,
                    success=False,
                    error_code=error_code,
                ),
                ctx,
            )
            _notify_auth_opaque(ctx, success=False, error_code=error_code)
            return True

        emit(
            AuthenticateResult(
                kind=AuthenticateResult.KIND,
                at=UNSET_AT,
                seq=UNSET_SEQ,
                classification=UNSET_CLASSIFICATION,
                route=UNSET_ROUTE,
                session_id=UNSET_SESSION_ID,
                success=True,
                error_code=0,
            ),
            ctx,
        )
        _apply_auth_csm_updates(state, emit, ctx, payload)
        _notify_auth_opaque(ctx, success=True, error_code=0)
        return True

    return handler_control_authenticate


def make_control_get_trouble_handler(state: PanelState, emit: EmitFn, now: NowFn):
    """
    Handler for ("control","get_trouble") responses.
    """

    def handler_control_get_trouble(msg: Mapping[str, Any], ctx: DispatchContext) -> bool:
        control_obj = _as_mapping(msg.get("control"))
        if control_obj is None:
            return False

        payload = _as_mapping(control_obj.get("get_trouble"))
        if payload is None:
            return False
        if getattr(ctx, "classification", None) == "BROADCAST":
            LOG.warning("control.get_trouble broadcast received")

        error_code = payload.get("error_code", control_obj.get("error_code"))
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
                        scope="control",
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
                    scope="control",
                    entity_id=None,
                    message=None,
                ),
                ctx,
            )
            return True

        state.control_status["get_trouble"] = dict(payload)
        state.panel.last_message_at = now()
        return True

    return handler_control_get_trouble


def _notify_auth_opaque(ctx: DispatchContext, *, success: bool, error_code: int | None) -> None:
    match = ctx.response_match
    if match is None:
        return
    opaque = match.opaque
    if opaque is None:
        return
    payload = {"success": success, "error_code": error_code}
    put_nowait = getattr(opaque, "put_nowait", None)
    if callable(put_nowait):
        try:
            put_nowait(payload)
            return
        except Exception as exc:
            LOG.warning("auth opaque put_nowait failed: %s", exc, exc_info=True)
            return
    put = getattr(opaque, "put", None)
    if callable(put):
        try:
            put(payload)
        except Exception as exc:
            LOG.warning("auth opaque put failed: %s", exc, exc_info=True)
            return


def _apply_auth_csm_updates(
    state: PanelState,
    emit: EmitFn,
    ctx: DispatchContext,
    payload: Mapping[str, Any],
) -> None:
    for key, value in payload.items():
        domain = _domain_from_csm_key(key)
        if domain is None:
            continue
        parsed = _coerce_csm_value(key, value, source="authenticate")
        if parsed is None:
            continue
        old = state.domain_csm_by_name.get(domain)
        if old == parsed:
            continue
        state.domain_csm_by_name[domain] = parsed
        emit(
            DomainCsmChanged(
                kind=DomainCsmChanged.KIND,
                at=UNSET_AT,
                seq=UNSET_SEQ,
                classification=UNSET_CLASSIFICATION,
                route=UNSET_ROUTE,
                session_id=UNSET_SESSION_ID,
                csm_domain=domain,
                old=old,
                new=parsed,
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


def _domain_from_csm_key(key: Any) -> str | None:
    if not isinstance(key, str):
        return None
    if not key.endswith("_csm"):
        return None
    domain = key[:-4].strip().lower()
    return domain if domain else None


def _coerce_csm_value(key: Any, value: Any, *, source: str) -> int | None:
    if isinstance(value, bool):
        LOG.warning("%s payload csm field %r has invalid bool value.", source, key)
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if text.isdigit():
            return int(text)
    LOG.warning("%s payload csm field %r has non-int value %r.", source, key, value)
    return None


def make_control_get_version_info_handler(state: PanelState, emit: EmitFn, now: NowFn):
    """
    Handler for ("control","get_version_info") where payload is msg["control"]["get_version_info"].
    """

    def handler_control_get_version_info(msg: Mapping[str, Any], ctx: DispatchContext) -> bool:
        control_obj = _as_mapping(msg.get("control"))
        if control_obj is None:
            return False

        payload = _as_mapping(control_obj.get("get_version_info"))
        if payload is None:
            return False

        error_code = payload.get("error_code")
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
                        scope="control",
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
                    scope="control",
                    entity_id=None,
                    message=None,
                ),
                ctx,
            )
            return True

        outcome = _reconcile_control_get_version_info(state, payload, now=now())

        if outcome.changed_fields:
            emit(
                PanelVersionInfoUpdated(
                    kind=PanelVersionInfoUpdated.KIND,
                    at=UNSET_AT,
                    seq=UNSET_SEQ,
                    classification=UNSET_CLASSIFICATION,
                    route=UNSET_ROUTE,
                    session_id=UNSET_SESSION_ID,
                    changed_fields=outcome.changed_fields,
                ),
                ctx,
            )

        if outcome.warnings:
            emit(
                DispatchRoutingError(
                    kind=DispatchRoutingError.KIND,
                    at=UNSET_AT,
                    seq=UNSET_SEQ,
                    classification=UNSET_CLASSIFICATION,
                    route=UNSET_ROUTE,
                    session_id=UNSET_SESSION_ID,
                    code="schema_warnings",
                    message="control.get_version_info payload contained type/schema warnings.",
                    keys=outcome.warnings,
                    severity="info",
                ),
                ctx,
            )

        return True

    return handler_control_get_version_info
