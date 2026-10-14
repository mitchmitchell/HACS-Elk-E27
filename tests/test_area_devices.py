# ruff: noqa: S101, TC002, PLR2004, ARG001
"""Tests for E2 per-area devices."""

from __future__ import annotations

import dataclasses
from types import MappingProxyType
from unittest.mock import MagicMock

from elke27_lib import AreaState, ZoneState
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.elke27 import (
    _async_prune_area_devices,
    async_remove_config_entry_device,
)
from custom_components.elke27.const import DOMAIN
from custom_components.elke27.entity import (
    area_device_identifier,
    area_device_info,
    area_has_device,
    area_id_from_device_identifier,
    device_info_for_zone,
    entry_identity_key,
    zone_entity_name,
)
from custom_components.elke27.services import _entity_ids_from_service_call
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import device_registry as dr, entity_registry as er
from tests.conftest import ClientHarness, panel_snapshot

KEY = "00:11:22:33:44:55"


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def _device(hass: HomeAssistant, identifier: str) -> dr.DeviceEntry | None:
    return dr.async_get(hass).async_get_device(identifiers={(DOMAIN, identifier)})


def _with_areas(
    snapshot: object, areas: dict[int, AreaState], **kwargs: object
) -> object:
    return dataclasses.replace(snapshot, areas=MappingProxyType(areas), **kwargs)  # type: ignore[type-var]


async def test_area_device_and_entity_placement(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: ClientHarness
) -> None:
    """Area panel and its zones sit on the area device; the rest on the panel."""
    mock_client.snapshot = dataclasses.replace(
        panel_snapshot(),
        zones=MappingProxyType(
            {
                1: ZoneState(zone_id=1, name="Front Door", area_id=1, open=False),
                2: ZoneState(zone_id=2, name="Shed", area_id=None, open=False),
                3: ZoneState(zone_id=3, name="Gate", area_id=7, open=False),
            }
        ),
    )
    await _setup(hass, mock_config_entry)

    panel = _device(hass, KEY)
    area = _device(hass, f"{KEY}:area:1")
    assert panel is not None
    assert area is not None
    assert area.name == "House"
    assert area.model == "Area"
    assert area.via_device_id == panel.id
    assert area.connections == set()

    registry = er.async_get(hass)
    alarm = registry.async_get("alarm_control_panel.house")
    assert alarm is not None
    assert alarm.unique_id == f"{KEY}:area:1"
    assert alarm.device_id == area.id
    state = hass.states.get("alarm_control_panel.house")
    assert state is not None
    assert state.name == "House"

    front = registry.async_get("binary_sensor.house_front_door")
    assert front is not None
    assert front.unique_id == f"{KEY}:zone:1"
    assert front.device_id == area.id
    for unique_id in (f"{KEY}:zone:2", f"{KEY}:zone:3"):
        entity_id = registry.async_get_entity_id("binary_sensor", DOMAIN, unique_id)
        assert entity_id is not None
        entity = registry.async_get(entity_id)
        assert entity is not None
        assert entity.device_id == panel.id

    others = [
        entity
        for entity in er.async_entries_for_config_entry(
            registry, mock_config_entry.entry_id
        )
        if entity.domain not in ("alarm_control_panel", "binary_sensor")
    ]
    assert others
    assert all(entity.device_id == panel.id for entity in others)


async def test_new_area_after_setup_creates_device(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: ClientHarness
) -> None:
    """An area that appears after setup gets its own device via the listener."""
    await _setup(hass, mock_config_entry)
    coordinator = mock_config_entry.runtime_data.coordinator
    snapshot = _with_areas(
        mock_client.snapshot,
        {1: AreaState(area_id=1, name="House"), 2: AreaState(area_id=2, name=None)},
    )
    coordinator.async_set_updated_data(snapshot)
    await hass.async_block_till_done()
    area = _device(hass, f"{KEY}:area:2")
    assert area is not None
    assert area.name == "Area 2"
    assert hass.states.get("alarm_control_panel.area_2") is not None


