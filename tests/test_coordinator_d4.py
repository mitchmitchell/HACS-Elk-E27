# ruff: noqa: S101, SLF001, PLR2004, FBT001, FBT003, D102, D107, TC001
"""D4: adopt elke27 status_refresh_ok; keep snapshot_with_status as fallback."""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from elke27_lib import ArmMode, CommandResult
from elke27_lib.client import Result
from elke27_lib.errors import Elke27PanelError, Elke27PinRequiredError
import pytest

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from tests.conftest import make_coordinator, panel_snapshot

WRITES = {"arm", "disarm", "bypass"}
READS = {"area_get_status", "zone_get_all_zones_status"}


class FakeClient:
    """A client whose public arm/disarm/bypass return a chosen CommandResult."""

    def __init__(self, write_result: CommandResult | BaseException) -> None:
        self.write_result = write_result
        self.read_ok = True
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.snapshot = panel_snapshot()
        self.get_snapshot = MagicMock(side_effect=lambda: self.snapshot)
        self.async_execute = AsyncMock(side_effect=self._execute)
        self.read_gate: asyncio.Event | None = None

    async def _write(self, kind: str, **params: Any) -> CommandResult:
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

    async def _execute(self, command_key: str, **params: Any) -> Result[Any]:
        self.calls.append((command_key, params))
        if self.read_gate is not None:
            await self.read_gate.wait()
        if not self.read_ok:
            return Result.failure(TimeoutError("read timed out"))
        if command_key == "area_get_status":
            return Result.success({"arm_state": "DISARMED"})
        return Result.success({"zones": []})

    def keys(self, kind: set[str]) -> list[str]:
        return [key for key, _ in self.calls if key in kind]


def _ok(refresh: bool | None) -> CommandResult:
    return CommandResult(route="test", status_refresh_ok=refresh)


def _coordinator(hass: HomeAssistant, client: FakeClient) -> Any:
    coordinator = make_coordinator(hass)
    coordinator._client = client
    coordinator.async_set_updated_data = MagicMock()
    return coordinator


async def _run(coordinator: Any, kind: str) -> bool:
    if kind == "arm":
        return bool(await coordinator.async_arm_area(1, ArmMode.ARMED_AWAY, "0123"))
    if kind == "disarm":
        return bool(await coordinator.async_disarm_area(1, "1234"))
    return bool(await coordinator.async_set_zone_bypass(1, bypassed=True, pin="1234"))


@pytest.mark.parametrize("kind", ["arm", "disarm", "bypass"])
@pytest.mark.parametrize("refresh", [True, None])
async def test_refresh_ok_true_or_none_skips_fallback(
    hass: HomeAssistant, kind: str, refresh: bool | None
) -> None:
    """True/None: no extra status call, snapshot pushed once, no counter."""
    client = FakeClient(_ok(refresh))
    coordinator = _coordinator(hass, client)
    assert await _run(coordinator, kind)
    assert len(client.keys(WRITES)) == 1
    assert client.keys(READS) == []
    coordinator.async_set_updated_data.assert_called_once_with(client.snapshot)
    assert coordinator.fallback_refresh_count == 0
    assert coordinator.last_fallback_at is None


async def test_writes_use_public_methods_with_result(hass: HomeAssistant) -> None:
    """Arm, disarm and bypass call the public methods with return_result=True."""
    client = FakeClient(_ok(True))
    coordinator = _coordinator(hass, client)
    await _run(coordinator, "arm")
    await _run(coordinator, "disarm")
    await _run(coordinator, "bypass")
    assert client.calls == [
        (
            "arm",
            {
                "area_id": 1,
                "mode": ArmMode.ARMED_AWAY,
                "pin": "0123",
                "auto_stay_cancel": False,
                "exit_delay_cancel": False,
                "return_result": True,
            },
        ),
        (
            "disarm",
            {
                "area_id": 1,
                "pin": "1234",
                "auto_stay_cancel": False,
                "exit_delay_cancel": False,
                "return_result": True,
            },
        ),
        (
            "bypass",
            {"zone_id": 1, "bypassed": True, "pin": "1234", "return_result": True},
        ),
    ]
    client.async_execute.assert_not_called()


@pytest.mark.parametrize("kind", ["arm", "disarm"])
async def test_refresh_ok_false_one_area_fallback(
    hass: HomeAssistant, kind: str, caplog: pytest.LogCaptureFixture
) -> None:
    """False: exactly one area_get_status fallback, counter and time set."""
    caplog.set_level(logging.DEBUG)
    client = FakeClient(_ok(False))
    coordinator = _coordinator(hass, client)
    assert await _run(coordinator, kind)
    assert client.keys(READS) == ["area_get_status"]
    assert len(client.keys(WRITES)) == 1
    assert coordinator.fallback_refresh_count == 1
    assert coordinator.last_fallback_at is not None
    coordinator.async_set_updated_data.assert_called_once()
    assert "fallback read 1" in caplog.text


async def test_bypass_fallback_reads_area_and_zones(hass: HomeAssistant) -> None:
    """Bypass with False: area status plus all-zone status, once each."""
    client = FakeClient(_ok(False))
    coordinator = _coordinator(hass, client)
    assert await _run(coordinator, "bypass")
    assert client.keys(READS) == ["area_get_status", "zone_get_all_zones_status"]
    assert len(client.keys(WRITES)) == 1


async def test_bypass_fallback_zone_without_area(hass: HomeAssistant) -> None:
    """A zone with no known area still gets its zone status re-read."""
    client = FakeClient(_ok(False))
    coordinator = _coordinator(hass, client)
    assert await coordinator.async_set_zone_bypass(7, bypassed=True, pin="1234")
    assert client.keys(READS) == ["zone_get_all_zones_status"]


