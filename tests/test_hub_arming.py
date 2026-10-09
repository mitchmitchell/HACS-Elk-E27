# ruff: noqa: S101, PT027
"""Tests for Elke27 hub arming and disarming."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from typing import Any
import unittest
from unittest.mock import MagicMock, create_autospec

_HAS_DEPS = all(
    importlib.util.find_spec(name) is not None
    for name in ("homeassistant", "elke27_lib")
)

if _HAS_DEPS:
    sys.path.insert(0, str(Path(__file__).parents[1]))

    from elke27_lib import ArmMode
    from elke27_lib.client import Elke27Client
    from elke27_lib.errors import (
        Elke27AuthError,
        Elke27PinRequiredError,
        Elke27ProtocolError,
    )

    from custom_components.elke27 import _service_mode_to_arm_mode
    from custom_components.elke27.hub import Elke27Hub
    from homeassistant.exceptions import HomeAssistantError


if _HAS_DEPS:

    class _PanelRejectionError(Elke27ProtocolError):
        """Stand-in for elke27 0.3.10's Elke27PanelError."""

        MESSAGE = (
            "Panel rejected the request: area not ready (open or faulted zones) "
            "(error 11015)."
        )

        def __init__(self) -> None:
            super().__init__(self.MESSAGE)
            self.panel_error_code = 11015


def _hub_with_client(client: Any) -> Any:
    hub = Elke27Hub(MagicMock(), "192.0.2.10", 2101, "{}", "123456789012", None)
    hub._client = client  # noqa: SLF001
    return hub


def _client() -> Any:
    # Autospec enforces the real elke27 signatures (keyword-only mode/pin).
    return create_autospec(Elke27Client, instance=True)


