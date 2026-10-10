# ruff: noqa: S101, SLF001, PT027
"""
Command timeouts and transport errors surface as clean HomeAssistantErrors.

PR #42 hardware run: with the Ethernet cable pulled, light and lock showed a raw
"async_execute timeout waiting for light_set_status seq=115" and switch.turn_on
raised an unhandled Elke27TimeoutError (HTTP 500).
"""

from __future__ import annotations

import dataclasses
import importlib.util
from pathlib import Path
import sys
from types import MappingProxyType
from typing import Any
import unittest
from unittest.mock import MagicMock, create_autospec

_HAS_DEPS = all(
    importlib.util.find_spec(name) is not None
    for name in ("homeassistant", "elke27_lib")
)

if _HAS_DEPS:
    sys.path.insert(0, str(Path(__file__).parents[1]))

    from elke27_lib import LightState, LockState, OutputState, PanelInfo, PanelSnapshot
    from elke27_lib.client import Elke27Client, Result
    from elke27_lib.errors import (
        ConnectionLost,
        E27Timeout,
        Elke27DisconnectedError,
        Elke27TimeoutError,
    )

    from custom_components.elke27.hub import (
        CONNECTION_MESSAGE,
        TIMEOUT_MESSAGE,
        Elke27Hub,
    )
    from custom_components.elke27.light import Elke27Light
    from custom_components.elke27.lock import Elke27Lock
    from custom_components.elke27.switch import Elke27OutputSwitch
    from homeassistant.exceptions import HomeAssistantError


def _client() -> Any:
    client = create_autospec(Elke27Client, instance=True)
    # Added in elke27 0.3.11; attach it so the test works with 0.3.10 installed.
    client.request_link_check = MagicMock()
    return client


def _hub(client: Any) -> Any:
    hass = MagicMock()
    hass.async_create_background_task.side_effect = lambda coro, _name: coro.close()
    hub = Elke27Hub(hass, "192.0.2.10", 2101, "{}", "123456789012", None)
    hub._client = client
    return hub


def _snapshot(**kwargs: Any) -> Any:
    values = {key: MappingProxyType(value) for key, value in kwargs.items()}
    values["panel"] = PanelInfo(mac="00:11:22:33:44:55")
    return dataclasses.replace(PanelSnapshot.empty(), **values)


def _coordinator(snapshot: Any) -> Any:
    coordinator = MagicMock()
    coordinator.data = snapshot
    return coordinator


def _entry() -> Any:
    entry = MagicMock()
    entry.data = {"host": "192.0.2.10"}
    entry.title = "Panel"
    entry.unique_id = None
    entry.entry_id = "entry-1"
    return entry


def _switch(hub: Any) -> Any:
    output = OutputState(output_id=3, state=False)
    snap = _snapshot(outputs={3: output})
    return Elke27OutputSwitch(_coordinator(snap), hub, _entry(), 3, output)


def _light(hub: Any) -> Any:
    light = LightState(light_id=1, state=False)
    snap = _snapshot(lights={1: light})
    return Elke27Light(_coordinator(snap), hub, _entry(), 1, light)


def _lock(hub: Any) -> Any:
    lock = LockState(lock_id=1, locked=True)
    snap = _snapshot(locks={1: lock})
    return Elke27Lock(_coordinator(snap), hub, _entry(), 1, lock)


@unittest.skipUnless(_HAS_DEPS, "homeassistant and elke27 are required")
class SwitchCommandErrorTest(unittest.IsolatedAsyncioTestCase):
    """switch.turn_on/off never leaks a library exception."""

    async def test_timeout_raises_clean_error_and_checks_link(self) -> None:
        """An Elke27TimeoutError becomes a HomeAssistantError, not an HTTP 500."""
        client = _client()
        client.async_set_output.side_effect = Elke27TimeoutError()
        entity = _switch(_hub(client))
        with self.assertRaises(HomeAssistantError) as ctx:
            await entity.async_turn_on()
        assert str(ctx.exception) == TIMEOUT_MESSAGE
        assert isinstance(ctx.exception.__cause__, Elke27TimeoutError)
        client.request_link_check.assert_called_once()

    async def test_disconnect_raises_clean_error(self) -> None:
        """A dropped session gives the connection message, with no link check."""
        client = _client()
        client.async_set_output.side_effect = Elke27DisconnectedError()
        entity = _switch(_hub(client))
        with self.assertRaises(HomeAssistantError) as ctx:
            await entity.async_turn_off()
        assert str(ctx.exception) == CONNECTION_MESSAGE
        client.request_link_check.assert_not_called()


@unittest.skipUnless(_HAS_DEPS, "homeassistant and elke27 are required")
class LightCommandErrorTest(unittest.IsolatedAsyncioTestCase):
    """light.turn_on/off map async_execute failures to a clean message."""

    async def test_result_timeout_is_not_raw(self) -> None:
        """A Result carrying E27Timeout no longer shows the raw seq message."""
        client = _client()
        client.async_execute.return_value = Result.failure(
            E27Timeout("async_execute timeout waiting for light_set_status seq=115")
        )
        entity = _light(_hub(client))
        with self.assertRaises(HomeAssistantError) as ctx:
            await entity.async_turn_on()
        assert str(ctx.exception) == TIMEOUT_MESSAGE
        assert "seq=" not in str(ctx.exception)
        client.request_link_check.assert_called_once()

    async def test_raised_transport_error_is_clean(self) -> None:
        """An exception raised by async_execute is caught too."""
        client = _client()
        client.async_execute.side_effect = ConnectionLost("Session disconnected.")
        entity = _light(_hub(client))
        with self.assertRaises(HomeAssistantError) as ctx:
            await entity.async_turn_off()
        assert str(ctx.exception) == CONNECTION_MESSAGE


@unittest.skipUnless(_HAS_DEPS, "homeassistant and elke27 are required")
class LockCommandErrorTest(unittest.IsolatedAsyncioTestCase):
    """lock.lock/unlock map async_execute failures to a clean message."""

    async def test_result_timeout_is_not_raw(self) -> None:
        """A timed-out lock command gives the timeout message and a link check."""
        client = _client()
        client.async_execute.return_value = Result.failure(
            E27Timeout("async_execute timeout waiting for lock_set_status seq=116")
        )
        entity = _lock(_hub(client))
        with self.assertRaises(HomeAssistantError) as ctx:
            await entity.async_unlock()
        assert str(ctx.exception) == TIMEOUT_MESSAGE
        client.request_link_check.assert_called_once()

    async def test_raised_timeout_is_clean(self) -> None:
        """A raw TimeoutError from async_execute is caught as well."""
        client = _client()
        client.async_execute.side_effect = TimeoutError
        entity = _lock(_hub(client))
        with self.assertRaises(HomeAssistantError) as ctx:
            await entity.async_lock()
        assert str(ctx.exception) == TIMEOUT_MESSAGE

    async def test_old_library_without_link_check(self) -> None:
        """elke27 0.3.10 has no request_link_check; the error is still clean."""
        client = create_autospec(Elke27Client, instance=True)
        client.async_execute.side_effect = E27Timeout("timeout")
        entity = _lock(_hub(client))
        with self.assertRaises(HomeAssistantError) as ctx:
            await entity.async_lock()
        assert str(ctx.exception) == TIMEOUT_MESSAGE
