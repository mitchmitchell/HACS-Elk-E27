from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, cast

import pytest

from elke27_lib.client import Elke27Client
from test.helpers.internal import get_kernel, get_private


class _FakeSession:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    def send_json(
        self,
        msg: dict[str, Any],
        *,
        priority: object = None,
        on_sent: Callable[[float], None] | None = None,
        on_fail: Callable[[BaseException], None] | None = None,
    ) -> None:
        del priority, on_fail
        self.sent.append(msg)
        if on_sent is not None:
            on_sent(0.0)


def _set_session(kernel: object, session: _FakeSession) -> None:
    cast(Any, kernel)._session = session


@pytest.mark.asyncio
async def test_area_set_status_payload_chime_true_false() -> None:
    client = Elke27Client()
    kernel = get_kernel(client)
    fake_session = _FakeSession()
    _set_session(kernel, fake_session)
    kernel.state.panel.session_id = 1

    task_true = asyncio.create_task(client.async_execute("area_set_status", area_id=1, chime=True))
    await asyncio.sleep(0)
    sent_true = fake_session.sent[0]["area"]["set_status"]
    assert sent_true == {"area_id": 1, "Chime": True}
    on_message = get_private(kernel, "_on_message")
    on_message(
        {
            "seq": fake_session.sent[0]["seq"],
            "area": {"set_status": {"area_id": 1, "Chime": True}},
        }
    )
    await task_true

    task_false = asyncio.create_task(
        client.async_execute("area_set_status", area_id=2, chime=False)
    )
    await asyncio.sleep(0)
    sent_false = fake_session.sent[1]["area"]["set_status"]
    assert sent_false == {"area_id": 2, "Chime": False}
    on_message = get_private(kernel, "_on_message")
    on_message(
        {
            "seq": fake_session.sent[1]["seq"],
            "area": {"set_status": {"area_id": 2, "Chime": False}},
        }
    )
    await task_false


@pytest.mark.asyncio
async def test_area_set_status_ack_vs_broadcast() -> None:
    client = Elke27Client()
    kernel = get_kernel(client)
    fake_session = _FakeSession()
    _set_session(kernel, fake_session)
    kernel.state.panel.session_id = 1

    task = asyncio.create_task(client.async_execute("area_set_status", area_id=1, chime=True))
    await asyncio.sleep(0)
    sent = fake_session.sent[0]
    seq = sent["seq"]

    on_message = get_private(kernel, "_on_message")
    on_message({"seq": 0, "area": {"set_status": {"area_id": 1, "Chime": True}}})
    await asyncio.sleep(0)
    assert not task.done()

    on_message = get_private(kernel, "_on_message")
    on_message({"seq": seq, "area": {"set_status": {"area_id": 1, "Chime": True}}})
    result = await task
    assert result.ok is True
    pending = get_private(kernel, "_pending_responses")
    assert pending.pending_count() == 0
