# ruff: noqa: S101, PT027, PLR2004
"""Tests for the Elke27 integration actions."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from typing import Any
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import yaml

_HAS_DEPS = all(
    importlib.util.find_spec(name) is not None
    for name in ("homeassistant", "elke27_lib")
)

_COMPONENT = Path(__file__).parents[1] / "custom_components" / "elke27"

if _HAS_DEPS:
    sys.path.insert(0, str(Path(__file__).parents[1]))
    sys.path.insert(0, str(Path(__file__).parent))

    from elke27_lib import AreaState, ArmMode, ZoneState
    from test_hub_bypass import _hub, _snapshot, _two_area_snapshot

    from custom_components import elke27 as integration
    from custom_components.elke27.hub import ZoneBypassFailedError
    from homeassistant.exceptions import HomeAssistantError, ServiceValidationError


def _call(data: dict[str, Any]) -> Any:
    call = MagicMock()
    call.data = data
    return call


def _runtime(*, acknowledged: bool = True) -> Any:
    runtime = MagicMock()
    runtime.hub.async_set_zone_bypass = AsyncMock(return_value=acknowledged)
    return runtime


def _entry(unique_id: str, *, config_entry_id: str = "config-entry-1") -> Any:
    entry = MagicMock()
    entry.unique_id = unique_id
    entry.config_entry_id = config_entry_id
    return entry


def _alarm_runtime(snapshot: Any) -> Any:
    hub = _hub()
    hub.async_set_zone_bypass = AsyncMock(return_value=True)
    hub.async_arm_area = AsyncMock(return_value=True)
    runtime = MagicMock()
    runtime.hub = hub
    runtime.coordinator = MagicMock()
    runtime.coordinator.data = snapshot
    return runtime


@unittest.skipUnless(_HAS_DEPS, "homeassistant and elke27 are required")
class ZoneBypassServiceTest(unittest.IsolatedAsyncioTestCase):
    """Test the elke27.zone_bypass action."""

    async def _run(
        self, data: dict[str, Any], entities: dict[str, tuple[Any, Any]]
    ) -> None:
        with (
            patch.object(
                integration,
                "_entity_ids_from_service_call",
                return_value=sorted(entities),
            ),
            patch.object(
                integration,
                "_entity_runtime_data",
                side_effect=lambda _hass, entity_id, _platform, _label: entities[
                    entity_id
                ],
            ),
        ):
            await integration._async_handle_zone_bypass(MagicMock(), _call(data))  # noqa: SLF001

    async def test_bypass_each_targeted_zone(self) -> None:
        """Each targeted zone is bypassed with the given code."""
        runtime = _runtime()
        entities = {
            "binary_sensor.front": (_entry("aa:bb:cc:dd:ee:ff:zone:3"), runtime),
            "binary_sensor.rear": (_entry("aa:bb:cc:dd:ee:ff:zone:12"), runtime),
        }
        data = integration.SERVICE_ZONE_BYPASS_SCHEMA(
            {"entity_id": list(entities), "code": "1234"}
        )
        assert data["bypass"] is True
        await self._run(data, entities)
        assert runtime.hub.async_set_zone_bypass.await_args_list == [
            unittest.mock.call(3, bypassed=True, pin="1234"),
            unittest.mock.call(12, bypassed=True, pin="1234"),
        ]

    async def test_unbypass(self) -> None:
        """bypass: false removes the bypass."""
        runtime = _runtime()
        entities = {
            "binary_sensor.front": (_entry("aa:bb:cc:dd:ee:ff:zone:3"), runtime)
        }
        await self._run({"code": "1234", "bypass": False}, entities)
        runtime.hub.async_set_zone_bypass.assert_awaited_once_with(
            3, bypassed=False, pin="1234"
        )

    async def test_unacknowledged_bypass_raises(self) -> None:
        """No client (not acknowledged) is reported as an error."""
        entities = {
            "binary_sensor.front": (
                _entry("aa:bb:cc:dd:ee:ff:zone:3"),
                _runtime(acknowledged=False),
            )
        }
        with self.assertRaises(HomeAssistantError):
            await self._run({"code": "1234", "bypass": True}, entities)

    async def test_no_target_raises(self) -> None:
        """A call without targets is rejected."""
        with self.assertRaises(ServiceValidationError):
            await self._run({"code": "1234", "bypass": True}, {})

    def test_schema_requires_code(self) -> None:
        """The code is required."""
        with self.assertRaises(integration.vol.Invalid):
            integration.SERVICE_ZONE_BYPASS_SCHEMA({"entity_id": ["binary_sensor.x"]})

    def test_numeric_id_from_unique_id(self) -> None:
        """IDs are parsed from <base>:<domain>:<id> unique IDs."""
        parse = integration._numeric_id_from_unique_id  # noqa: SLF001
        assert parse("aa:bb:cc:dd:ee:ff:zone:7", "zone") == 7
        assert integration._area_id_from_unique_id("x:area:2") == 2  # noqa: SLF001
        with self.assertRaises(ServiceValidationError):
            parse("aa:bb:cc:dd:ee:ff:area:7", "zone")
        with self.assertRaises(ServiceValidationError):
            parse("base:zone:abc", "zone")


@unittest.skipUnless(_HAS_DEPS, "homeassistant and elke27 are required")
class AlarmArmAutomaticServiceTest(unittest.IsolatedAsyncioTestCase):
    """Test the elke27.alarm_arm_automatic action."""

    async def _run(
        self,
        hass: Any,
        data: dict[str, Any],
        entities: dict[str, tuple[Any, Any]],
    ) -> None:
        with (
            patch.object(
                integration,
                "_entity_ids_from_service_call",
                return_value=sorted(entities),
            ),
            patch.object(
                integration,
                "_entity_runtime_data",
                side_effect=lambda _hass, entity_id, _platform, _label: entities[
                    entity_id
                ],
            ),
        ):
            await integration._async_handle_alarm_arm_automatic(hass, _call(data))  # noqa: SLF001

    async def test_geofence_arm_bypasses_faulted_zones_then_arms(self) -> None:
        """Open zones in the targeted area are bypassed, then the area is armed."""
        hass = MagicMock()
        runtime = _alarm_runtime(_two_area_snapshot())
        entities = {
            "alarm_control_panel.house": (
                _entry("aa:bb:cc:dd:ee:ff:area:1"),
                runtime,
            )
        }
        await self._run(
            hass,
            {"mode": "away", "code": "1234"},
            entities,
        )
        assert runtime.hub.async_set_zone_bypass.await_args_list == [
            unittest.mock.call(1, bypassed=True, pin="1234"),
            unittest.mock.call(2, bypassed=True, pin="1234"),
        ]
        runtime.hub.async_arm_area.assert_awaited_once_with(
            1,
            ArmMode.ARMED_AWAY,
            "1234",
            auto_stay_cancel=True,
            exit_delay_cancel=True,
        )

    async def test_other_areas_are_not_bypassed(self) -> None:
        """Faulted zones outside the armed area are never bypassed."""
        hass = MagicMock()
        runtime = _alarm_runtime(_two_area_snapshot())
        entities = {
            "alarm_control_panel.garage": (
                _entry("aa:bb:cc:dd:ee:ff:area:2"),
                runtime,
            )
        }
        await self._run(hass, {"mode": "home", "code": "1234"}, entities)
        runtime.hub.async_set_zone_bypass.assert_awaited_once_with(
            4, bypassed=True, pin="1234"
        )
        runtime.hub.async_arm_area.assert_awaited_once_with(
            2,
            ArmMode.ARMED_STAY,
            "1234",
            auto_stay_cancel=True,
            exit_delay_cancel=True,
        )

    async def test_no_faulted_zones_arms_without_bypass(self) -> None:
        """When every zone is ready, arming skips bypass calls."""
        hass = MagicMock()
        snapshot = _snapshot(
            areas={1: AreaState(area_id=1, name="House", ready=True)},
            zones={1: ZoneState(zone_id=1, name="Front Door", area_id=1, open=False)},
        )
        runtime = _alarm_runtime(snapshot)
        entities = {
            "alarm_control_panel.house": (
                _entry("aa:bb:cc:dd:ee:ff:area:1"),
                runtime,
            )
        }
        await self._run(hass, {"mode": "away", "code": "1234"}, entities)
        runtime.hub.async_set_zone_bypass.assert_not_called()
        runtime.hub.async_arm_area.assert_awaited_once_with(
            1,
            ArmMode.ARMED_AWAY,
            "1234",
            auto_stay_cancel=True,
            exit_delay_cancel=True,
        )

    async def test_bypass_failure_aborts_arm_and_alerts(self) -> None:
        """A failed bypass does not arm and notifies the user."""
        hass = MagicMock()
        runtime = _alarm_runtime(_two_area_snapshot())

        panel_reason = "not authorized (error 11008)"

        async def _bypass(zone_id: int, **_kwargs: Any) -> bool:
            if zone_id == 2:
                raise HomeAssistantError(panel_reason)
            return True

        runtime.hub.async_set_zone_bypass = AsyncMock(side_effect=_bypass)
        entities = {
            "alarm_control_panel.house": (
                _entry("aa:bb:cc:dd:ee:ff:area:1", config_entry_id="entry-abc"),
                runtime,
            )
        }
        with (
            patch.object(
                integration.persistent_notification,
                "async_create",
            ) as create_notification,
            self.assertRaises(ZoneBypassFailedError) as ctx,
        ):
            await self._run(hass, {"mode": "away", "code": "1234"}, entities)
        assert str(ctx.exception) == "Back Door: not authorized (error 11008)"
        runtime.hub.async_arm_area.assert_not_called()
        create_notification.assert_called_once()
        assert (
            create_notification.call_args.kwargs["notification_id"]
            == "elke27_arm_automatic_entry-abc_1"
        )
        hass.bus.async_fire.assert_called_once_with(
            integration.EVENT_ARM_AUTOMATIC_FAILED,
            {
                "entity_id": "alarm_control_panel.house",
                "area_id": 1,
                "zone_id": 2,
                "reason": panel_reason,
            },
        )


@unittest.skipUnless(_HAS_DEPS, "homeassistant and elke27 are required")
class ServiceDescriptionTest(unittest.TestCase):
    """Check the action descriptions."""

    def test_services_yaml_and_strings_match(self) -> None:
        """Both actions are declared, and arm automatic documents area bypass."""
        services = yaml.safe_load((_COMPONENT / "services.yaml").read_text())
        assert set(services) == {"alarm_arm_automatic", "zone_bypass"}
        assert services["zone_bypass"]["target"]["entity"]["domain"] == "binary_sensor"
        arm_description = services["alarm_arm_automatic"]["description"]
        assert "does not bypass zones" not in arm_description
        assert "bypassed before arming" in arm_description
        import json  # noqa: PLC0415

        for name in ("strings.json", "translations/en.json"):
            strings = json.loads((_COMPONENT / name).read_text())["services"]
            assert set(strings) == {"alarm_arm_automatic", "zone_bypass"}
            description = strings["alarm_arm_automatic"]["description"]
            assert "does not bypass zones" not in description
            assert "bypassed before arming" in description
            assert set(strings["zone_bypass"]["fields"]) == {"code", "bypass"}
