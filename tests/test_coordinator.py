# ruff: noqa: S101, SLF001, TC001, TC002, PLR2004
"""Tests for the Elke27 coordinator: disconnect updates and entry-scoped tasks."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch

from elke27_lib.errors import (
    Elke27AuthError,
    Elke27ConnectionError,
    Elke27ProtocolError,
)
from elke27_lib.events import DomainCsmChanged
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.elke27.const import DOMAIN
from homeassistant.config_entries import SOURCE_REAUTH
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from tests.conftest import ClientHarness, connection_event, link_event

if TYPE_CHECKING:
    from collections.abc import Callable

ALARM = "alarm_control_panel.house"


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_disconnect_event_updates_entities(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: ClientHarness
) -> None:
    """A disconnect event makes entities unavailable right away."""
    await _setup(hass, mock_config_entry)
    assert hass.states.get(ALARM).state != STATE_UNAVAILABLE

    mock_client.client.is_ready = False
    mock_client.emit(connection_event(connected=False))
    await hass.async_block_till_done()
    assert hass.states.get(ALARM).state == STATE_UNAVAILABLE


async def test_connect_event_refreshes_in_entry_task(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: ClientHarness
) -> None:
    """A connect event refreshes the CSM; a refresh failure does not escape."""
    await _setup(hass, mock_config_entry)
    mock_client.client.async_refresh_csm.reset_mock()
    mock_client.client.async_refresh_csm.side_effect = None

    mock_client.emit(connection_event(connected=True))
    await hass.async_block_till_done()
    mock_client.client.async_refresh_csm.assert_awaited_once()


async def test_pending_debounce_cancelled_on_unload(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: ClientHarness
) -> None:
    """A debounced domain refresh is tied to the entry and cleaned up on unload."""
    await _setup(hass, mock_config_entry)
    coordinator = mock_config_entry.runtime_data.coordinator
    coordinator._debounce_seconds = 3600
    mock_client.client.async_refresh_domain_config = AsyncMock()

    mock_client.emit(
        DomainCsmChanged(
            kind="csm",
            at=0.0,
            seq=None,
            classification="BROADCAST",
            route=("zone", "csm"),
            session_id=None,
            csm_domain="zone",
            old=None,
            new=1,
        )
    )
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    task = coordinator._debounce_task
    assert task is not None
    assert task in mock_config_entry._background_tasks

    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert task.done()
    mock_client.client.async_refresh_domain_config.assert_not_awaited()


async def test_auto_reconnect_pushes_update_without_connected_event(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: ClientHarness
) -> None:
    """After an automatic reconnect, entities recover even if no event arrives."""
    await _setup(hass, mock_config_entry)
    hub = mock_config_entry.runtime_data.coordinator
    mock_client.client.is_ready = False
    mock_client.emit(connection_event(connected=False))
    await hass.async_block_till_done()
    assert hass.states.get(ALARM).state == STATE_UNAVAILABLE

    # The reconnect succeeds, but the connected event fired before callbacks
    # were re-attached, so nothing is emitted to the coordinator.
    mock_client.client.is_ready = True
    mock_client.client.async_refresh_csm.reset_mock()
    hub._stopping = False
    await hub._reconnect._async_run()
    await hass.async_block_till_done()
    assert hass.states.get(ALARM).state != STATE_UNAVAILABLE
    mock_client.client.async_refresh_csm.assert_awaited_once()


def _domain_event(domain: str) -> DomainCsmChanged:
    return DomainCsmChanged(
        kind="csm",
        at=0.0,
        seq=None,
        classification="BROADCAST",
        route=(domain, "csm"),
        session_id=None,
        csm_domain=domain,
        old=None,
        new=1,
    )


async def test_reconnect_and_connected_event_refresh_once(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: ClientHarness
) -> None:
    """The reconnect listener and a connected event share one refresh."""
    await _setup(hass, mock_config_entry)
    coordinator = mock_config_entry.runtime_data.coordinator
    release = asyncio.Event()

    async def _slow_refresh() -> None:
        await release.wait()

    mock_client.client.async_refresh_csm.reset_mock()
    mock_client.client.async_refresh_csm.side_effect = _slow_refresh

    coordinator._handle_reconnected()
    mock_client.emit(connection_event(connected=True))
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    task = coordinator._refresh_task
    assert task is not None
    assert task in mock_config_entry._background_tasks
    release.set()
    await task
    mock_client.client.async_refresh_csm.assert_awaited_once()


async def test_debounced_refresh_stops_when_client_disconnects(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: ClientHarness
) -> None:
    """A debounced refresh stops cleanly when the hub has no client."""
    await _setup(hass, mock_config_entry)
    coordinator = mock_config_entry.runtime_data.coordinator
    hub = mock_config_entry.runtime_data.coordinator
    coordinator._debounce_seconds = 0
    hub._client = None
    refresh = AsyncMock()
    mock_client.client.async_refresh_domain_config = refresh

    mock_client.emit(_domain_event("light"))
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    task = coordinator._debounce_task
    assert task is not None
    await task
    refresh.assert_not_awaited()
    assert coordinator._pending_domains == set()


def _reauth_flows(hass: HomeAssistant) -> list[dict]:
    return [
        flow
        for flow in hass.config_entries.flow.async_progress()
        if flow["handler"] == DOMAIN and flow["context"]["source"] == SOURCE_REAUTH
    ]


async def test_refresh_after_connect_auth_error_starts_reauth_once(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: ClientHarness
) -> None:
    """An auth failure after reconnect starts reauth once, not a dead-link retry."""
    await _setup(hass, mock_config_entry)
    coordinator = mock_config_entry.runtime_data.coordinator
    hub = mock_config_entry.runtime_data.coordinator
    mock_client.client.async_refresh_csm.side_effect = Elke27AuthError("bad")

    coordinator._handle_reconnected()
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    task = coordinator._refresh_task
    assert task is not None
    await task
    await hass.async_block_till_done()
    assert len(_reauth_flows(hass)) == 1
    assert hub._reconnect.reauth_requested is True

    await coordinator._async_refresh_after_connect()
    await hass.async_block_till_done()
    assert len(_reauth_flows(hass)) == 1


async def test_debounced_refresh_auth_error_starts_reauth_once(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: ClientHarness
) -> None:
    """An auth failure during debounced refresh starts reauth only once."""
    await _setup(hass, mock_config_entry)
    coordinator = mock_config_entry.runtime_data.coordinator
    coordinator._debounce_seconds = 0
    mock_client.client.async_refresh_domain_config = AsyncMock(
        side_effect=Elke27AuthError("bad")
    )

    mock_client.emit(_domain_event("light"))
    mock_client.emit(_domain_event("zone"))
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    task = coordinator._debounce_task
    assert task is not None
    await task
    await hass.async_block_till_done()
    assert len(_reauth_flows(hass)) == 1
    assert (
        mock_config_entry.runtime_data.coordinator._reconnect.reauth_requested is True
    )

    mock_client.emit(_domain_event("lock"))
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    second = coordinator._debounce_task
    assert second is not None
    await second
    await hass.async_block_till_done()
    assert len(_reauth_flows(hass)) == 1


async def test_debounced_refresh_stops_on_connection_error(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: ClientHarness
) -> None:
    """A connection error stops the domain refresh and drops the queue."""
    await _setup(hass, mock_config_entry)
    coordinator = mock_config_entry.runtime_data.coordinator
    coordinator._debounce_seconds = 0
    refresh = AsyncMock(side_effect=Elke27ConnectionError("down"))
    mock_client.client.async_refresh_domain_config = refresh

    mock_client.emit(_domain_event("light"))
    mock_client.emit(_domain_event("zone"))
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    task = coordinator._debounce_task
    assert task is not None
    await task
    assert refresh.await_count == 1
    assert coordinator._pending_domains == set()


async def test_debounced_refresh_continues_after_rejection(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: ClientHarness
) -> None:
    """A refused domain refresh is logged and the other domains still refresh."""
    await _setup(hass, mock_config_entry)
    coordinator = mock_config_entry.runtime_data.coordinator
    coordinator._debounce_seconds = 0
    refresh = AsyncMock(side_effect=[Elke27ProtocolError("refused"), None])
    mock_client.client.async_refresh_domain_config = refresh

    mock_client.emit(_domain_event("light"))
    mock_client.emit(_domain_event("zone"))
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    task = coordinator._debounce_task
    assert task is not None
    await task
    assert refresh.await_count == 2


async def test_full_refresh_waits_for_domain_refresh(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: ClientHarness
) -> None:
    """A full refresh and a domain refresh never overlap."""
    await _setup(hass, mock_config_entry)
    coordinator = mock_config_entry.runtime_data.coordinator
    release = asyncio.Event()
    order: list[str] = []

    async def _slow_domain(domain: str) -> None:
        order.append(f"domain {domain} start")
        await release.wait()
        order.append(f"domain {domain} end")

    async def _full() -> None:
        order.append("full")

    mock_client.client.async_refresh_domain_config = AsyncMock(side_effect=_slow_domain)
    mock_client.client.async_refresh_csm.side_effect = _full
    domain_task = hass.async_create_task(coordinator.async_refresh_domains(["light"]))
    await asyncio.sleep(0)
    full_task = hass.async_create_task(coordinator.async_refresh_now())
    await asyncio.sleep(0)
    release.set()
    await domain_task
    await full_task
    assert order == ["domain light start", "domain light end", "full"]


# --- #97: entities go unavailable on a link-only drop ---------------------

_REAL_SLEEP = asyncio.sleep
ZONE = "binary_sensor.house_front_door"


async def _settle() -> None:
    for _ in range(50):
        await _REAL_SLEEP(0)


def _supervisor_held() -> Any:
    """
    Keep the reconnect supervisor from starting (it would replace the client).

    A raw READY can only end a drop while the dropped client is still the
    coordinator's client: once the supervisor's first attempt runs, the old
    client is torn down (and a failed attempt leaves no client during the
    backoff), so its READY is ignored. Holding the supervisor in its backoff
    would therefore never reach the raw-READY path.
    """
    return patch("custom_components.elke27.reconnect.ReconnectSupervisor.start")


def _block_connect(mock_client: ClientHarness) -> None:
    """Make the supervisor's reconnects hang (panel unreachable)."""

    async def _connect_blocks(*_args: object, **_kwargs: object) -> None:
        await asyncio.Event().wait()

    mock_client.client.async_connect.side_effect = _connect_blocks


