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

PANEL_FULL = 99  # highest explicit level; the panel reports it back as 100
PANEL_RESTORE_LAST = 100  # sent only for "on" with no brightness
HA_FULL = 255


async def _load_light(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: ClientHarness,
    light: LightState,
) -> str:
    mock_client.snapshot = dataclasses.replace(
        mock_client.snapshot, lights=MappingProxyType({1: light})
    )
    mock_client.client.async_execute.return_value = Result.success({})
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    (entity,) = hass.states.async_entity_ids("light")
    return entity


async def _turn_on_levels(
    hass: HomeAssistant, mock_client: ClientHarness, entity: str, **service_data: Any
) -> list[int]:
    mock_client.client.async_execute.reset_mock()
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": entity, **service_data}, blocking=True
    )
    return [
        c.kwargs["level"]
        for c in mock_client.client.async_execute.call_args_list
        if c.args[0] == "light_set_status"
    ]


@pytest.mark.parametrize(
    ("service_data", "level"),
    [
        ({}, PANEL_RESTORE_LAST),
        ({"brightness": 255}, PANEL_FULL),
        ({"brightness": 254}, PANEL_FULL),
        ({"brightness": 253}, PANEL_FULL),
        ({"brightness": 252}, PANEL_FULL),
        ({"brightness_pct": 100}, PANEL_FULL),
        ({"brightness": 128}, 50),
        ({"brightness_pct": 50}, 50),
        ({"brightness": 1}, 1),
    ],
    ids=[
        "no-brightness",
        "brightness-255",
        "brightness-254",
        "brightness-253",
        "brightness-252",
        "pct-100",
        "brightness-128",
        "pct-50",
        "brightness-1",
    ],
)
async def test_turn_on_level_sent_to_panel(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: ClientHarness,
    service_data: dict[str, Any],
    level: int,
) -> None:
    """No brightness restores the last level (100); explicit brightness is 1-99."""
    entity = await _load_light(
        hass,
        mock_config_entry,
        mock_client,
        LightState(light_id=1, name="Dimmer", state=False, level=0),
    )
    assert await _turn_on_levels(hass, mock_client, entity, **service_data) == [level]
    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)


async def test_turn_on_never_sends_100(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: ClientHarness
) -> None:
    """No explicit brightness ever sends 100, the panel's 'last level' value (#99)."""
    entity = await _load_light(
        hass,
        mock_config_entry,
        mock_client,
        LightState(light_id=1, name="Dimmer", state=False, level=0),
    )
    sent: set[int] = set()
    for brightness in range(1, 256):
        sent.update(
            await _turn_on_levels(hass, mock_client, entity, brightness=brightness)
        )
    assert max(sent) == PANEL_FULL
    assert min(sent) == 1
    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)


async def test_full_read_back_resends_99(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: ClientHarness
) -> None:
    """The panel reports 100 after a 99 command; re-sending that brightness is 99."""
    entity = await _load_light(
        hass,
        mock_config_entry,
        mock_client,
        LightState(light_id=1, name="Dimmer", state=True, level=100),
    )
    brightness = hass.states.get(entity).attributes["brightness"]
    assert brightness == HA_FULL
    assert await _turn_on_levels(hass, mock_client, entity, brightness=brightness) == [
        PANEL_FULL
    ]
    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
