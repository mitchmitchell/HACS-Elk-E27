from __future__ import annotations

from elke27_lib.const import E27ErrorCode
from elke27_lib.dispatcher import DispatchContext
from elke27_lib.events import ApiError, AreaStatusUpdated, Event
from elke27_lib.handlers.area import make_area_get_status_handler
from elke27_lib.states import PanelState
from test.helpers.dispatch import make_ctx


class _EmitSpy:
    def __init__(self) -> None:
        self.events: list[Event] = []

    def __call__(self, evt: Event, _ctx: DispatchContext) -> None:
        self.events.append(evt)


_Ctx = make_ctx


def test_area_get_status_updates_state_and_emits_event() -> None:
    state = PanelState()
    emit = _EmitSpy()
    handler = make_area_get_status_handler(state, emit, now=lambda: 123.0)

    msg = {
        "area": {
            "get_status": {
                "area_id": 1,
                "arm_state": "armed",
                "ready_status": "ready",
                "alarm_state": "none",
                "error_code": E27ErrorCode.ELKERR_NONE,
            }
        }
    }

    assert handler(msg, _Ctx()) is True
    area = state.areas[1]
    assert area.arm_state == "armed"
    assert area.ready_status == "ready"
    assert area.alarm_state == "none"
    assert any(isinstance(e, AreaStatusUpdated) for e in emit.events)


def test_area_get_status_error_code_emits_api_error() -> None:
    state = PanelState()
    emit = _EmitSpy()
    handler = make_area_get_status_handler(state, emit, now=lambda: 123.0)

    msg = {"area": {"get_status": {"area_id": 1, "error_code": E27ErrorCode.ELKERR_INVALID_PARAM}}}

    assert handler(msg, _Ctx()) is True
    assert any(isinstance(e, ApiError) for e in emit.events)
