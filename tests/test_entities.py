# ruff: noqa: S101, PT027, PLR2004
"""Tests for Elke27 entities against elke27 0.3.8 snapshot types."""

from __future__ import annotations

import asyncio
import dataclasses
import importlib.util
from pathlib import Path
import sys
from types import MappingProxyType
from typing import Any
import unittest
from unittest.mock import AsyncMock, MagicMock

_HAS_DEPS = all(
    importlib.util.find_spec(name) is not None
    for name in ("homeassistant", "elke27_lib")
)

if _HAS_DEPS:
    sys.path.insert(0, str(Path(__file__).parents[1]))

    from elke27_lib import (
        AreaState,
        ArmMode,
        LightState,
        PanelInfo,
        PanelSnapshot,
        ThermostatState,
        ZoneDefinition,
        ZoneState,
    )

    from custom_components.elke27.alarm_control_panel import (
        Elke27AreaAlarmControlPanel,
        _area_state_to_ha,
    )
    from custom_components.elke27.binary_sensor import Elke27ZoneBinarySensor
    from custom_components.elke27.climate import Elke27Thermostat
    from custom_components.elke27.entity import get_panel_field
    from custom_components.elke27.hub import ZoneBypassFailedError, area_faulted_zones
    from custom_components.elke27.light import Elke27Light
    from homeassistant.components.alarm_control_panel import (
        AlarmControlPanelEntity,
        AlarmControlPanelEntityFeature,
        AlarmControlPanelState,
    )
    from homeassistant.components.binary_sensor import BinarySensorDeviceClass


def _snapshot(**kwargs: Any) -> Any:
    values = {
        key: MappingProxyType(value) if isinstance(value, dict) else value
        for key, value in kwargs.items()
    }
    values.setdefault("panel", PanelInfo(mac="00:11:22:33:44:55"))
    return dataclasses.replace(PanelSnapshot.empty(), **values)


def _coordinator(snapshot: Any) -> Any:
    coordinator = MagicMock()
    coordinator.data = snapshot
    return coordinator


def _hub() -> Any:
    hub = MagicMock()
    hub.panel_name = None
    hub.is_ready = True
    hub.async_set_zone_bypass = AsyncMock(return_value=True)
    hub.async_arm_area = AsyncMock(return_value=True)
    # No live client snapshot: the entity falls back to the coordinator copy.
    hub.get_snapshot = MagicMock(return_value=None)
    locks: dict[int, asyncio.Lock] = {}
    hub.area_arm_lock = MagicMock(
        side_effect=lambda area_id: locks.setdefault(area_id, asyncio.Lock())
    )

    async def _bypass_faulted(area_id: int, snapshot: Any, pin: str | None) -> None:
        for zone in area_faulted_zones(snapshot, area_id):
            bypassed = await hub.async_set_zone_bypass(
                zone.zone_id, bypassed=True, pin=pin
            )
            if not bypassed:
                raise ZoneBypassFailedError(zone, "bypass was not acknowledged.")

    hub.async_bypass_faulted_zones = AsyncMock(side_effect=_bypass_faulted)
    return hub


def _entry() -> Any:
    entry = MagicMock()
    entry.data = {"host": "192.0.2.10"}
    entry.title = "Panel"
    entry.unique_id = None
    entry.entry_id = "entry-1"
    return entry


def _two_area_snapshot() -> Any:
    return _snapshot(
        areas={
            1: AreaState(area_id=1, name="House", ready=False),
            2: AreaState(area_id=2, name="Garage", ready=True),
        },
        zones={
            1: ZoneState(zone_id=1, name="Front Door", area_id=1, open=True),
            2: ZoneState(zone_id=2, name="Back Door", area_id=1, open=True),
            3: ZoneState(
                zone_id=3, name="Bypassed", area_id=1, open=True, bypassed=True
            ),
            4: ZoneState(zone_id=4, name="Garage Door", area_id=2, open=True),
            5: ZoneState(zone_id=5, name="No Area", area_id=None, open=True),
            6: ZoneState(zone_id=6, name="Closed", area_id=1, open=False),
        },
        zone_definitions={
            2: ZoneDefinition(zone_id=2, name="Rear Door", definition="BURG EE DELAY"),
        },
    )


