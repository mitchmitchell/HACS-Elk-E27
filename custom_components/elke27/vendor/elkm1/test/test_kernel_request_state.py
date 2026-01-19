import asyncio
import unittest

from elke27_lib.errors import ConnectionLost, E27Timeout
from elke27_lib.kernel import E27Kernel, _RequestState


class FakeSession:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    def send_json(self, obj, *, priority, on_sent=None, on_fail=None) -> None:
        self.sent.append(obj)
        if on_sent is not None:
            on_sent(0.0)


class KernelRequestStateTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.kernel = E27Kernel(request_timeout_s=0.05)
        self.kernel._session = FakeSession()
        self.kernel._loop = asyncio.get_running_loop()

    def _create_pending(self, seq: int) -> asyncio.Future:
        return self.kernel._pending_responses.create(
            seq,
            command_key="test",
            expected_route=("system", "ping"),
            loop=asyncio.get_running_loop(),
        )

    def _send_request(self, seq: int, *, timeout_s: float = 0.05) -> None:
        self.kernel._send_request_with_seq(
            seq,
            "system",
            "ping",
            {"x": 1},
            pending=False,
            opaque=None,
            expected_route=("system", "ping"),
            timeout_s=timeout_s,
        )

    async def test_normal_reply_path(self) -> None:
        seq = 101
        future = self._create_pending(seq)
        self._send_request(seq)
        await asyncio.sleep(0)
        self.assertEqual(len(self.kernel._session.sent), 1)
        self.assertEqual(self.kernel._request_state, _RequestState.IN_FLIGHT)

        msg = {"seq": seq, "system": {"ping": {"ok": True}}}
        self.kernel._on_message(msg)
        reply = await asyncio.wait_for(future, timeout=0.1)
        self.assertEqual(reply, msg)
        self.assertEqual(self.kernel._request_state, _RequestState.IDLE)
        self.assertIsNone(self.kernel._active_seq)

    async def test_timeout_path(self) -> None:
        seq = 102
        future = self._create_pending(seq)
        self._send_request(seq, timeout_s=0.02)
        await asyncio.sleep(0.05)
        with self.assertRaises(E27Timeout):
            await asyncio.wait_for(future, timeout=0.1)
        self.assertEqual(self.kernel._request_state, _RequestState.IDLE)

    async def test_reply_then_timeout_race(self) -> None:
        seq = 103
        future = self._create_pending(seq)
        self._send_request(seq, timeout_s=0.5)
        await asyncio.sleep(0)

        msg = {"seq": seq, "system": {"ping": {"ok": True}}}
        self.kernel._on_message(msg)
        self.kernel._on_reply_timeout(seq)
        reply = await asyncio.wait_for(future, timeout=0.1)
        self.assertEqual(reply, msg)
        self.assertEqual(self.kernel._request_state, _RequestState.IDLE)
        self.assertIsNone(self.kernel._active_seq)

    async def test_late_reply_after_timeout(self) -> None:
        seq = 104
        future = self._create_pending(seq)
        self._send_request(seq, timeout_s=0.5)
        await asyncio.sleep(0)

        self.kernel._on_reply_timeout(seq)
        with self.assertRaises(E27Timeout):
            await asyncio.wait_for(future, timeout=0.1)

        msg = {"seq": seq, "system": {"ping": {"ok": True}}}
        self.kernel._on_message(msg)
        self.assertEqual(self.kernel._request_state, _RequestState.IDLE)
        self.assertIsNone(self.kernel._active_seq)

    async def test_disconnect_while_in_flight(self) -> None:
        seq = 105
        future = self._create_pending(seq)
        self._send_request(seq, timeout_s=0.5)
        await asyncio.sleep(0)

        self.kernel._abort_requests(ConnectionLost("Session disconnected."))
        with self.assertRaises(ConnectionLost):
            await asyncio.wait_for(future, timeout=0.1)
        self.assertEqual(self.kernel._request_state, _RequestState.IDLE)

    async def test_no_concurrent_sends(self) -> None:
        seq1 = 106
        seq2 = 107
        future1 = self._create_pending(seq1)
        future2 = self._create_pending(seq2)
        self._send_request(seq1, timeout_s=0.5)
        self._send_request(seq2, timeout_s=0.5)
        await asyncio.sleep(0)

        self.assertEqual(len(self.kernel._session.sent), 1)
        self.kernel._on_message({"seq": seq1, "system": {"ping": {"ok": True}}})
        await asyncio.wait_for(future1, timeout=0.1)
        await asyncio.sleep(0)
        self.assertEqual(len(self.kernel._session.sent), 2)
        self.kernel._on_message({"seq": seq2, "system": {"ping": {"ok": True}}})
        await asyncio.wait_for(future2, timeout=0.1)


def test_bootstrap_requests_zone_defs() -> None:
    kernel = E27Kernel()
    kernel._session = object()
    recorded: list[tuple[tuple[str, str], dict]] = []

    def _fake_request(route, **kwargs):
        recorded.append((route, dict(kwargs)))

    kernel.request = _fake_request  # type: ignore[assignment]
    kernel.requests.get = lambda route: object()  # type: ignore[assignment]

    kernel._bootstrap_requests()
    assert ("zone", "get_defs") in [route for route, _ in recorded]