async def _setup_blocked(
    hass: HomeAssistant, entry: MockConfigEntry, mock_client: ClientHarness
) -> None:
    """Load the entry; later reconnects block (the panel stays unreachable)."""
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(ALARM).state == "disarmed"

    _block_connect(mock_client)


async def _setup_and_drop(
    hass: HomeAssistant, entry: MockConfigEntry, mock_client: ClientHarness
) -> None:
    """Load the entry, then drop the link with only a raw DISCONNECTED event."""
    await _setup_blocked(hass, entry, mock_client)
    mock_client.client.is_ready = False
    mock_client.emit_link(link_event(connected=False))
    await _settle()


@pytest.mark.parametrize("entity_id", [ALARM, ZONE])
@pytest.mark.usefixtures("fast_backoff")
async def test_entities_unavailable_right_after_link_drop(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: ClientHarness,
    entity_id: str,
) -> None:
    """After a link-only drop, entities go unavailable with no refresh (#97)."""
    await _setup_and_drop(hass, mock_config_entry, mock_client)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == STATE_UNAVAILABLE
    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)


@pytest.mark.parametrize("first", ["link", "typed"])
@pytest.mark.usefixtures("fast_backoff")
async def test_link_and_typed_disconnect_is_one_listener_update(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: ClientHarness,
    first: str,
) -> None:
    """Both disconnect paths for one drop give exactly one listener update."""
    await _setup_blocked(hass, mock_config_entry, mock_client)
    coordinator = mock_config_entry.runtime_data.coordinator
    calls: list[None] = []
    remove = coordinator.async_add_listener(lambda: calls.append(None))
    mock_client.client.is_ready = False
    if first == "link":
        mock_client.emit_link(link_event(connected=False))
        mock_client.emit(connection_event(connected=False))
    else:
        mock_client.emit(connection_event(connected=False))
        mock_client.emit_link(link_event(connected=False))
    await _settle()
    remove()
    assert len(calls) == 1
    assert hass.states.get(ALARM).state == STATE_UNAVAILABLE
    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)


