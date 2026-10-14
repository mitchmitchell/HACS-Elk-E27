# ruff: noqa: TC003, ARG001
"""Shared fixtures for the Elke27 tests (pytest-homeassistant-custom-component)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Generator
import dataclasses
from types import MappingProxyType
from typing import Any
from unittest.mock import AsyncMock, MagicMock, create_autospec, patch

from elke27_lib import AreaState, LinkKeys, PanelInfo, PanelSnapshot, ZoneState
from elke27_lib.client import Elke27Client
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.elke27.const import (
    CONF_INTEGRATION_ID,
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
INTEGRATION_ID = "123456789012"
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


@pytest.fixture(autouse=True)
def mock_discover_host() -> Generator[AsyncMock]:
    """Stub unicast discovery (E1a); default is no reply. Never touches the network."""
    mock = AsyncMock(return_value=None)
    with patch("custom_components.elke27.panel_mac.async_discover_host", mock):
        yield mock


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
            CONF_INTEGRATION_ID: INTEGRATION_ID,
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
        "custom_components.elke27.coordinator.Elke27Client", return_value=harness.client
    ):
        yield harness


def make_coordinator(
    hass: Any,
    *,
    panel_name: str | None = None,
    link_keys_json: str = "{}",
    host: str = HOST,
    port: int = PORT,
) -> Any:
    """
    Build a coordinator for unit tests without a real config entry.

    Replaces the pre-D3 ``Elke27Hub(hass, host, port, keys, serial, name)``.
    Background tasks go to ``hass.async_create_background_task``, as the hub
    did without an entry, so tests can capture them there.
    """
    from custom_components.elke27.coordinator import (  # noqa: PLC0415
        Elke27DataUpdateCoordinator,
    )

    entry = MagicMock()
    entry.entry_id = "test-entry"
    entry.title = "Test panel"
    entry.data = {
        CONF_HOST: host,
        CONF_PORT: port,
        CONF_LINK_KEYS_JSON: link_keys_json,
        CONF_INTEGRATION_ID: INTEGRATION_ID,
        "panel": {"panel_name": panel_name} if panel_name else None,
    }
    entry.async_create_background_task.side_effect = (
        lambda hass_, coro, name: hass_.async_create_background_task(coro, name)
    )
    return Elke27DataUpdateCoordinator(hass, entry)


_PUBLIC_WRITES = {
    "_async_write_arm": "async_arm_area",
    "_async_write_disarm": "async_disarm_area",
    "_async_write_zone_bypass": "async_set_zone_bypass",
}


@pytest.fixture(autouse=True)
def bridge_public_writes(request: pytest.FixtureRequest) -> Generator[None]:
    """
    Route D4's writes to the mocked elke27 public methods (status_refresh_ok None).

    Pre-D4 tests mock client.async_arm_area / async_disarm_area /
    async_set_zone_bypass; the bridge keeps them meaningful. D4 tests that
    exercise the real async_execute path use @pytest.mark.real_writes.
    """
    if request.node.get_closest_marker("real_writes") is not None:
        yield
        return
    from custom_components.elke27.coordinator import (  # noqa: PLC0415
        Elke27DataUpdateCoordinator,
    )

    patches = []
    for method, public in _PUBLIC_WRITES.items():

        async def _bridge(
            _self: Any, client: Any, *args: Any, _public: str = public, **kwargs: Any
        ) -> None:
            await getattr(client, _public)(*args, **kwargs)

        patches.append(patch.object(Elke27DataUpdateCoordinator, method, _bridge))
    for p in patches:
        p.start()
    yield
    for p in patches:
        p.stop()