async def test_fallback_failure_pushes_snapshot(hass: HomeAssistant) -> None:
    """A failed fallback pushes the client snapshot; the command succeeds."""
    client = FakeClient(_ok(False))
    client.read_ok = False
    coordinator = _coordinator(hass, client)
    assert await _run(coordinator, "arm")
    assert coordinator.fallback_refresh_count == 1
    coordinator.async_set_updated_data.assert_called_once_with(client.snapshot)


async def test_fallback_exception_is_swallowed(hass: HomeAssistant) -> None:
    """A fallback that raises still pushes the snapshot."""
    client = FakeClient(_ok(False))
    coordinator = _coordinator(hass, client)
    coordinator.async_refresh_area_arm_status = AsyncMock(
        side_effect=TimeoutError("gone")
    )
    assert await _run(coordinator, "arm")
    coordinator.async_set_updated_data.assert_called_once_with(client.snapshot)


async def test_counter_accumulates(hass: HomeAssistant) -> None:
    """Each fallback hit counts; True results do not."""
    client = FakeClient(_ok(False))
    coordinator = _coordinator(hass, client)
    await _run(coordinator, "arm")
    await _run(coordinator, "bypass")
    client.write_result = _ok(True)
    await _run(coordinator, "disarm")
    assert coordinator.fallback_refresh_count == 2


async def test_command_failure_unchanged(hass: HomeAssistant) -> None:
    """A raised panel error: mapped, no fallback, no push, no repeat."""
    client = FakeClient(Elke27PanelError(11008, "rejected"))
    coordinator = _coordinator(hass, client)
    with pytest.raises(HomeAssistantError) as info:
        await _run(coordinator, "arm")
    assert isinstance(info.value.__cause__, Elke27PanelError)
    assert client.keys(READS) == []
    assert len(client.keys(WRITES)) == 1
    assert coordinator.fallback_refresh_count == 0
    coordinator.async_set_updated_data.assert_not_called()


async def test_pin_required_is_raised(hass: HomeAssistant) -> None:
    """A PIN-required error keeps its own type."""
    client = FakeClient(Elke27PinRequiredError("pin"))
    coordinator = _coordinator(hass, client)
    with pytest.raises(Elke27PinRequiredError):
        await _run(coordinator, "bypass")


async def test_result_without_status_is_no_reread(hass: HomeAssistant) -> None:
    """A write that returns no CommandResult (None) means no re-read."""
    client = FakeClient(_ok(True))
    client.write_result = None  # type: ignore[assignment]
    coordinator = _coordinator(hass, client)
    assert await _run(coordinator, "disarm")
    assert client.keys(READS) == []
    assert coordinator.fallback_refresh_count == 0


async def test_fallback_waits_for_q5_then_reads_fresh(hass: HomeAssistant) -> None:
    """A Q5 refresh in flight predates the write: wait for it, then read again."""
    client = FakeClient(_ok(False))
    coordinator = _coordinator(hass, client)
    release = asyncio.Event()

    async def _stale() -> None:
        await release.wait()

    coordinator._stale_task = hass.async_create_task(_stale())
    task = hass.async_create_task(_run(coordinator, "arm"))
    for _ in range(5):
        await asyncio.sleep(0)
    # Still waiting on the older refresh: no read of its own yet.
    assert client.keys(READS) == []
    assert not task.done()
    release.set()
    assert await task
    # The post-command read starts only after the stale refresh finished.
    assert client.keys(READS) == ["area_get_status"]
    assert coordinator.fallback_refresh_count == 1


async def test_concurrent_fallbacks_each_read_after_write(hass: HomeAssistant) -> None:
    """A second fallback never reuses a read that started before its write."""
    client = FakeClient(_ok(False))
    client.read_gate = asyncio.Event()
    coordinator = _coordinator(hass, client)
    first = hass.async_create_task(_run(coordinator, "arm"))
    second = hass.async_create_task(_run(coordinator, "disarm"))
    for _ in range(5):
        await asyncio.sleep(0)
    # One panel read at a time: the second waits for the first.
    assert client.keys(READS) == ["area_get_status"]
    client.read_gate.set()
    assert await first
    assert await second
    assert client.keys(READS) == ["area_get_status", "area_get_status"]
    assert coordinator.fallback_refresh_count == 2


@pytest.mark.parametrize("fallback_running", [True, False])
async def test_q5_skips_while_fallback_in_flight(
    hass: HomeAssistant, fallback_running: bool
) -> None:
    """Q5 skips during a fallback read and schedules once there is none."""
    client = FakeClient(_ok(True))
    client.is_ready = True
    client.snapshot = dataclasses.replace(panel_snapshot(), stale=True)
    coordinator = _coordinator(hass, client)
    gate = asyncio.Event()
    if fallback_running:
        coordinator._fallback_task = hass.async_create_task(gate.wait())
    coordinator._stale_since = 0.0
    coordinator._create_background_task = MagicMock()
    coordinator._async_check_stale(None)
    if fallback_running:
        coordinator._create_background_task.assert_not_called()
        gate.set()
        await coordinator._fallback_task
    else:
        coordinator._create_background_task.assert_called_once()
        coordinator._create_background_task.call_args.args[0].close()


async def test_stale_properties(hass: HomeAssistant) -> None:
    """snapshot.stale and stale ids are exposed read-only."""
    coordinator = make_coordinator(hass)
    assert coordinator.snapshot_stale is False
    assert coordinator.stale_area_ids == frozenset()
    snap = MagicMock(stale=True, stale_area_ids={2}, stale_zone_ids={5, 6})
    coordinator.data = snap
    assert coordinator.snapshot_stale is True
    assert coordinator.stale_area_ids == frozenset({2})
    assert coordinator.stale_zone_ids == frozenset({5, 6})