def _area_entity(snapshot: Any, hub: Any, area_id: int = 1) -> Any:
    return Elke27AreaAlarmControlPanel(
        _coordinator(snapshot), hub, _entry(), area_id, snapshot.areas[area_id]
    )


@unittest.skipUnless(_HAS_DEPS, "homeassistant and elke27 are required")
class AlarmEntityTest(unittest.IsolatedAsyncioTestCase):
    """Test the area alarm control panel."""

    async def test_custom_bypass_only_bypasses_zones_in_area(self) -> None:
        """Only open, non-bypassed zones in this area are bypassed, then arm away."""
        hub = _hub()
        entity = _area_entity(_two_area_snapshot(), hub)
        await entity.async_alarm_arm_custom_bypass("0123")
        bypassed = [call.args[0] for call in hub.async_set_zone_bypass.await_args_list]
        assert bypassed == [1, 2]
        for call in hub.async_set_zone_bypass.await_args_list:
            assert call.kwargs == {"bypassed": True, "pin": "0123"}
        hub.async_arm_area.assert_awaited_once_with(1, ArmMode.ARMED_AWAY, "0123")

    async def test_custom_bypass_failure_stops_arming(self) -> None:
        """An unacknowledged bypass raises and the area is not armed."""
        hub = _hub()
        hub.async_set_zone_bypass.return_value = False
        entity = _area_entity(_two_area_snapshot(), hub)
        with self.assertRaises(ZoneBypassFailedError):
            await entity.async_alarm_arm_custom_bypass("1234")
        assert hub.async_set_zone_bypass.await_count == 1
        hub.async_arm_area.assert_not_called()

    async def test_arm_night_not_offered(self) -> None:
        """The E27 has no night mode, so arm night is not advertised or sent."""
        hub = _hub()
        entity = _area_entity(_two_area_snapshot(), hub)
        features = entity.supported_features
        assert not features & AlarmControlPanelEntityFeature.ARM_NIGHT
        assert features & AlarmControlPanelEntityFeature.ARM_AWAY
        assert features & AlarmControlPanelEntityFeature.ARM_HOME
        assert features & AlarmControlPanelEntityFeature.ARM_CUSTOM_BYPASS
        # Not overridden: Home Assistant's base implementation is not supported.
        assert (
            type(entity).async_alarm_arm_night
            is AlarmControlPanelEntity.async_alarm_arm_night
        )
        hub.async_arm_area.assert_not_called()

    def test_attributes_are_area_scoped(self) -> None:
        """Attributes list only this area's faulted zones and drop dead fields."""
        entity = _area_entity(_two_area_snapshot(), _hub())
        assert entity.extra_state_attributes == {
            "ready": False,
            "ready_status": getattr(
                entity.coordinator.data.areas[1], "ready_status", None
            ),
            "faulted_zone_ids": [1, 2],
            "faulted_zones": ["Front Door", "Rear Door"],
        }
        garage = _area_entity(_two_area_snapshot(), _hub(), area_id=2)
        assert garage.extra_state_attributes["faulted_zone_ids"] == [4]

    def test_ready_status_attribute(self) -> None:
        """ready_status is shown when the library provides it (elke27 0.3.10+)."""
        snapshot = _two_area_snapshot()
        area = snapshot.areas[1]
        if not hasattr(area, "ready_status"):
            self.skipTest("elke27 without AreaState.ready_status")
        area = dataclasses.replace(area, ready=False, ready_status="RDY_NOT")
        snapshot = dataclasses.replace(
            snapshot, areas=MappingProxyType({**snapshot.areas, 1: area})
        )
        attrs = _area_entity(snapshot, _hub()).extra_state_attributes
        assert attrs["ready"] is False
        assert attrs["ready_status"] == "RDY_NOT"

    def test_missing_area_attributes(self) -> None:
        """A missing area reports every attribute as None."""
        snapshot = _two_area_snapshot()
        entity = _area_entity(snapshot, _hub())
        entity.coordinator.data = dataclasses.replace(
            snapshot, areas=MappingProxyType({})
        )
        assert entity.extra_state_attributes == {
            "ready": None,
            "ready_status": None,
            "faulted_zone_ids": None,
            "faulted_zones": None,
        }

    def test_state_mapping(self) -> None:
        """Arm modes map to Home Assistant alarm states."""
        cases = {
            None: AlarmControlPanelState.DISARMED,
            ArmMode.DISARMED: AlarmControlPanelState.DISARMED,
            ArmMode.ARMED_STAY: AlarmControlPanelState.ARMED_HOME,
            ArmMode.ARMED_NIGHT: AlarmControlPanelState.ARMED_NIGHT,
            ArmMode.ARMED_AWAY: AlarmControlPanelState.ARMED_AWAY,
        }
        for mode, expected in cases.items():
            assert _area_state_to_ha(AreaState(area_id=1, arm_mode=mode)) is expected
        triggered = AreaState(area_id=1, arm_mode=ArmMode.ARMED_AWAY, alarm_active=True)
        assert _area_state_to_ha(triggered) is AlarmControlPanelState.TRIGGERED

    def test_exit_delay_shows_arming(self) -> None:
        """Exit delay (disarmed with pending arm) maps to ARMING."""
        area = AreaState(
            area_id=1,
            arm_mode=ArmMode.DISARMED,
            arm_cmd_mode=ArmMode.ARMED_AWAY,
            ee_timer=45,
            alarm_zone="",
        )
        assert _area_state_to_ha(area) is AlarmControlPanelState.ARMING

    def test_arming_transitions_to_armed_away(self) -> None:
        """The entity leaves ARMING when the snapshot reports fully armed away."""
        base = _two_area_snapshot()
        arming = dataclasses.replace(
            base,
            areas=MappingProxyType(
                {
                    **base.areas,
                    1: AreaState(
                        area_id=1,
                        name="House",
                        arm_mode=ArmMode.DISARMED,
                        arm_cmd_mode=ArmMode.ARMED_AWAY,
                        ee_timer=12,
                        alarm_zone="",
                    ),
                }
            ),
        )
        entity = _area_entity(arming, _hub())
        assert entity.alarm_state is AlarmControlPanelState.ARMING
        armed = dataclasses.replace(
            arming,
            areas=MappingProxyType(
                {
                    **arming.areas,
                    1: dataclasses.replace(
                        arming.areas[1],
                        arm_mode=ArmMode.ARMED_AWAY,
                        arm_cmd_mode=None,
                        ee_timer=None,
                        alarm_zone=None,
                    ),
                }
            ),
        )
        entity.coordinator.data = armed
        assert entity.alarm_state is AlarmControlPanelState.ARMED_AWAY

    def test_missing_area(self) -> None:
        """A missing area makes the entity unavailable with no state."""
        snapshot = _two_area_snapshot()
        entity = _area_entity(snapshot, _hub())
        entity.coordinator.data = dataclasses.replace(
            snapshot, areas=MappingProxyType({})
        )
        assert entity.alarm_state is None
        assert not entity.available