@pytest.mark.usefixtures("fast_backoff")
async def test_queued_push_skipped_when_link_ready_again(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: ClientHarness,
) -> None:
    """A queued link push does nothing if the client is ready by the time it runs."""
    await _setup_blocked(hass, mock_config_entry, mock_client)
    coordinator = mock_config_entry.runtime_data.coordinator
    calls: list[None] = []
    remove = coordinator.async_add_listener(lambda: calls.append(None))
    # is_ready stays True: the queued push hits the is_ready guard.
    mock_client.emit_link(link_event(connected=False))
    await _settle()
    remove()
    assert calls == []
    assert hass.states.get(ALARM).state == "disarmed"
    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)


@pytest.mark.usefixtures("fast_backoff")
async def test_push_queued_before_a_recovery_and_new_drop_is_superseded(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: ClientHarness
) -> None:
    """
    Drop, typed recovery, drop again before the loop runs: one push per drop.

    The first drop's queued push belongs to an older epoch by the time it
    runs, so only the second drop's push and the recovery refresh update
    listeners (an unguarded stale push would add a third).
    """
    await _setup_blocked(hass, mock_config_entry, mock_client)
    coordinator = mock_config_entry.runtime_data.coordinator
    calls: list[None] = []
    remove = coordinator.async_add_listener(lambda: calls.append(None))
    mock_client.client.is_ready = False
    live = _live_link_listener(mock_client)
    # Keep the supervisor out (it would replace the client mid-test).
    with _supervisor_held():
        live(link_event(connected=False))
        mock_client.emit(connection_event(connected=True))
        live(link_event(connected=False))
        await _settle()
        await hass.async_block_till_done()
    remove()
    assert len(calls) == 2  # second drop's push + the recovery refresh push
    assert hass.states.get(ALARM).state == STATE_UNAVAILABLE
    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)


