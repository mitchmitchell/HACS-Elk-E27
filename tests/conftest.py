# ruff: noqa: TC001, ARG001, D102, D103, D107, FBT001, S101
"""Shared fixtures for the Elke27 tests (pytest-homeassistant-custom-component)."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Generator
import dataclasses
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any
from unittest.mock import AsyncMock, MagicMock, create_autospec, patch

from elke27_lib import (
    AreaState,
    ArmMode,
    CommandResult,
    Elke27Event,
    EventType,
    LinkKeys,
    PanelInfo,
    PanelSnapshot,
    ZoneState,
)
from elke27_lib.client import Elke27Client, Result
from elke27_lib.events import ConnectionStateChanged
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.elke27.const import (
    CONF_INTEGRATION_ID,
    CONF_LINK_KEYS_JSON,
    DOMAIN,
)
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant

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


def snapshot_with(snapshot: Any, **maps: Any) -> Any:
    """Return `snapshot` with the given domain maps replaced (read-only views)."""
    return dataclasses.replace(
        snapshot, **{k: MappingProxyType(v) for k, v in maps.items()}
    )


def entity_ids(hass: HomeAssistant, domain: str) -> list[str]:
    """Return the entity ids currently in the state machine for `domain`."""
    return [s.entity_id for s in hass.states.async_all(domain)]


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

    def emit_link(self, event: Any) -> None:
        """Send a connection event to the client.subscribe listeners."""
        for call in list(self.client.subscribe.call_args_list):
            call.args[0](event)


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
    from custom_components.elke27.coordinator import Elke27DataUpdateCoordinator  # noqa: PLC0415

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
    entry.async_create_background_task.side_effect = lambda hass_, coro, name: (
        hass_.async_create_background_task(coro, name)
    )
    coordinator = Elke27DataUpdateCoordinator(hass, entry)
    # async_setup_entry sets this before the platforms load.
    coordinator.panel_device_id = "panel-device-id"
    return coordinator


def link_event(*, connected: bool) -> Elke27Event:
    """Return the client.subscribe connection event (DISCONNECTED or READY)."""
    return Elke27Event(
        event_type=EventType.READY if connected else EventType.DISCONNECTED,
        data={},
        seq=1,
        timestamp=datetime.now(UTC),
    )


def connection_event(*, connected: bool) -> ConnectionStateChanged:
    """Return a library connection event (connected or disconnected)."""
    return ConnectionStateChanged(
        kind="connection",
        at=0.0,
        seq=None,
        classification="BROADCAST",
        route=("system", "connection"),
        session_id=None,
        connected=connected,
    )


class FakeClient:
    """
    Panel behaviour installed on the autospecced client of a loaded entry.

    Writes return a chosen CommandResult; status reads (async_execute) and the
    Q5 CSM refresh (async_refresh_csm) can be held on a gate so a test can
    observe what runs concurrently. Nothing here touches coordinator internals.
    """

    def __init__(self, write_result: CommandResult | BaseException) -> None:
        self.write_result = write_result
        self.read_ok = True
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.snapshot = panel_snapshot()
        self.read_gate: asyncio.Event | None = None
        self.csm_gate: asyncio.Event | None = None
        self.reads_in_flight = 0
        self.max_reads_in_flight = 0
        self.async_execute: AsyncMock = AsyncMock()
        # Per-read controls, consumed in order by area/zone status reads:
        # a gate to hold that read, or an error it raises instead of replying.
        self.read_gates: list[asyncio.Event | None] = []
        self.read_errors: list[BaseException | None] = []
        # Called when a held read is cancelled (entry unload), before it ends.
        self.on_cancel: Callable[[], Awaitable[None]] | None = None
        # Called at the start of each write, as if the panel sent events then.
        self.on_write: Callable[[], Awaitable[None]] | None = None

    def install(self, mock: Any) -> None:
        """Wire this behaviour onto the harness client after setup."""
        mock.get_snapshot.side_effect = lambda: self.snapshot
        mock.is_ready = True
        mock.async_arm_area.side_effect = self.async_arm_area
        mock.async_disarm_area.side_effect = self.async_disarm_area
        mock.async_set_zone_bypass.side_effect = self.async_set_zone_bypass
        mock.async_execute.reset_mock()
        mock.async_execute.side_effect = self._execute
        mock.async_refresh_csm.side_effect = self._refresh_csm
        self.async_execute = mock.async_execute

    async def _write(self, kind: str, **params: Any) -> CommandResult:
        if self.on_write is not None:
            await self.on_write()
        self.calls.append((kind, params))
        if isinstance(self.write_result, BaseException):
            raise self.write_result
        return self.write_result

    async def async_arm_area(self, area_id: int, **params: Any) -> CommandResult:
        return await self._write("arm", area_id=area_id, **params)

    async def async_disarm_area(self, area_id: int, **params: Any) -> CommandResult:
        return await self._write("disarm", area_id=area_id, **params)

    async def async_set_zone_bypass(self, zone_id: int, **params: Any) -> CommandResult:
        return await self._write("bypass", zone_id=zone_id, **params)

    async def _gated(self, gate: asyncio.Event | None) -> None:
        self.reads_in_flight += 1
        self.max_reads_in_flight = max(self.max_reads_in_flight, self.reads_in_flight)
        try:
            if gate is not None:
                try:
                    await gate.wait()
                except asyncio.CancelledError:
                    if self.on_cancel is not None:
                        await self.on_cancel()
                    raise
            # A real read takes a few loop turns, so overlaps are observable.
            for _ in range(3):
                await asyncio.sleep(0)
        finally:
            self.reads_in_flight -= 1

    async def _execute(self, command_key: str, **params: Any) -> Result[Any]:
        self.calls.append((command_key, params))
        gate = self.read_gates.pop(0) if self.read_gates else self.read_gate
        await self._gated(gate)
        error = self.read_errors.pop(0) if self.read_errors else None
        if error is not None:
            raise error
        if not self.read_ok:
            return Result.failure(TimeoutError("read timed out"))
        if command_key == "area_get_status":
            return Result.success({"arm_state": "DISARMED"})
        return Result.success({"zones": []})

    async def _refresh_csm(self) -> None:
        self.calls.append(("csm", {}))
        await self._gated(self.csm_gate)

    def keys(self, kind: set[str]) -> list[str]:
        return [key for key, _ in self.calls if key in kind]


def command_result(refresh: bool | None) -> CommandResult:
    return CommandResult(route="test", status_refresh_ok=refresh)


Setup = Callable[[FakeClient], Awaitable[Any]]


@pytest.fixture
def setup(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: ClientHarness
) -> Setup:
    """Load the entry, then give its client the FakeClient behaviour."""

    async def _setup(client: FakeClient) -> Any:
        mock_client.snapshot = client.snapshot
        mock_config_entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()
        client.install(mock_client.client)
        coordinator = mock_config_entry.runtime_data.coordinator
        coordinator.async_set_updated_data = MagicMock(
            wraps=coordinator.async_set_updated_data
        )
        return coordinator

    return _setup


async def settle() -> None:
    for _ in range(10):
        await asyncio.sleep(0)


async def run_command(coordinator: Any, kind: str) -> bool:
    if kind == "arm":
        return bool(await coordinator.async_arm_area(1, ArmMode.ARMED_AWAY, "0123"))
    if kind == "disarm":
        return bool(await coordinator.async_disarm_area(1, "1234"))
    return bool(await coordinator.async_set_zone_bypass(1, bypassed=True, pin="1234"))
