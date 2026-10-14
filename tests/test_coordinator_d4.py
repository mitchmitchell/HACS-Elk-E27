# ruff: noqa: S101, SLF001, PLR2004, FBT001, FBT003, D102, D107, TC001
"""D4: adopt elke27 status_refresh_ok; keep snapshot_with_status as fallback."""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from elke27_lib import ArmMode
from elke27_lib.client import Elke27Client, Result
from elke27_lib.errors import (
    E27PanelErrorResponse,
    Elke27PanelError,
    Elke27PinRequiredError,
)
import pytest

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from tests.conftest import make_coordinator, panel_snapshot

pytestmark = pytest.mark.real_writes

WRITES = {"area_set_arm_state", "zone_set_status"}
READS = {"area_get_status", "zone_get_all_zones_status"}


class FakeClient:
    """A client whose async_execute answers writes with a chosen Result."""

    # The real client's error mapping, so ok=False is raised as in elke27.
    _raise_v2_command_error = Elke27Client._raise_v2_command_error

    def __init__(self, write_result: Result[Any]) -> None:
        self._log = logging.getLogger("fake_elke27")
        self.write_result = write_result
        self.read_ok = True
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.snapshot = panel_snapshot()
        self.get_snapshot = MagicMock(side_effect=lambda: self.snapshot)
        self.async_execute = AsyncMock(side_effect=self._execute)
        self.read_gate: asyncio.Event | None = None

    async def _execute(self, command_key: str, **params: Any) -> Result[Any]:
        self.calls.append((command_key, params))
        if command_key in WRITES:
            return self.write_result
        if self.read_gate is not None:
            await self.read_gate.wait()
        if not self.read_ok:
            return Result.failure(TimeoutError("read timed out"))
        if command_key == "area_get_status":
            return Result.success({"arm_state": "DISARMED"})
        return Result.success({"zones": []})

    def keys(self, kind: set[str]) -> list[str]:
        return [key for key, _ in self.calls if key in kind]


def _ok(refresh: bool | None) -> Result[Any]:
    return Result(ok=True, data={}, error=None, status_refresh_ok=refresh)


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


async def test_write_payloads_match_public_methods(hass: HomeAssistant) -> None:
    """The writes send what elke27's public methods send (PIN as an int)."""
    client = FakeClient(_ok(True))
    coordinator = _coordinator(hass, client)
    await _run(coordinator, "arm")
    await _run(coordinator, "disarm")
    await _run(coordinator, "bypass")
    assert client.calls == [
        (
            "area_set_arm_state",
            {
                "area_id": 1,
                "arm_state": "ARMED_AWAY",
                "pin": 123,
                "auto_stay_cancel": False,
                "exit_delay_cancel": False,
            },
        ),
        (
            "area_set_arm_state",
            {
                "area_id": 1,
                "arm_state": "DISARMED",
                "pin": 1234,
                "auto_stay_cancel": False,
                "exit_delay_cancel": False,
            },
        ),
        (
            "zone_set_status",
            {"zone_id": 1, "pin": 1234, "bypassed": True, "timeout_s": 15.0},
        ),
    ]


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
    """ok=False: mapped panel error, no fallback, no push, no repeat."""
    err = E27PanelErrorResponse(
        command_key="area_set_arm_state", panel_error_code=11008
    )
    client = FakeClient(Result.failure(err))
    coordinator = _coordinator(hass, client)
    with pytest.raises(HomeAssistantError) as info:
        await _run(coordinator, "arm")
    assert isinstance(info.value.__cause__, Elke27PanelError)
    assert client.keys(READS) == []
    assert len(client.keys(WRITES)) == 1
    assert coordinator.fallback_refresh_count == 0
    coordinator.async_set_updated_data.assert_not_called()


async def test_pin_required_result_is_raised(hass: HomeAssistant) -> None:
    """A PIN-required Result keeps its own error type."""
    client = FakeClient(Result.failure(Elke27PinRequiredError("pin")))
    coordinator = _coordinator(hass, client)
    with pytest.raises(Elke27PinRequiredError):
        await _run(coordinator, "bypass")


async def test_failure_without_error(hass: HomeAssistant) -> None:
    """ok=False with no error: reported as a failed command."""
    client = FakeClient(Result(ok=False))
    coordinator = _coordinator(hass, client)
    with pytest.raises(HomeAssistantError, match="Failed to disarm area"):
        await _run(coordinator, "disarm")


async def test_all_zero_pin_rejected_before_send(hass: HomeAssistant) -> None:
    """An all-zero PIN is refused without sending, as elke27 does."""
    client = FakeClient(_ok(True))
    coordinator = _coordinator(hass, client)
    with pytest.raises(HomeAssistantError):
        await coordinator.async_arm_area(1, ArmMode.ARMED_AWAY, "0000")
    with pytest.raises(HomeAssistantError):
        await coordinator.async_disarm_area(0, "1234")
    assert client.calls == []


async def test_fallback_shares_q5_stale_refresh(hass: HomeAssistant) -> None:
    """A fallback while Q5's refresh runs waits on it instead of reading."""
    client = FakeClient(_ok(False))
    coordinator = _coordinator(hass, client)
    release = asyncio.Event()

    async def _stale() -> None:
        await release.wait()

    coordinator._stale_task = hass.async_create_task(_stale())
    task = hass.async_create_task(_run(coordinator, "arm"))
    await asyncio.sleep(0)
    release.set()
    assert await task
    assert client.keys(READS) == []
    assert coordinator.fallback_refresh_count == 1


async def test_concurrent_fallbacks_single_flight(hass: HomeAssistant) -> None:
    """Two fallbacks at once share one read."""
    client = FakeClient(_ok(False))
    client.read_gate = asyncio.Event()
    coordinator = _coordinator(hass, client)
    first = hass.async_create_task(_run(coordinator, "arm"))
    second = hass.async_create_task(_run(coordinator, "disarm"))
    for _ in range(5):
        await asyncio.sleep(0)
    client.read_gate.set()
    assert await first
    assert await second
    assert client.keys(READS) == ["area_get_status"]
    assert coordinator.fallback_refresh_count == 2


async def test_q5_skips_while_fallback_in_flight(hass: HomeAssistant) -> None:
    """Q5's stale check does not start a refresh during a fallback read."""
    client = FakeClient(_ok(True))
    coordinator = _coordinator(hass, client)
    coordinator.data = type("S", (), {"stale": True})()
    gate = asyncio.Event()
    coordinator._fallback_task = hass.async_create_task(gate.wait())
    coordinator._stale_since = 0.0
    coordinator._create_background_task = MagicMock()
    coordinator._async_check_stale(None)
    coordinator._create_background_task.assert_not_called()
    gate.set()
    await coordinator._fallback_task


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
