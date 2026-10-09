# ruff: noqa: S101, PT027, SLF001
"""Tests for Elke27 hub use of the elke27 0.3.8 client API."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
import importlib.util
from pathlib import Path
import sys
from typing import Any
import unittest
from unittest.mock import AsyncMock, MagicMock, create_autospec, patch

_HAS_DEPS = all(
    importlib.util.find_spec(name) is not None
    for name in ("homeassistant", "elke27_lib")
)

if _HAS_DEPS:
    sys.path.insert(0, str(Path(__file__).parents[1]))

    from elke27_lib import Elke27Event, EventType, LinkKeys, PanelInfo, PanelSnapshot
    from elke27_lib.client import Elke27Client, Result
    from elke27_lib.errors import Elke27AuthError, Elke27PinRequiredError
    from elke27_lib.generators.registry import COMMANDS

    from custom_components.elke27 import hub as hub_module
    from custom_components.elke27.hub import Elke27Hub, _connection_state
    from homeassistant.exceptions import HomeAssistantError


def _hub(client: Any, panel_name: str | None = None) -> Any:
    hub = Elke27Hub(MagicMock(), "192.0.2.10", 2101, "{}", "123456789012", panel_name)
    hub._client = client
    return hub


def _client() -> Any:
    return create_autospec(Elke27Client, instance=True)


def _executing_client(sent: list[tuple[str, dict[str, Any]]]) -> Any:
    """Return a client whose async_execute runs the real command generators."""
    client = _client()

    async def _execute(command_key: str, /, **params: Any) -> Any:
        spec = COMMANDS[command_key]
        payload, _route = spec.generator(**params)
        sent.append((command_key, payload))
        return Result.success({})

    client.async_execute.side_effect = _execute
    return client


def _event(event_type: Any, data: dict[str, Any]) -> Any:
    return Elke27Event(
        event_type=event_type, data=data, seq=1, timestamp=datetime.now(UTC)
    )


@unittest.skipUnless(_HAS_DEPS, "homeassistant and elke27 are required")
class HubConnectTest(unittest.IsolatedAsyncioTestCase):
    """Test the hub connection lifecycle against the client API."""

    async def test_connect_sets_identity_and_skips_discovery(self) -> None:
        """Connect uses set_client_identity and never runs UDP discovery."""
        client = _client()
        client.wait_ready.return_value = True
        link_keys = LinkKeys(tempkey_hex="00", linkkey_hex="11", linkhmac_hex="22")
        hub = Elke27Hub(
            MagicMock(), "192.0.2.10", 2101, link_keys.to_json(), "123456789012", None
        )
        with patch.object(hub_module, "Elke27Client", return_value=client):
            await hub.async_connect()

        client.set_client_identity.assert_called_once_with(
            {"mn": "222", "sn": "123456789012"}
        )
        client.async_connect.assert_awaited_once()
        client.async_discover.assert_not_called()
        assert hub.client is client


@unittest.skipUnless(_HAS_DEPS, "homeassistant and elke27 are required")
class HubPanelNameTest(unittest.TestCase):
    """Test where the hub gets the panel name from."""

    def test_panel_name_prefers_snapshot(self) -> None:
        """The panel-reported name in the snapshot wins."""
        client = _client()
        client.get_snapshot.return_value = dataclasses.replace(
            PanelSnapshot.empty(), panel=PanelInfo(panel_name="Main Panel")
        )
        assert _hub(client, panel_name="Configured").panel_name == "Main Panel"

    def test_panel_name_falls_back_to_configured(self) -> None:
        """Without a snapshot name, the configured name is used."""
        client = _client()
        client.get_snapshot.return_value = PanelSnapshot.empty()
        assert _hub(client, panel_name="Configured").panel_name == "Configured"
        assert _hub(None, panel_name="Configured").panel_name == "Configured"

    def test_get_snapshot_uses_public_api(self) -> None:
        """get_snapshot calls the public client method."""
        client = _client()
        snapshot = PanelSnapshot.empty()
        client.get_snapshot.return_value = snapshot
        assert _hub(client).get_snapshot() is snapshot
        assert _hub(None).get_snapshot() is None


@unittest.skipUnless(_HAS_DEPS, "homeassistant and elke27 are required")
class HubZoneBypassTest(unittest.IsolatedAsyncioTestCase):
    """Test zone bypass through the client API."""

    async def test_bypass_uses_library_api_with_string_pin(self) -> None:
        """The PIN is passed as a string so leading zeros are kept."""
        client = _client()
        hub = _hub(client)
        assert await hub.async_set_zone_bypass(5, bypassed=True, pin="0123")
        client.async_set_zone_bypass.assert_awaited_once_with(
            5, bypassed=True, pin="0123"
        )
        client.async_execute.assert_not_called()

    async def test_bypass_requires_numeric_pin(self) -> None:
        """Missing or non-numeric PINs never reach the panel."""
        client = _client()
        hub = _hub(client)
        with self.assertRaises(Elke27PinRequiredError):
            await hub.async_set_zone_bypass(5, bypassed=True, pin=None)
        with self.assertRaises(HomeAssistantError):
            await hub.async_set_zone_bypass(5, bypassed=True, pin="12a4")
        client.async_set_zone_bypass.assert_not_called()

    async def test_bypass_library_error_becomes_ha_error(self) -> None:
        """Library failures surface as HomeAssistantError."""
        client = _client()
        client.async_set_zone_bypass.side_effect = Elke27AuthError("Not allowed.")
        with self.assertRaises(HomeAssistantError):
            await _hub(client).async_set_zone_bypass(5, bypassed=True, pin="1234")

    async def test_bypass_pin_required_error_propagates(self) -> None:
        """A PIN-required error from the library is re-raised unchanged."""
        client = _client()
        client.async_set_zone_bypass.side_effect = Elke27PinRequiredError("PIN.")
        with self.assertRaises(Elke27PinRequiredError):
            await _hub(client).async_set_zone_bypass(5, bypassed=True, pin="1234")

    async def test_bypass_without_client_returns_false(self) -> None:
        """Without a client the bypass is reported as not done."""
        assert not await _hub(None).async_set_zone_bypass(5, bypassed=True, pin="1")


@unittest.skipUnless(_HAS_DEPS, "homeassistant and elke27 are required")
class HubCommandTest(unittest.IsolatedAsyncioTestCase):
    """Test output/light/lock/thermostat commands against real generators."""

    async def test_output_uses_library_api(self) -> None:
        """Outputs use async_set_output(on=...)."""
        client = _client()
        assert await _hub(client).async_set_output(4, state=True)
        client.async_set_output.assert_awaited_once_with(4, on=True)

    async def test_light_payloads(self) -> None:
        """Light commands produce valid light_set_status payloads."""
        sent: list[tuple[str, dict[str, Any]]] = []
        hub = _hub(_executing_client(sent))
        await hub.async_set_light(2, state=True)
        await hub.async_set_light(2, state=True, level=50)
        await hub.async_set_light(2, state=False, level=80)
        assert sent == [
            ("light_set_status", {"light_id": 2, "status": "ON", "level": 99}),
            ("light_set_status", {"light_id": 2, "status": "ON", "level": 50}),
            ("light_set_status", {"light_id": 2, "status": "OFF", "level": 0}),
        ]

    async def test_lock_payloads(self) -> None:
        """Lock is status ON, unlock is status OFF."""
        sent: list[tuple[str, dict[str, Any]]] = []
        hub = _hub(_executing_client(sent))
        await hub.async_set_lock(1, locked=True)
        await hub.async_set_lock(1, locked=False)
        assert sent == [
            ("lock_set_status", {"lock_id": 1, "status": "ON"}),
            ("lock_set_status", {"lock_id": 1, "status": "OFF"}),
        ]

    async def test_tstat_payload_whole_degrees(self) -> None:
        """Setpoints are sent as whole degrees by elke27 0.3.8."""
        sent: list[tuple[str, dict[str, Any]]] = []
        hub = _hub(_executing_client(sent))
        await hub.async_set_tstat_status(
            1, mode="HEAT", heat_setpoint=68.0, cool_setpoint=75.5
        )
        assert sent == [
            (
                "tstat_set_status",
                {
                    "tstat_id": 1,
                    "mode": "HEAT",
                    "cool_setpoint": 76,
                    "heat_setpoint": 68,
                },
            )
        ]

    async def test_tstat_without_changes_sends_nothing(self) -> None:
        """No fields means no command."""
        client = _client()
        assert await _hub(client).async_set_tstat_status(1)
        client.async_execute.assert_not_called()

    async def test_command_error_is_raised(self) -> None:
        """A failed command raises the library error."""
        client = _client()
        error = Elke27AuthError("Denied.")
        client.async_execute = AsyncMock(return_value=Result.failure(error))
        with self.assertRaises(Elke27AuthError):
            await _hub(client).async_set_lock(1, locked=True)

    async def test_commands_without_client_return_false(self) -> None:
        """Without a client nothing is sent."""
        hub = _hub(None)
        assert not await hub.async_set_output(1, state=True)
        assert not await hub.async_set_light(1, state=True)
        assert not await hub.async_set_lock(1, locked=True)
        assert not await hub.async_set_tstat_status(1, mode="OFF")


@unittest.skipUnless(_HAS_DEPS, "homeassistant and elke27 are required")
class ConnectionStateTest(unittest.TestCase):
    """Test mapping of client connection events."""

    def test_connection_state(self) -> None:
        """READY/DISCONNECTED/CONNECTION events map to True/False."""
        assert _connection_state(_event(EventType.READY, {"connected": True}))
        assert _connection_state(_event(EventType.DISCONNECTED, {})) is False
        assert (
            _connection_state(_event(EventType.CONNECTION, {"connected": False}))
            is False
        )
        assert _connection_state(_event(EventType.CONNECTION, {"connected": True}))
        assert _connection_state(_event(EventType.AREA, {})) is None


if __name__ == "__main__":
    unittest.main()
