from __future__ import annotations

from elke27_lib.dispatcher import DispatchContext, MessageKind
from elke27_lib.handlers.zone import make_zone_get_all_zones_status_handler
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


def test_bulk_status_respects_configured_zones() -> None:
    state = PanelState()
    state.inventory.configured_zones = {1, 2, 3}
    state.get_or_create_zone(1)
    emit = _EmitSpy()
    handler = make_zone_get_all_zones_status_handler(state, emit, now=lambda: 123.0)

    msg = {"zone": {"get_all_zones_status": {"status": "33333", "error_code": 0}}}
    assert handler(msg, _ctx(("zone", "get_all_zones_status"))) is True
    assert set(state.zones.keys()) == {1}
