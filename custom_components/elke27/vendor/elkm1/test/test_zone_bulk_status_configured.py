from __future__ import annotations

from elke27_lib.handlers.zone import make_zone_get_all_zones_status_handler
from elke27_lib.states import PanelState


class _EmitSpy:
    def __init__(self) -> None:
        self.events = []

    def __call__(self, evt, ctx) -> None:
        self.events.append(evt)


class _Ctx:
    classification = "RESPONSE"


def test_bulk_status_respects_configured_zones() -> None:
    state = PanelState()
    state.inventory.configured_zones = {1, 2, 3}
    state.get_or_create_zone(1)
    emit = _EmitSpy()
    handler = make_zone_get_all_zones_status_handler(state, emit, now=lambda: 123.0)

    msg = {"zone": {"get_all_zones_status": {"status": "33333", "error_code": 0}}}
    assert handler(msg, _Ctx()) is True
    assert set(state.zones.keys()) == {1}
