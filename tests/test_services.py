# ruff: noqa: S101, PT027, PLR2004
"""Tests for the Elke27 integration actions."""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import importlib.util
from pathlib import Path
import sys
from types import MappingProxyType
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
    from elke27_lib.errors import (
        Elke27ConnectionError,
        Elke27PanelError,
        Elke27PinRequiredError,
        Elke27TimeoutError,
    )
    from test_entities import _area_entity
    from test_hub_bypass import _hub, _snapshot, _two_area_snapshot

    from custom_components import elke27 as integration
    from custom_components.elke27 import hub as elke27_hub
    from custom_components.elke27.hub import ZoneBypassFailedError
    from homeassistant.exceptions import HomeAssistantError, ServiceValidationError


@contextlib.contextmanager
def _fast_arm_status_poll() -> Any:
    """Shorten arm-status polling in tests."""
    with (
        patch.object(elke27_hub, "ARM_STATUS_POLL_INTERVAL", 0.01),
        patch.object(elke27_hub, "ARM_STATUS_POLL_TIMEOUT", 0.05),
    ):
        yield


def _area_status_client(
    snapshot: Any,
    *,
    arm_state: Any = "DISARMED",
) -> Any:
    """Build a mock elke27 client driven by area_get_status replies."""
    client = MagicMock()

    async def _execute(command_key: str, **_params: Any) -> Any:
        if command_key == "area_get_status":
            state = arm_state() if callable(arm_state) else arm_state
            payload: Any = {"area_id": 1, "arm_state": state}
        elif command_key == "zone_get_all_zones_status":
            payload = {"status": "99"}
        else:
            payload = {}
        return MagicMock(ok=True, data=payload, error=None)

    client.async_execute = AsyncMock(side_effect=_execute)
    client.get_snapshot = MagicMock(return_value=snapshot)
    client.async_arm_area = AsyncMock(return_value=None)
    client.async_disarm_area = AsyncMock(return_value=None)
    client.async_disconnect = AsyncMock(return_value=None)
    return client


def _runtime_for_hub(hub: Any) -> Any:
    runtime = MagicMock()
    runtime.hub = hub
    runtime.coordinator = MagicMock()
    runtime.coordinator.data = None
    return runtime


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


def _armed_after_first_call(before: Any) -> Any:
    """Area 1 armed away with zones 1 and 2 bypassed."""
    return dataclasses.replace(
        before,
        areas=MappingProxyType(
            {
                **before.areas,
                1: dataclasses.replace(before.areas[1], arm_mode=ArmMode.ARMED_AWAY),
            }
        ),
        zones=MappingProxyType(
            {
                **before.zones,
                1: dataclasses.replace(before.zones[1], bypassed=True),
                2: dataclasses.replace(before.zones[2], bypassed=True),
            }
        ),
    )


