import logging

from elke27_lib.handlers.area import make_area_get_configured_handler
from elke27_lib.states import PanelState


def test_area_get_configured_block_id_is_used(caplog):
    state = PanelState()
    emitted = []

    def _emit(evt, ctx):
        emitted.append(evt)

    handler = make_area_get_configured_handler(state, _emit, lambda: 0.0)
    msg = {"area": {"get_configured": {"block_id": 1, "block_count": 1, "areas": [1, 2]}}}

    with caplog.at_level(logging.WARNING):
        assert handler(msg, ctx=None) is True

    assert not caplog.records
    assert state.inventory.configured_areas == {1, 2}
