# ruff: noqa: S101, PT027, PLR2004
"""Tests for Elke27 hub area bypass helpers."""

from __future__ import annotations

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

    from elke27_lib import AreaState, PanelInfo, PanelSnapshot, ZoneState
    from elke27_lib.errors import Elke27PinRequiredError

    from custom_components.elke27.hub import (
        Elke27Hub,
        ZoneBypassFailedError,
        area_faulted_zones,
        zone_bypass_label,
    )
    from homeassistant.exceptions import HomeAssistantError


def _snapshot(**kwargs: Any) -> PanelSnapshot:
    values = {
        key: MappingProxyType(value) if isinstance(value, dict) else value
        for key, value in kwargs.items()
    }
    values.setdefault("panel", PanelInfo(mac="00:11:22:33:44:55"))
    return dataclasses.replace(PanelSnapshot.empty(), **values)


def _two_area_snapshot() -> PanelSnapshot:
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
    )


def _hub() -> Elke27Hub:
    hass = MagicMock()
    return Elke27Hub(hass, "192.0.2.10", 2101, "{}", "123456789012", None)


@unittest.skipUnless(_HAS_DEPS, "homeassistant and elke27 are required")
class AreaFaultedZonesTest(unittest.TestCase):
    """Test filtering faulted zones by area."""

    def test_area_faulted_zones_only_includes_target_area(self) -> None:
        """Open, non-bypassed zones in other areas are excluded."""
        snapshot = _two_area_snapshot()
        assert [zone.zone_id for zone in area_faulted_zones(snapshot, 1)] == [1, 2]
        assert [zone.zone_id for zone in area_faulted_zones(snapshot, 2)] == [4]
        assert area_faulted_zones(None, 1) == []

    def test_zone_bypass_label(self) -> None:
        """Zones are labeled by name and zone number."""
        zone = ZoneState(zone_id=9, name="  Porch  ", area_id=1, open=True)
        assert zone_bypass_label(zone) == "Porch (zone 9)"
        unnamed = ZoneState(zone_id=9, name="", area_id=1, open=True)
        assert zone_bypass_label(unnamed) == "Zone 9"


