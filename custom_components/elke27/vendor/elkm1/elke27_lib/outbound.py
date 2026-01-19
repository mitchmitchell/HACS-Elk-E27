"""Outbound send queue with rate limiting and priority."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Awaitable, Callable, Optional


class OutboundPriority(str, Enum):
    HIGH = "high"
    NORMAL = "normal"


@dataclass(slots=True)
class OutboundItem:
    payload: bytes
    seq: Optional[int]
    kind: str
    priority: OutboundPriority
    enqueued_at: float
    on_sent: Optional[Callable[[float], None]] = None
    on_fail: Optional[Callable[[BaseException], None]] = None
    label: Optional[str] = None


class OutboundQueue:
    """
    Single outbound send queue with global rate limiting and priority.

    Policy: if the queue is stopped, pending items are failed with the provided exception.
    """

    def __init__(
        self,
        *,
        loop: asyncio.AbstractEventLoop,
        send_fn: Callable[[bytes], None],
        min_interval_s: float = 0.05,
        max_burst: int = 1,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._loop = loop
        self._send_fn = send_fn
        self._min_interval_s = max(0.0, float(min_interval_s))
        self._max_burst = max(1, int(max_burst))
        self._log = logger or logging.getLogger(__name__)
        self._high_q: asyncio.Queue[OutboundItem] = asyncio.Queue()
        self._normal_q: asyncio.Queue[OutboundItem] = asyncio.Queue()
        self._stop_event = asyncio.Event()
        self._worker: Optional[asyncio.Task[None]] = None
        self._tokens: float = float(self._max_burst)
        self._last_refill: float = self._loop.time()
        self._sending: bool = False

    def start(self) -> None:
        if self._worker is None or self._worker.done():
            self._stop_event.clear()
            self._worker = self._loop.create_task(self._run())

    def stop(self, *, fail_exc: Optional[BaseException] = None) -> None:
        def _stop() -> None:
            if not self._stop_event.is_set():
                self._stop_event.set()
            self._drain_with_failure(fail_exc)
            if self._worker is not None:
                self._worker.cancel()

        if self._loop.is_running():
            self._loop.call_soon_threadsafe(_stop)
        else:
            _stop()

    def enqueue(self, item: OutboundItem) -> None:
        def _put() -> None:
            queue = self._high_q if item.priority is OutboundPriority.HIGH else self._normal_q
            queue.put_nowait(item)
            if self._log.isEnabledFor(logging.DEBUG):
                depth = self._high_q.qsize() + self._normal_q.qsize()
                self._log.debug(
                    "Outbound enqueue: seq=%s kind=%s priority=%s depth=%s",
                    item.seq,
                    item.kind,
                    item.priority.value,
                    depth,
                )

        if self._loop.is_running():
            self._loop.call_soon_threadsafe(_put)
        else:
            _put()

    def is_idle(self) -> bool:
        return self._high_q.empty() and self._normal_q.empty() and not self._sending

    async def wait_idle(self, *, timeout_s: Optional[float] = None) -> bool:
        deadline = None
        if timeout_s is not None:
            deadline = self._loop.time() + float(timeout_s)
        while True:
            if self._high_q.empty() and self._normal_q.empty() and not self._sending:
                return True
            if deadline is not None and self._loop.time() >= deadline:
                return False
            await asyncio.sleep(0.01)

    def _drain_with_failure(self, exc: Optional[BaseException]) -> None:
        if exc is None:
            return
        for queue in (self._high_q, self._normal_q):
            while not queue.empty():
                try:
                    item = queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if item.on_fail is not None:
                    try:
                        item.on_fail(exc)
                    except Exception:
                        pass
                queue.task_done()

    async def _run(self) -> None:
        while not self._stop_event.is_set():
            item = await self._next_item()
            if item is None:
                continue
            await self._throttle()
            self._sending = True
            try:
                await asyncio.to_thread(self._send_fn, item.payload)
                sent_at = time.monotonic()
                if item.on_sent is not None:
                    item.on_sent(sent_at)
                if self._log.isEnabledFor(logging.DEBUG):
                    waited = max(0.0, sent_at - item.enqueued_at)
                    self._log.debug(
                        "Outbound send: seq=%s kind=%s bytes=%s waited=%.4fs",
                        item.seq,
                        item.kind,
                        len(item.payload),
                        waited,
                    )
            except Exception as exc:
                if item.on_fail is not None:
                    item.on_fail(exc)
                if self._log.isEnabledFor(logging.DEBUG):
                    self._log.debug(
                        "Outbound send failed: seq=%s kind=%s error=%s",
                        item.seq,
                        item.kind,
                        exc,
                    )
            finally:
                self._sending = False
                self._high_q.task_done() if item.priority is OutboundPriority.HIGH else self._normal_q.task_done()

    async def _next_item(self) -> Optional[OutboundItem]:
        if self._stop_event.is_set():
            return None
        if not self._high_q.empty():
            return await self._high_q.get()
        try:
            return await asyncio.wait_for(self._normal_q.get(), timeout=0.05)
        except asyncio.TimeoutError:
            if not self._high_q.empty():
                return await self._high_q.get()
            return None

    async def _throttle(self) -> None:
        if self._min_interval_s <= 0:
            return
        now = self._loop.time()
        elapsed = now - self._last_refill
        if elapsed > 0:
            self._tokens = min(self._max_burst, self._tokens + elapsed / self._min_interval_s)
            self._last_refill = now
        if self._tokens >= 1:
            self._tokens -= 1
            return
        wait_time = (1 - self._tokens) * self._min_interval_s
        if wait_time > 0:
            await asyncio.sleep(wait_time)
        now = self._loop.time()
        self._last_refill = now
        self._tokens = max(0.0, self._tokens - 1)
