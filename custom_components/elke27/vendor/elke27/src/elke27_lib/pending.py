"""Async pending response manager keyed by seq."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

ResponseKey = tuple[str, str]
LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class PendingResponse:
    seq: int
    command_key: str
    expected_route: ResponseKey
    created_at: float
    future: asyncio.Future[Mapping[str, Any]]
    loop: asyncio.AbstractEventLoop


class PendingResponseManager:
    _now: Callable[[], float]
    _pending: dict[int, PendingResponse]
    _lock: threading.Lock

    def __init__(self, *, now: Callable[[], float] = time.monotonic) -> None:
        self._now = now
        self._pending = {}
        self._lock = threading.Lock()

    def create(
        self,
        seq: int,
        *,
        command_key: str,
        expected_route: ResponseKey,
        loop: asyncio.AbstractEventLoop,
    ) -> asyncio.Future[Mapping[str, Any]]:
        future: asyncio.Future[Mapping[str, Any]] = loop.create_future()
        entry = PendingResponse(
            seq=seq,
            command_key=command_key,
            expected_route=expected_route,
            created_at=self._now(),
            future=future,
            loop=loop,
        )
        with self._lock:
            self._pending[seq] = entry
        return future

    def resolve(self, seq: int, msg: Mapping[str, Any]) -> bool:
        entry = self._pop(seq)
        if entry is None:
            return False

        def _set_result() -> None:
            if not entry.future.done():
                entry.future.set_result(msg)

        self._call_in_loop(entry, _set_result)
        return True

    def fail(self, seq: int, exc: BaseException) -> bool:
        entry = self._pop(seq)
        if entry is None:
            return False

        def _set_exc() -> None:
            if not entry.future.done():
                entry.future.set_exception(exc)

        self._call_in_loop(entry, _set_exc)
        return True

    def drop(self, seq: int) -> None:
        with self._lock:
            self._pending.pop(seq, None)

    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)

    def _pop(self, seq: int) -> PendingResponse | None:
        with self._lock:
            return self._pending.pop(seq, None)

    @staticmethod
    def _call_in_loop(entry: PendingResponse, fn: Callable[[], None]) -> None:
        try:
            entry.loop.call_soon_threadsafe(fn)
        except RuntimeError as exc:
            LOG.warning("PendingResponse loop dispatch failed: %s", exc, exc_info=True)
            fn()
