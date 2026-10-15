# ruff: noqa: S101
"""
Fallback-read races found in review after D4 (Bugbot/Codex on #63 and #66).

Every scenario is driven through the fake client (writes, status reads,
typed and link events) and the config entry; no coordinator internals.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

from elke27_lib import ArmMode, PanelSnapshot

from tests.conftest import (
    ClientHarness,
    FakeClient,
    Setup,
    command_result,
    connection_event,
    link_event,
    run_command,
    settle,
)

if TYPE_CHECKING:
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from homeassistant.core import HomeAssistant

AREA_READ = {"area_get_status"}


def _pushes(coordinator: Any) -> list[Any]:
    return [c.args[0] for c in coordinator.async_set_updated_data.call_args_list]


def _published_read(coordinator: Any) -> bool:
    """Whether the last push is the status-read overlay (area 1 DISARMED)."""
    return _pushes(coordinator)[-1].areas[1].arm_mode == ArmMode.DISARMED


async def test_event_during_read_triggers_one_reread(
    hass: HomeAssistant, setup: Setup, mock_client: ClientHarness
) -> None:
    """A push while the read runs (reconnect refresh overlap) re-reads once."""
    client = FakeClient(command_result(refresh=False))
    coordinator = await setup(client)
    client.read_gate = asyncio.Event()
    task = hass.async_create_task(run_command(coordinator, "arm"))
    await settle()
    assert client.keys(AREA_READ) == ["area_get_status"]
    mock_client.emit(connection_event(connected=True))
    await settle()
    client.read_gate.set()
    assert await task
    await hass.async_block_till_done()
    assert client.keys(AREA_READ) == ["area_get_status"] * 2
    assert _published_read(coordinator)


async def test_unrelated_event_during_write_publishes_read(
    hass: HomeAssistant, setup: Setup, mock_client: ClientHarness
) -> None:
    """An unrelated zone event during the write does not drop the read result."""
    client = FakeClient(command_result(refresh=False))
    coordinator = await setup(client)

    async def _zone_event() -> None:
        mock_client.emit(MagicMock(name="ZoneStatusUpdated"))
        await settle()

    client.on_write = _zone_event
    assert await run_command(coordinator, "arm")
    await hass.async_block_till_done()
    assert client.keys(AREA_READ) == ["area_get_status"]
    assert _published_read(coordinator)


async def test_read_after_awaited_refresh_publishes(
    hass: HomeAssistant, setup: Setup, mock_client: ClientHarness
) -> None:
    """A fallback that waited on the reconnect refresh still publishes its read."""
    client = FakeClient(command_result(refresh=False))
    coordinator = await setup(client)
    client.csm_gate = asyncio.Event()
    mock_client.emit(connection_event(connected=True))
    await settle()
    assert client.keys({"csm"}) == ["csm"]
    task = hass.async_create_task(run_command(coordinator, "arm"))
    await settle()
    assert client.keys(AREA_READ) == []
    client.csm_gate.set()
    assert await task
    await hass.async_block_till_done()
    assert client.keys(AREA_READ) == ["area_get_status"]
    assert _published_read(coordinator)


async def test_epoch_taken_before_write(
    hass: HomeAssistant, setup: Setup, mock_client: ClientHarness
) -> None:
    """A reconnect during the write itself discards the fallback result."""
    client = FakeClient(command_result(refresh=False))
    coordinator = await setup(client)

    async def _bounce() -> None:
        mock_client.emit(connection_event(connected=False))
        mock_client.emit(connection_event(connected=True))
        await settle()

    client.on_write = _bounce
    await hass.async_block_till_done()
    coordinator.async_set_updated_data.reset_mock()
    assert await run_command(coordinator, "arm")
    await hass.async_block_till_done()
    for pushed in _pushes(coordinator):
        assert pushed is client.snapshot


async def test_link_drop_during_failing_read_never_publishes_empty(
    hass: HomeAssistant,
    setup: Setup,
    mock_client: ClientHarness,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A link drop while a failing read runs never publishes an empty snapshot."""
    client = FakeClient(command_result(refresh=False))
    coordinator = await setup(client)
    gate = asyncio.Event()
    client.read_gates = [gate]
    client.read_errors = [TimeoutError("read timed out")]
    task = hass.async_create_task(run_command(coordinator, "arm"))
    await settle()
    mock_client.emit_link(link_event(connected=False))
    await settle()
    gate.set()
    assert await task
    await settle()
    assert PanelSnapshot.empty() not in _pushes(coordinator)
    # Stop the reconnect loop the link drop started.
    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)


async def test_failed_reread_publishes_client_snapshot(
    hass: HomeAssistant, setup: Setup, mock_client: ClientHarness
) -> None:
    """If the re-read fails, the stale first read is never published."""
    client = FakeClient(command_result(refresh=False))
    coordinator = await setup(client)
    gate = asyncio.Event()
    client.read_gates = [gate, None]
    client.read_errors = [None, TimeoutError("read timed out")]
    task = hass.async_create_task(run_command(coordinator, "arm"))
    await settle()
    mock_client.emit(MagicMock(name="AreaStatusUpdated"))
    await settle()
    gate.set()
    assert await task
    await hass.async_block_till_done()
    assert client.keys(AREA_READ) == ["area_get_status"] * 2
    # A failed status read carries no reply, so only live client state is
    # published, never the first read's overlay.
    assert _pushes(coordinator)[-1] == client.snapshot
    assert not _published_read(coordinator)


async def test_push_during_reread_publishes_client_snapshot(
    hass: HomeAssistant, setup: Setup, mock_client: ClientHarness
) -> None:
    """A disarm push during an arm fallback's re-read is not overwritten."""
    client = FakeClient(command_result(refresh=False))
    coordinator = await setup(client)
    first, second = asyncio.Event(), asyncio.Event()
    client.read_gates = [first, second]
    task = hass.async_create_task(run_command(coordinator, "arm"))
    await settle()
    mock_client.emit(MagicMock(name="ZoneStatusUpdated"))
    await settle()
    first.set()
    await settle()
    assert client.keys(AREA_READ) == ["area_get_status"] * 2
    # The disarm lands while the re-read is in flight.
    mock_client.emit(MagicMock(name="AreaStatusUpdated"))
    await settle()
    second.set()
    assert await task
    await hass.async_block_till_done()
    assert _pushes(coordinator)[-1] is client.snapshot


async def test_unload_ignores_link_events_while_tasks_stop(
    hass: HomeAssistant,
    setup: Setup,
    mock_client: ClientHarness,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A link drop reported while unload waits on its tasks starts no reconnect."""
    client = FakeClient(command_result(refresh=False))
    coordinator = await setup(client)
    client.read_gate = asyncio.Event()

    async def _link_drop() -> None:
        mock_client.emit_link(link_event(connected=False))
        await settle()

    client.on_cancel = _link_drop
    task = hass.async_create_task(run_command(coordinator, "arm"))
    await settle()
    connects = mock_client.client.async_connect.call_count
    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    assert mock_client.client.async_connect.call_count == connects
