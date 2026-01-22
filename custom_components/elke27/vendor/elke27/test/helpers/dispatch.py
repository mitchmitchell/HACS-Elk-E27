from __future__ import annotations

from elke27_lib.dispatcher import DispatchContext, MessageKind, RouteKey


def make_ctx(
    route: RouteKey = ("__test__", "__test__"),
    *,
    kind: MessageKind = MessageKind.DIRECTED,
    classification: str = "RESPONSE",
    seq: int | None = None,
    session_id: int | None = None,
) -> DispatchContext:
    return DispatchContext(
        kind=kind,
        seq=seq,
        session_id=session_id,
        route=route,
        classification=classification,
    )
