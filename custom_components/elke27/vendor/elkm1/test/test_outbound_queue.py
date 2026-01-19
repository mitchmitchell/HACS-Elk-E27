import asyncio
import time

import pytest

from elke27_lib.outbound import OutboundItem, OutboundPriority, OutboundQueue


@pytest.mark.asyncio
async def test_outbound_queue_throttle() -> None:
    loop = asyncio.get_running_loop()
    sent_times: list[float] = []
    done = asyncio.Event()

    def _send(payload: bytes) -> None:
        del payload

    def _on_sent(ts: float) -> None:
        sent_times.append(ts)
        if len(sent_times) >= 5:
            done.set()

    queue = OutboundQueue(loop=loop, send_fn=_send, min_interval_s=0.05, max_burst=1)
    queue.start()

    for i in range(5):
        queue.enqueue(
            OutboundItem(
                payload=b"x",
                seq=i,
                kind="request",
                priority=OutboundPriority.NORMAL,
                enqueued_at=time.monotonic(),
                on_sent=_on_sent,
            )
        )

    await asyncio.wait_for(done.wait(), timeout=2.0)
    deltas = [b - a for a, b in zip(sent_times, sent_times[1:])]
    assert all(delta >= 0.04 for delta in deltas)


@pytest.mark.asyncio
async def test_outbound_queue_priority() -> None:
    loop = asyncio.get_running_loop()
    sent: list[str] = []
    done = asyncio.Event()

    def _send(payload: bytes) -> None:
        del payload

    def _make_on_sent(label: str) -> None:
        def _cb(_: float) -> None:
            sent.append(label)
            if len(sent) >= 4:
                done.set()
        return _cb

    queue = OutboundQueue(loop=loop, send_fn=_send, min_interval_s=0.01, max_burst=1)
    queue.start()

    queue.enqueue(
        OutboundItem(
            payload=b"n1",
            seq=1,
            kind="request",
            priority=OutboundPriority.NORMAL,
            enqueued_at=time.monotonic(),
            on_sent=_make_on_sent("n1"),
        )
    )
    queue.enqueue(
        OutboundItem(
            payload=b"n2",
            seq=2,
            kind="request",
            priority=OutboundPriority.NORMAL,
            enqueued_at=time.monotonic(),
            on_sent=_make_on_sent("n2"),
        )
    )
    queue.enqueue(
        OutboundItem(
            payload=b"n3",
            seq=3,
            kind="request",
            priority=OutboundPriority.NORMAL,
            enqueued_at=time.monotonic(),
            on_sent=_make_on_sent("n3"),
        )
    )
    queue.enqueue(
        OutboundItem(
            payload=b"h1",
            seq=99,
            kind="request",
            priority=OutboundPriority.HIGH,
            enqueued_at=time.monotonic(),
            on_sent=_make_on_sent("h1"),
        )
    )

    await asyncio.wait_for(done.wait(), timeout=2.0)
    assert "h1" in sent
    assert sent.index("h1") < sent.index("n3")


@pytest.mark.asyncio
async def test_outbound_queue_stop_fails_pending() -> None:
    loop = asyncio.get_running_loop()
    failed = asyncio.Event()

    def _send(payload: bytes) -> None:
        del payload

    def _on_fail(exc: BaseException) -> None:
        assert isinstance(exc, RuntimeError)
        failed.set()

    queue = OutboundQueue(loop=loop, send_fn=_send, min_interval_s=0.5, max_burst=1)
    queue.start()

    queue.enqueue(
        OutboundItem(
            payload=b"x",
            seq=1,
            kind="request",
            priority=OutboundPriority.NORMAL,
            enqueued_at=time.monotonic(),
            on_fail=_on_fail,
        )
    )

    queue.stop(fail_exc=RuntimeError("transport gone"))
    await asyncio.wait_for(failed.wait(), timeout=1.0)
