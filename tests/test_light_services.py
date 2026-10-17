# ruff: noqa: S101, D103, PLR2004, TC001, TC002, ARG001
"""Light service tests on a loaded entry (Tester feature-map coverage, 2026-10-17)."""

from __future__ import annotations

from typing import Any

from elke27_lib import LightState
from elke27_lib.client import Result
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from tests.conftest import ClientHarness, entity_ids, settle, snapshot_with


@pytest.fixture
async def light_loaded(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: ClientHarness
) -> Any:
    mock_client.snapshot = snapshot_with(
        mock_client.snapshot,
        lights={1: LightState(light_id=1, name="Dimmer", state=True, level=99)},
    )
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    return mock_config_entry.runtime_data.coordinator


def _record(hass: HomeAssistant, entity: str) -> list[str]:
    seen: list[str] = []

    def _cb(event: Any) -> None:
        if event.data["entity_id"] == entity and event.data["new_state"]:
            seen.append(event.data["new_state"].state)

    hass.bus.async_listen("state_changed", _cb)
    return seen


async def test_dim_does_not_publish_transient_off(
    hass: HomeAssistant, light_loaded: Any, mock_client: ClientHarness
) -> None:
    coordinator = light_loaded
    (entity,) = entity_ids(hass, "light")
    seen = _record(hass, entity)
    base = mock_client.snapshot

    async def _execute(key: str, **params: Any) -> Result[Any]:
        if key == "light_get_status":
            # Z-Wave ramp: the immediate status read reports 0 before the target.
            snap = snapshot_with(
                base,
                lights={1: LightState(light_id=1, name="Dimmer", state=False, level=0)},
            )
            mock_client.snapshot = snap
            coordinator.async_set_updated_data(snap)
        return Result.success({})

    mock_client.client.async_execute.side_effect = _execute
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": entity, "brightness": 26}, blocking=True
    )
    final = snapshot_with(
        base, lights={1: LightState(light_id=1, name="Dimmer", state=True, level=10)}
    )
    coordinator.async_set_updated_data(final)
    await hass.async_block_till_done()
    await settle()
    assert STATE_OFF not in seen
    assert hass.states.get(entity).state == STATE_ON


async def test_on_without_brightness_sends_100(
    hass: HomeAssistant, light_loaded: Any, mock_client: ClientHarness
) -> None:
    mock_client.client.async_execute.return_value = Result.success({})
    (entity,) = entity_ids(hass, "light")
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": entity}, blocking=True
    )
    sets = [
        c
        for c in mock_client.client.async_execute.call_args_list
        if c.args[0] == "light_set_status"
    ]
    assert sets[0].kwargs["level"] == 100


async def test_turn_off_sends_level_zero_and_one_off(
    hass: HomeAssistant, light_loaded: Any, mock_client: ClientHarness
) -> None:
    mock_client.client.async_execute.return_value = Result.success({})
    (entity,) = entity_ids(hass, "light")
    await hass.services.async_call(
        "light", "turn_off", {"entity_id": entity}, blocking=True
    )
    sets = [
        c
        for c in mock_client.client.async_execute.call_args_list
        if c.args[0] == "light_set_status"
    ]
    assert len(sets) == 1
    assert sets[0].kwargs["level"] == 0
    assert sets[0].kwargs["status"] == "OFF"
