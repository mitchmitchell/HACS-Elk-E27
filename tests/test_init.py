# ruff: noqa: S101, SLF001, TC001, TC002, ARG001
"""Tests for Elke27 setup and unload."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from elke27_lib.errors import Elke27ConnectionError, Elke27LinkRequiredError
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from tests.conftest import ClientHarness


async def test_setup_and_unload(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: ClientHarness
) -> None:
    """Setup connects; unload stops the coordinator and disconnects once."""
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_config_entry.state is ConfigEntryState.LOADED
    mock_client.client.async_connect.assert_awaited_once()
    assert hass.states.get("alarm_control_panel.test_panel_house") is not None

    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_config_entry.state is ConfigEntryState.NOT_LOADED
    mock_client.client.async_disconnect.assert_awaited_once()


async def test_unload_platform_failure_keeps_client(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: ClientHarness
) -> None:
    """If a platform fails to unload, the client is not torn down."""
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    with patch.object(
        hass.config_entries, "async_unload_platforms", AsyncMock(return_value=False)
    ):
        assert not await hass.config_entries.async_unload(mock_config_entry.entry_id)
    assert mock_config_entry.state is ConfigEntryState.FAILED_UNLOAD
    mock_client.client.async_disconnect.assert_not_awaited()
    hub = mock_config_entry.runtime_data.hub
    assert hub.client is mock_client.client


async def test_setup_failure_after_connect_disconnects(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: ClientHarness
) -> None:
    """A failure after connecting still disconnects the client."""
    mock_client.client.async_refresh_csm.side_effect = RuntimeError("boom")
    mock_config_entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_config_entry.state is ConfigEntryState.SETUP_ERROR
    mock_client.client.async_disconnect.assert_awaited()


@pytest.mark.parametrize(
    ("error", "state"),
    [
        (Elke27ConnectionError("down"), ConfigEntryState.SETUP_RETRY),
        (Elke27LinkRequiredError("relink"), ConfigEntryState.SETUP_ERROR),
    ],
)
async def test_setup_connect_errors(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: ClientHarness,
    error: Exception,
    state: ConfigEntryState,
) -> None:
    """Connection errors retry; a link error needs reauth."""
    mock_client.client.async_connect.side_effect = error
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_config_entry.state is state


async def test_unload_does_not_log_connection_lost(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: ClientHarness,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An intentional disconnect on unload is not logged as a lost connection."""
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert "Panel connection lost" not in caplog.text


async def test_reconnect_task_cancelled_on_unload(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: ClientHarness
) -> None:
    """The reconnect loop is an entry task, so unload cancels it."""
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    hub = mock_config_entry.runtime_data.hub

    mock_client.client.async_connect.side_effect = Elke27ConnectionError("down")
    hub._schedule_reconnect()
    task = hub._reconnect_task
    assert task is not None
    assert task in mock_config_entry._background_tasks
    await hass.async_block_till_done(wait_background_tasks=False)

    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert task.done()
