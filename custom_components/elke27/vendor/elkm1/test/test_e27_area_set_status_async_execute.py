import asyncio

import pytest

from elke27_lib.client import Elke27Client


class _FakeSession:
    def __init__(self) -> None:
        self.sent = []

    def send_json(self, msg, *, priority=None, on_sent=None, on_fail=None):
        self.sent.append(msg)
        if on_sent is not None:
            on_sent(0.0)


@pytest.mark.asyncio
async def test_area_set_status_payload_chime_true_false():
    client = Elke27Client()
    kernel = client._kernel
    kernel._session = _FakeSession()
    kernel.state.panel.session_id = 1

    task_true = asyncio.create_task(client.async_execute("area_set_status", area_id=1, chime=True))
    await asyncio.sleep(0)
    sent_true = kernel._session.sent[0]["area"]["set_status"]
    assert sent_true == {"area_id": 1, "Chime": True}
    kernel._on_message(
        {"seq": kernel._session.sent[0]["seq"], "area": {"set_status": {"area_id": 1, "Chime": True}}}
    )
    await task_true

    task_false = asyncio.create_task(client.async_execute("area_set_status", area_id=2, chime=False))
    await asyncio.sleep(0)
    sent_false = kernel._session.sent[1]["area"]["set_status"]
    assert sent_false == {"area_id": 2, "Chime": False}
    kernel._on_message(
        {"seq": kernel._session.sent[1]["seq"], "area": {"set_status": {"area_id": 2, "Chime": False}}}
    )
    await task_false


@pytest.mark.asyncio
async def test_area_set_status_ack_vs_broadcast():
    client = Elke27Client()
    kernel = client._kernel
    kernel._session = _FakeSession()
    kernel.state.panel.session_id = 1

    task = asyncio.create_task(client.async_execute("area_set_status", area_id=1, chime=True))
    await asyncio.sleep(0)
    sent = kernel._session.sent[0]
    seq = sent["seq"]

    kernel._on_message({"seq": 0, "area": {"set_status": {"area_id": 1, "Chime": True}}})
    await asyncio.sleep(0)
    assert not task.done()

    kernel._on_message({"seq": seq, "area": {"set_status": {"area_id": 1, "Chime": True}}})
    result = await task
    assert result.ok is True
    assert kernel._pending_responses.pending_count() == 0