def _alarm_runtime(snapshot: Any) -> Any:
    hub = _hub()
    hub.async_set_zone_bypass = AsyncMock(return_value=True)
    hub.async_arm_area = AsyncMock(return_value=True)
    hub.async_disarm_area = AsyncMock(return_value=True)
    runtime = MagicMock()
    runtime.hub = hub
    # The live client snapshot is what arm automatic reads inside the lock; the
    # coordinator copy is deliberately stale here to prove it is not used.
    hub.get_snapshot = MagicMock(return_value=snapshot)

    async def _refresh(_area_id: int) -> Any:
        return hub.get_snapshot()

    hub.async_refresh_area_state = AsyncMock(side_effect=_refresh)

    async def _poll(area_id: int, *, gate: Any = None) -> tuple[Any, bool]:
        if gate is not None:
            await gate()
        snap = hub.get_snapshot()
        from custom_components.elke27.hub import area_is_armed  # noqa: PLC0415

        return snap, area_is_armed(snap, area_id)

    hub.async_poll_until_area_armed = AsyncMock(side_effect=_poll)

    async def _refresh_arm(_area_id: int) -> Any:
        return hub.get_snapshot()

    hub.async_refresh_area_arm_status = AsyncMock(side_effect=_refresh_arm)
    runtime.coordinator = MagicMock()
    runtime.coordinator.data = None
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

    async def test_bypass_takes_the_zone_area_lock(self) -> None:
        """zone_bypass holds the zone's area lock while it sends the bypass."""
        hub = _hub()
        hub.get_snapshot = MagicMock(return_value=_two_area_snapshot())
        lock = hub.area_arm_lock(2)
        held: list[bool] = []

        async def _bypass(*_args: Any, **_kwargs: Any) -> bool:
            held.append(lock.locked())
            return True

        hub.async_set_zone_bypass = AsyncMock(side_effect=_bypass)
        runtime = MagicMock()
        runtime.hub = hub
        entities = {
            "binary_sensor.garage": (_entry("aa:bb:cc:dd:ee:ff:zone:4"), runtime)
        }
        await self._run({"code": "1234", "bypass": False}, entities)
        assert held == [True]
        assert not lock.locked()

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

    async def test_partial_bypass_failure_names_bypassed_zones(self) -> None:
        """Zone A bypasses, zone B fails: no arm, A is rolled back once."""
        hass = MagicMock()
        runtime = _alarm_runtime(_two_area_snapshot())
        panel_reason = "not authorized (error 11008)"

        async def _bypass(zone_id: int, **kwargs: Any) -> bool:
            if zone_id == 2 and kwargs["bypassed"]:
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
                integration.persistent_notification, "async_create"
            ) as create_notification,
            self.assertRaises(HomeAssistantError) as ctx,
        ):
            await self._run(hass, {"mode": "away", "code": "1234"}, entities)
        assert str(ctx.exception) == (
            "Area 1 was not armed: Back Door (zone 2): not authorized (error 11008)."
            " The bypasses made before the failure were undone: Front Door (zone 1)."
        )
        assert isinstance(ctx.exception.__cause__, ZoneBypassFailedError)
        # No retry of the refusal; zone 1 is un-bypassed exactly once.
        assert runtime.hub.async_set_zone_bypass.await_args_list == [
            unittest.mock.call(1, bypassed=True, pin="1234"),
            unittest.mock.call(2, bypassed=True, pin="1234"),
            unittest.mock.call(1, bypassed=False, pin="1234"),
        ]
        runtime.hub.async_arm_area.assert_not_called()
        create_notification.assert_called_once()
        kwargs = create_notification.call_args.kwargs
        assert kwargs["notification_id"] == "elke27_arm_automatic_entry-abc_1"
        message = create_notification.call_args.kwargs["message"]
        assert "Area 1 (alarm_control_panel.house) was not armed" in message
        assert (
            "Back Door (zone 2) could not be bypassed: not authorized (error 11008)."
            in message
        )
        assert "were undone: Front Door (zone 1)." in message
        assert ".." not in message
        assert "until the area is disarmed" not in message
        assert "1234" not in message
        hass.bus.async_fire.assert_called_once_with(
            integration.EVENT_ARM_AUTOMATIC_FAILED,
            {
                "entity_id": "alarm_control_panel.house",
                "area_id": 1,
                "stage": "bypass",
                "outcome": "not_armed",
                "zone_id": 2,
                "reason": panel_reason,
                "bypassed_zone_ids": [1],
                "rolled_back_zone_ids": [1],
                "still_bypassed_zone_ids": [],
            },
        )

    async def test_first_bypass_failure_reports_no_bypassed_zones(self) -> None:
        """A failure on the first zone reports an empty bypassed list."""
        hass = MagicMock()
        runtime = _alarm_runtime(_two_area_snapshot())
        runtime.hub.async_set_zone_bypass = AsyncMock(return_value=False)
        entities = {
            "alarm_control_panel.house": (_entry("aa:bb:cc:dd:ee:ff:area:1"), runtime)
        }
        with (
            patch.object(integration.persistent_notification, "async_create"),
            self.assertRaises(HomeAssistantError),
        ):
            await self._run(hass, {"mode": "away", "code": "1234"}, entities)
        runtime.hub.async_set_zone_bypass.assert_awaited_once()
        runtime.hub.async_arm_area.assert_not_called()
        data = hass.bus.async_fire.call_args.args[1]
        assert data["zone_id"] == 1
        assert data["bypassed_zone_ids"] == []

    async def test_area_one_arms_area_two_fails(self) -> None:
        """Areas are independent: area 1 stays armed, area 2's failure is reported."""
        hass = MagicMock()
        snapshot = _two_area_snapshot()
        house = _alarm_runtime(snapshot)
        garage = _alarm_runtime(snapshot)
        arm_reason = "Panel rejected the request: area not ready (error 11015)."
        garage.hub.async_arm_area = AsyncMock(
            side_effect=HomeAssistantError(arm_reason)
        )
        entities = {
            "alarm_control_panel.a_house": (
                _entry("aa:bb:cc:dd:ee:ff:area:1", config_entry_id="entry-abc"),
                house,
            ),
            "alarm_control_panel.b_garage": (
                _entry("aa:bb:cc:dd:ee:ff:area:2", config_entry_id="entry-abc"),
                garage,
            ),
        }
        with (
            patch.object(
                integration.persistent_notification, "async_create"
            ) as create_notification,
            self.assertRaises(HomeAssistantError) as ctx,
        ):
            await self._run(hass, {"mode": "away", "code": "1234"}, entities)
        assert str(ctx.exception) == (
            f"Area 2 was not armed: {arm_reason}"
            " The bypasses made before the failure were undone: Garage Door (zone 4)."
        )
        house.hub.async_arm_area.assert_awaited_once()
        garage.hub.async_arm_area.assert_awaited_once()  # tried once, not retried
        create_notification.assert_called_once()
        assert (
            create_notification.call_args.kwargs["notification_id"]
            == "elke27_arm_automatic_entry-abc_2"
        )
        assert (
            "Area 2 (alarm_control_panel.b_garage)"
            in (create_notification.call_args.kwargs["message"])
        )
        hass.bus.async_fire.assert_called_once_with(
            integration.EVENT_ARM_AUTOMATIC_FAILED,
            {
                "entity_id": "alarm_control_panel.b_garage",
                "area_id": 2,
                "stage": "arm",
                "outcome": "not_armed",
                "zone_id": None,
                "reason": arm_reason,
                "bypassed_zone_ids": [4],
                "rolled_back_zone_ids": [4],
                "still_bypassed_zone_ids": [],
            },
        )

    async def test_failure_in_first_area_does_not_stop_later_areas(self) -> None:
        """A failed first area is reported; later areas are still armed."""
        hass = MagicMock()
        snapshot = _two_area_snapshot()
        house = _alarm_runtime(snapshot)
        garage = _alarm_runtime(snapshot)
        house.hub.async_arm_area = AsyncMock(side_effect=HomeAssistantError("busy"))
        garage.hub.async_arm_area = AsyncMock(side_effect=HomeAssistantError("nope"))
        entities = {
            "alarm_control_panel.a_house": (_entry("aa:bb:cc:dd:ee:ff:area:1"), house),
            "alarm_control_panel.b_garage": (
                _entry("aa:bb:cc:dd:ee:ff:area:2"),
                garage,
            ),
        }
        with (
            patch.object(integration.persistent_notification, "async_create"),
            self.assertRaises(HomeAssistantError) as ctx,
        ):
            await self._run(hass, {"mode": "away", "code": "1234"}, entities)
        assert str(ctx.exception).startswith("Automatic arming failed for 2 of 2 areas")
        assert "Area 1 was not armed: busy" in str(ctx.exception)
        assert "Area 2 was not armed: nope" in str(ctx.exception)
        assert hass.bus.async_fire.call_count == 2

    async def test_rollback_partial_failure_names_still_bypassed(self) -> None:
        """An un-bypass that fails is named, with how to clear it, and not retried."""
        hass = MagicMock()
        runtime = _alarm_runtime(_two_area_snapshot())
        runtime.hub.async_arm_area = AsyncMock(
            side_effect=HomeAssistantError("area not ready (error 11015).")
        )

        async def _bypass(zone_id: int, **kwargs: Any) -> bool:
            if zone_id == 2 and not kwargs["bypassed"]:
                refusal = "not authorized (error 11008)"
                raise HomeAssistantError(refusal)
            return True

        runtime.hub.async_set_zone_bypass = AsyncMock(side_effect=_bypass)
        entities = {
            "alarm_control_panel.house": (_entry("aa:bb:cc:dd:ee:ff:area:1"), runtime)
        }
        with (
            patch.object(
                integration.persistent_notification, "async_create"
            ) as create_notification,
            self.assertRaises(HomeAssistantError) as ctx,
        ):
            await self._run(hass, {"mode": "away", "code": "1234"}, entities)
        assert runtime.hub.async_set_zone_bypass.await_args_list == [
            unittest.mock.call(1, bypassed=True, pin="1234"),
            unittest.mock.call(2, bypassed=True, pin="1234"),
            unittest.mock.call(2, bypassed=False, pin="1234"),
            unittest.mock.call(1, bypassed=False, pin="1234"),
        ]
        runtime.hub.async_arm_area.assert_awaited_once()
        for text in (
            str(ctx.exception),
            create_notification.call_args.kwargs["message"],
        ):
            assert "still bypassed: Back Door (zone 2)." in text
            assert "were undone: Front Door (zone 1)." in text
            assert "elke27.zone_bypass" in text
            assert "bypass: false" in text
            assert ".." not in text
            assert "1234" not in text
        data = hass.bus.async_fire.call_args.args[1]
        assert data["stage"] == "arm"
        assert data["bypassed_zone_ids"] == [1, 2]
        assert data["rolled_back_zone_ids"] == [1]
        assert data["still_bypassed_zone_ids"] == [2]

    async def test_arm_failure_rolls_back_each_zone_once(self) -> None:
        """Arm refused after bypasses: every bypass is undone once, in reverse."""
        hass = MagicMock()
        runtime = _alarm_runtime(_two_area_snapshot())
        runtime.hub.async_arm_area = AsyncMock(
            side_effect=HomeAssistantError("area not ready (error 11015)")
        )
        entities = {
            "alarm_control_panel.house": (_entry("aa:bb:cc:dd:ee:ff:area:1"), runtime)
        }
        with (
            patch.object(integration.persistent_notification, "async_create"),
            self.assertRaises(HomeAssistantError) as ctx,
        ):
            await self._run(hass, {"mode": "away", "code": "1234"}, entities)
        assert runtime.hub.async_set_zone_bypass.await_args_list == [
            unittest.mock.call(1, bypassed=True, pin="1234"),
            unittest.mock.call(2, bypassed=True, pin="1234"),
            unittest.mock.call(2, bypassed=False, pin="1234"),
            unittest.mock.call(1, bypassed=False, pin="1234"),
        ]
        runtime.hub.async_arm_area.assert_awaited_once()
        assert "still bypassed" not in str(ctx.exception)
        data = hass.bus.async_fire.call_args.args[1]
        assert data["rolled_back_zone_ids"] == [2, 1]
        assert data["still_bypassed_zone_ids"] == []

    async def test_arm_not_sent_is_a_failure_with_rollback(self) -> None:
        """async_arm_area returning False is reported as a failure and rolled back."""
        hass = MagicMock()
        runtime = _alarm_runtime(_two_area_snapshot())
        runtime.hub.async_arm_area = AsyncMock(return_value=False)
        entities = {
            "alarm_control_panel.house": (_entry("aa:bb:cc:dd:ee:ff:area:1"), runtime)
        }
        with (
            patch.object(integration.persistent_notification, "async_create") as note,
            self.assertRaises(HomeAssistantError) as ctx,
        ):
            await self._run(hass, {"mode": "away", "code": "1234"}, entities)
        assert "Area 1 was not armed: the arm command was not sent" in str(
            ctx.exception
        )
        assert runtime.hub.async_set_zone_bypass.await_args_list[2:] == [
            unittest.mock.call(2, bypassed=False, pin="1234"),
            unittest.mock.call(1, bypassed=False, pin="1234"),
        ]
        runtime.hub.async_arm_area.assert_awaited_once()
        note.assert_called_once()
        data = hass.bus.async_fire.call_args.args[1]
        assert data["stage"] == "arm"
        assert data["outcome"] == "not_armed"
        assert data["rolled_back_zone_ids"] == [2, 1]

    async def test_arm_timeout_is_uncertain_and_not_rolled_back(self) -> None:
        """A transport timeout on arm leaves the bypasses and reports unknown."""
        hass = MagicMock()
        runtime = _alarm_runtime(_two_area_snapshot())
        timeout = HomeAssistantError("Timed out waiting for the panel")
        timeout.__cause__ = Elke27TimeoutError("timeout")
        runtime.hub.async_arm_area = AsyncMock(side_effect=timeout)
        entities = {
            "alarm_control_panel.house": (_entry("aa:bb:cc:dd:ee:ff:area:1"), runtime)
        }
        with (
            patch.object(integration.persistent_notification, "async_create") as note,
            self.assertRaises(HomeAssistantError) as ctx,
        ):
            await self._run(hass, {"mode": "away", "code": "1234"}, entities)
        # Only the two bypasses: no un-bypass, no retry of the arm.
        assert runtime.hub.async_set_zone_bypass.await_args_list == [
            unittest.mock.call(1, bypassed=True, pin="1234"),
            unittest.mock.call(2, bypassed=True, pin="1234"),
        ]
        runtime.hub.async_arm_area.assert_awaited_once()
        for text in (str(ctx.exception), note.call_args.kwargs["message"]):
            assert "arm result is unknown" in text
            assert "Check the area's state on the panel." in text
            assert "Front Door (zone 1), Back Door (zone 2)" in text
            assert "elke27.zone_bypass" in text
            assert ".." not in text
            assert "1234" not in text
        data = hass.bus.async_fire.call_args.args[1]
        assert data["stage"] == "arm_uncertain"
        assert data["outcome"] == "unknown"
        assert data["bypassed_zone_ids"] == [1, 2]
        assert data["rolled_back_zone_ids"] == []
        assert data["still_bypassed_zone_ids"] == [1, 2]

    async def test_arm_panel_refusal_is_rolled_back(self) -> None:
        """A panel refusal (Elke27PanelError cause) on arm rolls the bypasses back."""
        hass = MagicMock()
        runtime = _alarm_runtime(_two_area_snapshot())
        refusal = HomeAssistantError("area not ready (error 11015)")
        refusal.__cause__ = Elke27PanelError(11015, "area not ready")
        runtime.hub.async_arm_area = AsyncMock(side_effect=refusal)
        entities = {
            "alarm_control_panel.house": (_entry("aa:bb:cc:dd:ee:ff:area:1"), runtime)
        }
        with (
            patch.object(integration.persistent_notification, "async_create"),
            self.assertRaises(HomeAssistantError) as ctx,
        ):
            await self._run(hass, {"mode": "away", "code": "1234"}, entities)
        assert str(ctx.exception).startswith("Area 1 was not armed:")
        data = hass.bus.async_fire.call_args.args[1]
        assert data["stage"] == "arm"
        assert data["outcome"] == "not_armed"
        assert data["rolled_back_zone_ids"] == [2, 1]
        assert data["still_bypassed_zone_ids"] == []

    async def test_concurrent_calls_on_same_area_run_one_after_another(
        self,
    ) -> None:
        """A queued second call waits, then sees the first call's live state."""
        hass = MagicMock()
        before = _two_area_snapshot()
        live = {"snapshot": before}
        runtime = _alarm_runtime(before)
        runtime.hub.get_snapshot = MagicMock(side_effect=lambda: live["snapshot"])
        order: list[str] = []
        first_arm_started = asyncio.Event()
        release_first_arm = asyncio.Event()

        async def _bypass(zone_id: int, **kwargs: Any) -> bool:
            order.append(f"bypass{zone_id}:{kwargs['bypassed']}")
            return True

        async def _arm(*_args: Any, **_kwargs: Any) -> bool:
            order.append("arm")
            first_arm_started.set()
            await release_first_arm.wait()
            # The panel is now armed with zones 1 and 2 bypassed.
            live["snapshot"] = _armed_after_first_call(before)
            return True

        runtime.hub.async_set_zone_bypass = AsyncMock(side_effect=_bypass)
        runtime.hub.async_arm_area = AsyncMock(side_effect=_arm)
        entities = {
            "alarm_control_panel.house": (_entry("aa:bb:cc:dd:ee:ff:area:1"), runtime)
        }
        with patch.object(integration.persistent_notification, "async_create"):
            first = asyncio.create_task(
                self._run(hass, {"mode": "away", "code": "1234"}, entities)
            )
            await first_arm_started.wait()
            second = asyncio.create_task(
                self._run(hass, {"mode": "away", "code": "1234"}, entities)
            )
            for _ in range(5):
                await asyncio.sleep(0)
            # The second call waits on the area lock: nothing new has run.
            assert order == ["bypass1:True", "bypass2:True", "arm"]
            release_first_arm.set()
            await asyncio.gather(first, second)
        # The second call saw the area armed: no bypass, arm or rollback.
        assert order == ["bypass1:True", "bypass2:True", "arm"]
        hass.bus.async_fire.assert_not_called()

    async def test_concurrent_second_call_no_ops_when_status_refresh_lags(
        self,
    ) -> None:
        """Queued call polls until area_get_status shows armed (#43)."""
        hass = MagicMock()
        before = _two_area_snapshot()
        panel_armed = {"value": False}

        def _arm_state() -> str:
            return "ARMED_AWAY" if panel_armed["value"] else "DISARMED"

        hub = _hub()
        client = _area_status_client(before, arm_state=_arm_state)
        order: list[str] = []
        first_arm_started = asyncio.Event()
        release_first_arm = asyncio.Event()

        async def _panel_arm(_area_id: int, **_kwargs: Any) -> None:
            order.append("arm")
            first_arm_started.set()
            await release_first_arm.wait()
            panel_armed["value"] = True

        client.async_arm_area = AsyncMock(side_effect=_panel_arm)
        hub._client = client  # noqa: SLF001
        hub.async_set_zone_bypass = AsyncMock(return_value=True)
        runtime = _runtime_for_hub(hub)
        entities = {
            "alarm_control_panel.house": (_entry("aa:bb:cc:dd:ee:ff:area:1"), runtime)
        }
        with (
            _fast_arm_status_poll(),
            patch.object(integration.persistent_notification, "async_create"),
        ):
            first = asyncio.create_task(
                self._run(hass, {"mode": "away", "code": "1234"}, entities)
            )
            await first_arm_started.wait()
            second = asyncio.create_task(
                self._run(hass, {"mode": "away", "code": "1234"}, entities)
            )
            for _ in range(5):
                await asyncio.sleep(0)
            assert order == ["arm"]
            release_first_arm.set()
            await asyncio.gather(first, second)
        assert order == ["arm"]
        assert client.async_arm_area.await_count == 1
        assert hub.async_set_zone_bypass.await_count == 2
        hass.bus.async_fire.assert_not_called()

    async def test_second_call_skips_zones_already_bypassed(self) -> None:
        """Live zones already bypassed are not re-bypassed or rolled back."""
        hass = MagicMock()
        before = _two_area_snapshot()
        snapshot = dataclasses.replace(
            before,
            zones=MappingProxyType(
                {
                    **before.zones,
                    1: dataclasses.replace(before.zones[1], bypassed=True),
                    2: dataclasses.replace(before.zones[2], bypassed=True),
                }
            ),
        )
        runtime = _alarm_runtime(snapshot)
        runtime.hub.async_arm_area = AsyncMock(
            side_effect=HomeAssistantError("area not ready (error 11015)")
        )
        entities = {
            "alarm_control_panel.house": (_entry("aa:bb:cc:dd:ee:ff:area:1"), runtime)
        }
        with (
            patch.object(integration.persistent_notification, "async_create"),
            self.assertRaises(HomeAssistantError),
        ):
            await self._run(hass, {"mode": "away", "code": "1234"}, entities)
        # Nothing was bypassed by this call, so nothing is un-bypassed either.
        runtime.hub.async_set_zone_bypass.assert_not_called()
        data = hass.bus.async_fire.call_args.args[1]
        assert data["bypassed_zone_ids"] == []
        assert data["rolled_back_zone_ids"] == []

    async def test_already_armed_area_is_a_no_op(self) -> None:
        """An area the live snapshot shows armed is left alone and succeeds."""
        hass = MagicMock()
        runtime = _alarm_runtime(_armed_after_first_call(_two_area_snapshot()))
        entities = {
            "alarm_control_panel.house": (_entry("aa:bb:cc:dd:ee:ff:area:1"), runtime)
        }
        await self._run(hass, {"mode": "home", "code": "1234"}, entities)
        runtime.hub.async_set_zone_bypass.assert_not_called()
        runtime.hub.async_arm_area.assert_not_called()
        hass.bus.async_fire.assert_not_called()

    async def test_already_armed_refusal_is_not_rolled_back(self) -> None:
        """Panel error 11028 (not allowed when armed) keeps the bypasses."""
        hass = MagicMock()
        runtime = _alarm_runtime(_two_area_snapshot())
        refusal = HomeAssistantError("not allowed when armed (error 11028)")
        refusal.__cause__ = Elke27PanelError(11028, "not allowed when armed")
        runtime.hub.async_arm_area = AsyncMock(side_effect=refusal)
        entities = {
            "alarm_control_panel.house": (_entry("aa:bb:cc:dd:ee:ff:area:1"), runtime)
        }
        with (
            patch.object(integration.persistent_notification, "async_create"),
            self.assertRaises(HomeAssistantError),
        ):
            await self._run(hass, {"mode": "away", "code": "1234"}, entities)
        assert runtime.hub.async_set_zone_bypass.await_count == 2  # no un-bypass
        data = hass.bus.async_fire.call_args.args[1]
        assert data["stage"] == "arm_uncertain"
        assert data["rolled_back_zone_ids"] == []
        assert data["still_bypassed_zone_ids"] == [1, 2]

    async def test_no_live_snapshot_is_not_sent(self) -> None:
        """Without a client snapshot nothing is bypassed and the failure is reported."""
        hass = MagicMock()
        runtime = _alarm_runtime(None)
        entities = {
            "alarm_control_panel.house": (_entry("aa:bb:cc:dd:ee:ff:area:1"), runtime)
        }
        with (
            patch.object(integration.persistent_notification, "async_create"),
            self.assertRaises(HomeAssistantError),
        ):
            await self._run(hass, {"mode": "away", "code": "1234"}, entities)
        runtime.hub.async_set_zone_bypass.assert_not_called()
        runtime.hub.async_arm_area.assert_not_called()
        assert hass.bus.async_fire.call_args.args[1]["outcome"] == "not_armed"

    async def test_disarm_mid_bypass_cancels_automatic_arming(self) -> None:
        """A disarm during the bypasses cancels the call and is sent at once."""
        hass = MagicMock()
        snapshot = _two_area_snapshot()
        runtime = _alarm_runtime(snapshot)
        hub = runtime.hub
        order: list[str] = []
        first_bypass_sent = asyncio.Event()
        never = asyncio.Event()

        async def _bypass(zone_id: int, **kwargs: Any) -> bool:
            order.append(f"bypass{zone_id}:{kwargs['bypassed']}")
            first_bypass_sent.set()
            await never.wait()  # the panel has not answered yet
            return True

        async def _disarm(area_id: int, _pin: str | None, **_kw: Any) -> bool:
            order.append(f"disarm{area_id}")
            return True

        hub.async_set_zone_bypass = AsyncMock(side_effect=_bypass)
        hub.async_disarm_area = AsyncMock(side_effect=_disarm)
        entity = _area_entity(snapshot, hub)
        entities = {
            "alarm_control_panel.house": (
                _entry("aa:bb:cc:dd:ee:ff:area:1", config_entry_id="entry-abc"),
                runtime,
            )
        }
        with patch.object(
            integration.persistent_notification, "async_create"
        ) as create_notification:
            automatic = asyncio.create_task(
                self._run(hass, {"mode": "away", "code": "1234"}, entities)
            )
            await first_bypass_sent.wait()
            # The automatic call holds the area lock; disarm must not wait on it.
            assert hub.area_arm_lock(1).locked()
            await asyncio.wait_for(entity.async_alarm_disarm("1234"), timeout=1)
            assert order == ["bypass1:True", "disarm1"]
            with self.assertRaises(HomeAssistantError) as ctx:
                await automatic
        # No further bypass, no arm, no rollback after the cancel.
        assert order == ["bypass1:True", "disarm1"]
        hub.async_arm_area.assert_not_called()
        assert "cancelled by a disarm" in str(ctx.exception)
        assert "Front Door (zone 1)" in str(ctx.exception)
        assert not hub.area_arm_lock(1).locked()
        message = create_notification.call_args.kwargs["message"]
        assert "cancelled by a disarm" in message
        assert "elke27.zone_bypass" in message
        assert "1234" not in message
        hass.bus.async_fire.assert_called_once_with(
            integration.EVENT_ARM_AUTOMATIC_FAILED,
            {
                "entity_id": "alarm_control_panel.house",
                "area_id": 1,
                "stage": "cancelled",
                "outcome": "not_armed",
                "zone_id": None,
                "reason": "cancelled by a disarm of this area",
                "bypassed_zone_ids": [1],
                "rolled_back_zone_ids": [],
                "still_bypassed_zone_ids": [],
            },
        )

    async def test_disarm_cancels_a_queued_automatic_call(self) -> None:
        """A call still waiting for the area lock is cancelled before it sends."""
        hass = MagicMock()
        snapshot = _two_area_snapshot()
        runtime = _alarm_runtime(snapshot)
        hub = runtime.hub
        entity = _area_entity(snapshot, hub)
        entities = {
            "alarm_control_panel.house": (_entry("aa:bb:cc:dd:ee:ff:area:1"), runtime)
        }
        lock = hub.area_arm_lock(1)
        await lock.acquire()  # e.g. a manual arm in progress
        with patch.object(integration.persistent_notification, "async_create"):
            automatic = asyncio.create_task(
                self._run(hass, {"mode": "away", "code": "1234"}, entities)
            )
            for _ in range(5):
                await asyncio.sleep(0)
            await entity.async_alarm_disarm("1234")
            lock.release()
            with self.assertRaises(HomeAssistantError):
                await automatic
        hub.async_set_zone_bypass.assert_not_called()
        hub.async_arm_area.assert_not_called()
        hub.async_disarm_area.assert_awaited_once_with(1, "1234")
        data = hass.bus.async_fire.call_args.args[1]
        assert data["stage"] == "cancelled"
        assert data["bypassed_zone_ids"] == []

    async def test_disarm_without_automatic_call_is_unchanged(self) -> None:
        """With nothing in flight, disarm just disarms."""
        snapshot = _two_area_snapshot()
        runtime = _alarm_runtime(snapshot)
        hub = runtime.hub
        hub.async_disarm_area = AsyncMock(return_value=True)
        entity = _area_entity(snapshot, hub)
        assert hub.cancel_arm_automatic(1) == 0
        await entity.async_alarm_disarm("1234")
        hub.async_disarm_area.assert_awaited_once_with(1, "1234")
        hub.async_set_zone_bypass.assert_not_called()

    async def test_disarm_of_other_area_does_not_cancel(self) -> None:
        """Only the disarmed area's automatic call is cancelled."""
        hub = _alarm_runtime(_two_area_snapshot()).hub
        task = asyncio.create_task(asyncio.sleep(10))
        hub.register_arm_automatic(1, task)
        assert hub.cancel_arm_automatic(2) == 0
        assert not task.cancelled()
        assert hub.cancel_arm_automatic(1) == 1
        with self.assertRaises(asyncio.CancelledError):
            await task
        hub.unregister_arm_automatic(1, task)

    async def test_stale_snapshot_bypass_11028_when_armed_is_a_no_op(self) -> None:
        """Stale snapshot, bypass refused with 11028, area actually armed: no-op."""
        hass = MagicMock()
        stale = _two_area_snapshot()
        fresh = _armed_after_first_call(stale)
        runtime = _alarm_runtime(stale)
        hub = runtime.hub
        # The refresh before acting still returns the stale state; the re-read
        # after the 11028 shows the area armed.
        hub.async_refresh_area_state = AsyncMock(return_value=stale)
        hub.async_refresh_area_arm_status = AsyncMock(return_value=fresh)
        refusal = HomeAssistantError("not allowed when armed (error 11028)")
        refusal.__cause__ = Elke27PanelError(11028, "not allowed when armed")
        hub.async_set_zone_bypass = AsyncMock(side_effect=refusal)
        entities = {
            "alarm_control_panel.house": (_entry("aa:bb:cc:dd:ee:ff:area:1"), runtime)
        }
        with (
            patch.object(integration.persistent_notification, "async_create") as note,
            self.assertLogs(integration._LOGGER, "INFO") as logs,  # noqa: SLF001
        ):
            await self._run(hass, {"mode": "away", "code": "1234"}, entities)
        hub.async_set_zone_bypass.assert_awaited_once_with(
            1, bypassed=True, pin="1234"
        )  # no rollback, no retry
        hub.async_arm_area.assert_not_called()
        note.assert_not_called()
        hass.bus.async_fire.assert_not_called()
        assert any("already armed" in line for line in logs.output)
        hub.async_poll_until_area_armed.assert_not_called()
        hub.async_refresh_area_arm_status.assert_awaited_once()

    async def test_bypass_11028_when_not_armed_is_uncertain(self) -> None:
        """11028 at the bypass stage with the area not armed: uncertain, no rollback."""
        hass = MagicMock()
        runtime = _alarm_runtime(_two_area_snapshot())
        hub = runtime.hub

        async def _bypass(zone_id: int, **_kwargs: Any) -> bool:
            if zone_id == 2:
                refusal = HomeAssistantError("not allowed when armed (error 11028)")
                refusal.__cause__ = Elke27PanelError(11028, "not allowed when armed")
                raise refusal
            return True

        hub.async_set_zone_bypass = AsyncMock(side_effect=_bypass)
        stale = _two_area_snapshot()
        hub.async_refresh_area_arm_status = AsyncMock(return_value=stale)
        entities = {
            "alarm_control_panel.house": (_entry("aa:bb:cc:dd:ee:ff:area:1"), runtime)
        }
        with (
            patch.object(integration.persistent_notification, "async_create"),
            self.assertRaises(HomeAssistantError) as ctx,
        ):
            await self._run(hass, {"mode": "away", "code": "1234"}, entities)
        assert "arm result is unknown" in str(ctx.exception)
        hub.async_poll_until_area_armed.assert_not_called()
        hub.async_refresh_area_arm_status.assert_awaited_once()
        assert hub.async_set_zone_bypass.await_count == 2  # no un-bypass
        data = hass.bus.async_fire.call_args.args[1]
        assert data["stage"] == "arm_uncertain"
        assert data["rolled_back_zone_ids"] == []

    async def test_refresh_is_awaited_before_acting(self) -> None:
        """The area and zone status are refreshed from the panel inside the lock."""
        hass = MagicMock()
        runtime = _alarm_runtime(_two_area_snapshot())
        hub = runtime.hub
        held: list[bool] = []

        async def _refresh(_area_id: int) -> Any:
            held.append(hub.area_arm_lock(1).locked())
            return _two_area_snapshot()

        hub.async_refresh_area_state = AsyncMock(side_effect=_refresh)
        entities = {
            "alarm_control_panel.house": (_entry("aa:bb:cc:dd:ee:ff:area:1"), runtime)
        }
        await self._run(hass, {"mode": "away", "code": "1234"}, entities)
        assert held == [True]
        hub.async_arm_area.assert_awaited_once()

    async def test_disarm_with_bad_code_does_not_cancel(self) -> None:
        """An invalid or missing code is rejected before anything is cancelled."""
        snapshot = _two_area_snapshot()
        runtime = _alarm_runtime(snapshot)
        hub = runtime.hub
        task = asyncio.create_task(asyncio.sleep(10))
        hub.register_arm_automatic(1, task)
        entity = _area_entity(snapshot, hub)
        for bad in ("12a4", None):
            with self.assertRaises(HomeAssistantError):
                await entity.async_alarm_disarm(bad)
        hub.async_disarm_area.assert_not_called()
        assert not task.cancelled()
        await hub.async_wait_disarm_clear(1)  # no disarm left pending
        task.cancel()
        hub.unregister_arm_automatic(1, task)

    async def test_disarm_refused_by_panel_lets_automatic_continue(self) -> None:
        """A refused disarm (wrong code) cancels nothing; arming carries on."""
        hass = MagicMock()
        snapshot = _two_area_snapshot()
        runtime = _alarm_runtime(snapshot)
        hub = runtime.hub
        order: list[str] = []
        first_bypass_sent = asyncio.Event()
        release = asyncio.Event()

        async def _bypass(zone_id: int, **_kwargs: Any) -> bool:
            order.append(f"bypass{zone_id}")
            first_bypass_sent.set()
            await release.wait()
            return True

        async def _disarm(*_args: Any, **_kwargs: Any) -> bool:
            order.append("disarm")
            release.set()  # the bypass reply arrives while the disarm is out
            for _ in range(5):
                await asyncio.sleep(0)
            # Paused: no further automatic command while the disarm is pending.
            assert order == ["bypass1", "disarm"]
            refusal = "Panel rejected the request: invalid user code (error 11004)."
            raise HomeAssistantError(refusal)

        hub.async_set_zone_bypass = AsyncMock(side_effect=_bypass)
        hub.async_disarm_area = AsyncMock(side_effect=_disarm)
        entity = _area_entity(snapshot, hub)
        entities = {
            "alarm_control_panel.house": (_entry("aa:bb:cc:dd:ee:ff:area:1"), runtime)
        }
        automatic = asyncio.create_task(
            self._run(hass, {"mode": "away", "code": "1234"}, entities)
        )
        await first_bypass_sent.wait()
        with self.assertRaises(HomeAssistantError):
            await entity.async_alarm_disarm("9999")
        await automatic  # not cancelled: it finishes and arms
        assert order == ["bypass1", "disarm", "bypass2"]
        hub.async_arm_area.assert_awaited_once()
        hass.bus.async_fire.assert_not_called()

    async def _disarm_outcome(self, disarm: Any) -> tuple[bool, BaseException | None]:
        """Run a disarm with a pending automatic call; return (cancelled, error)."""
        snapshot = _two_area_snapshot()
        runtime = _alarm_runtime(snapshot)
        hub = runtime.hub
        hub.async_disarm_area = disarm
        task = asyncio.create_task(asyncio.sleep(10))
        hub.register_arm_automatic(1, task)
        entity = _area_entity(snapshot, hub)
        error: BaseException | None = None
        try:
            await entity.async_alarm_disarm("1234")
        except HomeAssistantError as err:
            error = err
        await asyncio.sleep(0)
        cancelled = task.cancelled()
        await hub.async_wait_disarm_clear(1)
        task.cancel()
        hub.unregister_arm_automatic(1, task)
        return cancelled, error

    async def test_disarm_timeout_cancels_automatic(self) -> None:
        """A disarm that timed out may have been accepted, so arming is cancelled."""
        timeout = Elke27TimeoutError("Timed out waiting for the reply.")
        disarm = AsyncMock(side_effect=_wrapped(timeout))
        cancelled, error = await self._disarm_outcome(disarm)
        assert cancelled
        assert error is not None

    async def test_disarm_transport_error_cancels_automatic(self) -> None:
        """A transport error after sending also cancels automatic arming."""
        disarm = AsyncMock(side_effect=_wrapped(Elke27ConnectionError("dropped")))
        cancelled, error = await self._disarm_outcome(disarm)
        assert cancelled
        assert error is not None

    async def test_disarm_wrong_code_lets_automatic_resume(self) -> None:
        """A definitive refusal (panel error code) does not cancel arming."""
        refusal = Elke27PanelError(11004, "invalid user code")
        disarm = AsyncMock(side_effect=_wrapped(refusal))
        cancelled, error = await self._disarm_outcome(disarm)
        assert not cancelled
        assert error is not None

    async def test_disarm_while_disconnected_raises_and_resumes(self) -> None:
        """A disarm that was never sent raises and leaves arming running."""
        cancelled, error = await self._disarm_outcome(AsyncMock(return_value=False))
        assert not cancelled
        assert error is not None
        assert "not connected" in str(error)

    async def test_disarm_while_arm_in_flight_ends_disarmed(self) -> None:
        """Arm already sent: the disarm goes after it, and nothing re-arms."""
        hass = MagicMock()
        snapshot = _two_area_snapshot()
        runtime = _alarm_runtime(snapshot)
        hub = runtime.hub
        panel: list[str] = []  # commands in the order the panel receives them
        arm_sent = asyncio.Event()
        arm_reply = asyncio.Event()

        async def _bypass(zone_id: int, **kwargs: Any) -> bool:
            panel.append(f"bypass{zone_id}:{kwargs['bypassed']}")
            return True

        async def _arm(*_args: Any, **_kwargs: Any) -> bool:
            panel.append("arm")
            arm_sent.set()
            await arm_reply.wait()
            return True

        async def _disarm(*_args: Any, **_kwargs: Any) -> bool:
            panel.append("disarm")
            arm_reply.set()
            return True

        hub.async_set_zone_bypass = AsyncMock(side_effect=_bypass)
        hub.async_arm_area = AsyncMock(side_effect=_arm)
        hub.async_disarm_area = AsyncMock(side_effect=_disarm)
        entity = _area_entity(snapshot, hub)
        entities = {
            "alarm_control_panel.house": (_entry("aa:bb:cc:dd:ee:ff:area:1"), runtime)
        }
        with patch.object(integration.persistent_notification, "async_create"):
            automatic = asyncio.create_task(
                self._run(hass, {"mode": "away", "code": "1234"}, entities)
            )
            await arm_sent.wait()
            await asyncio.wait_for(entity.async_alarm_disarm("1234"), timeout=1)
            with self.assertRaises(HomeAssistantError):
                await automatic
        # The disarm is the last command: no re-arm, no rollback after it.
        assert panel == ["bypass1:True", "bypass2:True", "arm", "disarm"]
        data = hass.bus.async_fire.call_args.args[1]
        assert data["stage"] == "cancelled"
        assert data["rolled_back_zone_ids"] == []

    async def test_pin_required_during_bypass_is_reported(self) -> None:
        """Elke27PinRequiredError from a bypass goes through failure reporting."""
        hass = MagicMock()
        runtime = _alarm_runtime(_two_area_snapshot())
        runtime.hub.async_set_zone_bypass = AsyncMock(
            side_effect=Elke27PinRequiredError("PIN required to bypass zones.")
        )
        entities = {
            "alarm_control_panel.house": (_entry("aa:bb:cc:dd:ee:ff:area:1"), runtime)
        }
        with (
            patch.object(
                integration.persistent_notification, "async_create"
            ) as create_notification,
            self.assertRaises(HomeAssistantError) as ctx,
        ):
            await self._run(hass, {"mode": "away", "code": "1234"}, entities)
        assert "a user code is required" in str(ctx.exception)
        runtime.hub.async_arm_area.assert_not_called()
        create_notification.assert_called_once()
        data = hass.bus.async_fire.call_args.args[1]
        assert data["stage"] == "bypass"
        assert data["zone_id"] == 1
        assert data["reason"] == "a user code is required"

    async def test_pin_required_during_arm_is_reported(self) -> None:
        """Elke27PinRequiredError from arming goes through failure reporting."""
        hass = MagicMock()
        snapshot = _snapshot(
            areas={1: AreaState(area_id=1, name="House", ready=True)},
            zones={1: ZoneState(zone_id=1, name="Front Door", area_id=1, open=False)},
        )
        runtime = _alarm_runtime(snapshot)
        runtime.hub.async_arm_area = AsyncMock(
            side_effect=Elke27PinRequiredError("PIN required to arm areas.")
        )
        entities = {
            "alarm_control_panel.house": (_entry("aa:bb:cc:dd:ee:ff:area:1"), runtime)
        }
        with (
            patch.object(
                integration.persistent_notification, "async_create"
            ) as create_notification,
            self.assertRaises(HomeAssistantError) as ctx,
        ):
            await self._run(hass, {"mode": "away", "code": "1234"}, entities)
        assert str(ctx.exception) == "Area 1 was not armed: a user code is required."
        create_notification.assert_called_once()
        hass.bus.async_fire.assert_called_once_with(
            integration.EVENT_ARM_AUTOMATIC_FAILED,
            {
                "entity_id": "alarm_control_panel.house",
                "area_id": 1,
                "stage": "arm",
                "outcome": "not_armed",
                "zone_id": None,
                "reason": "a user code is required",
                "bypassed_zone_ids": [],
                "rolled_back_zone_ids": [],
                "still_bypassed_zone_ids": [],
            },
        )

    async def test_uncontended_automatic_arm_does_not_poll_or_sleep(self) -> None:
        """Uncontended automatic arming reads panel status once and does not wait."""
        hass = MagicMock()
        before = _two_area_snapshot()
        hub = _hub()
        area_reads = {"count": 0}

        async def _execute(command_key: str, **_params: Any) -> Any:
            if command_key == "area_get_status":
                area_reads["count"] += 1
                payload: Any = {"area_id": 1, "arm_state": "DISARMED"}
            elif command_key == "zone_get_all_zones_status":
                payload = {"status": "99"}
            else:
                payload = {}
            return MagicMock(ok=True, data=payload, error=None)

        client = MagicMock()
        client.async_execute = AsyncMock(side_effect=_execute)
        client.get_snapshot = MagicMock(return_value=before)
        client.async_arm_area = AsyncMock(return_value=None)
        hub._client = client  # noqa: SLF001
        hub.async_set_zone_bypass = AsyncMock(return_value=True)
        runtime = _runtime_for_hub(hub)
        entities = {
            "alarm_control_panel.house": (_entry("aa:bb:cc:dd:ee:ff:area:1"), runtime)
        }

        async def _forbidden_sleep(*_args: Any, **_kwargs: Any) -> None:
            msg = "automatic arming must not sleep when the area lock was uncontended"
            raise AssertionError(msg)

        with (
            patch.object(integration.persistent_notification, "async_create"),
            patch.object(asyncio, "sleep", _forbidden_sleep),
        ):
            await self._run(hass, {"mode": "away", "code": "1234"}, entities)
        assert area_reads["count"] == 1
        client.async_arm_area.assert_awaited_once()

    async def test_keypad_disarm_after_ha_arm_then_automatic_arms(self) -> None:
        """Keypad disarm after an HA arm is not mistaken for still armed."""
        hass = MagicMock()
        before = _two_area_snapshot()
        panel = {"armed": True}
        hub = _hub()
        client = _area_status_client(
            before,
            arm_state=lambda: "ARMED_AWAY" if panel["armed"] else "DISARMED",
        )
        hub._client = client  # noqa: SLF001
        hub.async_set_zone_bypass = AsyncMock(return_value=True)
        await hub.async_arm_area(1, ArmMode.ARMED_AWAY, "1234")
        panel["armed"] = False
        client.async_arm_area.reset_mock()
        runtime = _runtime_for_hub(hub)
        entities = {
            "alarm_control_panel.house": (_entry("aa:bb:cc:dd:ee:ff:area:1"), runtime)
        }
        with (
            _fast_arm_status_poll(),
            patch.object(integration.persistent_notification, "async_create"),
        ):
            await self._run(hass, {"mode": "away", "code": "1234"}, entities)
        client.async_arm_area.assert_awaited_once()

    async def test_panel_drops_arm_then_automatic_arms(self) -> None:
        """When the panel drops an arm, automatic arming sends a new arm."""
        hass = MagicMock()
        before = _two_area_snapshot()
        panel = {"armed": True}
        hub = _hub()
        client = _area_status_client(
            before,
            arm_state=lambda: "ARMED_AWAY" if panel["armed"] else "DISARMED",
        )
        hub._client = client  # noqa: SLF001
        hub.async_set_zone_bypass = AsyncMock(return_value=True)
        panel["armed"] = False
        runtime = _runtime_for_hub(hub)
        entities = {
            "alarm_control_panel.house": (_entry("aa:bb:cc:dd:ee:ff:area:1"), runtime)
        }
        with (
            _fast_arm_status_poll(),
            patch.object(integration.persistent_notification, "async_create"),
        ):
            await self._run(hass, {"mode": "away", "code": "1234"}, entities)
        client.async_arm_area.assert_awaited_once()

    async def test_no_stale_arm_state_after_reconnect(self) -> None:
        """Reconnect does not leave automatic arming thinking an area is armed."""
        hass = MagicMock()
        before = _two_area_snapshot()
        first = _hub()
        client = _area_status_client(before, arm_state="ARMED_AWAY")
        first._client = client  # noqa: SLF001
        await first.async_arm_area(1, ArmMode.ARMED_AWAY, "1234")
        await first.async_disconnect()
        reloaded = _hub()
        reloaded._client = _area_status_client(before, arm_state="DISARMED")  # noqa: SLF001
        reloaded.async_set_zone_bypass = AsyncMock(return_value=True)
        runtime = _runtime_for_hub(reloaded)
        entities = {
            "alarm_control_panel.house": (_entry("aa:bb:cc:dd:ee:ff:area:1"), runtime)
        }
        with (
            _fast_arm_status_poll(),
            patch.object(integration.persistent_notification, "async_create"),
        ):
            await self._run(hass, {"mode": "away", "code": "1234"}, entities)
        reloaded._client.async_arm_area.assert_awaited_once()  # noqa: SLF001

    async def test_poll_timeout_while_disarmed_still_arms(self) -> None:
        """Poll timeout with a disarmed panel never skips automatic arming."""
        hass = MagicMock()
        before = _two_area_snapshot()
        hub = _hub()
        client = _area_status_client(before, arm_state="DISARMED")
        hub._client = client  # noqa: SLF001
        hub.async_set_zone_bypass = AsyncMock(return_value=True)
        runtime = _runtime_for_hub(hub)
        entities = {
            "alarm_control_panel.house": (_entry("aa:bb:cc:dd:ee:ff:area:1"), runtime)
        }
        with (
            _fast_arm_status_poll(),
            patch.object(integration.persistent_notification, "async_create"),
        ):
            await self._run(hass, {"mode": "away", "code": "1234"}, entities)
        client.async_arm_area.assert_awaited_once()

    async def test_failed_ha_disarm_does_not_skip_automatic_arm(self) -> None:
        """A failed HA disarm must not make automatic arming skip a disarmed panel."""
        hass = MagicMock()
        before = _two_area_snapshot()
        panel = {"armed": True}
        hub = _hub()
        client = _area_status_client(
            before,
            arm_state=lambda: "ARMED_AWAY" if panel["armed"] else "DISARMED",
        )
        client.async_disarm_area = AsyncMock(
            side_effect=HomeAssistantError("panel rejected disarm")
        )
        hub._client = client  # noqa: SLF001
        hub.async_set_zone_bypass = AsyncMock(return_value=True)
        await hub.async_arm_area(1, ArmMode.ARMED_AWAY, "1234")
        panel["armed"] = False
        with self.assertRaises(HomeAssistantError):
            await hub.async_disarm_area(1, "1234")
        client.async_arm_area.reset_mock()
        runtime = _runtime_for_hub(hub)
        entities = {
            "alarm_control_panel.house": (_entry("aa:bb:cc:dd:ee:ff:area:1"), runtime)
        }
        with (
            _fast_arm_status_poll(),
            patch.object(integration.persistent_notification, "async_create"),
        ):
            await self._run(hass, {"mode": "away", "code": "1234"}, entities)
        client.async_arm_area.assert_awaited_once()


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


def _wrapped(err: Exception) -> HomeAssistantError:
    """Wrap a library error the way the hub does."""
    wrapped = HomeAssistantError(str(err))
    wrapped.__cause__ = err
    return wrapped
