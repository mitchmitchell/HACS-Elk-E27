from __future__ import annotations

from elke27_lib.const import E27ErrorCode
from elke27_lib.dispatcher import DispatchContext, MessageKind
from elke27_lib.handlers.zone import make_zone_get_attribs_handler
from elke27_lib.states import PanelState


class _EmitSpy:
    def __init__(self) -> None:
        self.events: list[object] = []

    def __call__(self, evt: object, ctx: DispatchContext) -> None:
        self.events.append(evt)


def _ctx(route: tuple[str, str]) -> DispatchContext:
    return DispatchContext(
        kind=MessageKind.DIRECTED,
        seq=None,
        session_id=None,
        route=route,
        classification="RESPONSE",
    )


def test_zone_get_attribs_sets_name() -> None:
    state = PanelState()
    emit = _EmitSpy()
    handler = make_zone_get_attribs_handler(state, emit, now=lambda: 123.0)

    msg = {
        "zone": {
            "get_attribs": {
                "zone_id": 1,
                "name": "Front Door",
                "error_code": E27ErrorCode.ELKERR_NONE,
            }
        }
    }
    assert handler(msg, _ctx(("zone", "get_attribs"))) is True
    assert state.zones[1].name == "Front Door"
