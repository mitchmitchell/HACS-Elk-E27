from __future__ import annotations

from elke27_lib.const import E27ErrorCode
from elke27_lib.dispatcher import DispatchContext
from elke27_lib.events import Event
from elke27_lib.handlers.output import make_output_get_attribs_handler
from elke27_lib.states import PanelState
from test.helpers.dispatch import make_ctx


class _EmitSpy:
    def __init__(self) -> None:
        self.events: list[Event] = []

    def __call__(self, evt: Event, _ctx: DispatchContext) -> None:
        self.events.append(evt)


_Ctx = make_ctx


def test_output_get_attribs_sets_name() -> None:
    state = PanelState()
    emit = _EmitSpy()
    handler = make_output_get_attribs_handler(state, emit, now=lambda: 123.0)

    msg = {
        "output": {
            "get_attribs": {"output_id": 1, "name": "Aux 1", "error_code": E27ErrorCode.ELKERR_NONE}
        }
    }
    assert handler(msg, _Ctx()) is True
    assert state.outputs[1].name == "Aux 1"
