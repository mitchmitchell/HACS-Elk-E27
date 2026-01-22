from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, cast

import pytest

from elke27_lib.client import Elke27Client
from elke27_lib.errors import E27Timeout
from test.helpers.internal import get_kernel, get_private


class _FakeSession:
    cfg: object

    def __init__(self) -> None:
        self.cfg = type("_Cfg", (), {"host": "test-host", "port": 1})()
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
async def test_async_execute_sends_seq_and_resolves() -> None:
    client = Elke27Client()
    kernel = get_kernel(client)
    fake_session = _FakeSession()
    _set_session(kernel, fake_session)
    kernel.state.panel.session_id = 1

    task = asyncio.create_task(client.async_execute("control_get_version_info"))
    await asyncio.sleep(0)

    assert fake_session.sent
    sent = fake_session.sent[0]
    seq = sent["seq"]
    assert seq > 0

    on_message = get_private(kernel, "_on_message")
    on_message({"seq": seq, "control": {"get_version_info": {"version": "1.0"}}})
    result = await task

    assert result.ok is True
    assert result.data == {"version": "1.0"}
    pending = get_private(kernel, "_pending_responses")
    assert pending.pending_count() == 0


@pytest.mark.asyncio
async def test_async_execute_ignores_broadcast() -> None:
    client = Elke27Client()
    kernel = get_kernel(client)
    fake_session = _FakeSession()
    _set_session(kernel, fake_session)
    kernel.state.panel.session_id = 1

    task = asyncio.create_task(client.async_execute("control_get_version_info"))
    await asyncio.sleep(0)

    sent = fake_session.sent[0]
    seq = sent["seq"]

    on_message = get_private(kernel, "_on_message")
    on_message({"seq": 0, "control": {"get_version_info": {"version": "ignored"}}})
    await asyncio.sleep(0)
    assert not task.done()

    on_message = get_private(kernel, "_on_message")
    on_message({"seq": seq, "control": {"get_version_info": {"version": "1.1"}}})
    result = await task

    assert result.ok is True
    assert result.data == {"version": "1.1"}
    pending = get_private(kernel, "_pending_responses")
    assert pending.pending_count() == 0


@pytest.mark.asyncio
async def test_async_execute_times_out_and_cleans_pending() -> None:
    client = Elke27Client()
    kernel = get_kernel(client)
    fake_session = _FakeSession()
    _set_session(kernel, fake_session)
    kernel.state.panel.session_id = 1

    result = await client.async_execute("control_get_version_info", timeout_s=0.01)

    assert result.ok is False
    assert isinstance(result.error, E27Timeout)
    error_text = str(result.error)
    assert "control_get_version_info" in error_text or "seq=" in error_text
    pending = get_private(kernel, "_pending_responses")
    assert pending.pending_count() == 0
