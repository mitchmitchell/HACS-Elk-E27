# ruff: noqa: S101, SLF001, ARG001, FBT003, TC001, TC002, F401, F811
"""Fallback-read races found in review after D4 (Bugbot/Codex on #63 and #66)."""

from __future__ import annotations

import asyncio
import dataclasses
from typing import Any
from unittest.mock import MagicMock

from elke27_lib import ArmMode, PanelSnapshot
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.core import HomeAssistant
from tests.conftest import ClientHarness, connection_event
from tests.test_coordinator_d4 import FakeClient, Setup, _ok, _run, _settle, setup


def _pushes(coordinator: Any) -> list[Any]:
    return [c.args[0] for c in coordinator.async_set_updated_data.call_args_list]


async def test_event_during_read_triggers_one_reread(
    hass: HomeAssistant, setup: Setup, mock_client: ClientHarness
) -> None:
    """A push while the read runs (fix 4 overlap) re-reads once, then publishes."""
    client = FakeClient(_ok(False))
    coordinator = await setup(client)
    client.read_gate = asyncio.Event()
    task = hass.async_create_task(_run(coordinator, "arm"))
    await _settle()
    assert client.keys({"area_get_status"}) == ["area_get_status"]
    # The reconnect refresh (or any panel update) pushes while the read runs.
    mock_client.emit(connection_event(connected=True))
    await _settle()
    client.read_gate.set()
    assert await task
    await hass.async_block_till_done()
    assert client.keys({"area_get_status"}) == ["area_get_status"] * 2
    assert _pushes(coordinator)[-1].areas[1].arm_mode == ArmMode.DISARMED


async def test_unrelated_event_during_write_publishes_read(
    hass: HomeAssistant, setup: Setup, mock_client: ClientHarness
) -> None:
    """An unrelated zone event during the write does not drop the read result."""
    client = FakeClient(_ok(False))
    coordinator = await setup(client)
    write = client._write

    async def _write(kind: str, **params: Any) -> Any:
        mock_client.emit(MagicMock(name="ZoneStatusUpdated"))
        await _settle()
        return await write(kind, **params)

    client._write = _write  # type: ignore[method-assign]
    assert await _run(coordinator, "arm")
    await hass.async_block_till_done()
    assert client.keys({"area_get_status"}) == ["area_get_status"]
    assert _pushes(coordinator)[-1].areas[1].arm_mode == ArmMode.DISARMED


async def test_read_after_awaited_refresh_publishes(
    hass: HomeAssistant, setup: Setup, mock_client: ClientHarness
) -> None:
    """A fallback that waited on the reconnect refresh still publishes its read."""
    client = FakeClient(_ok(False))
    coordinator = await setup(client)
    client.csm_gate = asyncio.Event()
    mock_client.emit(connection_event(connected=True))
    await _settle()
    assert client.keys({"csm"}) == ["csm"]
    task = hass.async_create_task(_run(coordinator, "arm"))
    await _settle()
    assert client.keys({"area_get_status"}) == []
    client.csm_gate.set()
    assert await task
    await hass.async_block_till_done()
    assert client.keys({"area_get_status"}) == ["area_get_status"]
    assert _pushes(coordinator)[-1].areas[1].arm_mode == ArmMode.DISARMED


async def test_epoch_taken_before_write(
    hass: HomeAssistant, setup: Setup, mock_client: ClientHarness
) -> None:
    """A reconnect during the write itself discards the fallback result."""
    client = FakeClient(_ok(False))
    coordinator = await setup(client)
    write = client._write

    async def _write(kind: str, **params: Any) -> Any:
        mock_client.emit(connection_event(connected=False))
        mock_client.emit(connection_event(connected=True))
        await _settle()
        return await write(kind, **params)

    client._write = _write  # type: ignore[method-assign]
    await hass.async_block_till_done()
    coordinator.async_set_updated_data.reset_mock()
    assert await _run(coordinator, "arm")
    await hass.async_block_till_done()
    for pushed in _pushes(coordinator):
        assert pushed is client.snapshot


async def test_disconnect_during_fallback_never_publishes_empty(
    hass: HomeAssistant, setup: Setup
) -> None:
    """_async_disconnect clearing the client mid-read does not wipe state."""
    client = FakeClient(_ok(False))
    coordinator = await setup(client)
    gate = asyncio.Event()

    async def _failing_read(area_id: int) -> Any:
        await gate.wait()
        raise TimeoutError

    coordinator.async_refresh_area_arm_status = _failing_read
    task = hass.async_create_task(_run(coordinator, "arm"))
    await _settle()
    # _async_disconnect clears the client without a disconnect event.
    coordinator._client = None
    gate.set()
    await task
    assert PanelSnapshot.empty() not in _pushes(coordinator)


async def test_shutdown_sets_stopping_before_waiting(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: ClientHarness
) -> None:
    """While shutdown waits on cancelled tasks, _stopping is already set."""
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    coordinator = mock_config_entry.runtime_data.coordinator
    seen: list[bool] = []

    async def _slow() -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            seen.append(coordinator._stopping)
            raise

    coordinator._debounce_task = hass.async_create_task(_slow())
    await _settle()
    await coordinator.async_shutdown_client()
    assert seen == [True]


async def test_failed_reread_publishes_client_snapshot(
    hass: HomeAssistant, setup: Setup, mock_client: ClientHarness
) -> None:
    """If the re-read fails, the stale first read is never published."""
    client = FakeClient(_ok(False))
    coordinator = await setup(client)
    gate = asyncio.Event()
    reads: list[int] = []
    stale = dataclasses.replace(client.snapshot)

    async def _status(*, area_id: int | None, zone_id: int | None) -> Any:
        reads.append(1)
        if len(reads) == 1:
            await gate.wait()
            return stale
        raise TimeoutError

    coordinator._async_fallback_status = _status
    task = hass.async_create_task(_run(coordinator, "arm"))
    await _settle()
    newer = dataclasses.replace(client.snapshot)
    client.snapshot = newer
    mock_client.emit(MagicMock(name="AreaStatusUpdated"))
    await _settle()
    gate.set()
    assert await task
    await hass.async_block_till_done()
    assert reads == [1, 1]
    assert _pushes(coordinator)[-1] is newer
