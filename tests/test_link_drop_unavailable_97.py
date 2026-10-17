# ruff: noqa: S101, SLF001, TC001, TC002, TC003, ARG001
"""
HACS #97: entities go unavailable as soon as the panel link drops.

A raw ``EventType.DISCONNECTED`` on the ``client.subscribe`` link listener
must push the snapshot (one listener update) so HA writes ``unavailable``,
the same write the typed ``ConnectionStateChanged`` path makes. Repro by
Tester (2026-10-17); fix per Architect's design note (P1).
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
import logging
from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from tests.conftest import ClientHarness, connection_event, link_event

ALARM = "alarm_control_panel.house"
ZONE = "binary_sensor.house_front_door"
_real_sleep = asyncio.sleep


async def _settle() -> None:
    for _ in range(50):
        await _real_sleep(0)


async def _setup_blocked(
    hass: HomeAssistant, entry: MockConfigEntry, mock_client: ClientHarness
) -> None:
    """Load the entry; later reconnects block (the panel stays unreachable)."""
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(ALARM).state == "disarmed"

    async def _connect_blocks(*_args: object, **_kwargs: object) -> None:
        await asyncio.Event().wait()

    mock_client.client.async_connect.side_effect = _connect_blocks


async def _setup_and_drop(
    hass: HomeAssistant, entry: MockConfigEntry, mock_client: ClientHarness
) -> None:
    """Load the entry, then drop the link with only a raw DISCONNECTED event."""
    await _setup_blocked(hass, entry, mock_client)
    mock_client.client.is_ready = False
    mock_client.emit_link(link_event(connected=False))
    await _settle()


@pytest.fixture
def no_backoff_sleep() -> Iterator[None]:
    """Skip reconnect backoff sleeps (no jitter)."""
    with (
        patch("custom_components.elke27.reconnect.asyncio.sleep", AsyncMock()),
        patch("custom_components.elke27.reconnect.random.uniform", return_value=1.0),
    ):
        yield


@pytest.mark.parametrize("entity_id", [ALARM, ZONE])
async def test_entities_unavailable_right_after_link_drop(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: ClientHarness,
    no_backoff_sleep: None,
    entity_id: str,
) -> None:
    """After a link-only drop, entities go unavailable with no refresh (#97)."""
    await _setup_and_drop(hass, mock_config_entry, mock_client)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == STATE_UNAVAILABLE
    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)


async def test_link_drop_cause_is_missing_listener_update(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: ClientHarness,
    no_backoff_sleep: None,
) -> None:
    """
    Guard on the #97 cause (Tester).

    Right after the drop the coordinator is not ready and the drop is logged;
    a listener update is all it takes to reach 'unavailable'.
    """
    await _setup_and_drop(hass, mock_config_entry, mock_client)
    coordinator = mock_config_entry.runtime_data.coordinator
    assert coordinator.is_ready is False
    assert coordinator._unavailable_logged is True  # "Panel connection lost"
    coordinator.async_update_listeners()
    await hass.async_block_till_done()
    assert hass.states.get(ALARM).state == STATE_UNAVAILABLE
    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)


@pytest.mark.parametrize("first", ["link", "typed"])
async def test_link_and_typed_disconnect_is_one_listener_update(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: ClientHarness,
    no_backoff_sleep: None,
    first: str,
) -> None:
    """Both disconnect paths for one drop give exactly one listener update."""
    await _setup_blocked(hass, mock_config_entry, mock_client)
    coordinator = mock_config_entry.runtime_data.coordinator
    calls: list[None] = []
    remove = coordinator.async_add_listener(lambda: calls.append(None))
    mock_client.client.is_ready = False
    if first == "link":
        mock_client.emit_link(link_event(connected=False))
        mock_client.emit(connection_event(connected=False))
    else:
        mock_client.emit(connection_event(connected=False))
        mock_client.emit_link(link_event(connected=False))
    await _settle()
    remove()
    assert len(calls) == 1
    assert hass.states.get(ALARM).state == STATE_UNAVAILABLE
    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)


async def test_stale_push_after_reconnect_is_ignored(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: ClientHarness,
    no_backoff_sleep: None,
) -> None:
    """A push scheduled by a drop is dropped if the link is ready again."""
    await _setup_blocked(hass, mock_config_entry, mock_client)
    coordinator = mock_config_entry.runtime_data.coordinator
    calls: list[None] = []
    remove = coordinator.async_add_listener(lambda: calls.append(None))
    # The client is ready again by the time the scheduled push runs.
    mock_client.emit_link(link_event(connected=False))
    await _settle()
    remove()
    assert calls == []
    assert hass.states.get(ALARM).state == "disarmed"
    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)


async def test_superseded_push_is_ignored(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: ClientHarness,
    no_backoff_sleep: None,
) -> None:
    """A push from an older epoch does nothing."""
    await _setup_and_drop(hass, mock_config_entry, mock_client)
    coordinator = mock_config_entry.runtime_data.coordinator
    coordinator._unavailable_pushed = False
    calls: list[None] = []
    remove = coordinator.async_add_listener(lambda: calls.append(None))
    coordinator._push_unavailable(coordinator._connection_epoch - 1)
    remove()
    assert calls == []
    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)


async def test_warning_once_per_drop_with_push(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: ClientHarness,
    no_backoff_sleep: None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Two disconnect events in one drop still log one WARNING (#83)."""
    caplog.set_level(logging.DEBUG, logger="custom_components.elke27")
    await _setup_and_drop(hass, mock_config_entry, mock_client)
    mock_client.emit(connection_event(connected=False))
    mock_client.emit_link(link_event(connected=False))
    await _settle()
    lost = [
        r
        for r in caplog.records
        if r.name == "custom_components.elke27.coordinator"
        and r.getMessage() == "Panel connection lost"
    ]
    assert [r.levelno for r in lost] == [logging.WARNING]
    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