@unittest.skipUnless(_HAS_DEPS, "homeassistant and elke27 are required")
class LightEntityTest(unittest.TestCase):
    """Test the light entity."""

    def _light(self, light: Any) -> Any:
        snapshot = _snapshot(lights={light.light_id: light})
        return Elke27Light(
            _coordinator(snapshot), _hub(), _entry(), light.light_id, light
        )

    def test_is_on_uses_state(self) -> None:
        """is_on follows LightState.state, even without a level."""
        assert self._light(LightState(light_id=1, state=True)).is_on is True
        assert self._light(LightState(light_id=1, state=False, level=50)).is_on is False

    def test_is_on_falls_back_to_level(self) -> None:
        """Without a state, a non-zero level means on."""
        assert self._light(LightState(light_id=1, level=30)).is_on is True
        assert self._light(LightState(light_id=1, level=0)).is_on is False
        assert self._light(LightState(light_id=1)).is_on is None


@unittest.skipUnless(_HAS_DEPS, "homeassistant and elke27 are required")
class ClimateEntityTest(unittest.TestCase):
    """Test the climate entity."""

    def _climate(self, tstat: Any) -> Any:
        snapshot = _snapshot(thermostats={tstat.tstat_id: tstat})
        return Elke27Thermostat(
            _coordinator(snapshot), _hub(), _entry(), tstat.tstat_id, tstat
        )

    def test_current_humidity(self) -> None:
        """current_humidity comes from ThermostatState.humidity."""
        assert (
            self._climate(ThermostatState(tstat_id=1, humidity=45)).current_humidity
            == 45
        )
        assert (
            self._climate(ThermostatState(tstat_id=1, humidity=41.5)).current_humidity
            == 41.5
        )

    def test_current_humidity_missing(self) -> None:
        """No reading, or 0 (no sensor), is unknown."""
        assert self._climate(ThermostatState(tstat_id=1)).current_humidity is None
        assert (
            self._climate(ThermostatState(tstat_id=1, humidity=0)).current_humidity
            is None
        )


