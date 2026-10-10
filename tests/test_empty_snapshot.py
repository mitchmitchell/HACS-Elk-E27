# ruff: noqa: S101, TC001, TC002, ARG001
"""Tests that every platform tolerates an empty panel snapshot."""

from __future__ import annotations

from elke27_lib import PanelSnapshot
from elke27_lib.events import ConnectionStateChanged
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.core import HomeAssistant
from tests.conftest import ClientHarness


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


async def test_setup_with_empty_snapshot_creates_only_panel_sensors(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: ClientHarness
) -> None:
    """An empty CSM still loads the integration and panel diagnostic sensors."""
    mock_client.snapshot = PanelSnapshot.empty()
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    coordinator = mock_config_entry.runtime_data.coordinator
    assert coordinator.data is not None
    assert coordinator.data.areas == {}
    assert coordinator.data.zones == {}

    assert hass.states.get("alarm_control_panel.test_panel_house") is None
    assert hass.states.get("binary_sensor.test_panel_front_door") is None
    assert hass.states.get("sensor.test_panel_panel_name") is not None
    assert hass.states.get("sensor.test_panel_panel_ready") is not None


async def test_coordinator_data_never_none_after_init(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: ClientHarness
) -> None:
    """Coordinator data uses PanelSnapshot.empty() before the first refresh."""
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_config_entry.runtime_data.coordinator.data is not None


async def test_disconnect_uses_empty_snapshot_when_client_has_none(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: ClientHarness
) -> None:
    """When the hub has no client snapshot, coordinator data becomes empty."""
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    coordinator = mock_config_entry.runtime_data.coordinator
    mock_client.client.is_ready = False
    mock_client.snapshot = PanelSnapshot.empty()
    mock_client.emit(_connection_event(connected=False))
    await hass.async_block_till_done()
    assert coordinator.data.areas == {}
    assert coordinator.data.zones == {}
