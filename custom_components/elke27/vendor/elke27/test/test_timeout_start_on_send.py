from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from types import SimpleNamespace
from typing import cast

import pytest

from elke27_lib.client import Elke27Client
from elke27_lib.kernel import E27Kernel
from elke27_lib.pending import PendingResponseManager
from test.helpers.internal import get_private


class _FakeSession:
    _kernel: _FakeKernel
    _delay_s: float

    def __init__(self, kernel: _FakeKernel, delay_s: float) -> None:
        self._kernel = kernel
        self._delay_s = delay_s

    def send_json(
        self,
        msg: Mapping[str, object],
        *,
        priority: object | None = None,
        on_sent: Callable[[float], None] | None = None,
        on_fail: Callable[[BaseException], None] | None = None,
    ) -> None:
        del priority, on_fail
        loop = asyncio.get_running_loop()
        seq = cast(int, msg.get("seq"))

        def _fire() -> None:
            if on_sent is not None:
                on_sent(loop.time())
            response = {"seq": seq, "authenticate": {"error_code": 0}}
            self._kernel.resolve_pending(seq, response)

        loop.call_later(self._delay_s, _fire)


class _FakeKernel:
    _pending_responses: PendingResponseManager
    _seq: int
    _sent_events: dict[int, asyncio.Event]
    state: SimpleNamespace
    session: _FakeSession

    def __init__(self, delay_s: float) -> None:
        self._pending_responses = PendingResponseManager()
        self._seq = 1
        self._sent_events = {}
        self.state = SimpleNamespace(panel=SimpleNamespace(session_id=1))
        self.session = _FakeSession(self, delay_s=delay_s)

    @property
    def pending_responses(self) -> PendingResponseManager:
        return self._pending_responses

    def subscribe(self, _callback: Callable[[object], None], _kinds: set[str] | None = None) -> int:
        return 1

    def _next_seq(self) -> int:
        s = self._seq
        self._seq += 1
        return s

    def next_seq(self) -> int:
        return self._next_seq()

    def _register_sent_event(self, seq: int, event: asyncio.Event) -> asyncio.Event:
        self._sent_events[seq] = event
        return event

    def register_sent_event(self, seq: int, event: asyncio.Event) -> asyncio.Event:
        return self._register_sent_event(seq, event)

    def _mark_request_sent(self, seq: int) -> None:
        event = self._sent_events.pop(seq, None)
        if event is not None:
            event.set()

    def _mark_send_failed(self, seq: int, exc: BaseException) -> None:
        self._pending_responses.fail(seq, exc)
        event = self._sent_events.pop(seq, None)
        if event is not None:
            event.set()

    def resolve_pending(self, seq: int, response: Mapping[str, object]) -> None:
        self._pending_responses.resolve(seq, response)

    def _log_outbound(self, domain: str, name: str, msg: Mapping[str, object]) -> None:
        del domain, name, msg

    def _send_request_with_seq(
        self,
        seq: int,
        domain: str,
        name: str,
        payload: object,
        *,
        pending: bool,
        opaque: object,
        expected_route: tuple[str, str] | None,
        priority: object | None = None,
        timeout_s: float | None = None,
        expects_reply: bool = True,
    ) -> int:
        del pending, opaque, expected_route, timeout_s, expects_reply
        msg: dict[str, object] = (
            {"seq": seq, domain: payload}
            if name == "__root__"
            else {"seq": seq, domain: {name: payload}}
        )
        if self.state.panel.session_id is not None:
            msg["session_id"] = self.state.panel.session_id
        self.session.send_json(
            msg,
            priority=priority,
            on_sent=lambda _: self._mark_request_sent(seq),
            on_fail=lambda exc: self._mark_send_failed(seq, exc),
        )
        return seq

    def send_request_with_seq(
        self,
        seq: int,
        domain: str,
        name: str,
        payload: object,
        *,
        pending: bool,
        opaque: object,
        expected_route: tuple[str, str] | None,
        priority: object | None = None,
        timeout_s: float | None = None,
        expects_reply: bool = True,
    ) -> int:
        return self._send_request_with_seq(
            seq,
            domain,
            name,
            payload,
            pending=pending,
            opaque=opaque,
            expected_route=expected_route,
            priority=priority,
            timeout_s=timeout_s,
            expects_reply=expects_reply,
        )


@pytest.mark.asyncio
async def test_timeout_starts_on_send() -> None:
    kernel = _FakeKernel(delay_s=0.05)
    client = Elke27Client(kernel=cast(E27Kernel, cast(object, kernel)))

    authenticate = get_private(client, "_async_authenticate")
    result = await authenticate(pin=1234, timeout_s=0.01)
    assert result.ok is True
