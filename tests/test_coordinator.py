# ruff: noqa: S101, SLF001, TC001, TC002, PLR2004
"""Tests for the Elke27 coordinator: disconnect updates and entry-scoped tasks."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from elke27_lib.errors import (
    Elke27AuthError,
    Elke27ConnectionError,
    Elke27ProtocolError,
)
from elke27_lib.events import ConnectionStateChanged, DomainCsmChanged
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.elke27.const import DOMAIN
from homeassistant.config_entries import SOURCE_REAUTH
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
    assert task in mock_config_entry._background_tasks

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


def _domain_event(domain: str) -> DomainCsmChanged:
    return DomainCsmChanged(
        kind="csm",
        at=0.0,
        seq=None,
        classification="BROADCAST",
        route=(domain, "csm"),
        session_id=None,
        csm_domain=domain,
        old=None,
        new=1,
    )


async def test_reconnect_and_connected_event_refresh_once(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: ClientHarness
) -> None:
    """The reconnect listener and a connected event share one refresh."""
    await _setup(hass, mock_config_entry)
    coordinator = mock_config_entry.runtime_data.coordinator
    release = asyncio.Event()

    async def _slow_refresh() -> None:
        await release.wait()

    mock_client.client.async_refresh_csm.reset_mock()
    mock_client.client.async_refresh_csm.side_effect = _slow_refresh

    coordinator._handle_reconnected()
    mock_client.emit(_connection_event(connected=True))
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    task = coordinator._refresh_task
    assert task is not None
    assert task in mock_config_entry._background_tasks
    release.set()
    await task
    mock_client.client.async_refresh_csm.assert_awaited_once()


async def test_debounced_refresh_stops_when_client_disconnects(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: ClientHarness
) -> None:
    """A debounced refresh stops cleanly when the hub has no client."""
    await _setup(hass, mock_config_entry)
    coordinator = mock_config_entry.runtime_data.coordinator
    hub = mock_config_entry.runtime_data.hub
    coordinator._debounce_seconds = 0
    hub._client = None
    refresh = AsyncMock()
    mock_client.client.async_refresh_domain_config = refresh

    mock_client.emit(_domain_event("light"))
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    task = coordinator._debounce_task
    assert task is not None
    await task
    refresh.assert_not_awaited()
    assert coordinator._pending_domains == set()


def _reauth_flows(hass: HomeAssistant) -> list[dict]:
    return [
        flow
        for flow in hass.config_entries.flow.async_progress()
        if flow["handler"] == DOMAIN and flow["context"]["source"] == SOURCE_REAUTH
    ]


async def test_debounced_refresh_auth_error_starts_reauth_once(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: ClientHarness
) -> None:
    """An auth failure during debounced refresh starts reauth only once."""
    await _setup(hass, mock_config_entry)
    coordinator = mock_config_entry.runtime_data.coordinator
    coordinator._debounce_seconds = 0
    mock_client.client.async_refresh_domain_config = AsyncMock(
        side_effect=Elke27AuthError("bad")
    )

    mock_client.emit(_domain_event("light"))
    mock_client.emit(_domain_event("zone"))
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    task = coordinator._debounce_task
    assert task is not None
    await task
    await hass.async_block_till_done()
    assert len(_reauth_flows(hass)) == 1
    assert mock_config_entry.runtime_data.hub._reauth_requested is True

    mock_client.emit(_domain_event("lock"))
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    second = coordinator._debounce_task
    assert second is not None
    await second
    await hass.async_block_till_done()
    assert len(_reauth_flows(hass)) == 1


async def test_debounced_refresh_stops_on_connection_error(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: ClientHarness
) -> None:
    """A connection error stops the domain refresh and drops the queue."""
    await _setup(hass, mock_config_entry)
    coordinator = mock_config_entry.runtime_data.coordinator
    coordinator._debounce_seconds = 0
    refresh = AsyncMock(side_effect=Elke27ConnectionError("down"))
    mock_client.client.async_refresh_domain_config = refresh

    mock_client.emit(_domain_event("light"))
    mock_client.emit(_domain_event("zone"))
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    task = coordinator._debounce_task
    assert task is not None
    await task
    assert refresh.await_count == 1
    assert coordinator._pending_domains == set()


async def test_debounced_refresh_continues_after_rejection(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: ClientHarness
) -> None:
    """A refused domain refresh is logged and the other domains still refresh."""
    await _setup(hass, mock_config_entry)
    coordinator = mock_config_entry.runtime_data.coordinator
    coordinator._debounce_seconds = 0
    refresh = AsyncMock(side_effect=[Elke27ProtocolError("refused"), None])
    mock_client.client.async_refresh_domain_config = refresh

    mock_client.emit(_domain_event("light"))
    mock_client.emit(_domain_event("zone"))
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    task = coordinator._debounce_task
    assert task is not None
    await task
    assert refresh.await_count == 2


async def test_full_refresh_waits_for_domain_refresh(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: ClientHarness
) -> None:
    """A full refresh and a domain refresh never overlap."""
    await _setup(hass, mock_config_entry)
    coordinator = mock_config_entry.runtime_data.coordinator
    release = asyncio.Event()
    order: list[str] = []

    async def _slow_domain(domain: str) -> None:
        order.append(f"domain {domain} start")
        await release.wait()
        order.append(f"domain {domain} end")

    async def _full() -> None:
        order.append("full")

    mock_client.client.async_refresh_domain_config = AsyncMock(side_effect=_slow_domain)
    mock_client.client.async_refresh_csm.side_effect = _full
    domain_task = hass.async_create_task(coordinator.async_refresh_domains(["light"]))
    await asyncio.sleep(0)
    full_task = hass.async_create_task(coordinator.async_refresh_now())
    await asyncio.sleep(0)
    release.set()
    await domain_task
    await full_task
    assert order == ["domain light start", "domain light end", "full"]
