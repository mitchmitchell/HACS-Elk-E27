# ruff: noqa: S101, TC001, TC002
"""Area and zone entity IDs stay as documented on Home Assistant 2026.10."""

from __future__ import annotations

import dataclasses
from types import MappingProxyType
from unittest.mock import MagicMock

from elke27_lib import AreaState
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.elke27.const import DOMAIN
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from tests.conftest import ClientHarness, entity_ids, setup_entry

pytestmark = pytest.mark.usefixtures("mock_client")

KEY = "00:11:22:33:44:55"


async def test_area_and_zone_entity_ids_unchanged_on_fresh_setup(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """No panel-name prefix on area and zone entity IDs; area links by device id."""
    await setup_entry(hass, mock_config_entry)

    assert entity_ids(hass, "alarm_control_panel") == ["alarm_control_panel.house"]
    assert entity_ids(hass, "binary_sensor") == ["binary_sensor.house_front_door"]
    registry = dr.async_get(hass)
    area = next(
        device
        for device in registry.devices
        if device.config_entry_id == mock_config_entry.entry_id and device.via_device_id
    )
    assert area.via_device_id is not None
    panel = registry.async_get(area.via_device_id)
    assert panel is not None
    assert panel.via_device_id is None


async def test_upgrade_from_0_2_0_child_area_device_keeps_entities(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """
    0.2.0 on HA 2026.10 registered area devices as child devices.

    Registering the same identifiers as a regular device raised DeviceInfoError,
    so the area entities failed to load. The child device is kept instead: same
    device id, and the area and zone entities load with their 0.2.0 entity IDs.
    """
    mock_config_entry.add_to_hass(hass)
    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)
    panel = device_registry.async_get_or_create(
        config_entry_id=mock_config_entry.entry_id,
        identifiers={(DOMAIN, KEY)},
        name="Test Panel",
    )
    child = device_registry.async_get_or_create_child(
        config_entry_id=mock_config_entry.entry_id,
        identifiers={(DOMAIN, f"{KEY}:area:1")},
        name="House",
        parent_device_id=panel.id,
    )
    for domain, object_id in (
        ("alarm_control_panel", "test_panel_house"),
        ("binary_sensor", "test_panel_house_front_door"),
    ):
        entity_registry.async_get_or_create(
            domain,
            DOMAIN,
            f"{KEY}:1",
            suggested_object_id=object_id,
            config_entry=mock_config_entry,
            device_id=child.id,
        )

    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.LOADED
    assert entity_ids(hass, "alarm_control_panel") == [
        "alarm_control_panel.test_panel_house"
    ]
    assert entity_ids(hass, "binary_sensor") == [
        "binary_sensor.test_panel_house_front_door"
    ]
    kept = device_registry.async_get_child_device_by_identifier(
        (DOMAIN, f"{KEY}:area:1"), mock_config_entry.entry_id
    )
    assert kept is not None
    assert kept.id == child.id
    for entity_id in (
        "alarm_control_panel.test_panel_house",
        "binary_sensor.test_panel_house_front_door",
    ):
        entry = entity_registry.async_get(entity_id)
        assert entry is not None
        assert entry.device_id == child.id


async def test_late_areas_keep_0_2_0_child_device_and_new_ones_use_via_device_id(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: ClientHarness
) -> None:
    """
    Areas reported after setup get the right layout.

    Area 2 was created by 0.2.0 as a child device but isn't in the setup
    snapshot; when it appears it stays a child device with its 0.2.0 entity ID.
    Area 3 is new and gets a regular device linked by via_device_id.
    """
    mock_config_entry.add_to_hass(hass)
    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)
    panel = device_registry.async_get_or_create(
        config_entry_id=mock_config_entry.entry_id,
        identifiers={(DOMAIN, KEY)},
        name="Test Panel",
    )
    child = device_registry.async_get_or_create_child(
        config_entry_id=mock_config_entry.entry_id,
        identifiers={(DOMAIN, f"{KEY}:area:2")},
        name="Area 2",
        parent_device_id=panel.id,
    )
    entity_registry.async_get_or_create(
        "alarm_control_panel",
        DOMAIN,
        f"{KEY}:2",
        suggested_object_id="test_panel_area_2",
        config_entry=mock_config_entry,
        device_id=child.id,
    )
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    mock_client.snapshot = dataclasses.replace(
        mock_client.snapshot,
        areas=MappingProxyType(
            {
                1: AreaState(area_id=1, name="House"),
                2: AreaState(area_id=2, name=None),
                3: AreaState(area_id=3, name=None),
            }
        ),
    )
    mock_client.emit(MagicMock(name="AreaStatusUpdated"))
    await hass.async_block_till_done()

    assert sorted(entity_ids(hass, "alarm_control_panel")) == [
        "alarm_control_panel.area_3",
        "alarm_control_panel.house",
        "alarm_control_panel.test_panel_area_2",
    ]
    late_child = entity_registry.async_get("alarm_control_panel.test_panel_area_2")
    assert late_child is not None
    assert late_child.device_id == child.id
    new_area = device_registry.async_get_devices(
        identifiers={(DOMAIN, f"{KEY}:area:3")}
    )
    assert len(new_area) == 1
    assert new_area[0].via_device_id == panel.id
    assert (
        device_registry.async_get_child_device_by_identifier(
            (DOMAIN, f"{KEY}:area:3"), mock_config_entry.entry_id
        )
        is None
    )