async def test_remove_config_entry_device(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: ClientHarness
) -> None:
    """Only an area device the panel no longer reports can be deleted."""
    await _setup(hass, mock_config_entry)
    registry = dr.async_get(hass)
    gone = registry.async_get_or_create(
        config_entry_id=mock_config_entry.entry_id,
        identifiers={(DOMAIN, f"{KEY}:area:5")},
    )
    live = _device(hass, f"{KEY}:area:1")
    panel = _device(hass, KEY)
    assert live is not None
    assert panel is not None
    assert await async_remove_config_entry_device(hass, mock_config_entry, gone)
    assert not await async_remove_config_entry_device(hass, mock_config_entry, live)
    assert not await async_remove_config_entry_device(hass, mock_config_entry, panel)

    coordinator = mock_config_entry.runtime_data.coordinator
    coordinator.data = _with_areas(coordinator.data, {})
    assert not await async_remove_config_entry_device(hass, mock_config_entry, gone)


async def test_prune_removes_stale_area_device(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: ClientHarness
) -> None:
    """Setup's first full snapshot detaches area devices it does not list."""
    mock_config_entry.add_to_hass(hass)
    registry = dr.async_get(hass)
    stale = registry.async_get_or_create(
        config_entry_id=mock_config_entry.entry_id,
        identifiers={(DOMAIN, f"{KEY}:area:9")},
    )
    other = registry.async_get_or_create(
        config_entry_id=mock_config_entry.entry_id,
        identifiers={(DOMAIN, "aa:bb:cc:dd:ee:ff:area:9")},
    )
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert registry.async_get(stale.id) is None
    assert registry.async_get(other.id) is not None
    assert _device(hass, f"{KEY}:area:1") is not None


@pytest.mark.parametrize("variant", ["stale", "empty"])
async def test_prune_skips_untrusted_snapshot(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: ClientHarness,
    variant: str,
) -> None:
    """A stale or empty-area snapshot never removes area devices."""
    if variant == "stale":
        mock_client.snapshot = dataclasses.replace(mock_client.snapshot, stale=True)
    else:
        mock_client.snapshot = _with_areas(mock_client.snapshot, {})
    mock_config_entry.add_to_hass(hass)
    registry = dr.async_get(hass)
    kept = registry.async_get_or_create(
        config_entry_id=mock_config_entry.entry_id,
        identifiers={(DOMAIN, f"{KEY}:area:9")},
    )
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert registry.async_get(kept.id) is not None


def test_prune_skips_missing_snapshot() -> None:
    """No snapshot at all: nothing is pruned."""
    hass = MagicMock()
    coordinator = MagicMock()
    coordinator.data = None
    _async_prune_area_devices(hass, MagicMock(), coordinator)
    hass.assert_not_called()


def _coordinator(snapshot: object) -> MagicMock:
    coordinator = MagicMock()
    coordinator.data = snapshot
    coordinator.panel_name = None
    return coordinator


def _bare_entry(entry_id: str = "entry-1") -> MagicMock:
    entry = MagicMock()
    entry.entry_id = entry_id
    entry.title = "Panel"
    entry.data = {}
    return entry


def test_area_device_info_entry_id_tier() -> None:
    """With no MAC or serial, area devices hang off the entry id identity."""
    snapshot = dataclasses.replace(
        panel_snapshot(), panel=dataclasses.replace(panel_snapshot().panel, mac=None)
    )
    coordinator = _coordinator(snapshot)
    entry = _bare_entry()
    assert entry_identity_key(coordinator, entry) == "entry-1"
    info = area_device_info(coordinator, entry, 3, AreaState(area_id=3, name=None))
    assert info["identifiers"] == {(DOMAIN, "entry-1:area:3")}
    assert "via_device" not in info
    assert info["name"] == "Area 3"


def test_area_device_info_mac_tier() -> None:
    """The MAC tier key is used for the area identifier (no deprecated via_device)."""
    coordinator = _coordinator(panel_snapshot())
    info = area_device_info(
        coordinator, _bare_entry(), 1, AreaState(area_id=1, name="House")
    )
    assert info["identifiers"] == {(DOMAIN, f"{KEY}:area:1")}
    assert "via_device" not in info
    assert info["name"] == "House"