@pytest.mark.usefixtures("fast_backoff")
async def test_warning_once_per_drop_with_push(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: ClientHarness,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Two disconnect events in one drop still log one WARNING (#83)."""
    caplog.set_level(logging.DEBUG, logger="custom_components.elke27")
    await _setup_and_drop(hass, mock_config_entry, mock_client)
    mock_client.emit(connection_event(connected=False))
    mock_client.emit_link(link_event(connected=False))
    await _settle()
    lost = [
        r
        for r in caplog.records
        if r.name == "custom_components.elke27.coordinator"
        and r.getMessage() == "Panel connection lost"
    ]
    assert [r.levelno for r in lost] == [logging.WARNING]
    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)


def _live_link_listener(mock_client: ClientHarness) -> Callable[[Any], None]:
    """Return the most recent client.subscribe listener (the live session)."""
    return mock_client.client.subscribe.call_args.args[0]


@pytest.mark.usefixtures("fast_backoff")
async def test_second_drop_after_supervisor_reconnect_goes_unavailable(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: ClientHarness
) -> None:
    """Drop, supervisor reconnect (_async_connect), drop again: unavailable (#97)."""
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    # First drop; the supervisor's first attempt reconnects at once.

    async def _connect_ok(*_args: object, **_kwargs: object) -> None:
        mock_client.client.is_ready = True

    mock_client.client.async_connect.side_effect = _connect_ok
    mock_client.client.is_ready = False
    _live_link_listener(mock_client)(link_event(connected=False))
    await _settle()
    await hass.async_block_till_done()
    assert hass.states.get(ALARM).state == "disarmed"
    # Second drop with the panel unreachable. The reconnect unsubscribed the
    # first listener, so only the one it registered fires.
    _block_connect(mock_client)
    mock_client.client.is_ready = False
    _live_link_listener(mock_client)(link_event(connected=False))
    await _settle()
    assert hass.states.get(ALARM).state == STATE_UNAVAILABLE
    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)


@pytest.mark.usefixtures("fast_backoff")
async def test_second_drop_after_typed_connected_goes_unavailable(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: ClientHarness
) -> None:
    """Typed drop, typed connected=True, typed drop: unavailable again (#97)."""
    await _setup_blocked(hass, mock_config_entry, mock_client)
    mock_client.client.is_ready = False
    mock_client.emit(connection_event(connected=False))
    await _settle()
    assert hass.states.get(ALARM).state == STATE_UNAVAILABLE
    mock_client.client.is_ready = True
    mock_client.emit(connection_event(connected=True))
    await _settle()
    await hass.async_block_till_done()
    assert hass.states.get(ALARM).state == "disarmed"
    mock_client.client.is_ready = False
    mock_client.emit(connection_event(connected=False))
    await _settle()
    assert hass.states.get(ALARM).state == STATE_UNAVAILABLE
    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)


@pytest.mark.usefixtures("fast_backoff")
async def test_raw_ready_restores_and_rearms_drop_push(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: ClientHarness
) -> None:
    """A raw READY alone brings entities back, and the next drop pushes (#97)."""
    await _setup_blocked(hass, mock_config_entry, mock_client)
    # The existing client recovers by itself; keep the supervisor out of it
    # (it would replace the client) so only the raw READY can end the drop.
    with _supervisor_held():
        mock_client.client.is_ready = False
        mock_client.emit_link(link_event(connected=False))
        await _settle()
        assert hass.states.get(ALARM).state == STATE_UNAVAILABLE
        mock_client.client.is_ready = True
        mock_client.emit_link(link_event(connected=True))
        await _settle()
        assert hass.states.get(ALARM).state == "disarmed"
        mock_client.client.is_ready = False
        mock_client.emit_link(link_event(connected=False))
        await _settle()
        assert hass.states.get(ALARM).state == STATE_UNAVAILABLE
    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)


