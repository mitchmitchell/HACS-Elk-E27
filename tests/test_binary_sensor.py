# ruff: noqa: S101, TC001, TC002
"""Zone device classes through the binary_sensor platform (#101, #102)."""

from __future__ import annotations

import dataclasses
from types import MappingProxyType
from unittest.mock import patch

from elke27_lib import ZoneDefinition, ZoneState
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    snapshot_platform,
)
from pytest_homeassistant_custom_component.syrupy import HomeAssistantSnapshotExtension
from syrupy.assertion import SnapshotAssertion

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from tests.conftest import ClientHarness

# (name, definition, zone_type). The class comes from the definition only;
# names and zone types are deliberately misleading to prove they are ignored.
ZONES = {
    1: ("Front Door", "BURG EE DELAY", None),
    2: ("Kitchen Window", "BURG PERIM INST", None),
    3: ("Interior", "BURG INTERIOR", None),
    4: ("Hall Motion", "BURG INTERIOR", None),
    5: ("Basement Door", "BURG INTERIOR", "Door"),
    6: ("Motion", "BURG PERIM INST", "Motion"),
    7: ("Garage Window", "BURG EE DELAY", "Window"),
    8: ("Smoke", "FIRE", None),
    9: ("Hall Motion PIR", "FIRE", "Motion"),
    10: ("Living Room PIR", "BURG 24HR", None),
    11: ("Front Door", "WATER", "Door"),
    12: ("Lid", "BURG BOX TAMPER", None),
    13: ("CO", "CARBON MONOXIDE", None),
    14: ("Panic", "PANIC", None),
    15: ("Lights", "AUTOMATION", None),
    16: ("Odd", "SOMETHING NEW", None),
}

# Expected original device class per zone (None = generic binary sensor).
EXPECTED = {
    1: "opening",
    2: "opening",
    3: "motion",
    4: "motion",
    5: "motion",
    6: "opening",
    7: "opening",
    8: "smoke",
    9: "smoke",
    10: None,
    11: "moisture",
    12: None,
    13: "carbon_monoxide",
    14: None,
    15: None,
    16: None,
}


def _apply_zones(mock_client: ClientHarness) -> None:
    mock_client.snapshot = dataclasses.replace(
        mock_client.snapshot,
        zones=MappingProxyType(
            {
                zid: ZoneState(zone_id=zid, name=name, area_id=1, open=False)
                for zid, (name, _, _) in ZONES.items()
            }
        ),
        zone_definitions=MappingProxyType(
            {
                zid: ZoneDefinition(
                    zone_id=zid, name=name, definition=definition, zone_type=zone_type
                )
                for zid, (name, definition, zone_type) in ZONES.items()
            }
        ),
    )


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    with patch("custom_components.elke27.PLATFORMS", [Platform.BINARY_SENSOR]):
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()


def _entity_id_by_zone(
    entity_registry: er.EntityRegistry, entry: MockConfigEntry
) -> dict[int, str]:
    return {
        int(ent.unique_id.rsplit(":", 1)[1]): ent.entity_id
        for ent in er.async_entries_for_config_entry(entity_registry, entry.entry_id)
        if ent.domain == "binary_sensor"
    }


async def test_zone_device_classes(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    snapshot: SnapshotAssertion,
    mock_config_entry: MockConfigEntry,
    mock_client: ClientHarness,
) -> None:
    """Pin each zone's device class, name and state."""
    _apply_zones(mock_client)
    await _setup(hass, mock_config_entry)
    await snapshot_platform(
        hass,
        entity_registry,
        snapshot.use_extension(HomeAssistantSnapshotExtension),
        mock_config_entry.entry_id,
    )


async def test_device_class_follows_definition_only(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    mock_config_entry: MockConfigEntry,
    mock_client: ClientHarness,
) -> None:
    """Each definition maps to its class; names and zone types never matter."""
    _apply_zones(mock_client)
    await _setup(hass, mock_config_entry)
    ids = _entity_id_by_zone(entity_registry, mock_config_entry)
    assert set(ids) == set(EXPECTED)
    for zid, expected in EXPECTED.items():
        ent = entity_registry.async_get(ids[zid])
        assert ent is not None
        assert ent.original_device_class == expected, zid
        assert ent.device_class is None, zid
        state = hass.states.get(ids[zid])
        assert state is not None
        assert state.attributes.get("device_class") == expected, zid


async def test_name_does_not_change_device_class(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    mock_config_entry: MockConfigEntry,
    mock_client: ClientHarness,
) -> None:
    """The same definition gets the same class whatever the zone is called."""
    _apply_zones(mock_client)
    await _setup(hass, mock_config_entry)
    ids = _entity_id_by_zone(entity_registry, mock_config_entry)

    def cls(zid: int) -> str | None:
        ent = entity_registry.async_get(ids[zid])
        assert ent is not None
        return ent.original_device_class

    # "Interior" / "Hall Motion" / "Basement Door" are all BURG INTERIOR.
    assert cls(3) == cls(4) == cls(5) == "motion"
    # A perimeter zone named "Motion" is still an opening.
    assert cls(6) == "opening"
    # A non-burglary zone named like a motion detector stays generic.
    assert cls(10) is None
    # A water zone named "Front Door" with zone_type Door is moisture.
    assert cls(11) == "moisture"


async def test_user_device_class_override_survives_reload(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    mock_config_entry: MockConfigEntry,
    mock_client: ClientHarness,
) -> None:
    """Motion is only the default: a 'Show as' override survives a reload."""
    _apply_zones(mock_client)
    await _setup(hass, mock_config_entry)
    entity_id = _entity_id_by_zone(entity_registry, mock_config_entry)[3]
    entity_registry.async_update_entity(entity_id, device_class="window")
    await hass.async_block_till_done()

    assert await hass.config_entries.async_reload(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    ent = entity_registry.async_get(entity_id)
    assert ent is not None
    assert ent.device_class == "window"
    assert ent.original_device_class == "motion"
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.attributes.get("device_class") == "window"