def test_zone_device_fallbacks() -> None:
    """Zones without a known area use the panel device."""
    coordinator = _coordinator(panel_snapshot())
    entry = _bare_entry()
    for area_id in (None, 0, 42):
        zone = ZoneState(zone_id=1, name="Z", area_id=area_id)
        info = device_info_for_zone(coordinator, entry, zone)
        assert info["identifiers"] == {(DOMAIN, KEY)}
    coordinator.data = None
    info = device_info_for_zone(coordinator, entry, ZoneState(zone_id=1, area_id=1))
    assert info["identifiers"] == {(DOMAIN, "entry-1")}


def test_q2_every_reported_area_has_device() -> None:
    """Q2 default: every area in snapshot.areas gets a device, no others."""
    snapshot = panel_snapshot()
    assert area_has_device(snapshot, 1)
    assert not area_has_device(snapshot, 2)
    assert not area_has_device(snapshot, 0)
    assert not area_has_device(snapshot, None)
    assert not area_has_device(None, 1)


def test_q3_zone_name_kept_verbatim() -> None:
    """Q3 default: a leading area name is not stripped from the zone name."""
    assert zone_entity_name("Garage Garage door", "Garage") == "Garage Garage door"
    assert zone_entity_name("Garage", "Garage") == "Garage"


def test_area_identifier_round_trip() -> None:
    """Identifiers parse back to the area only for this entry's key."""
    identifier = area_device_identifier(KEY, 4)
    assert identifier == f"{KEY}:area:4"
    assert area_id_from_device_identifier(KEY, identifier) == 4
    assert area_id_from_device_identifier(KEY, KEY) is None
    assert area_id_from_device_identifier(KEY, f"{KEY}:zone:4") is None
    assert area_id_from_device_identifier(KEY, f"{KEY}:area:x") is None
    assert area_id_from_device_identifier(KEY, f"{KEY}:area:\u00b2") is None
    assert area_id_from_device_identifier("other", identifier) is None


async def test_area_device_service_target_filters_platform(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: ClientHarness
) -> None:
    """An area-device target yields only the service's own platform."""
    await _setup(hass, mock_config_entry)
    area = _device(hass, f"{KEY}:area:1")
    assert area is not None
    call = ServiceCall(hass, DOMAIN, "zone_bypass", {"device_id": [area.id]})
    assert _entity_ids_from_service_call(hass, call, Platform.BINARY_SENSOR) == [
        "binary_sensor.house_front_door"
    ]
    assert _entity_ids_from_service_call(hass, call, Platform.ALARM_CONTROL_PANEL) == [
        "alarm_control_panel.house"
    ]
    named = ServiceCall(
        hass, DOMAIN, "zone_bypass", {"entity_id": ["alarm_control_panel.house"]}
    )
    assert _entity_ids_from_service_call(hass, named, Platform.BINARY_SENSOR) == [
        "alarm_control_panel.house"
    ]


async def test_cleanup_and_delete_route_through_area_has_device(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: ClientHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prune and the delete check both follow area_has_device (one Q2 switch)."""
    mock_config_entry.add_to_hass(hass)
    registry = dr.async_get(hass)
    unlisted = registry.async_get_or_create(
        config_entry_id=mock_config_entry.entry_id,
        identifiers={(DOMAIN, f"{KEY}:area:9")},
    )
    answer = {"value": True}
    calls: list[int | None] = []

    def _fake(snapshot: object, area_id: int | None) -> bool:
        calls.append(area_id)
        return answer["value"]

    monkeypatch.setattr("custom_components.elke27.area_has_device", _fake)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert 9 in calls
    assert registry.async_get(unlisted.id) is not None
    assert not await async_remove_config_entry_device(hass, mock_config_entry, unlisted)

    live = _device(hass, f"{KEY}:area:1")
    assert live is not None
    answer["value"] = False
    calls.clear()
    assert await async_remove_config_entry_device(hass, mock_config_entry, live)
    assert calls == [1]
