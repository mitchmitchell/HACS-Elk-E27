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

# (name, definition, zone_type). No zone_type is what the TEST panel sends.
ZONES = {
    1: ("Front Door", "BURG EE DELAY", "Door"),
    2: ("Kitchen Window", "BURG PERIM INST", "Window"),
    3: ("Interior", "BURG INTERIOR", None),
    4: ("Hall Motion", "BURG INTERIOR", None),
    5: ("Living Room PIR", "BURG INTERIOR", None),
    6: ("Basement Door", "BURG INTERIOR", None),
    7: ("Motion", "BURG PERIM INST", None),
    8: ("Den Motion", "BURG INTERIOR", "Door"),
    9: ("Pirate Room", "BURG INTERIOR", None),  # whole words only: not "pir"
    10: ("MOTION-2", "BURG INTERIOR", None),
}


async def test_zone_device_classes(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    snapshot: SnapshotAssertion,
    mock_config_entry: MockConfigEntry,
    mock_client: ClientHarness,
) -> None:
    """Pin each zone's device class, name and state for typed and untyped zones."""
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
    with patch("custom_components.elke27.PLATFORMS", [Platform.BINARY_SENSOR]):
        mock_config_entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()
    await snapshot_platform(
        hass,
        entity_registry,
        snapshot.use_extension(HomeAssistantSnapshotExtension),
        mock_config_entry.entry_id,
    )
