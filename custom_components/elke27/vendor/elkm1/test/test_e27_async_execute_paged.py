import asyncio

import pytest

from elke27_lib.client import Elke27Client
from elke27_lib.errors import E27Timeout


class _FakeSession:
    def __init__(self) -> None:
        self.sent = []

    def send_json(self, msg, *, priority=None, on_sent=None, on_fail=None):
        self.sent.append(msg)
        if on_sent is not None:
            on_sent(0.0)


async def _wait_for_sent(session: _FakeSession, count: int, *, timeout_s: float = 0.1) -> None:
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout_s
    while len(session.sent) < count and loop.time() < end:
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_async_execute_paged_blocks_merges():
    client = Elke27Client()
    kernel = client._kernel
    kernel._session = _FakeSession()
    kernel.state.panel.session_id = 1

    task = asyncio.create_task(client.async_execute("zone_get_configured"))
    await asyncio.sleep(0)

    sent1 = kernel._session.sent[0]
    seq1 = sent1["seq"]
    assert sent1["zone"]["get_configured"]["block_id"] == 1

    kernel._on_message(
        {
            "seq": seq1,
            "zone": {"get_configured": {"block_id": 1, "block_count": 3, "zones": [1, 2]}},
        }
    )
    await _wait_for_sent(kernel._session, 2)

    sent2 = kernel._session.sent[1]
    seq2 = sent2["seq"]
    assert sent2["zone"]["get_configured"]["block_id"] == 2

    kernel._on_message(
        {
            "seq": seq2,
            "zone": {"get_configured": {"block_id": 2, "block_count": 3, "zones": [3]}},
        }
    )
    await _wait_for_sent(kernel._session, 3)

    sent3 = kernel._session.sent[2]
    seq3 = sent3["seq"]
    assert sent3["zone"]["get_configured"]["block_id"] == 3

    kernel._on_message(
        {
            "seq": seq3,
            "zone": {"get_configured": {"block_id": 3, "block_count": 3, "zones": [4, 5]}},
        }
    )

    result = await task
    assert result.ok is True
    assert result.data == {"zones": [1, 2, 3, 4, 5], "block_count": 3}
    assert kernel._pending_responses.pending_count() == 0


@pytest.mark.asyncio
async def test_async_execute_paged_timeout_on_block():
    client = Elke27Client()
    kernel = client._kernel
    kernel._session = _FakeSession()
    kernel.state.panel.session_id = 1

    task = asyncio.create_task(client.async_execute("zone_get_configured", timeout_s=0.01))
    await asyncio.sleep(0)

    sent1 = kernel._session.sent[0]
    seq1 = sent1["seq"]

    kernel._on_message(
        {
            "seq": seq1,
            "zone": {"get_configured": {"block_id": 1, "block_count": 2, "zones": [1]}},
        }
    )

    result = await task
    assert result.ok is False
    assert isinstance(result.error, E27Timeout)
    assert kernel._pending_responses.pending_count() == 0