@unittest.skipUnless(_HAS_DEPS, "homeassistant and elke27 are required")
class HubArmingTest(unittest.IsolatedAsyncioTestCase):
    """Test that the hub calls the elke27 arm API with the right arguments."""

    async def test_arm_away_uses_library_api(self) -> None:
        """Arm away calls async_arm_area with keyword arguments."""
        client = _client()
        hub = _hub_with_client(client)
        assert await hub.async_arm_area(1, ArmMode.ARMED_AWAY, "1234")
        client.async_arm_area.assert_awaited_once_with(
            1,
            mode=ArmMode.ARMED_AWAY,
            pin="1234",
            auto_stay_cancel=False,
            exit_delay_cancel=False,
        )
        client.async_execute.assert_not_called()

    async def test_arm_home_maps_to_stay(self) -> None:
        """Arm home maps to the panel's stay mode."""
        client = _client()
        hub = _hub_with_client(client)
        await hub.async_arm_area(2, ArmMode.ARMED_STAY, "0042")
        client.async_arm_area.assert_awaited_once_with(
            2,
            mode=ArmMode.ARMED_STAY,
            pin="0042",
            auto_stay_cancel=False,
            exit_delay_cancel=False,
        )

    async def test_custom_bypass_arms_away(self) -> None:
        """Custom bypass arms away after the entity bypasses zones."""
        client = _client()
        hub = _hub_with_client(client)
        await hub.async_arm_area(1, "ARMED_CUSTOM_BYPASS", "1234")
        assert client.async_arm_area.await_args.kwargs["mode"] is ArmMode.ARMED_AWAY

    async def test_automatic_arming_flags_are_passed(self) -> None:
        """alarm_arm_automatic flags reach the library call."""
        client = _client()
        hub = _hub_with_client(client)
        for service_mode, expected in (
            ("away", ArmMode.ARMED_AWAY),
            ("home", ArmMode.ARMED_STAY),
        ):
            client.async_arm_area.reset_mock()
            await hub.async_arm_area(
                3,
                _service_mode_to_arm_mode(service_mode),
                "1234",
                auto_stay_cancel=True,
                exit_delay_cancel=True,
            )
            client.async_arm_area.assert_awaited_once_with(
                3,
                mode=expected,
                pin="1234",
                auto_stay_cancel=True,
                exit_delay_cancel=True,
            )

    async def test_arm_night_is_rejected(self) -> None:
        """The E27 has no night mode; arm night never reaches the panel."""
        client = _client()
        hub = _hub_with_client(client)
        with self.assertRaises(HomeAssistantError):
            await hub.async_arm_area(1, ArmMode.ARMED_NIGHT, "1234")
        client.async_arm_area.assert_not_called()

    async def test_panel_rejection_reason_is_shown_and_logged(self) -> None:
        """A panel rejection surfaces its reason and logs the panel error code."""
        client = _client()
        client.async_arm_area.side_effect = _PanelRejectionError()
        hub = _hub_with_client(client)
        with (
            self.assertLogs("custom_components.elke27.hub", level="WARNING") as logs,
            self.assertRaises(HomeAssistantError) as ctx,
        ):
            await hub.async_arm_area(1, ArmMode.ARMED_AWAY, "1234")
        assert str(ctx.exception) == _PanelRejectionError.MESSAGE
        assert "panel error 11015" in logs.output[0]
        assert "1234" not in logs.output[0]

    async def test_disarm_rejection_without_panel_code_is_logged(self) -> None:
        """Errors without a panel code are still logged as warnings."""
        client = _client()
        client.async_disarm_area.side_effect = Elke27AuthError("Denied.")
        hub = _hub_with_client(client)
        with (
            self.assertLogs("custom_components.elke27.hub", level="WARNING") as logs,
            self.assertRaises(HomeAssistantError),
        ):
            await hub.async_disarm_area(1, "1234")
        assert "Area 1 disarm failed: Denied." in logs.output[0]

    async def test_unknown_mode_is_rejected(self) -> None:
        """An unknown arm mode is rejected before reaching the panel."""
        client = _client()
        hub = _hub_with_client(client)
        with self.assertRaises(HomeAssistantError):
            await hub.async_arm_area(1, ArmMode.DISARMED, "1234")
        client.async_arm_area.assert_not_called()

    async def test_non_numeric_code_rejected(self) -> None:
        """Non-numeric codes are rejected before reaching the panel."""
        client = _client()
        hub = _hub_with_client(client)
        with self.assertRaises(HomeAssistantError):
            await hub.async_arm_area(1, ArmMode.ARMED_AWAY, "12a4")
        client.async_arm_area.assert_not_called()

    async def test_missing_code_raises_pin_required(self) -> None:
        """A missing code raises the PIN-required error the entities expect."""
        hub = _hub_with_client(_client())
        with self.assertRaises(Elke27PinRequiredError):
            await hub.async_arm_area(1, ArmMode.ARMED_AWAY, None)

    async def test_library_error_becomes_ha_error(self) -> None:
        """Library failures surface as HomeAssistantError, not a silent fallback."""
        client = _client()
        client.async_arm_area.side_effect = Elke27AuthError(
            "Authentication failed for this operation."
        )
        hub = _hub_with_client(client)
        with self.assertRaises(HomeAssistantError) as ctx:
            await hub.async_arm_area(1, ArmMode.ARMED_AWAY, "1234")
        assert "Authentication failed" in str(ctx.exception)
        client.async_execute.assert_not_called()

    async def test_disarm_uses_library_api(self) -> None:
        """Disarm calls async_disarm_area with keyword arguments."""
        client = _client()
        hub = _hub_with_client(client)
        assert await hub.async_disarm_area(1, "1234")
        client.async_disarm_area.assert_awaited_once_with(
            1,
            pin="1234",
            auto_stay_cancel=False,
            exit_delay_cancel=False,
        )
        client.async_execute.assert_not_called()

    async def test_no_client_returns_false(self) -> None:
        """Without a connected client the request is not sent."""
        hub = _hub_with_client(None)
        assert not await hub.async_arm_area(1, ArmMode.ARMED_AWAY, "1234")
        assert not await hub.async_disarm_area(1, "1234")


if __name__ == "__main__":
    unittest.main()
