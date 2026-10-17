# ruff: noqa: S101, ARG001, ANN001, TC001, TC002, TC003, F811
"""
Hardware-free tests for the once-per-drop link-loss WARNING (#80, #83).

Written by Tester 2026-10-16.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock

from elke27_lib.errors import Elke27ConnectionError
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.core import HomeAssistant

from .test_init import (  # reuse the integration harness
    _disconnect,
    _script_connect,
    _setup,
    fast_backoff,  # noqa: F401  (fixture)
)

DOWN = Elke27ConnectionError("down")


def _lost(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [
        r
        for r in caplog.records
        if r.getMessage() == "Panel connection lost"
        and r.name == "custom_components.elke27.coordinator"
    ]


async def test_83_one_warning_per_drop_across_many_retries(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client,
    fast_backoff: AsyncMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Six failed retries in one drop still log exactly one WARNING."""
    caplog.set_level(logging.DEBUG)
    await _setup(hass, mock_config_entry)
    _script_connect(mock_client, [DOWN] * 6)
    await _disconnect(mock_client)
    assert [r.levelno for r in _lost(caplog)] == [logging.WARNING]
    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)


async def test_83_second_drop_logs_second_warning_and_restore_is_info(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client,
    fast_backoff: AsyncMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Drop, reconnect, drop again: two WARNINGs; one INFO 'restored'."""
    caplog.set_level(logging.DEBUG)
    await _setup(hass, mock_config_entry)
    _script_connect(mock_client, [DOWN, None])
    await _disconnect(mock_client)
    restored = [
        r for r in caplog.records if r.getMessage() == "Panel connection restored"
    ]
    assert [r.levelno for r in restored] == [logging.INFO]
    _script_connect(mock_client, [])
    await _disconnect(mock_client)
    assert [r.levelno for r in _lost(caplog)] == [logging.WARNING, logging.WARNING]
    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)


async def test_83_warning_visible_at_default_warning_level(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client,
    fast_backoff: AsyncMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """At HA's default WARNING level the drop is still recorded."""
    caplog.set_level(logging.WARNING)
    await _setup(hass, mock_config_entry)
    _script_connect(mock_client, [])
    await _disconnect(mock_client)
    assert len(_lost(caplog)) == 1
    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
