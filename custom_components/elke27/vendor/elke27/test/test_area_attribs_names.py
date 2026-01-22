from __future__ import annotations

from elke27_lib.const import E27ErrorCode
from elke27_lib.dispatcher import DispatchContext
from elke27_lib.events import Event
from elke27_lib.handlers.area import make_area_get_attribs_handler
from elke27_lib.states import PanelState
from test.helpers.dispatch import make_ctx


class _EmitSpy:
    def __init__(self) -> None:
        self.events: list[Event] = []

    def __call__(self, evt: Event, _ctx: DispatchContext) -> None:
        self.events.append(evt)


_Ctx = make_ctx


def test_area_get_attribs_sets_name() -> None:
    state = PanelState()
    emit = _EmitSpy()
    handler = make_area_get_attribs_handler(state, emit, now=lambda: 123.0)

    msg = {
        "area": {
            "get_attribs": {"area_id": 1, "name": "Main", "error_code": E27ErrorCode.ELKERR_NONE}
        }
    }
    assert handler(msg, _Ctx()) is True
    assert state.areas[1].name == "Main"
