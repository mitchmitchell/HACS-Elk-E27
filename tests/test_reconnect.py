# ruff: noqa: S101, SLF001, TC001, ARG001, PLR2004, PT018, TC003
"""Direct tests for ReconnectSupervisor (ADR-0125 D2)."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from unittest.mock import AsyncMock, MagicMock, call, patch

from elke27_lib.errors import (
    Elke27AuthError,
    Elke27ConnectionError,
    Elke27ProtocolError,
)
import pytest

from custom_components.elke27.const import DOMAIN, ISSUE_RECONNECT_FAILED
from custom_components.elke27.reconnect import ReconnectSupervisor
from homeassistant.core import HomeAssistant

MODULE = "custom_components.elke27.reconnect"
ISSUE_ID = f"{ISSUE_RECONNECT_FAILED}_entry1"


class _Harness:
    """A supervisor with a fake connect and a fake client that must stay idle."""

    def __init__(self, hass: HomeAssistant, errors: list[BaseException | None]) -> None:
        self.errors = list(errors)
        self.stopping = False
        self.client = MagicMock()  # any command call on this would be a replay
        self.connect = AsyncMock(side_effect=self._connect)
        self.on_reconnected = MagicMock()
        self.entry = MagicMock()
        self.entry.entry_id = "entry1"
        self.entry.title = "Panel"
        self.entry.async_create_background_task.side_effect = (
            lambda hass_, coro, name: hass_.async_create_background_task(coro, name)
        )
        self.supervisor = ReconnectSupervisor(
            hass,
            self.entry,
            connect=self.connect,
            is_stopping=lambda: self.stopping,
            on_reconnected=self.on_reconnected,
        )

    async def _connect(self) -> None:
        err = self.errors.pop(0) if self.errors else None
        if not self.errors and err is not None:
            # Stop after the last scripted failure so the loop ends.
            self.stopping = True
        if err is not None:
            raise err


@pytest.fixture
def ir_mock() -> Iterator[MagicMock]:
    """Patch the issue registry helpers used by the supervisor."""
    manager = MagicMock()
    with (
        patch(f"{MODULE}.ir.async_create_issue", manager.create),
        patch(f"{MODULE}.ir.async_delete_issue", manager.delete),
        patch(f"{MODULE}.asyncio.sleep", AsyncMock()) as sleep_mock,
        patch(f"{MODULE}.random.uniform", return_value=1.0),
    ):
        manager.sleep = sleep_mock
        yield manager


async def test_start_twice_creates_one_task(hass: HomeAssistant) -> None:
    """start() is single-flight while a task is running."""
    h = _Harness(hass, [])
    gate = asyncio.Event()

    async def _blocked() -> None:
        await gate.wait()

    h.connect.side_effect = _blocked
    h.supervisor.start()
    first = h.supervisor.task
    h.supervisor.start()
    assert h.supervisor.task is first
    assert h.entry.async_create_background_task.call_count == 1
    gate.set()
    await hass.async_block_till_done()
    assert first is not None and first.done()


async def test_start_while_stopping_does_nothing(hass: HomeAssistant) -> None:
    """start() is a no-op once the hub is stopping."""
    h = _Harness(hass, [])
    h.stopping = True
    h.supervisor.start()
    assert h.supervisor.task is None
    h.entry.async_create_background_task.assert_not_called()


async def test_backoff_bounds_and_cap(hass: HomeAssistant, ir_mock: MagicMock) -> None:
    """Base delay is min(300, 2**n), jitter scales it, the cap is 300 * 1.2."""
    h = _Harness(hass, [])
    # Ten transport failures, then success.
    h.connect.side_effect = [Elke27ConnectionError("down")] * 10 + [None]
    with patch(f"{MODULE}.random.uniform", return_value=1.2) as uniform:
        await h.supervisor._async_run()
    uniform.assert_called_with(0.8, 1.2)
    delays = [c.args[0] for c in ir_mock.sleep.await_args_list]
    expected = [min(300, 2**n) * 1.2 for n in range(1, 11)]
    assert delays == pytest.approx(expected)
    assert max(delays) == pytest.approx(300 * 1.2)
    assert h.supervisor.attempts == 0


async def test_non_transport_issue_on_third_failure(
    hass: HomeAssistant, ir_mock: MagicMock
) -> None:
    """Two non-transport failures raise no issue; the third does."""
    h = _Harness(hass, [Elke27ProtocolError("a"), Elke27ProtocolError("b")])
    await h.supervisor._async_run()
    ir_mock.create.assert_not_called()
    assert not h.supervisor.issue_active

    h = _Harness(hass, [Elke27ProtocolError("a")] * 3)
    await h.supervisor._async_run()
    assert ir_mock.create.call_count == 1
    assert ir_mock.create.call_args.args[1:3] == (DOMAIN, ISSUE_ID)
    assert h.supervisor.issue_active


async def test_transport_failures_never_raise_issue(
    hass: HomeAssistant, ir_mock: MagicMock
) -> None:
    """Ten transport failures in a row raise no Repairs issue."""
    h = _Harness(hass, [Elke27ConnectionError("down")] * 10)
    await h.supervisor._async_run()
    ir_mock.create.assert_not_called()
    assert h.supervisor.attempts == 10


async def test_transport_failure_does_not_reset_streak(
    hass: HomeAssistant, ir_mock: MagicMock
) -> None:
    """A transport failure between non-transport failures keeps the streak."""
    h = _Harness(
        hass,
        [
            Elke27ProtocolError("1"),
            Elke27ProtocolError("2"),
            Elke27ConnectionError("down"),
            Elke27ProtocolError("3"),
        ],
    )
    await h.supervisor._async_run()
    assert ir_mock.create.call_count == 1


async def test_auth_deletes_issue_before_reauth_once(
    hass: HomeAssistant, ir_mock: MagicMock
) -> None:
    """Auth refusal deletes the issue first, then starts reauth exactly once."""
    h = _Harness(hass, [])
    h.entry.async_start_reauth = ir_mock.reauth
    h.connect.side_effect = Elke27AuthError("bad")
    await h.supervisor._async_run()
    await h.supervisor._async_run()
    ordered = [c for c in ir_mock.mock_calls if c[0] in ("delete", "reauth")]
    assert ordered[:2] == [call.delete(hass, DOMAIN, ISSUE_ID), call.reauth(hass)]
    assert ir_mock.reauth.call_count == 1
    assert h.supervisor.reauth_requested
    assert h.supervisor.attempts == 0
    assert h.connect.await_count == 2


async def test_success_clears_issue_and_notifies_once(
    hass: HomeAssistant, ir_mock: MagicMock
) -> None:
    """Success clears the issue, resets attempts and notifies exactly once."""
    h = _Harness(hass, [])
    h.connect.side_effect = [Elke27ProtocolError("x")] * 3 + [None]
    await h.supervisor._async_run()
    assert ir_mock.create.call_count == 1
    ir_mock.delete.assert_called_with(hass, DOMAIN, ISSUE_ID)
    assert not h.supervisor.issue_active
    assert h.supervisor.attempts == 0
    h.on_reconnected.assert_called_once_with()


async def test_cancel_mid_sleep(hass: HomeAssistant) -> None:
    """cancel() during the backoff sleep cancels the task and resets attempts."""
    h = _Harness(hass, [])
    h.connect.side_effect = Elke27ConnectionError("down")
    with patch(f"{MODULE}.random.uniform", return_value=1.0):
        h.supervisor.start()
        task = h.supervisor.task
        assert task is not None
        for _ in range(5):
            await asyncio.sleep(0)
        assert h.supervisor.attempts == 1
        h.supervisor.cancel()
        await asyncio.wait([task])
    assert task.cancelled()
    assert h.supervisor.task is None
    assert h.supervisor.attempts == 0


async def test_async_stop_leaves_no_pending_task(hass: HomeAssistant) -> None:
    """The unload path awaits the cancelled task so nothing is left pending."""
    h = _Harness(hass, [])
    h.connect.side_effect = Elke27ConnectionError("down")
    with patch(f"{MODULE}.random.uniform", return_value=1.0):
        h.supervisor.start()
        task = h.supervisor.task
        for _ in range(5):
            await asyncio.sleep(0)
        await h.supervisor.async_stop()
    assert task is not None and task.done()
    assert h.supervisor.task is None


async def test_connect_is_the_only_side_effect(
    hass: HomeAssistant, ir_mock: MagicMock
) -> None:
    """The supervisor never sends commands: it only calls connect (invariant 1)."""
    h = _Harness(hass, [])
    h.connect.side_effect = [
        Elke27ConnectionError("down"),
        Elke27ProtocolError("p"),
        None,
    ]
    await h.supervisor._async_run()
    assert h.connect.await_count == 3
    assert h.client.mock_calls == []
