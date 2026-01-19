import asyncio
from types import SimpleNamespace

import pytest

from elke27_lib.client import Elke27Client
from elke27_lib.pending import PendingResponseManager


class _FakeSession:
    def __init__(self, kernel, delay_s: float) -> None:
        self._kernel = kernel
        self._delay_s = delay_s

    def send_json(self, msg, *, priority=None, on_sent=None, on_fail=None):  # type: ignore[override]
        del priority, on_fail
        loop = asyncio.get_running_loop()
        seq = msg.get("seq")

        def _fire() -> None:
            if on_sent is not None:
                on_sent(loop.time())
            response = {"seq": seq, "authenticate": {"error_code": 0}}
            self._kernel._pending_responses.resolve(seq, response)

        loop.call_later(self._delay_s, _fire)


class _FakeKernel:
    def __init__(self, delay_s: float) -> None:
        self._pending_responses = PendingResponseManager()
        self._seq = 1
        self._sent_events = {}
        self.state = SimpleNamespace(panel=SimpleNamespace(session_id=1))
        self.session = _FakeSession(self, delay_s=delay_s)

    def subscribe(self, callback, kinds=None):
        del callback, kinds
        return 1

    def _next_seq(self) -> int:
        s = self._seq
        self._seq += 1
        return s

    def _register_sent_event(self, seq, event):
        self._sent_events[seq] = event
        return event

    def _mark_request_sent(self, seq: int) -> None:
        event = self._sent_events.pop(seq, None)
        if event is not None:
            event.set()

    def _mark_send_failed(self, seq: int, exc: BaseException) -> None:
        self._pending_responses.fail(seq, exc)
        event = self._sent_events.pop(seq, None)
        if event is not None:
            event.set()

    def _log_outbound(self, domain, name, msg) -> None:
        del domain, name, msg

    def _send_request_with_seq(
        self,
        seq: int,
        domain: str,
        name: str,
        payload,
        *,
        pending,
        opaque,
        expected_route,
        priority=None,
        timeout_s=None,
        expects_reply=True,
    ) -> int:
        del pending, opaque, expected_route, timeout_s, expects_reply
        if name == "__root__":
            msg = {"seq": seq, domain: payload}
        else:
            msg = {"seq": seq, domain: {name: payload}}
        if self.state.panel.session_id is not None:
            msg["session_id"] = self.state.panel.session_id
        self.session.send_json(
            msg,
            priority=priority,
            on_sent=lambda _: self._mark_request_sent(seq),
            on_fail=lambda exc: self._mark_send_failed(seq, exc),
        )
        return seq


@pytest.mark.asyncio
async def test_timeout_starts_on_send() -> None:
    kernel = _FakeKernel(delay_s=0.05)
    client = Elke27Client(kernel=kernel)

    result = await client._async_authenticate(pin=1234, timeout_s=0.01)
    assert result.ok is True
