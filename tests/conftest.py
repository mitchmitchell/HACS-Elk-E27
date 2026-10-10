# ruff: noqa: TC003, ARG001
"""Shared fixtures for the Elke27 tests (pytest-homeassistant-custom-component)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Generator
import dataclasses
from types import MappingProxyType
from typing import Any
from unittest.mock import MagicMock, create_autospec, patch

from elke27_lib import AreaState, LinkKeys, PanelInfo, PanelSnapshot, ZoneState
from elke27_lib.client import Elke27Client
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.elke27.const import (
    CONF_INTEGRATION_SERIAL,
    CONF_LINK_KEYS_JSON,
    DOMAIN,
)
from homeassistant.const import CONF_HOST, CONF_PORT

PANEL_MAC = "00:11:22:33:44:55"
PANEL_SERIAL = "elk-panel-serial-001"
PANEL_SERIAL_2 = "elk-panel-serial-002"
HOST = "192.0.2.10"
HOST_2 = "192.0.2.20"
PORT = 2101
INTEGRATION_SERIAL = "123456789012"
LINK_KEYS_JSON = LinkKeys(
    tempkey_hex="00", linkkey_hex="11", linkhmac_hex="22"
).to_json()


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item: pytest.Item) -> None:
    """
    Give unittest-style tests a current event loop.

    The older tests use unittest (asyncio.run / IsolatedAsyncioTestCase), which
    leaves no current loop behind; the Home Assistant plugin's autouse fixtures
    expect one.
    """
    if item.get_closest_marker("asyncio") is None:
        asyncio.set_event_loop(asyncio.new_event_loop())


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(
    enable_custom_integrations: None,
) -> Generator[None]:
    """Load custom_components/elke27 in every Home Assistant test."""
    return


def panel_snapshot() -> PanelSnapshot:
    """Return a small snapshot: one area with one closed zone."""
    return dataclasses.replace(
        PanelSnapshot.empty(),
        panel=PanelInfo(mac=PANEL_MAC, panel_name="Test Panel"),
        areas=MappingProxyType({1: AreaState(area_id=1, name="House", ready=True)}),
        zones=MappingProxyType(
            {1: ZoneState(zone_id=1, name="Front Door", area_id=1, open=False)}
        ),
    )


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """Return a linked Elke27 config entry (no PIN or code is stored)."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Test Panel",
        unique_id=PANEL_MAC,
        data={
            CONF_HOST: HOST,
            CONF_PORT: PORT,
            CONF_LINK_KEYS_JSON: LINK_KEYS_JSON,
            CONF_INTEGRATION_SERIAL: INTEGRATION_SERIAL,
        },
    )


class ClientHarness:
    """A mocked elke27 client plus helpers to drive it."""

    def __init__(self) -> None:
        """Create the autospecced client."""
        self.client: Any = create_autospec(Elke27Client, instance=True)
        self.client.wait_ready.return_value = True
        self.client.is_ready = True
        self.snapshot = panel_snapshot()
        self.client.get_snapshot.side_effect = lambda: self.snapshot
        self.typed_listeners: list[Callable[[Any], None]] = []
        self.client.subscribe.return_value = MagicMock()

        def _subscribe_typed(listener: Callable[[Any], None]) -> Callable[[], None]:
            self.typed_listeners.append(listener)
            return MagicMock()

        self.client.subscribe_typed.side_effect = _subscribe_typed

    def emit(self, event: Any) -> None:
        """Send a typed event to every subscribed listener."""
        for listener in list(self.typed_listeners):
            listener(event)


@pytest.fixture
def mock_client() -> Generator[ClientHarness]:
    """Patch the elke27 client the hub creates."""
    harness = ClientHarness()
    with patch(
        "custom_components.elke27.hub.Elke27Client", return_value=harness.client
    ):
        yield harness
