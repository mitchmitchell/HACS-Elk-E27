# ruff: noqa: S101, TC001, TC002
"""Outbound light levels through the HA light service (#99)."""

from __future__ import annotations

import dataclasses
from types import MappingProxyType
from typing import Any

from elke27_lib import LightState
from elke27_lib.client import Result
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.core import HomeAssistant
from tests.conftest import ClientHarness


@pytest.mark.parametrize(
    ("service_data", "level"),
    [
        ({}, 99),
        ({"brightness": 255}, 99),
        ({"brightness_pct": 100}, 99),
        ({"brightness_pct": 50}, 50),
        ({"brightness": 1}, 1),
    ],
    ids=["no-brightness", "brightness-255", "pct-100", "pct-50", "brightness-1"],
)
async def test_turn_on_level_sent_to_panel(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: ClientHarness,
    service_data: dict[str, Any],
    level: int,
) -> None:
    """Full brightness is sent as 99: the panel treats 100 as 'previous level'."""
    mock_client.snapshot = dataclasses.replace(
        mock_client.snapshot,
        lights=MappingProxyType(
            {1: LightState(light_id=1, name="Dimmer", state=False, level=0)}
        ),
    )
    mock_client.client.async_execute.return_value = Result.success({})
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    (entity,) = hass.states.async_entity_ids("light")
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": entity, **service_data}, blocking=True
    )
    sets = [
        c
        for c in mock_client.client.async_execute.call_args_list
        if c.args[0] == "light_set_status"
    ]
    assert sets
    assert sets[0].kwargs["level"] == level
    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
