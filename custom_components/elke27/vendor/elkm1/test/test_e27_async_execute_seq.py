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


@pytest.mark.asyncio
async def test_async_execute_sends_seq_and_resolves():
    client = Elke27Client()
    kernel = client._kernel
    kernel._session = _FakeSession()
    kernel.state.panel.session_id = 1

    task = asyncio.create_task(client.async_execute("control_get_version_info"))
    await asyncio.sleep(0)

    assert kernel._session.sent
    sent = kernel._session.sent[0]
    seq = sent["seq"]
    assert seq > 0

    kernel._on_message({"seq": seq, "control": {"get_version_info": {"version": "1.0"}}})
    result = await task

    assert result.ok is True
    assert result.data == {"version": "1.0"}
    assert kernel._pending_responses.pending_count() == 0


@pytest.mark.asyncio
async def test_async_execute_ignores_broadcast():
    client = Elke27Client()
    kernel = client._kernel
    kernel._session = _FakeSession()
    kernel.state.panel.session_id = 1

    task = asyncio.create_task(client.async_execute("control_get_version_info"))
    await asyncio.sleep(0)

    sent = kernel._session.sent[0]
    seq = sent["seq"]

    kernel._on_message({"seq": 0, "control": {"get_version_info": {"version": "ignored"}}})
    await asyncio.sleep(0)
    assert not task.done()

    kernel._on_message({"seq": seq, "control": {"get_version_info": {"version": "1.1"}}})
    result = await task

    assert result.ok is True
    assert result.data == {"version": "1.1"}
    assert kernel._pending_responses.pending_count() == 0


@pytest.mark.asyncio
async def test_async_execute_times_out_and_cleans_pending():
    client = Elke27Client()
    kernel = client._kernel
    kernel._session = _FakeSession()
    kernel.state.panel.session_id = 1

    result = await client.async_execute("control_get_version_info", timeout_s=0.01)

    assert result.ok is False
    assert isinstance(result.error, E27Timeout)
    assert "control_get_version_info" in str(result.error)
    assert kernel._pending_responses.pending_count() == 0
