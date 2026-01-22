import logging

from _pytest.logging import LogCaptureFixture

from elke27_lib.dispatcher import DispatchContext
from elke27_lib.events import Event
from elke27_lib.handlers.area import make_area_get_configured_handler
from elke27_lib.states import PanelState
from test.helpers.dispatch import make_ctx


def test_area_get_configured_block_id_is_used(caplog: LogCaptureFixture) -> None:
    state = PanelState()
    emitted: list[Event] = []

    def _emit(evt: Event, _ctx: DispatchContext) -> None:
        emitted.append(evt)

    handler = make_area_get_configured_handler(state, _emit, lambda: 0.0)
    msg = {"area": {"get_configured": {"block_id": 1, "block_count": 1, "areas": [1, 2]}}}

    with caplog.at_level(logging.WARNING):
        assert handler(msg, make_ctx()) is True

    assert not caplog.records
    assert state.inventory.configured_areas == {1, 2}