@unittest.skipUnless(_HAS_DEPS, "homeassistant and elke27 are required")
class AsyncBypassFaultedZonesTest(unittest.IsolatedAsyncioTestCase):
    """Test bypassing faulted zones through the hub."""

    async def test_bypasses_each_faulted_zone_in_area(self) -> None:
        """Each open zone in the area is bypassed with the code."""
        hub = _hub()
        hub.async_set_zone_bypass = AsyncMock(return_value=True)
        bypassed = await hub.async_bypass_faulted_zones(1, _two_area_snapshot(), "1234")
        assert [zone.zone_id for zone in bypassed] == [1, 2]
        assert hub.async_set_zone_bypass.await_args_list == [
            unittest.mock.call(1, bypassed=True, pin="1234"),
            unittest.mock.call(2, bypassed=True, pin="1234"),
        ]

    async def test_other_areas_are_not_bypassed(self) -> None:
        """Only zones assigned to the requested area are bypassed."""
        hub = _hub()
        hub.async_set_zone_bypass = AsyncMock(return_value=True)
        await hub.async_bypass_faulted_zones(2, _two_area_snapshot(), "1234")
        hub.async_set_zone_bypass.assert_awaited_once_with(4, bypassed=True, pin="1234")

    async def test_panel_error_names_zone_and_keeps_reason(self) -> None:
        """A panel rejection becomes a zone-specific HomeAssistantError."""
        hub = _hub()
        hub.async_set_zone_bypass = AsyncMock(
            side_effect=HomeAssistantError("not authorized (error 11008)")
        )
        with self.assertRaises(ZoneBypassFailedError) as ctx:
            await hub.async_bypass_faulted_zones(1, _two_area_snapshot(), "1234")
        assert ctx.exception.zone.zone_id == 1
        assert ctx.exception.reason == "not authorized (error 11008)"
        assert str(ctx.exception) == "Front Door (zone 1): not authorized (error 11008)"
        assert ctx.exception.bypassed_zones == ()

    async def test_partial_failure_names_zones_already_bypassed(self) -> None:
        """Zone A bypasses, zone B fails: A is reported; the hub does not retry."""
        hub = _hub()
        refusal = "zone cannot be bypassed (error 11023)"

        async def _bypass(zone_id: int, **_kwargs: Any) -> bool:
            if zone_id == 2:
                raise HomeAssistantError(refusal)
            return True

        hub.async_set_zone_bypass = AsyncMock(side_effect=_bypass)
        with self.assertRaises(ZoneBypassFailedError) as ctx:
            await hub.async_bypass_faulted_zones(1, _two_area_snapshot(), "1234")
        assert ctx.exception.zone.zone_id == 2
        assert [zone.zone_id for zone in ctx.exception.bypassed_zones] == [1]
        assert str(ctx.exception) == (
            "Back Door (zone 2): zone cannot be bypassed (error 11023)"
            " (already bypassed: Front Door (zone 1))"
        )
        # One call per zone: no retry of the refusal (rollback is the caller's job).
        assert hub.async_set_zone_bypass.await_args_list == [
            unittest.mock.call(1, bypassed=True, pin="1234"),
            unittest.mock.call(2, bypassed=True, pin="1234"),
        ]

    async def test_pin_required_is_a_bypass_failure(self) -> None:
        """A missing code is reported like any other bypass failure."""
        hub = _hub()
        hub.async_set_zone_bypass = AsyncMock(side_effect=Elke27PinRequiredError("x"))
        with self.assertRaises(ZoneBypassFailedError) as ctx:
            await hub.async_bypass_faulted_zones(1, _two_area_snapshot(), None)
        assert ctx.exception.pin_required is True
        assert ctx.exception.reason == "a user code is required"
        assert hub.async_set_zone_bypass.await_count == 1

    async def test_unacknowledged_bypass_raises(self) -> None:
        """A bypass that is not acknowledged stops before later zones."""
        hub = _hub()
        hub.async_set_zone_bypass = AsyncMock(return_value=False)
        with self.assertRaises(ZoneBypassFailedError) as ctx:
            await hub.async_bypass_faulted_zones(1, _two_area_snapshot(), "1234")
        assert ctx.exception.zone.zone_id == 1
        assert hub.async_set_zone_bypass.await_count == 1


@unittest.skipUnless(_HAS_DEPS, "homeassistant and elke27 are required")
class AsyncRollbackBypassesTest(unittest.IsolatedAsyncioTestCase):
    """Test undoing bypasses through the hub."""

    def _zones(self) -> list[ZoneState]:
        return [
            ZoneState(zone_id=zone_id, name="Perimeter", area_id=1, open=True)
            for zone_id in (1, 2, 3)
        ]

    async def test_rollback_all_ok_reverse_order_once_each(self) -> None:
        """Each zone is un-bypassed once, last bypassed first."""
        hub = _hub()
        hub.async_set_zone_bypass = AsyncMock(return_value=True)
        rolled_back, still = await hub.async_rollback_bypasses(self._zones(), "1234")
        assert [zone.zone_id for zone in rolled_back] == [3, 2, 1]
        assert still == []
        assert hub.async_set_zone_bypass.await_args_list == [
            unittest.mock.call(3, bypassed=False, pin="1234"),
            unittest.mock.call(2, bypassed=False, pin="1234"),
            unittest.mock.call(1, bypassed=False, pin="1234"),
        ]

    async def test_rollback_failures_are_not_retried(self) -> None:
        """A refused or unacknowledged un-bypass is reported, never retried."""
        hub = _hub()

        async def _unbypass(zone_id: int, **_kwargs: Any) -> bool:
            if zone_id == 2:
                refusal = "not authorized (error 11008)"
                raise HomeAssistantError(refusal)
            return zone_id != 3

        hub.async_set_zone_bypass = AsyncMock(side_effect=_unbypass)
        rolled_back, still = await hub.async_rollback_bypasses(self._zones(), "1234")
        assert [zone.zone_id for zone in rolled_back] == [1]
        assert [zone.zone_id for zone in still] == [3, 2]
        assert hub.async_set_zone_bypass.await_count == 3


