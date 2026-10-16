# ruff: noqa: S101, ARG001, PLR2004, FBT001, FBT003, TC001
"""D4: adopt elke27 status_refresh_ok; keep snapshot_with_status as fallback."""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import re
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from elke27_lib import ArmMode
from elke27_lib.errors import Elke27PanelError, Elke27PinRequiredError
import pytest
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.elke27.coordinator import (
    STALE_CHECK_INTERVAL,
    STALE_REFRESH_AFTER,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from tests.conftest import (
    ClientHarness,
    FakeClient,
    Setup,
    command_result,
    connection_event,
    make_coordinator,
    run_command,
    settle,
)

if TYPE_CHECKING:
    from freezegun.api import FrozenDateTimeFactory

ALARM = "alarm_control_panel.house"

WRITES = {"arm", "disarm", "bypass"}
READS = {"area_get_status", "zone_get_all_zones_status"}


async def _tick_stale_timer(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """Advance past STALE_REFRESH_AFTER in STALE_CHECK_INTERVAL steps (Q5 timer)."""
    steps = int(STALE_REFRESH_AFTER / STALE_CHECK_INTERVAL.total_seconds()) + 2
    for _ in range(steps):
        freezer.tick(STALE_CHECK_INTERVAL)
        async_fire_time_changed(hass)
        await settle()


@pytest.mark.parametrize("kind", ["arm", "disarm", "bypass"])
@pytest.mark.parametrize("refresh", [True, None])
async def test_refresh_ok_true_or_none_skips_fallback(
    hass: HomeAssistant, setup: Setup, kind: str, refresh: bool | None
) -> None:
    """True/None: no extra status call, snapshot pushed once, no counter."""
    client = FakeClient(command_result(refresh))
    coordinator = await setup(client)
    assert await run_command(coordinator, kind)
    assert len(client.keys(WRITES)) == 1
    assert client.keys(READS) == []
    coordinator.async_set_updated_data.assert_called_once_with(client.snapshot)
    assert coordinator.fallback_refresh_count == 0
    assert coordinator.last_fallback_at is None


async def test_writes_use_public_methods_with_result(
    hass: HomeAssistant, setup: Setup
) -> None:
    """Arm, disarm and bypass call the public methods with return_result=True."""
    client = FakeClient(command_result(True))
    coordinator = await setup(client)
    await run_command(coordinator, "arm")
    await run_command(coordinator, "disarm")
    await run_command(coordinator, "bypass")
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
    hass: HomeAssistant, setup: Setup, kind: str, caplog: pytest.LogCaptureFixture
) -> None:
    """False: exactly one area_get_status fallback, counter and time set."""
    caplog.set_level(logging.DEBUG)
    client = FakeClient(command_result(False))
    coordinator = await setup(client)
    assert await run_command(coordinator, kind)
    assert client.keys(READS) == ["area_get_status"]
    assert len(client.keys(WRITES)) == 1
    assert coordinator.fallback_refresh_count == 1
    assert coordinator.last_fallback_at is not None
    coordinator.async_set_updated_data.assert_called_once()
    assert "fallback read 1" in caplog.text


async def test_bypass_fallback_reads_area_and_zones(
    hass: HomeAssistant, setup: Setup
) -> None:
    """Bypass with False: area status plus all-zone status, once each."""
    client = FakeClient(command_result(False))
    coordinator = await setup(client)
    assert await run_command(coordinator, "bypass")
    assert client.keys(READS) == ["area_get_status", "zone_get_all_zones_status"]
    assert len(client.keys(WRITES)) == 1


async def test_bypass_fallback_zone_without_area(
    hass: HomeAssistant, setup: Setup
) -> None:
    """A zone with no known area still gets its zone status re-read."""
    client = FakeClient(command_result(False))
    coordinator = await setup(client)
    assert await coordinator.async_set_zone_bypass(7, bypassed=True, pin="1234")
    assert client.keys(READS) == ["zone_get_all_zones_status"]


async def test_fallback_failure_pushes_snapshot(
    hass: HomeAssistant, setup: Setup
) -> None:
    """A failed fallback pushes the client snapshot; the command succeeds."""
    client = FakeClient(command_result(False))
    client.read_ok = False
    coordinator = await setup(client)
    assert await run_command(coordinator, "arm")
    assert coordinator.fallback_refresh_count == 1
    coordinator.async_set_updated_data.assert_called_once_with(client.snapshot)


async def test_fallback_exception_is_swallowed(
    hass: HomeAssistant, setup: Setup
) -> None:
    """A fallback that raises still pushes the snapshot."""
    client = FakeClient(command_result(False))
    coordinator = await setup(client)
    client.async_execute.side_effect = TimeoutError("gone")
    assert await run_command(coordinator, "arm")
    coordinator.async_set_updated_data.assert_called_once_with(client.snapshot)


async def test_counter_accumulates(hass: HomeAssistant, setup: Setup) -> None:
    """Each fallback hit counts; True results do not."""
    client = FakeClient(command_result(False))
    coordinator = await setup(client)
    await run_command(coordinator, "arm")
    await run_command(coordinator, "bypass")
    client.write_result = command_result(True)
    await run_command(coordinator, "disarm")
    assert coordinator.fallback_refresh_count == 2


@pytest.mark.parametrize("refresh", [True, False])
async def test_after_write_logs_status_refreshcommand_result(
    hass: HomeAssistant,
    setup: Setup,
    caplog: pytest.LogCaptureFixture,
    refresh: bool,
) -> None:
    """Every write logs kind, area/zone and status_refresh_ok at DEBUG."""
    coordinator = await setup(FakeClient(command_result(refresh)))
    caplog.set_level(logging.DEBUG)
    assert await run_command(coordinator, "bypass")
    assert re.search(
        rf"After \S+ write \(area=\S+ zone=1\): status_refresh_ok={refresh}",
        caplog.text,
    )


async def test_command_failure_unchanged(hass: HomeAssistant, setup: Setup) -> None:
    """A raised panel error: mapped, no fallback, no push, no repeat."""
    client = FakeClient(Elke27PanelError(11008, "rejected"))
    coordinator = await setup(client)
    with pytest.raises(HomeAssistantError) as info:
        await run_command(coordinator, "arm")
    assert isinstance(info.value.__cause__, Elke27PanelError)
    assert client.keys(READS) == []
    assert len(client.keys(WRITES)) == 1
    assert coordinator.fallback_refresh_count == 0
    coordinator.async_set_updated_data.assert_not_called()


async def test_pin_required_is_raised(hass: HomeAssistant, setup: Setup) -> None:
    """A PIN-required error keeps its own type."""
    client = FakeClient(Elke27PinRequiredError("pin"))
    coordinator = await setup(client)
    with pytest.raises(Elke27PinRequiredError):
        await run_command(coordinator, "bypass")


async def test_result_without_status_is_no_reread(
    hass: HomeAssistant, setup: Setup
) -> None:
    """A CommandResult with status_refresh_ok None means no re-read."""
    client = FakeClient(command_result(None))
    coordinator = await setup(client)
    assert await run_command(coordinator, "disarm")
    assert client.keys(READS) == []
    assert coordinator.fallback_refresh_count == 0


async def test_fallback_waits_for_q5_then_reads_fresh(
    hass: HomeAssistant, setup: Setup, freezer: FrozenDateTimeFactory
) -> None:
    """A Q5 refresh in flight predates the write: wait for it, then read again."""
    client = FakeClient(command_result(False))
    coordinator = await setup(client)
    client.csm_gate = asyncio.Event()
    client.snapshot = dataclasses.replace(client.snapshot, stale=True)
    await _tick_stale_timer(hass, freezer)
    assert client.keys({"csm"}) == ["csm"]
    task = hass.async_create_task(run_command(coordinator, "arm"))
    await settle()
    # Still waiting on the older refresh: no read of its own yet.
    assert client.keys(READS) == []
    assert not task.done()
    client.csm_gate.set()
    assert await task
    # The post-command read starts only after the stale refresh finished.
    assert client.keys(READS) == ["area_get_status"]
    assert client.max_reads_in_flight == 1
    assert coordinator.fallback_refresh_count == 1


async def test_fallback_through_alarm_services(
    hass: HomeAssistant, setup: Setup
) -> None:
    """Arm and disarm service calls each get one post-command fallback read."""
    client = FakeClient(command_result(False))
    coordinator = await setup(client)
    await hass.services.async_call(
        "alarm_control_panel",
        "alarm_arm_away",
        {"entity_id": ALARM, "code": "0123"},
        blocking=True,
    )
    await hass.services.async_call(
        "alarm_control_panel",
        "alarm_disarm",
        {"entity_id": ALARM, "code": "1234"},
        blocking=True,
    )
    assert client.keys(WRITES) == ["arm", "disarm"]
    assert client.keys(READS) == ["area_get_status", "area_get_status"]
    assert coordinator.fallback_refresh_count == 2


async def test_three_concurrent_fallbacks_never_overlap(
    hass: HomeAssistant, setup: Setup
) -> None:
    """Commands queued on the same in-flight read never read concurrently."""
    client = FakeClient(command_result(False))
    coordinator = await setup(client)
    client.read_gate = asyncio.Event()
    tasks = [
        hass.async_create_task(run_command(coordinator, kind))
        for kind in ("arm", "disarm", "arm")
    ]
    await settle()
    # Each write went out; only the first fallback read has started.
    assert len(client.keys(WRITES)) == 3
    assert client.keys(READS) == ["area_get_status"]
    client.read_gate.set()
    for task in tasks:
        assert await task
    # Each command got its own read, started after its write, one at a time.
    assert client.keys(READS) == ["area_get_status"] * 3
    assert client.max_reads_in_flight == 1
    assert coordinator.fallback_refresh_count == 3


async def test_q5_waits_for_all_fallback_reads(
    hass: HomeAssistant, setup: Setup, freezer: FrozenDateTimeFactory
) -> None:
    """The stale timer never starts Q5 while any fallback read runs or waits."""
    client = FakeClient(command_result(False))
    coordinator = await setup(client)
    client.read_gate = asyncio.Event()
    client.snapshot = dataclasses.replace(client.snapshot, stale=True)
    tasks = [
        hass.async_create_task(run_command(coordinator, kind))
        for kind in ("arm", "disarm", "arm")
    ]
    await settle()
    await _tick_stale_timer(hass, freezer)
    # One fallback read running, two queued: Q5 stays off.
    assert client.keys({"csm"}) == []
    client.read_gate.set()
    # Keep the stale timer firing while the queued reads run one by one.
    # Bounded, so a regression fails here instead of hanging the test.
    for _ in range(50):
        if all(task.done() for task in tasks):
            break
        freezer.tick(STALE_CHECK_INTERVAL)
        async_fire_time_changed(hass)
        await asyncio.sleep(0)
    else:
        pytest.fail("fallback reads did not finish within 50 stale ticks")
    for task in tasks:
        assert await task
    await _tick_stale_timer(hass, freezer)
    # Q5 ran only once every fallback read was done, and never overlapped one.
    order = client.keys(READS | {"csm"})
    assert order[:3] == ["area_get_status"] * 3
    assert order[3:]
    assert set(order[3:]) == {"csm"}
    assert client.max_reads_in_flight == 1


async def test_fallback_from_before_disconnect_is_discarded(
    hass: HomeAssistant, setup: Setup, mock_client: ClientHarness
) -> None:
    """A fallback read that spans a disconnect never pushes its result."""
    client = FakeClient(command_result(False))
    coordinator = await setup(client)
    client.read_gate = asyncio.Event()
    task = hass.async_create_task(run_command(coordinator, "arm"))
    await settle()
    assert client.keys(READS) == ["area_get_status"]
    # The link drops and comes back while the fallback read is in flight.
    mock_client.emit(connection_event(connected=False))
    mock_client.emit(connection_event(connected=True))
    await settle()
    pushes = coordinator.async_set_updated_data.call_count
    client.read_gate.set()
    assert await task
    await settle()
    assert coordinator.async_set_updated_data.call_count == pushes


async def test_fallback_waits_for_reconnect_refresh(
    hass: HomeAssistant, setup: Setup, mock_client: ClientHarness
) -> None:
    """A fallback read never overlaps the post-reconnect CSM refresh."""
    client = FakeClient(command_result(False))
    coordinator = await setup(client)
    client.csm_gate = asyncio.Event()
    mock_client.emit(connection_event(connected=True))
    await settle()
    assert client.keys({"csm"}) == ["csm"]
    task = hass.async_create_task(run_command(coordinator, "arm"))
    await settle()
    # The reconnect refresh is still running, so the fallback read waits.
    assert client.keys(READS) == []
    client.csm_gate.set()
    assert await task
    assert client.keys(READS | {"csm"}) == ["csm", "area_get_status"]
    assert client.max_reads_in_flight == 1


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
