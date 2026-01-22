from __future__ import annotations

from elke27_lib.const import E27ErrorCode
from elke27_lib.dispatcher import DispatchContext
from elke27_lib.events import Event
from elke27_lib.handlers.system import make_system_get_trouble_handler
from elke27_lib.states import PanelState
from test.helpers.dispatch import make_ctx


class _EmitSpy:
    def __init__(self) -> None:
        self.events: list[Event] = []

    def __call__(self, evt: Event, _ctx: DispatchContext) -> None:
        self.events.append(evt)


_Ctx = make_ctx


def test_system_get_trouble_sets_status_snapshot() -> None:
    state = PanelState()
    emit = _EmitSpy()
    handler = make_system_get_trouble_handler(state, emit, now=lambda: 123.0)

    msg: dict[str, object] = {
        "system": {
            "get_trouble": {
                "troubles": ["Low batt", "ac fail"],
                "at": [0, 0, 0, 0, 0, 0, 0, 0, 0],
                "error_code": E27ErrorCode.ELKERR_NONE,
            }
        }
    }
    assert handler(msg, _Ctx()) is True
    assert state.system_status.get("troubles") == ["Low batt", "ac fail"]


def test_system_get_troubles_variant_updates_snapshot() -> None:
    state = PanelState()
    emit = _EmitSpy()
    handler = make_system_get_trouble_handler(state, emit, now=lambda: 123.0)

    msg: dict[str, object] = {
        "system": {"get_troubles": {"troubles": [], "error_code": E27ErrorCode.ELKERR_NONE}}
    }
    assert handler(msg, _Ctx()) is True
    assert state.system_status.get("troubles") == []