@unittest.skipUnless(_HAS_DEPS, "homeassistant and elke27 are required")
class DefinitiveRefusalTest(unittest.TestCase):
    """Classify arm failures as definitive refusals or uncertain results."""

    def test_classification(self) -> None:
        """Panel codes, PIN and argument errors are definitive; transport is not."""
        from elke27_lib.errors import (  # noqa: PLC0415
            Elke27AuthError,
            Elke27ConnectionError,
            Elke27DisconnectedError,
            Elke27InvalidArgument,
            Elke27LinkRequiredError,
            Elke27PanelError,
            Elke27PermissionError,
            Elke27PinRequiredError,
            Elke27ProtocolError,
            Elke27TimeoutError,
        )

        from custom_components.elke27.hub import (  # noqa: PLC0415
            is_already_armed_refusal,
            is_definitive_refusal,
        )

        def _wrapped(cause: BaseException) -> HomeAssistantError:
            err = HomeAssistantError(str(cause))
            err.__cause__ = cause
            return err

        assert is_definitive_refusal(_wrapped(Elke27PanelError(11008, "denied")))
        assert is_definitive_refusal(Elke27PinRequiredError("pin"))
        assert is_definitive_refusal(_wrapped(Elke27InvalidArgument("bad")))
        assert is_definitive_refusal(HomeAssistantError("Code must be numeric."))
        assert not is_definitive_refusal(_wrapped(Elke27TimeoutError("timeout")))
        assert not is_definitive_refusal(_wrapped(Elke27DisconnectedError("drop")))
        assert not is_definitive_refusal(_wrapped(RuntimeError("unknown")))
        # Raised by elke27 only before the arm is sent.
        assert is_definitive_refusal(_wrapped(Elke27PermissionError("denied")))
        assert is_definitive_refusal(_wrapped(Elke27AuthError("auth")))
        # Can come from the receive path, or is unreachable: kept uncertain.
        assert not is_definitive_refusal(_wrapped(Elke27ConnectionError("not ready")))
        assert not is_definitive_refusal(_wrapped(Elke27LinkRequiredError("link")))
        assert not is_definitive_refusal(
            _wrapped(Elke27ProtocolError("Failed to arm area."))
        )
        # 11028 is a panel code, but callers must not roll back on it.
        already = _wrapped(Elke27PanelError(11028, "not allowed when armed"))
        assert is_already_armed_refusal(already)
        assert not is_already_armed_refusal(
            _wrapped(Elke27PanelError(11015, "not ready"))
        )


@unittest.skipUnless(_HAS_DEPS, "homeassistant and elke27 are required")
class RefreshAreaStateTest(unittest.IsolatedAsyncioTestCase):
    """Test the status refresh before automatic arming."""

    async def test_requests_area_and_zone_status_then_snapshot(self) -> None:
        """Area and zone status are requested once each, then the snapshot read."""
        hub = _hub()
        client = MagicMock()
        ok = MagicMock(ok=True)
        client.async_execute = AsyncMock(return_value=ok)
        client.get_snapshot = MagicMock(return_value="snapshot")
        hub._client = client  # noqa: SLF001
        assert await hub.async_refresh_area_state(3) == "snapshot"
        assert client.async_execute.await_args_list == [
            unittest.mock.call("area_get_status", area_id=3),
            unittest.mock.call("zone_get_all_zones_status"),
        ]

    async def test_failed_refresh_falls_back_to_snapshot(self) -> None:
        """A failed status request is not retried; the snapshot is used."""
        from elke27_lib.errors import Elke27TimeoutError  # noqa: PLC0415

        hub = _hub()
        client = MagicMock()
        client.async_execute = AsyncMock(side_effect=Elke27TimeoutError("timeout"))
        client.get_snapshot = MagicMock(return_value="snapshot")
        hub._client = client  # noqa: SLF001
        assert await hub.async_refresh_area_state(1) == "snapshot"
        assert client.async_execute.await_count == 2

    async def test_no_client_returns_none(self) -> None:
        """Without a client there is nothing to refresh."""
        assert await _hub().async_refresh_area_state(1) is None
