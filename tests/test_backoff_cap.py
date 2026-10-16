# ruff: noqa: S101, PLR2004, TC001
"""
Hardware-free tests for the reconnect backoff cap (#79, #82).

Written by Tester 2026-10-16.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from elke27_lib.errors import Elke27ConnectionError
import pytest

from homeassistant.core import HomeAssistant

from .test_reconnect import MODULE, _Harness, _run

DOWN = Elke27ConnectionError("down")


def _sleeps(h_sleep: AsyncMock) -> list[float]:
    return [c.args[0] for c in h_sleep.await_args_list if c.args and c.args[0]]


def _patched(jitter: float) -> tuple[Any, Any, Any, Any]:
    return (
        patch(f"{MODULE}.ir.async_create_issue", MagicMock()),
        patch(f"{MODULE}.ir.async_delete_issue", MagicMock()),
        patch(f"{MODULE}.asyncio.sleep", AsyncMock()),
        patch(f"{MODULE}.random.uniform", return_value=jitter),
    )


def test_82_cap_constant_is_60() -> None:
    """const.RECONNECT_BACKOFF_MAX exists and is 60."""
    from custom_components.elke27 import const  # noqa: PLC0415

    assert getattr(const, "RECONNECT_BACKOFF_MAX", None) == 60


async def test_82_cap_reached_at_attempt_6(hass: HomeAssistant) -> None:
    """Base sequence is 2,4,8,16,32 then the cap from attempt 6 on."""
    h = _Harness(hass, [DOWN] * 8)
    p = _patched(1.0)
    with p[0], p[1], p[2] as sleep, p[3]:
        await _run(h)
    assert _sleeps(sleep) == [2, 4, 8, 16, 32, 60, 60, 60]


async def test_82_no_sleep_over_72s_on_long_outage_high_jitter(
    hass: HomeAssistant,
) -> None:
    """
    20 failures (~20 min outage), max jitter: no sleep above 72 s.

    1373fdc saw 72.6 s and rising.
    """
    h = _Harness(hass, [DOWN] * 20)
    p = _patched(1.2)
    with p[0], p[1], p[2] as sleep, p[3]:
        await _run(h)
    delays = _sleeps(sleep)
    assert max(delays) == pytest.approx(72.0)
    assert all(d <= 72.0 + 1e-9 for d in delays)
    assert delays[-14:] == pytest.approx([72.0] * 14)


async def test_82_low_jitter_at_cap_is_48s(hass: HomeAssistant) -> None:
    """At the cap with min jitter the sleep is 48 s, so retries land every 48-72 s."""
    h = _Harness(hass, [DOWN] * 8)
    p = _patched(0.8)
    with p[0], p[1], p[2] as sleep, p[3]:
        await _run(h)
    assert _sleeps(sleep)[-1] == pytest.approx(48.0)


async def test_82_backoff_resets_for_next_outage(hass: HomeAssistant) -> None:
    """After a successful reconnect, the next outage starts again at 2 s."""
    h = _Harness(hass, [DOWN, DOWN, DOWN, None])
    p = _patched(1.0)
    with p[0], p[1], p[2] as sleep, p[3]:
        await _run(h)
        assert _sleeps(sleep) == [2, 4, 8]
        assert h.supervisor.attempts == 0
        sleep.reset_mock()
        h.errors = [DOWN, None]
        h.stopping = False
        await _run(h)
        assert _sleeps(sleep) == [2]
