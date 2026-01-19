import logging

from elke27_lib.dispatcher import Dispatcher, PendingRequest


def test_paged_missing_block_id_warns_and_skips(caplog):
    dispatcher = Dispatcher()
    route = ("area", "get_configured")
    handled = []

    def _handler(msg, ctx):
        handled.append(msg)
        return True

    dispatcher.register(route, _handler)
    dispatcher.register_paged(route, merge_fn=lambda blocks, count: {"areas": [], "block_count": count})
    dispatcher.add_pending(PendingRequest(seq=1, expected_route=route))

    msg = {"seq": 1, "area": {"get_configured": {"block_count": 2, "areas": [1]}}}

    with caplog.at_level(logging.WARNING):
        result = dispatcher.dispatch(msg)

    assert result.handled is True
    assert handled == []
    assert any("missing/invalid block_id" in record.getMessage() for record in caplog.records)
