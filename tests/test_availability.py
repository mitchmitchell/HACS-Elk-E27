# ruff: noqa: S101, SLF001, TC001, TC002, PT012
"""Tests for entity availability and commands sent while disconnected."""

from __future__ import annotations

import dataclasses
from types import MappingProxyType
from unittest.mock import AsyncMock

from elke27_lib import LockState, OutputState
from elke27_lib.client import Result
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.elke27.entity import NOT_CONNECTED_MESSAGE
from custom_components.elke27.hub import NOT_ACCEPTED_MESSAGE
from homeassistant.const import ATTR_CODE, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from tests.conftest import ClientHarness

ALARM = "alarm_control_panel.test_panel_house"
SWITCH = "switch.test_panel_siren"
LOCK = "lock.test_panel_back_door"


@pytest.fixture
async def loaded(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: ClientHarness
) -> MockConfigEntry:
    """Set up the entry with an area, an output and a lock."""
    mock_client.snapshot = dataclasses.replace(
        mock_client.snapshot,
        outputs=MappingProxyType({1: OutputState(output_id=1, name="Siren")}),
        locks=MappingProxyType({1: LockState(lock_id=1, name="Back Door")}),
    )
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    return mock_config_entry


@pytest.mark.parametrize(
    "entity_id", [ALARM, SWITCH, LOCK, "binary_sensor.test_panel_front_door"]
)
async def test_unavailable_when_coordinator_update_failed(
    hass: HomeAssistant, loaded: MockConfigEntry, entity_id: str
) -> None:
    """Entities honour the coordinator's availability (super().available)."""
    assert hass.states.get(entity_id).state != STATE_UNAVAILABLE
    coordinator = loaded.runtime_data.coordinator
    coordinator.last_update_success = False
    coordinator.async_update_listeners()
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == STATE_UNAVAILABLE


@pytest.mark.parametrize(
    ("domain", "service", "entity_id", "data"),
    [
        ("alarm_control_panel", "alarm_arm_away", ALARM, {ATTR_CODE: "1234"}),
        ("alarm_control_panel", "alarm_arm_home", ALARM, {ATTR_CODE: "1234"}),
        ("alarm_control_panel", "alarm_disarm", ALARM, {ATTR_CODE: "1234"}),
        ("switch", "turn_on", SWITCH, {}),
        ("lock", "lock", LOCK, {}),
    ],
)
async def test_command_while_disconnected_raises(
    hass: HomeAssistant,
    loaded: MockConfigEntry,
    mock_client: ClientHarness,
    domain: str,
    service: str,
    entity_id: str,
    data: dict[str, str],
) -> None:
    """A command with no panel connection raises instead of silently succeeding."""
    hub = loaded.runtime_data.hub
    # The client drops between the entity's availability check and the call.
    hub._client = None
    entity = hass.data["entity_components"][domain].get_entity(entity_id)
    with pytest.raises(HomeAssistantError, match=NOT_CONNECTED_MESSAGE):
        if domain == "alarm_control_panel":
            await getattr(entity, f"async_{service}")(code=data[ATTR_CODE])
        else:
            await getattr(entity, f"async_{service}")()
    mock_client.client.async_arm_area.assert_not_awaited()
    mock_client.client.async_disarm_area.assert_not_awaited()
    mock_client.client.async_set_output.assert_not_awaited()
    mock_client.client.async_execute.assert_not_awaited()


async def test_not_sent_with_client_says_not_accepted(
    hass: HomeAssistant, loaded: MockConfigEntry
) -> None:
    """A False result while connected is reported as not accepted."""
    hub = loaded.runtime_data.hub
    hub.async_set_output = AsyncMock(return_value=False)
    entity = hass.data["entity_components"]["switch"].get_entity(SWITCH)
    with pytest.raises(HomeAssistantError, match=NOT_ACCEPTED_MESSAGE):
        await entity.async_turn_on()


@pytest.mark.usefixtures("loaded")
async def test_execute_not_ok_without_error_raises_not_accepted(
    hass: HomeAssistant, mock_client: ClientHarness
) -> None:
    """A lock command the panel answers without accepting raises."""
    mock_client.client.async_execute = AsyncMock(return_value=Result.failure(None))
    entity = hass.data["entity_components"]["lock"].get_entity(LOCK)
    with pytest.raises(HomeAssistantError, match=NOT_ACCEPTED_MESSAGE):
        await entity.async_lock()
