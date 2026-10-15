# ruff: noqa: S101, SLF001, TC001, TC002, ARG001, PLR2004
"""D3: the coordinator owns the client (no hub.py), Q5 stale refresh, tasks."""

from __future__ import annotations

import asyncio
import dataclasses
import importlib.util
from typing import Any
from unittest.mock import MagicMock, patch

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.elke27 import coordinator as coordinator_module
from custom_components.elke27.tasks import create_background_task
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from tests.conftest import PANEL_MAC, ClientHarness
from tests.test_empty_snapshot import _connection_event

WRITE_METHODS = (
    "async_arm_area",
    "async_disarm_area",
    "async_set_output",
    "async_set_zone_bypass",
    "async_execute",
)


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> Any:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry.runtime_data.coordinator


def test_hub_module_is_deleted() -> None:
    """hub.py is gone with no shim (decision 13)."""
    assert importlib.util.find_spec("custom_components.elke27.hub") is None


async def test_runtime_data_holds_only_the_coordinator(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: ClientHarness
) -> None:
    """runtime_data has the coordinator, which owns the client."""
    coordinator = await _setup(hass, mock_config_entry)
    assert not hasattr(mock_config_entry.runtime_data, "hub")
    assert coordinator.client is mock_client.client
    assert coordinator.update_interval is None


async def test_identity_unchanged(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: ClientHarness
) -> None:
    """Unique IDs are <mac>:<id> (ADR-0126 decision 16); sensors use their key."""
    await _setup(hass, mock_config_entry)
    registry = er.async_get(hass)
    unique_ids = {
        e.unique_id
        for e in er.async_entries_for_config_entry(registry, mock_config_entry.entry_id)
    }
    assert f"{PANEL_MAC}:1" in unique_ids
    assert f"{PANEL_MAC}:panel_name" in unique_ids
    assert f"{PANEL_MAC}:panel_ready" in unique_ids
    assert not any(":area:" in u or ":zone:" in u for u in unique_ids)
    assert all(uid.startswith(f"{PANEL_MAC}:") for uid in unique_ids)


async def test_disconnect_is_one_listener_update(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: ClientHarness
) -> None:
    """A lost connection updates listeners once; entities go unavailable."""
    coordinator = await _setup(hass, mock_config_entry)
    calls: list[None] = []
    remove = coordinator.async_add_listener(lambda: calls.append(None))
    mock_client.client.is_ready = False
    mock_client.emit(_connection_event(connected=False))
    await hass.async_block_till_done()
    remove()
    assert len(calls) == 1
    assert hass.states.get("alarm_control_panel.house").state == (STATE_UNAVAILABLE)


async def test_stale_snapshot_refreshes_once_per_window(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: ClientHarness
) -> None:
    """Q5: stale > 60 s while connected triggers one read-only CSM refresh."""
    coordinator = await _setup(hass, mock_config_entry)
    mock_client.snapshot = dataclasses.replace(mock_client.snapshot, stale=True)
    refresh = mock_client.client.async_refresh_csm
    refresh.reset_mock()
    for name in WRITE_METHODS:
        getattr(mock_client.client, name).reset_mock()

    async def _check_at(when: float) -> None:
        with patch.object(coordinator_module, "_monotonic", return_value=when):
            coordinator._async_check_stale(MagicMock())
        await hass.async_block_till_done()

    await _check_at(1000.0)  # first seen stale
    await _check_at(1059.0)
    assert refresh.await_count == 0
    await _check_at(1061.0)
    assert refresh.await_count == 1
    await _check_at(1100.0)  # within 60 s of the last stale refresh
    assert refresh.await_count == 1
    await _check_at(1122.0)
    assert refresh.await_count == 2
    assert coordinator.stale_auto_refresh_count == 2  # D5 soak counter
    for name in WRITE_METHODS:
        getattr(mock_client.client, name).assert_not_called()


async def test_stale_refresh_skipped_while_reconnecting_or_fresh(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: ClientHarness
) -> None:
    """No stale refresh while reconnecting, and a fresh snapshot resets it."""
    coordinator = await _setup(hass, mock_config_entry)
    refresh = mock_client.client.async_refresh_csm
    refresh.reset_mock()

    async def _check_at(when: float) -> None:
        with patch.object(coordinator_module, "_monotonic", return_value=when):
            coordinator._async_check_stale(MagicMock())
        await hass.async_block_till_done()

    mock_client.snapshot = dataclasses.replace(mock_client.snapshot, stale=True)
    running = MagicMock()
    running.done.return_value = False
    with patch.object(type(coordinator.reconnect), "task", running):
        await _check_at(0.0)
        await _check_at(100.0)
    assert refresh.await_count == 0

    mock_client.snapshot = dataclasses.replace(mock_client.snapshot, stale=False)
    await _check_at(200.0)  # resets the stale clock
    mock_client.snapshot = dataclasses.replace(mock_client.snapshot, stale=True)
    await _check_at(210.0)
    await _check_at(260.0)
    assert refresh.await_count == 0

    mock_client.client.is_ready = False
    await _check_at(400.0)
    assert refresh.await_count == 0


async def test_unload_stops_stale_timer(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: ClientHarness
) -> None:
    """Unload removes the stale timer and leaves no client."""
    coordinator = await _setup(hass, mock_config_entry)
    assert coordinator._unsubscribe_stale_timer is not None
    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert coordinator._unsubscribe_stale_timer is None
    assert coordinator.client is None


def test_background_task_helper_prefers_entry() -> None:
    """One helper: entry-bound when there is an entry, else hass-bound."""
    hass = MagicMock()
    entry = MagicMock()
    coro = MagicMock()
    create_background_task(hass, entry, coro, "name")
    entry.async_create_background_task.assert_called_once_with(hass, coro, "name")
    hass.async_create_background_task.assert_not_called()
    create_background_task(hass, None, coro, "other")
    hass.async_create_background_task.assert_called_once_with(coro, "other")


async def test_stale_refresh_does_not_block_reconnect_refresh(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: ClientHarness
) -> None:
    """A Q5 refresh in flight never suppresses the post-reconnect refresh."""
    coordinator = await _setup(hass, mock_config_entry)
    mock_client.snapshot = dataclasses.replace(mock_client.snapshot, stale=True)
    release = asyncio.Event()
    calls: list[str] = []

    async def _slow_refresh() -> None:
        calls.append("refresh")
        if len(calls) == 1:
            await release.wait()

    mock_client.client.async_refresh_csm.side_effect = _slow_refresh
    with patch.object(coordinator_module, "_monotonic", return_value=0.0):
        coordinator._async_check_stale(MagicMock())
    with patch.object(coordinator_module, "_monotonic", return_value=61.0):
        coordinator._async_check_stale(MagicMock())
    await asyncio.sleep(0)
    assert calls == ["refresh"]  # stale refresh is in flight
    coordinator._handle_reconnected()
    assert coordinator._refresh_task is not None
    reconnect_task = coordinator._refresh_task
    assert not reconnect_task.done()
    release.set()
    await reconnect_task
    assert calls == ["refresh", "refresh"]