async def _drop_then_raw_ready(hass: HomeAssistant, mock_client: ClientHarness) -> None:
    """Drop the link, then let the same client recover (raw READY only)."""
    mock_client.client.is_ready = False
    mock_client.emit_link(link_event(connected=False))
    await _settle()
    mock_client.client.is_ready = True
    mock_client.emit_link(link_event(connected=True))
    await _settle()
    await hass.async_block_till_done()


@pytest.mark.usefixtures("fast_backoff")
async def test_raw_ready_recovery_refreshes_csm(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: ClientHarness
) -> None:
    """Self-recovery re-reads the panel, like a supervisor reconnect (#97)."""
    await _setup_blocked(hass, mock_config_entry, mock_client)
    refresh = mock_client.client.async_refresh_csm
    with _supervisor_held():
        refresh.reset_mock()
        await _drop_then_raw_ready(hass, mock_client)
        assert refresh.await_count == 1
    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)


@pytest.mark.usefixtures("fast_backoff")
async def test_raw_ready_recovery_logs_restored_and_next_drop_warns(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: ClientHarness,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Self-recovery logs "restored" once, so the next drop warns again (#83, #97)."""
    caplog.set_level(logging.DEBUG, logger="custom_components.elke27")
    await _setup_blocked(hass, mock_config_entry, mock_client)
    with _supervisor_held():
        await _drop_then_raw_ready(hass, mock_client)
        mock_client.client.is_ready = False
        mock_client.emit_link(link_event(connected=False))
        await _settle()
    lines = [
        (r.levelno, r.getMessage())
        for r in caplog.records
        if r.name == "custom_components.elke27.coordinator"
        and r.getMessage() in {"Panel connection lost", "Panel connection restored"}
    ]
    assert lines == [
        (logging.WARNING, "Panel connection lost"),
        (logging.INFO, "Panel connection restored"),
        (logging.WARNING, "Panel connection lost"),
    ]
    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)


@pytest.mark.usefixtures("fast_backoff")
async def test_late_typed_disconnect_after_reconnect_does_not_block_next_drop(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: ClientHarness
) -> None:
    """
    A stale typed disconnect from the old session must not re-arm the guard.

    It lands while the supervisor reconnects and is processed once the client
    is ready again; the next real drop must still write 'unavailable' (#97).
    """
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    async def _connect_with_late_event(*_args: object, **_kwargs: object) -> None:
        mock_client.emit(connection_event(connected=False))  # old session, late
        mock_client.client.is_ready = True

    mock_client.client.async_connect.side_effect = _connect_with_late_event
    mock_client.client.is_ready = False
    _live_link_listener(mock_client)(link_event(connected=False))
    await _settle()
    await hass.async_block_till_done()
    assert hass.states.get(ALARM).state == "disarmed"
    _block_connect(mock_client)
    mock_client.client.is_ready = False
    _live_link_listener(mock_client)(link_event(connected=False))
    await _settle()
    assert hass.states.get(ALARM).state == STATE_UNAVAILABLE
    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)


@pytest.mark.parametrize(
    "after_ready", ["newer-drop", "newer-drop-ready-again", "not-ready"]
)
@pytest.mark.usefixtures("fast_backoff")
async def test_stale_raw_ready_does_not_restore(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: ClientHarness,
    caplog: pytest.LogCaptureFixture,
    after_ready: str,
) -> None:
    """
    A raw READY that is stale when it runs does not restore or refresh (#97).

    newer-drop: a new drop arrives before the READY's callback runs (older
    epoch, client down). newer-drop-ready-again: same, but the client reads
    ready again by then (only the epoch shows it is stale). not-ready: no new
    drop, but the client is not ready when the callback runs.
    """
    caplog.set_level(logging.DEBUG, logger="custom_components.elke27")
    await _setup_blocked(hass, mock_config_entry, mock_client)
    refresh = mock_client.client.async_refresh_csm
    with _supervisor_held():
        mock_client.client.is_ready = False
        mock_client.emit_link(link_event(connected=False))
        await _settle()
        assert hass.states.get(ALARM).state == STATE_UNAVAILABLE
        refresh.reset_mock()
        mock_client.client.is_ready = True
        mock_client.emit_link(link_event(connected=True))
        if after_ready != "not-ready":
            mock_client.client.is_ready = False
            mock_client.emit_link(link_event(connected=False))
        mock_client.client.is_ready = after_ready == "newer-drop-ready-again"
        await _settle()
        await hass.async_block_till_done()
    restored = [
        r for r in caplog.records if r.getMessage() == "Panel connection restored"
    ]
    assert restored == []
    assert refresh.await_count == 0
    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
