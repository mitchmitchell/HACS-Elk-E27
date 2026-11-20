# ruff: noqa: S101
"""make_coordinator's background-task shim runs tasks without hiding failures."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from tests.conftest import make_coordinator

_RESULT = 7


class _BackgroundTaskError(Exception):
    """Raised by the background task under test."""


async def test_background_task_failure_reaches_awaiter() -> None:
    """A background task created through the shim re-raises its exception."""
    hass = MagicMock()
    make_coordinator(hass)

    async def _fail() -> None:
        raise _BackgroundTaskError

    task = hass.async_create_background_task(_fail(), "test")
    with pytest.raises(_BackgroundTaskError):
        await task


async def test_background_task_result_reaches_awaiter() -> None:
    """A background task created through the shim returns its result."""
    hass = MagicMock()
    make_coordinator(hass)

    async def _value() -> int:
        return _RESULT

    task = hass.async_create_background_task(_value(), "test")
    assert await task == _RESULT
