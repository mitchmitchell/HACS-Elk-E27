# ruff: noqa: S101, SLF001, TC001, TC002
"""Tests for the Elke27 coordinator: disconnect updates and entry-scoped tasks."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from elke27_lib.events import ConnectionStateChanged, DomainCsmChanged
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from tests.conftest import ClientHarness

ALARM = "alarm_control_panel.test_panel_house"


def _connection_event(*, connected: bool) -> ConnectionStateChanged:
    return ConnectionStateChanged(
        kind="connection",
        at=0.0,
        seq=None,
        classification="BROADCAST",
        route=("system", "connection"),
        session_id=None,
        connected=connected,
    )


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_disconnect_event_updates_entities(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: ClientHarness
) -> None:
    """A disconnect event makes entities unavailable right away."""
    await _setup(hass, mock_config_entry)
    assert hass.states.get(ALARM).state != STATE_UNAVAILABLE

    mock_client.client.is_ready = False
    mock_client.emit(_connection_event(connected=False))
    await hass.async_block_till_done()
    assert hass.states.get(ALARM).state == STATE_UNAVAILABLE


async def test_connect_event_refreshes_in_entry_task(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: ClientHarness
) -> None:
    """A connect event refreshes the CSM; a refresh failure does not escape."""
    await _setup(hass, mock_config_entry)
    mock_client.client.async_refresh_csm.reset_mock()
    mock_client.client.async_refresh_csm.side_effect = None

    mock_client.emit(_connection_event(connected=True))
    await hass.async_block_till_done()
    mock_client.client.async_refresh_csm.assert_awaited_once()


async def test_pending_debounce_cancelled_on_unload(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: ClientHarness
) -> None:
    """A debounced domain refresh is tied to the entry and cleaned up on unload."""
    await _setup(hass, mock_config_entry)
    coordinator = mock_config_entry.runtime_data.coordinator
    coordinator._debounce_seconds = 3600
    mock_client.client.async_refresh_domain_config = AsyncMock()

    mock_client.emit(
        DomainCsmChanged(
            kind="csm",
            at=0.0,
            seq=None,
            classification="BROADCAST",
            route=("zone", "csm"),
            session_id=None,
            csm_domain="zone",
            old=None,
            new=1,
        )
    )
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    task = coordinator._debounce_task
    assert task is not None
    assert task in mock_config_entry._tasks

    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert task.done()
    mock_client.client.async_refresh_domain_config.assert_not_awaited()


async def test_auto_reconnect_pushes_update_without_connected_event(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: ClientHarness
) -> None:
    """After an automatic reconnect, entities recover even if no event arrives."""
    await _setup(hass, mock_config_entry)
    hub = mock_config_entry.runtime_data.hub
    mock_client.client.is_ready = False
    mock_client.emit(_connection_event(connected=False))
    await hass.async_block_till_done()
    assert hass.states.get(ALARM).state == STATE_UNAVAILABLE

    # The reconnect succeeds, but the connected event fired before callbacks
    # were re-attached, so nothing is emitted to the coordinator.
    mock_client.client.is_ready = True
    mock_client.client.async_refresh_csm.reset_mock()
    hub._stopping = False
    await hub._async_reconnect_loop()
    await hass.async_block_till_done()
    assert hass.states.get(ALARM).state != STATE_UNAVAILABLE
    mock_client.client.async_refresh_csm.assert_awaited_once()