@unittest.skipUnless(_HAS_DEPS, "homeassistant and elke27 are required")
class ZoneEntityTest(unittest.TestCase):
    """Test the zone binary sensor."""

    def test_definition_and_device_class_from_zone_definition(self) -> None:
        """Definition, name and device class come from ZoneDefinition."""
        zone = ZoneState(zone_id=7, name="Raw", open=True, trouble=False)
        definition = ZoneDefinition(
            zone_id=7,
            name="Kitchen Window",
            definition="BURG PERIM INST",
            zone_type="Window",
        )
        snapshot = _snapshot(zones={7: zone}, zone_definitions={7: definition})
        entity = Elke27ZoneBinarySensor(
            _coordinator(snapshot), _hub(), _entry(), 7, zone, definition
        )
        assert entity.name == "Kitchen Window"
        assert entity.device_class is BinarySensorDeviceClass.WINDOW
        assert entity.is_on is True
        assert entity.icon == "mdi:window-open"
        assert entity.extra_state_attributes == {
            "definition": "BURG PERIM INST",
            "bypassed": None,
            "trouble": False,
        }

    def test_no_zone_definition(self) -> None:
        """Without a ZoneDefinition, fall back to the zone name and opening class."""
        zone = ZoneState(zone_id=8, name="Hall", open=False)
        snapshot = _snapshot(zones={8: zone})
        entity = Elke27ZoneBinarySensor(
            _coordinator(snapshot), _hub(), _entry(), 8, zone, None
        )
        assert entity.name == "Hall"
        assert entity.device_class is BinarySensorDeviceClass.OPENING
        assert entity.icon is None
        assert entity.is_on is False


@unittest.skipUnless(_HAS_DEPS, "homeassistant and elke27 are required")
class PanelFieldTest(unittest.TestCase):
    """Test panel field lookup from the typed snapshot."""

    def test_panel_fields(self) -> None:
        """Fields come from snapshot.panel; a hub name overrides the name."""
        snapshot = _snapshot(
            panel=PanelInfo(
                mac="aa:bb", serial="S1", model="E27", firmware="1.2", panel_name="Main"
            )
        )
        assert get_panel_field(snapshot, None, "name") == "Main"
        assert get_panel_field(snapshot, "Override", "name") == "Override"
        assert get_panel_field(snapshot, None, "mac") == "aa:bb"
        assert get_panel_field(snapshot, None, "serial") == "S1"
        assert get_panel_field(snapshot, None, "model") == "E27"
        assert get_panel_field(snapshot, None, "firmware") == "1.2"
        assert get_panel_field(None, None, "mac") is None


if __name__ == "__main__":
    unittest.main()
