import logging
from collections.abc import Mapping

from _pytest.logging import LogCaptureFixture

from elke27_lib.dispatcher import DispatchContext, Dispatcher, PagedBlock, PendingRequest


def test_paged_missing_block_id_warns_and_skips(caplog: LogCaptureFixture) -> None:
    dispatcher = Dispatcher()
    route = ("area", "get_configured")
    handled: list[Mapping[str, object]] = []

    def _handler(msg: Mapping[str, object], _ctx: DispatchContext) -> bool:
        handled.append(msg)
        return True

    dispatcher.register(route, _handler)

    def _merge(_blocks: list[PagedBlock], count: int) -> dict[str, object]:
        return {"areas": [], "block_count": count}

    dispatcher.register_paged(route, merge_fn=_merge)
    dispatcher.add_pending(PendingRequest(seq=1, expected_route=route))

    msg = {"seq": 1, "area": {"get_configured": {"block_count": 2, "areas": [1]}}}

    with caplog.at_level(logging.WARNING):
        result = dispatcher.dispatch(msg)

    assert result.handled is True
    assert handled == []
    assert any("missing/invalid block_id" in record.getMessage() for record in caplog.records)
