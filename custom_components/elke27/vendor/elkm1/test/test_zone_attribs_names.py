from __future__ import annotations

from elke27_lib.const import E27ErrorCode
from elke27_lib.handlers.zone import make_zone_get_attribs_handler
from elke27_lib.states import PanelState


class _EmitSpy:
    def __init__(self) -> None:
        self.events = []

    def __call__(self, evt, ctx) -> None:
        self.events.append(evt)


class _Ctx:
    pass


def test_zone_get_attribs_sets_name() -> None:
    state = PanelState()
    emit = _EmitSpy()
    handler = make_zone_get_attribs_handler(state, emit, now=lambda: 123.0)

    msg = {
        "zone": {
            "get_attribs": {"zone_id": 1, "name": "Front Door", "error_code": E27ErrorCode.ELKERR_NONE}
        }
    }
    assert handler(msg, _Ctx()) is True
    assert state.zones[1].name == "Front Door"
