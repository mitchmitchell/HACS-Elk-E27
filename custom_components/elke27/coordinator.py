"""Data update coordinator for the Elke27 integration (owns the panel client)."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import timedelta
import logging
import time
from typing import TYPE_CHECKING, Any

from elke27_lib import (
    ClientConfig,
    CommandResult,
    Elke27Event,
    EventType,
    LinkKeys,
    PanelSnapshot,
    ZoneState,
)
from elke27_lib.client import Elke27Client
from elke27_lib.errors import (
    E27Error,
    Elke27Error,
    Elke27LinkRequiredError,
    Elke27PinRequiredError,
)
from elke27_lib.events import (
    ConnectionStateChanged,
    CsmSnapshotUpdated,
    DomainCsmChanged,
    TableCsmChanged,
    ZoneStatusUpdated,
)

from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
    HomeAssistantError,
)
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .arming import (
    ARM_STATUS_POLL_INTERVAL,
    ARM_STATUS_POLL_TIMEOUT,
    PIN_REQUIRED_REASON,
    ZoneBypassFailedError,
    _library_arm_mode,
    _validated_pin,
    area_faulted_zones,
    area_skip_automatic_arm_from_status_reply,
    snapshot_with_status,
)
from .const import (
    CONF_INTEGRATION_ID,
    CONF_LINK_KEYS_JSON,
    CONF_PANEL,
    DOMAIN,
    READY_TIMEOUT,
)
from .errors import (
    AUTH_ERRORS,
    COMMAND_ERRORS,
    NOT_ACCEPTED_MESSAGE,
    _error_message,
    command_error,
    is_connection_error,
    is_timeout_error,
    panel_error_code,
)
from .identity import build_client_identity
from .panel_mac import async_verify_panel_mac
from .reconnect import ReconnectSupervisor
from .tasks import create_background_task

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Coroutine, Iterable
    from datetime import datetime

    from .models import Elke27ConfigEntry

_LOGGER = logging.getLogger(__name__)


# Seconds to wait before re-reading a light's status after a set_status.
LIGHT_REFRESH_DELAY = 3.0

# ADR-0125 Q5: a snapshot that stays stale this long while connected gets one
# read-only CSM refresh, at most once per STALE_REFRESH_AFTER seconds.
STALE_REFRESH_AFTER = 60.0
STALE_CHECK_INTERVAL = timedelta(seconds=10)
_monotonic = time.monotonic

# Domains whose config is read during setup.
PRIMED_DOMAINS = ("light", "lock", "tstat")


REFRESH_AFTER_CONNECT_ERRORS: tuple[type[Exception], ...] = (
    *COMMAND_ERRORS,
    HomeAssistantError,
)
DEBOUNCED_REFRESH_ERRORS: tuple[type[Exception], ...] = (
    *COMMAND_ERRORS,
    HomeAssistantError,
)


class Elke27DataUpdateCoordinator(DataUpdateCoordinator[PanelSnapshot]):
    """Own the Elke27 client: connection, subscriptions, commands and updates."""

    config_entry: Elke27ConfigEntry
    # Set by async_setup_entry before the platforms load.
    panel_device_id: str

    def __init__(
        self,
        hass: HomeAssistant,
        entry: Elke27ConfigEntry,
        *,
        debounce_seconds: float = 0.3,
    ) -> None:
        """Initialize the coordinator from the config entry."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            config_entry=entry,
            update_interval=None,
        )
        self._host: str = entry.data[CONF_HOST]
        self._port: int = entry.data[CONF_PORT]
        self._link_keys_json: dict[str, str] = entry.data[CONF_LINK_KEYS_JSON]
        self._integration_id: str = entry.data[CONF_INTEGRATION_ID]
        self._panel_name = _panel_name_from_entry(entry.data.get(CONF_PANEL))
        self._client: Elke27Client | None = None
        # Device id of the panel device, set in async_setup_entry (E2 via link).
        self._connection_unsubscribe: Callable[[], object] | None = None
        self._connect_lock = asyncio.Lock()
        self._area_arm_locks: dict[int, asyncio.Lock] = {}
        self._arm_automatic_tasks: dict[int, set[asyncio.Task[Any]]] = {}
        self._disarm_cancelled: set[asyncio.Task[Any]] = set()
        self._disarm_pending: dict[int, int] = {}
        self._disarm_clear: dict[int, asyncio.Event] = {}
        self._stopping = False
        self._unavailable_logged = False
        # True once this drop has pushed the unavailable state to entities;
        # cleared on the next successful connect (#97).
        self._unavailable_pushed = False
        self._typed_callbacks: dict[
            Callable[[Any], None], Callable[[], object] | None
        ] = {}
        self._debounce_seconds = debounce_seconds
        self._pending_domains: set[str] = set()
        self._refresh_lock = asyncio.Lock()
        self._debounce_task: asyncio.Task[None] | None = None
        self._refresh_task: asyncio.Task[None] | None = None
        self._unsubscribe: Callable[[], None] | None = None
        self._stale_since: float | None = None
        self._last_stale_refresh: float | None = None
        self._unsubscribe_stale_timer: Callable[[], None] | None = None
        self._stale_task: asyncio.Task[None] | None = None
        # D4: post-command fallback reads (status_refresh_ok False). Kept for
        # the soak gate; removed with snapshot_with_status after the soak.
        # Every fallback read task still running, and a lock so only one of
        # them reads the panel at a time (Q5 also skips while any is running).
        self._fallback_tasks: set[asyncio.Task[PanelSnapshot | None]] = set()
        self._fallback_lock = asyncio.Lock()
        # Bumped on every disconnect. A fallback read that started before a
        # disconnect read the old session, so its result is discarded.
        self._connection_epoch = 0
        # Bumped on every snapshot push. A fallback read that saw a push while
        # it ran is re-read once, so it never overwrites newer state.
        self._snapshot_seq = 0
        self._fallback_refresh_count = 0
        self._last_fallback_at: datetime | None = None
        # D5 soak counter (Architect default, pending Mitch's OK): automatic
        # stale-snapshot reads since this entry loaded.
        self._stale_auto_refresh_count = 0
        self._reconnect = ReconnectSupervisor(
            hass,
            entry,
            connect=self._async_connect,
            is_stopping=lambda: self._stopping,
            on_reconnected=self._handle_reconnected,
        )
        self.async_set_updated_data(PanelSnapshot.empty())

    async def _async_setup(self) -> None:
        """Connect to the panel and subscribe to its events (first refresh)."""
        try:
            await self.async_connect()
            if self._unsubscribe is None:
                self._unsubscribe = self.subscribe_typed(self._handle_event)
            if self._unsubscribe_stale_timer is None:
                self._unsubscribe_stale_timer = async_track_time_interval(
                    self.hass, self._async_check_stale, STALE_CHECK_INTERVAL
                )
        except AUTH_ERRORS as err:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN, translation_key="auth_failed"
            ) from err
        except SETUP_RETRY_ERRORS as err:
            raise self._setup_failed(err) from err

    async def _async_update_data(self) -> PanelSnapshot:
        """Read the CSM and the primed domain configs; return the snapshot."""
        try:
            await self.async_refresh_now()
            await self.async_refresh_domains(PRIMED_DOMAINS)
        except AUTH_ERRORS as err:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN, translation_key="auth_failed"
            ) from err
        except SETUP_RETRY_ERRORS as err:
            raise self._setup_failed(err) from err
        return self.get_snapshot() or PanelSnapshot.empty()

    def _setup_failed(self, err: BaseException) -> UpdateFailed:
        """Return the setup-retry error for a connection or library failure."""
        _LOGGER.debug("Panel setup failed: %s", err)
        return UpdateFailed(
            translation_domain=DOMAIN,
            translation_key="cannot_connect",
            translation_placeholders={"host": self._host, "port": str(self._port)},
        )

    async def async_shutdown_client(self) -> None:
        """
        Stop updates and disconnect the client (entry unload or failed setup).

        Order: stop event handling and refresh tasks, stop the reconnect
        supervisor (awaited), clear its Repairs issue, then disconnect under
        the connect lock so a running connect cannot race the teardown.
        """
        # Set first, so no callback starts new work while tasks are awaited.
        self._stopping = True
        if self._unsubscribe_stale_timer is not None:
            self._unsubscribe_stale_timer()
            self._unsubscribe_stale_timer = None
        if self._unsubscribe is not None:
            try:
                self._unsubscribe()
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("Event unsubscribe failed: %s", err)
            self._unsubscribe = None
        for task in (
            self._debounce_task,
            self._refresh_task,
            self._stale_task,
            *self._fallback_tasks,
        ):
            if task is not None and not task.done():
                task.cancel()
                # Wait for the task to finish without swallowing a cancellation
                # of this call itself.
                await asyncio.wait([task])
        self._debounce_task = None
        self._refresh_task = None
        self._stale_task = None
        self._fallback_tasks.clear()
        await self._reconnect.async_stop()
        self._reconnect.clear_issue()
        async with self._connect_lock:
            await self._async_disconnect()

    @property
    def reconnect(self) -> ReconnectSupervisor:
        """Return the reconnect supervisor."""
        return self._reconnect

    @callback
    def _async_check_stale(self, _now: datetime) -> None:
        """
        Refresh once when the snapshot has stayed stale too long (ADR Q5).

        Reads only, never while reconnecting or disconnected, at most once per
        STALE_REFRESH_AFTER seconds, in its own task (the refresh lock
        serializes it with other refreshes).
        """
        snapshot = self.get_snapshot()
        now = _monotonic()
        if (
            snapshot is None
            or not snapshot.stale
            or not self.is_ready
            or self._stopping
        ):
            self._stale_since = None
            return
        if self._stale_since is None:
            self._stale_since = now
        if self._reconnect.task is not None and not self._reconnect.task.done():
            return
        if now - self._stale_since < STALE_REFRESH_AFTER:
            return
        if (
            self._last_stale_refresh is not None
            and now - self._last_stale_refresh < STALE_REFRESH_AFTER
        ):
            return
        if self._refresh_task is not None and not self._refresh_task.done():
            return
        if self._stale_task is not None and not self._stale_task.done():
            return
        if self._fallback_lock.locked() or any(
            not task.done() for task in self._fallback_tasks
        ):
            # D4: a post-command fallback read is running or queued.
            return
        self._last_stale_refresh = now
        self._stale_auto_refresh_count += 1
        _LOGGER.debug("Snapshot stale for %.0f s; refreshing", now - self._stale_since)
        # Its own task, not _refresh_task: a stale refresh in flight must never
        # suppress the post-reconnect refresh (the refresh lock serializes them).
        self._stale_task = self._create_background_task(
            self._async_refresh_after_connect(), f"{DOMAIN} stale refresh"
        )

    def _create_background_task[T](
        self, coro: Coroutine[Any, Any, T], name: str
    ) -> asyncio.Task[T]:
        """Create a task that is cancelled when the config entry unloads."""
        return create_background_task(self.hass, self.config_entry, coro, name)

    @property
    def client(self) -> Elke27Client | None:
        """Return the underlying client."""
        return self._client

    @property
    def is_ready(self) -> bool:
        """Return if the client is ready."""
        return self._client is not None and self._client.is_ready

    @property
    def panel_name(self) -> str | None:
        """Return the panel name reported by the panel, else the configured name."""
        snapshot = self.get_snapshot()
        if snapshot is not None:
            panel_name = snapshot.panel.panel_name
            # Temporary until elke27_lib ships py.typed.
            if isinstance(panel_name, str) and panel_name:
                return panel_name
        return self._panel_name

    async def async_connect(self) -> None:
        """Connect the client, then await readiness."""
        self._stopping = False
        await self._async_connect()

    async def _async_connect(self) -> None:
        """Connect the client, then await readiness."""
        async with self._connect_lock:
            await self._async_disconnect()
            try:
                link_keys = LinkKeys.from_json(self._link_keys_json)
            except (AttributeError, TypeError, ValueError) as err:
                # Stored link keys that cannot be read need a relink, not retries.
                msg = "Stored link keys are invalid; relink required"
                raise Elke27LinkRequiredError(msg) from err
            client = Elke27Client(ClientConfig())
            client.set_client_identity(build_client_identity(self._integration_id))
            self._client = client

            def _raise_not_ready() -> None:
                raise ConfigEntryNotReady(
                    translation_domain=DOMAIN,
                    translation_key="panel_not_ready",
                )

            try:
                await client.async_connect(self._host, self._port, link_keys)
                ready = await client.wait_ready(timeout_s=READY_TIMEOUT)
                if not ready:
                    _raise_not_ready()
                self._connection_unsubscribe = client.subscribe(
                    self._handle_connection_event
                )
                self._resubscribe_typed_callbacks()
                self._schedule_panel_mac_check()
                self._unavailable_pushed = False
                if self._unavailable_logged:
                    _LOGGER.info("Panel connection restored")
                    self._unavailable_logged = False
            except BaseException:
                # Includes cancellation: tear the half-open client down, then
                # re-raise unchanged.
                self._client = None
                if self._connection_unsubscribe is not None:
                    try:
                        self._connection_unsubscribe()
                    except Exception as err:  # noqa: BLE001
                        _LOGGER.debug("Connection unsubscribe failed: %s", err)
                    self._connection_unsubscribe = None
                await self._async_disconnect_client(client)
                raise

    async def _async_disconnect(self) -> None:
        """Disconnect the client and unregister event handlers."""
        was_connected = self._client is not None
        if self._connection_unsubscribe is not None:
            try:
                self._connection_unsubscribe()
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("Connection unsubscribe failed: %s", err)
            self._connection_unsubscribe = None
        client = self._client
        self._client = None
        # Any fallback read in flight belongs to the old session.
        self._connection_epoch += 1
        if client is not None:
            await self._async_disconnect_client(client)
        self._clear_typed_subscriptions()
        # An intentional disconnect (unload or shutdown) is not a lost connection.
        if was_connected and not self._stopping:
            self._log_unavailable()

    @staticmethod
    async def _async_disconnect_client(client: Elke27Client) -> None:
        """Disconnect a client; a library or network error is logged, not raised."""
        try:
            await client.async_disconnect()
        except COMMAND_ERRORS as err:
            _LOGGER.debug("Client disconnect failed: %s", err)

    def get_snapshot(self) -> PanelSnapshot | None:
        """Return the latest client snapshot."""
        client = self._client
        if client is None:
            return None
        return client.get_snapshot()

    async def async_refresh_area_state(
        self, area_id: int
    ) -> tuple[PanelSnapshot | None, bool | None]:
        """
        Ask the panel for the area's arm state and all zone statuses.

        The answer is built from the reply payloads themselves. Reading the
        client snapshot afterwards is not enough: elke27 resolves the request
        before its handlers apply the reply, and it does not apply arm or bypass
        command replies at all. Each request is sent once; if one fails, that
        part falls back to the client snapshot for zone data only.

        The second value is whether the area_get_status reply reported armed or
        exit-delay arming (True/False), or None when that request failed.
        """
        return await self._async_area_state_snapshot(area_id, include_zones=True)

    async def async_refresh_area_arm_status(
        self, area_id: int
    ) -> tuple[PanelSnapshot | None, bool | None]:
        """Ask the panel for one area's arm state (no zone status)."""
        return await self._async_area_state_snapshot(area_id, include_zones=False)

    async def _async_area_state_snapshot(
        self, area_id: int, *, include_zones: bool
    ) -> tuple[PanelSnapshot | None, bool | None]:
        client = self._client
        if client is None:
            return None, None
        area_payload = await self._async_status_request(
            client, "area_get_status", area_id=area_id
        )
        zones_payload = (
            await self._async_status_request(client, "zone_get_all_zones_status")
            if include_zones
            else None
        )
        snapshot = snapshot_with_status(
            client.get_snapshot(), area_id, area_payload, zones_payload
        )
        return snapshot, area_skip_automatic_arm_from_status_reply(area_payload)

    async def async_poll_until_area_armed(
        self,
        area_id: int,
        *,
        gate: Callable[[], Awaitable[None]] | None = None,
    ) -> tuple[PanelSnapshot | None, bool]:
        """
        Poll area_get_status until the panel reports armed or exit delay arming.

        A failed read does not skip and polling continues. Return the last snapshot
        and whether a reply showed armed or arming before the timeout.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + ARM_STATUS_POLL_TIMEOUT
        last: PanelSnapshot | None = None
        while True:
            if gate is not None:
                await gate()
            last, skip = await self.async_refresh_area_arm_status(area_id)
            if skip is True:
                return last, True
            if loop.time() >= deadline:
                return last, False
            await asyncio.sleep(ARM_STATUS_POLL_INTERVAL)

    async def _async_status_request(
        self, client: Elke27Client, command_key: str, **params: Any
    ) -> Mapping[str, Any] | None:
        """Send one status request; return its reply payload, or None on failure."""
        try:
            result = await client.async_execute(command_key, **params)
        except COMMAND_ERRORS as err:
            _LOGGER.debug("Status refresh %s failed: %s", command_key, err)
            return None
        if not result.ok or not isinstance(result.data, Mapping):
            _LOGGER.debug("Status refresh %s failed: %s", command_key, result.error)
            return None
        return result.data

    async def refresh_csm(self) -> Any:
        """Refresh the panel CSM snapshot."""
        return await self._require_client().async_refresh_csm()

    async def refresh_domain_config(self, domain: str) -> None:
        """Refresh a domain configuration snapshot."""
        await self._require_client().async_refresh_domain_config(domain)

    def subscribe(self, listener: Callable[[Any], None]) -> Callable[[], bool]:
        """Subscribe to client events."""
        return self._require_client().subscribe(listener)

    def subscribe_typed(self, listener: Callable[[Any], None]) -> Callable[[], None]:
        """Subscribe to typed client events."""
        if listener not in self._typed_callbacks:
            self._typed_callbacks[listener] = None
        client = self._client
        if client is not None:
            self._typed_callbacks[listener] = client.subscribe_typed(listener)

        def _remove() -> None:
            self.unsubscribe_typed(listener)

        return _remove

    def unsubscribe_typed(self, listener: Callable[[Any], None]) -> bool:
        """Unsubscribe from typed client events."""
        if listener in self._typed_callbacks:
            unsubscribe = self._typed_callbacks.pop(listener)
            if unsubscribe is not None:
                unsubscribe()
            return True
        client = self._client
        if client is None:
            return False
        return client.unsubscribe_typed(listener)

    async def async_set_output(self, output_id: int, *, state: bool) -> bool:
        """Turn an output on or off."""
        client = self._client
        if client is None:
            return False
        try:
            await client.async_set_output(output_id, on=state)
        except Elke27PinRequiredError:
            raise
        except COMMAND_ERRORS as err:
            _log_command_failure("Output", output_id, "set", err)
            self._note_command_error(err)
            raise HomeAssistantError(_error_message(err)) from err
        return True

    async def async_set_light(
        self, light_id: int, *, state: bool, level: int | None = None
    ) -> bool:
        """Turn a light on or off, optionally at a dim level (0-100)."""
        if not state:
            level = 0
        elif level is None:
            level = 100
        ok = await self._async_execute(
            "light_set_status",
            light_id=light_id,
            status="ON" if state else "OFF",
            level=level,
        )
        if ok:
            # The set_status ack may not carry the new state (Z-Wave devices
            # report it later). After a turn-on, re-read only after a short
            # delay: an immediate light_get_status can return level 0 before the
            # panel has applied the change, which HA shows as a brief false "off"
            # (#89). A turn-off has no such transient, so read it back now too.
            if not state:
                await self._async_refresh_light(light_id)
            self._create_background_task(
                self._async_refresh_light_later(light_id),
                f"elke27 light {light_id} status refresh",
            )
        return ok

    async def _async_refresh_light(self, light_id: int) -> None:
        """Request a light's status; failures are logged, not raised."""
        try:
            await self._async_execute("light_get_status", light_id=light_id)
        except (Elke27PinRequiredError, HomeAssistantError) as err:
            _LOGGER.debug("Light %s status refresh failed: %s", light_id, err)

    async def _async_refresh_light_later(self, light_id: int) -> None:
        """Request a light's status again after the device has had time to report."""
        await asyncio.sleep(LIGHT_REFRESH_DELAY)
        if self._client is not None and not self._stopping:
            await self._async_refresh_light(light_id)

    async def async_set_lock(self, lock_id: int, *, locked: bool) -> bool:
        """Lock (status ON) or unlock (status OFF) a lock."""
        return await self._async_execute(
            "lock_set_status",
            lock_id=lock_id,
            status="ON" if locked else "OFF",
        )

    async def async_set_tstat_status(
        self,
        tstat_id: int,
        *,
        mode: str | None = None,
        fan_mode: str | None = None,
        cool_setpoint: float | None = None,
        heat_setpoint: float | None = None,
    ) -> bool:
        """Request thermostat status changes."""
        kwargs: dict[str, Any] = {
            "mode": mode,
            "fan_mode": fan_mode,
            "cool_setpoint": cool_setpoint,
            "heat_setpoint": heat_setpoint,
        }
        filtered_kwargs = {
            key: value for key, value in kwargs.items() if value is not None
        }
        if not filtered_kwargs:
            return True
        return await self._async_execute(
            "tstat_set_status", tstat_id=tstat_id, **filtered_kwargs
        )

    async def async_bypass_faulted_zones(
        self,
        area_id: int,
        snapshot: PanelSnapshot | None,
        pin: str | None,
        *,
        attempted: list[ZoneState] | None = None,
        gate: Callable[[], Awaitable[None]] | None = None,
    ) -> list[ZoneState]:
        """
        Bypass open, non-bypassed zones assigned to the given area.

        Return the zones that were bypassed. Stop at the first failure without
        retrying it (a refusal is reported, never retried); ZoneBypassFailedError
        names the failed zone, the reason, and the zones already bypassed. The
        caller decides whether to roll those back with async_rollback_bypasses.
        Each zone is appended to ``attempted`` (when given) before its bypass is
        sent, so a caller that is cancelled mid-way knows what may be bypassed.
        """
        bypassed: list[ZoneState] = []
        for zone in area_faulted_zones(snapshot, area_id):
            if gate is not None:
                await gate()
            if attempted is not None:
                attempted.append(zone)
            try:
                acknowledged = await self.async_set_zone_bypass(
                    zone.zone_id, bypassed=True, pin=pin
                )
            except Elke27PinRequiredError as err:
                raise ZoneBypassFailedError(
                    zone, PIN_REQUIRED_REASON, bypassed, pin_required=True
                ) from err
            except HomeAssistantError as err:
                raise ZoneBypassFailedError(zone, str(err), bypassed) from err
            if not acknowledged:
                raise ZoneBypassFailedError(
                    zone, "bypass was not acknowledged.", bypassed
                )
            bypassed.append(zone)
        return bypassed

    def area_arm_lock(self, area_id: int) -> asyncio.Lock:
        """
        Return the lock that serializes automatic arming of one area.

        The coordinator belongs to one config entry, so this is per (entry, area).
        """
        return self._area_arm_locks.setdefault(area_id, asyncio.Lock())

    def register_arm_automatic(self, area_id: int, task: asyncio.Task[Any]) -> None:
        """Track an automatic arming task (running or queued) for an area."""
        self._arm_automatic_tasks.setdefault(area_id, set()).add(task)

    def unregister_arm_automatic(self, area_id: int, task: asyncio.Task[Any]) -> None:
        """Stop tracking an automatic arming task."""
        tasks = self._arm_automatic_tasks.get(area_id)
        if tasks is not None:
            tasks.discard(task)
            if not tasks:
                del self._arm_automatic_tasks[area_id]
        self._disarm_cancelled.discard(task)

    def begin_disarm(self, area_id: int) -> None:
        """
        Mark a disarm of the area as pending.

        While pending, automatic arming of the area pauses before its next
        command (see async_wait_disarm_clear), so nothing it sends can land
        after the disarm.
        """
        self._disarm_pending[area_id] = self._disarm_pending.get(area_id, 0) + 1
        self._disarm_clear.setdefault(area_id, asyncio.Event()).clear()

    def end_disarm(self, area_id: int) -> None:
        """Clear one pending disarm; automatic arming resumes when none remain."""
        remaining = self._disarm_pending.get(area_id, 0) - 1
        if remaining > 0:
            self._disarm_pending[area_id] = remaining
            return
        self._disarm_pending.pop(area_id, None)
        event = self._disarm_clear.pop(area_id, None)
        if event is not None:
            event.set()

    async def async_wait_disarm_clear(self, area_id: int) -> None:
        """Wait while a disarm of the area is pending (automatic arming gate)."""
        while self._disarm_pending.get(area_id):
            await self._disarm_clear[area_id].wait()

    def cancel_arm_automatic(self, area_id: int) -> int:
        """
        Cancel every automatic arming task for an area, so a disarm wins.

        Returns how many tasks were cancelled. Nothing is awaited here: the
        disarm is sent straight away and does not wait for the tasks to stop.
        """
        count = 0
        for task in list(self._arm_automatic_tasks.get(area_id, ())):
            if not task.done():
                self._disarm_cancelled.add(task)
                task.cancel()
                count += 1
        return count

    def cancelled_by_disarm(self, task: asyncio.Task[Any] | None) -> bool:
        """Return True when the task was cancelled by cancel_arm_automatic."""
        return task is not None and task in self._disarm_cancelled

    async def async_rollback_bypasses(
        self,
        zones: list[ZoneState] | tuple[ZoneState, ...],
        pin: str | None,
        *,
        gate: Callable[[], Awaitable[None]] | None = None,
    ) -> tuple[list[ZoneState], list[ZoneState]]:
        """
        Un-bypass zones this integration just bypassed, in reverse order.

        Each zone is attempted exactly once; a failure is not retried, it is
        reported. Return (rolled_back, still_bypassed), both in attempt order.
        """
        rolled_back: list[ZoneState] = []
        still_bypassed: list[ZoneState] = []
        for zone in reversed(tuple(zones)):
            if gate is not None:
                await gate()
            try:
                acknowledged = await self.async_set_zone_bypass(
                    zone.zone_id, bypassed=False, pin=pin
                )
            except (Elke27PinRequiredError, HomeAssistantError) as err:
                _LOGGER.warning(
                    "Could not undo bypass of zone %s: %s", zone.zone_id, err
                )
                acknowledged = False
            if acknowledged:
                rolled_back.append(zone)
            else:
                still_bypassed.append(zone)
        return rolled_back, still_bypassed

    async def async_set_zone_bypass(
        self, zone_id: int, *, bypassed: bool, pin: str | None = None
    ) -> bool:
        """Bypass or unbypass a zone through the elke27 client."""
        client = self._client
        if client is None:
            return False
        if pin is None:
            msg = "PIN required to bypass zones."
            raise Elke27PinRequiredError(msg)
        pin_value = _validated_pin(pin)
        # Never log the user code.
        _LOGGER.debug(
            "Sending zone bypass request: zone_id=%s bypassed=%s",
            zone_id,
            bypassed,
        )
        epoch = self._connection_epoch
        try:
            refresh_ok = await self._async_write_zone_bypass(
                client, zone_id, bypassed=bypassed, pin=pin_value
            )
        except Elke27PinRequiredError:
            raise
        except AUTH_ERRORS as err:
            self.start_reauth_once()
            _log_command_failure("Zone", zone_id, "bypass", err)
            raise HomeAssistantError(_error_message(err)) from err
        except COMMAND_ERRORS as err:
            _log_command_failure("Zone", zone_id, "bypass", err)
            self._note_command_error(err)
            raise command_error(err) from err
        await self._async_after_write(
            "bypass", refresh_ok=refresh_ok, zone_id=zone_id, epoch=epoch
        )
        return True

    async def _async_execute(self, command_key: str, **params: Any) -> bool:
        """Run a raw elke27 command for domains without a public client method."""
        client = self._client
        if client is None:
            return False
        try:
            result = await client.async_execute(command_key, **params)
        except Elke27PinRequiredError:
            raise
        except AUTH_ERRORS as err:
            self.start_reauth_once()
            _LOGGER.warning("Command %s failed: %s", command_key, err)
            raise HomeAssistantError(_error_message(err)) from err
        except COMMAND_ERRORS as err:
            _LOGGER.warning("Command %s failed: %s", command_key, err)
            self._note_command_error(err)
            raise HomeAssistantError(_error_message(err)) from err
        if not result.ok:
            if isinstance(result.error, Elke27PinRequiredError):
                raise result.error
            if result.error is not None:
                if isinstance(result.error, AUTH_ERRORS):
                    self.start_reauth_once()
                else:
                    self._note_command_error(result.error)
                _LOGGER.warning("Command %s failed: %s", command_key, result.error)
                raise HomeAssistantError(_error_message(result.error)) from (
                    result.error
                )
            _LOGGER.warning("Command %s was not accepted by the panel", command_key)
            raise HomeAssistantError(NOT_ACCEPTED_MESSAGE)
        return True

    def _note_command_error(self, err: BaseException) -> None:
        """
        Treat a command timeout as evidence the link may be dead.

        Asks elke27 to probe the panel now, so a dead link is detected within
        the keepalive timeout instead of at the next scheduled keepalive. When
        the probe fails the library disconnects and the reconnect loop starts.
        """
        if not is_timeout_error(err):
            return
        client = self._client
        if client is None:
            return
        try:
            client.request_link_check()
        except COMMAND_ERRORS as check_err:
            _LOGGER.debug("Link check request failed: %s", check_err)

    def _require_client(self) -> Elke27Client:
        """Return the connected client or raise."""
        client = self._client
        if client is None:
            msg = "Client is not connected."
            raise HomeAssistantError(msg)
        return client

    async def async_arm_area(
        self,
        area_id: int,
        mode: Any,
        pin: str | None,
        *,
        auto_stay_cancel: bool = False,
        exit_delay_cancel: bool = False,
    ) -> bool:
        """Arm an area through the elke27 client."""
        client = self._client
        if client is None:
            return False
        if pin is None:
            msg = "PIN required to arm areas."
            raise Elke27PinRequiredError(msg)
        pin_value = _validated_pin(pin)
        arm_mode = _library_arm_mode(mode)
        epoch = self._connection_epoch
        try:
            refresh_ok = await self._async_write_arm(
                client,
                area_id,
                mode=arm_mode,
                pin=pin_value,
                auto_stay_cancel=auto_stay_cancel,
                exit_delay_cancel=exit_delay_cancel,
            )
        except Elke27PinRequiredError:
            raise
        except AUTH_ERRORS as err:
            self.start_reauth_once()
            _log_command_failure("Area", area_id, "arming", err)
            raise HomeAssistantError(_error_message(err)) from err
        except COMMAND_ERRORS as err:
            _log_command_failure("Area", area_id, "arming", err)
            self._note_command_error(err)
            raise command_error(err) from err
        await self._async_after_write(
            "arm", refresh_ok=refresh_ok, area_id=area_id, epoch=epoch
        )
        return True

    def _resubscribe_typed_callbacks(self) -> None:
        """Re-register typed callbacks on a new client connection."""
        client = self._client
        if client is None or not self._typed_callbacks:
            return
        for cb in list(self._typed_callbacks):
            self._typed_callbacks[cb] = client.subscribe_typed(cb)

    def _clear_typed_subscriptions(self) -> None:
        """Clear typed subscriptions when the client disconnects."""
        for cb, unsubscribe in list(self._typed_callbacks.items()):
            if unsubscribe is not None:
                try:
                    unsubscribe()
                except Exception as err:  # noqa: BLE001
                    _LOGGER.debug("Typed callback unsubscribe failed: %s", err)
            self._typed_callbacks[cb] = None

    async def async_disarm_area(
        self,
        area_id: int,
        pin: str | None,
        *,
        auto_stay_cancel: bool = False,
        exit_delay_cancel: bool = False,
    ) -> bool:
        """Disarm an area through the elke27 client."""
        client = self._client
        if client is None:
            return False
        if pin is None:
            msg = "PIN required to disarm areas."
            raise Elke27PinRequiredError(msg)
        pin_value = _validated_pin(pin)
        epoch = self._connection_epoch
        try:
            refresh_ok = await self._async_write_disarm(
                client,
                area_id,
                pin=pin_value,
                auto_stay_cancel=auto_stay_cancel,
                exit_delay_cancel=exit_delay_cancel,
            )
        except Elke27PinRequiredError:
            raise
        except AUTH_ERRORS as err:
            self.start_reauth_once()
            _log_command_failure("Area", area_id, "disarm", err)
            raise HomeAssistantError(_error_message(err)) from err
        except COMMAND_ERRORS as err:
            _log_command_failure("Area", area_id, "disarm", err)
            self._note_command_error(err)
            raise command_error(err) from err
        await self._async_after_write(
            "disarm", refresh_ok=refresh_ok, area_id=area_id, epoch=epoch
        )
        return True

    # D4: the three writes call the elke27 public methods with
    # return_result=True (elke27 0.3.14 CommandResult) and return its
    # status_refresh_ok. Failures raise the library's public errors.

    async def _async_write_arm(
        self,
        client: Elke27Client,
        area_id: int,
        *,
        mode: Any,
        pin: str,
        auto_stay_cancel: bool,
        exit_delay_cancel: bool,
    ) -> bool | None:
        result: CommandResult = await client.async_arm_area(
            area_id,
            mode=mode,
            pin=pin,
            auto_stay_cancel=auto_stay_cancel,
            exit_delay_cancel=exit_delay_cancel,
            return_result=True,
        )
        return result.status_refresh_ok

    async def _async_write_disarm(
        self,
        client: Elke27Client,
        area_id: int,
        *,
        pin: str,
        auto_stay_cancel: bool,
        exit_delay_cancel: bool,
    ) -> bool | None:
        result: CommandResult = await client.async_disarm_area(
            area_id,
            pin=pin,
            auto_stay_cancel=auto_stay_cancel,
            exit_delay_cancel=exit_delay_cancel,
            return_result=True,
        )
        return result.status_refresh_ok

    async def _async_write_zone_bypass(
        self, client: Elke27Client, zone_id: int, *, bypassed: bool, pin: str
    ) -> bool | None:
        result: CommandResult = await client.async_set_zone_bypass(
            zone_id, bypassed=bypassed, pin=pin, return_result=True
        )
        return result.status_refresh_ok

    async def _async_after_write(
        self,
        kind: str,
        *,
        refresh_ok: bool | None,
        area_id: int | None = None,
        zone_id: int | None = None,
        epoch: int,
    ) -> None:
        """
        Push post-command state; fall back to one status read if needed (D4).

        status_refresh_ok True or None: the library already refreshed (or the
        reply carried the state), so push its snapshot with no extra request.
        False: one fallback read via snapshot_with_status, then push. A failed
        fallback pushes the client snapshot as is; the command still succeeded.
        """
        _LOGGER.debug(
            "After %s write (area=%s zone=%s): status_refresh_ok=%s",
            kind,
            area_id,
            zone_id,
            refresh_ok,
        )
        if refresh_ok is not False:
            self._set_snapshot(self.get_snapshot())
            return
        self._fallback_refresh_count += 1
        self._last_fallback_at = dt_util.utcnow()
        _LOGGER.debug(
            "Post-command status refresh failed (%s area=%s zone=%s); fallback read %s",
            kind,
            area_id,
            zone_id,
            self._fallback_refresh_count,
        )
        await self._async_fallback_read(
            kind, area_id=area_id, zone_id=zone_id, epoch=epoch
        )

    async def _async_fallback_read(
        self, kind: str, *, area_id: int | None, zone_id: int | None, epoch: int
    ) -> None:
        """
        Run one fallback read that starts after this command's write.

        The read task is created now, after the write, and then waits for the
        fallback lock and any Q5 refresh in flight, so it never reuses an older
        read and never overlaps another fallback read or Q5. The lock is held
        by the task itself, so a cancelled caller cannot release it early, and
        the task publishes under the lock, so the next read starts after it.
        """
        task = self._create_background_task(
            self._async_locked_fallback_status(
                kind, area_id=area_id, zone_id=zone_id, epoch=epoch
            ),
            f"{DOMAIN} post-command fallback read",
        )
        self._fallback_tasks.add(task)
        task.add_done_callback(self._fallback_tasks.discard)
        await asyncio.shield(task)

    async def _async_locked_fallback_status(
        self, kind: str, *, area_id: int | None, zone_id: int | None, epoch: int
    ) -> None:
        """Wait for the lock and any refresh in flight, read, then publish."""
        async with self._fallback_lock:
            # Never overlap the Q5 stale refresh or the post-reconnect refresh.
            running = [
                task
                for task in (self._stale_task, self._refresh_task)
                if task is not None and not task.done()
            ]
            if running:
                await asyncio.wait(running)
            # Taken just before the read: a push while it runs (a panel event,
            # a later command or the reconnect refresh) means the reply may be
            # older than what was pushed, so read once more.
            seq = self._snapshot_seq
            ok, snapshot = await self._async_try_fallback_status(area_id, zone_id)
            if (
                ok
                and seq != self._snapshot_seq
                and epoch == self._connection_epoch
                and self._client is not None
            ):
                _LOGGER.debug("Snapshot pushed during fallback read; reading again")
                # The first read is older than the push: never publish it,
                # even when the re-read fails.
                seq = self._snapshot_seq
                _ok, snapshot = await self._async_try_fallback_status(area_id, zone_id)
                if seq != self._snapshot_seq:
                    # Pushed again during the re-read: the live snapshot is
                    # newer than either read.
                    snapshot = None
            if epoch != self._connection_epoch or self._client is None:
                _LOGGER.debug(
                    "Discarding pre-disconnect fallback read (%s area=%s zone=%s)",
                    kind,
                    area_id,
                    zone_id,
                )
                return
            self._set_snapshot(snapshot or self.get_snapshot())

    async def _async_try_fallback_status(
        self, area_id: int | None, zone_id: int | None
    ) -> tuple[bool, PanelSnapshot | None]:
        """Read once; a library error is logged and returns (False, None)."""
        try:
            return True, await self._async_fallback_status(
                area_id=area_id, zone_id=zone_id
            )
        except COMMAND_ERRORS as err:
            _LOGGER.debug("Post-command fallback read failed: %s", err)
            return False, None

    async def _async_fallback_status(
        self, *, area_id: int | None, zone_id: int | None
    ) -> PanelSnapshot | None:
        """Read area status (and all zone status for a bypass) once."""
        if zone_id is None:
            if area_id is None:
                return self.get_snapshot()
            snapshot, _skip = await self.async_refresh_area_arm_status(area_id)
            return snapshot
        current = self.get_snapshot()
        zone = current.zones.get(zone_id) if current is not None else None
        zone_area = zone.area_id if zone is not None else None
        if zone_area:
            snapshot, _skip = await self.async_refresh_area_state(zone_area)
            return snapshot
        client = self._client
        if client is None:
            return None
        zones_payload = await self._async_status_request(
            client, "zone_get_all_zones_status"
        )
        return snapshot_with_status(client.get_snapshot(), 0, None, zones_payload)

    @property
    def fallback_refresh_count(self) -> int:
        """Post-command fallback reads since this entry loaded (D4 soak gate)."""
        return self._fallback_refresh_count

    @property
    def last_fallback_at(self) -> datetime | None:
        """UTC time of the last post-command fallback read, if any."""
        return self._last_fallback_at

    @property
    def stale_auto_refresh_count(self) -> int:
        """Automatic stale-snapshot reads since this entry loaded (D5 soak aid)."""
        return self._stale_auto_refresh_count

    @property
    def snapshot_stale(self) -> bool:
        """Return the library's snapshot.stale flag (read-only, for D5)."""
        return self.data.stale

    @property
    def stale_area_ids(self) -> frozenset[int]:
        """Area ids the library marks stale (read-only, for D5)."""
        return self.data.stale_area_ids

    @property
    def stale_zone_ids(self) -> frozenset[int]:
        """Zone ids the library marks stale (read-only, for D5)."""
        return self.data.stale_zone_ids

    def _handle_connection_event(self, event: Elke27Event) -> None:
        """Handle connection lifecycle events from the client."""
        if self._client is None:
            return
        connected = _connection_state(event)
        if connected is False:
            self._connection_epoch += 1
            _LOGGER.debug("Panel disconnect event received; scheduling reconnect")
            self._log_unavailable()
            # Entities already report unavailable (client not ready), but only
            # a listener update makes HA write that state (#97). Queued before
            # the reconnect so it runs before the reconnect's own epoch bump.
            self.hass.loop.call_soon_threadsafe(
                self._push_unavailable, self._connection_epoch
            )
            self.hass.loop.call_soon_threadsafe(self._reconnect.start)
        elif connected is True:
            self.hass.loop.call_soon_threadsafe(
                self._on_raw_ready, self._connection_epoch
            )

    @callback
    def _on_raw_ready(self, epoch: int) -> None:
        """
        Cancel the reconnect and end the drop, only for a current READY.

        A READY queued behind a drop runs after the supervisor's start, which
        tears the old client down at once (new epoch, client not ready). That
        READY is stale: cancelling then would stop the only reconnect and leave
        the panel down (Bugbot, #98). The supervisor finishes the recovery.
        """
        if epoch != self._connection_epoch or not self.is_ready:
            return
        self._reconnect.cancel()
        self._push_ready(epoch)

    @callback
    def _push_unavailable(self, epoch: int) -> None:
        """
        Push the snapshot once per drop so entities write 'unavailable' (#97).

        A disconnect arrives on two paths (the link listener and the typed
        ConnectionStateChanged); together they give one listener update. A
        push from an older epoch (a newer disconnect or a reconnect came
        since), or one that runs while the client is ready again, is dropped
        without touching the per-drop flag, so the next real drop still pushes.
        """
        if epoch != self._connection_epoch or self.is_ready:
            return
        if self._unavailable_pushed:
            return
        self._unavailable_pushed = True
        self._set_snapshot(self.get_snapshot())

    @callback
    def _push_ready(self, epoch: int) -> None:
        """
        End this drop when the client recovers on its own (raw READY, #97).

        Does what a supervisor reconnect does: logs "Panel connection
        restored" once (so the next drop warns again), clears the per-drop
        flag, pushes the snapshot so entities come back, and refreshes the
        CSM for anything missed while the link was down. A READY queued before
        a newer disconnect (epoch moved on), or one that runs while the client
        is not ready, is stale and does nothing.
        """
        if epoch != self._connection_epoch or not self.is_ready:
            return
        if not (self._unavailable_pushed or self._unavailable_logged):
            return
        if self._unavailable_logged:
            _LOGGER.info("Panel connection restored")
            self._unavailable_logged = False
        self._unavailable_pushed = False
        self._handle_reconnected()

    @callback
    def _schedule_panel_mac_check(self) -> None:
        """Verify (or fill) the entry's panel MAC by unicast discovery."""
        self._create_background_task(
            async_verify_panel_mac(self.hass, self.config_entry),
            "elke27 panel mac check",
        )

    def _log_unavailable(self) -> None:
        """Log the panel as unavailable once."""
        if self._unavailable_logged:
            return
        _LOGGER.warning("Panel connection lost")
        self._unavailable_logged = True

    def start_reauth_once(self) -> None:
        """Start a reauth flow at most once until the entry reloads."""
        self._reconnect.start_reauth_once()

    @callback
    def _handle_reconnected(self) -> None:
        """Refresh and push an update after an automatic reconnect."""
        # Push right away so entities become available even on a quiet panel,
        # then refresh the CSM for anything missed while disconnected.
        self._set_snapshot(self.get_snapshot())
        self._schedule_refresh_after_connect()

    @callback
    def _schedule_refresh_after_connect(self) -> None:
        """
        Start one refresh after a (re)connect.

        Both the reconnect listener and ConnectionStateChanged(connected=True)
        ask for this; a refresh already pending or running covers both.
        """
        if self._refresh_task is not None and not self._refresh_task.done():
            return
        self._refresh_task = self._create_background_task(
            self._async_refresh_after_connect(), f"{DOMAIN} refresh"
        )

    async def async_refresh_now(self) -> None:
        """Perform a full CSM refresh and update the snapshot."""
        async with self._refresh_lock:
            await self.refresh_csm()
        self._set_snapshot(self.get_snapshot())

    async def async_refresh_domains(self, domains: Iterable[str]) -> None:
        """
        Refresh domain configs one by one, then push the snapshot.

        A connection error stops the refresh and is raised; any other library
        error is logged and the remaining domains are still refreshed.
        """
        async with self._refresh_lock:
            await self._async_refresh_domains_locked(set(domains))
        self._set_snapshot(self.get_snapshot())

    async def _async_refresh_domains_locked(self, domains: set[str]) -> None:
        for domain in sorted(domains):
            try:
                await self.refresh_domain_config(domain)
            except AUTH_ERRORS:
                raise
            except HomeAssistantError:
                raise
            except COMMAND_ERRORS as err:
                if is_connection_error(err) or is_timeout_error(err):
                    raise
                _LOGGER.debug("Domain refresh failed for %s: %s", domain, err)

    def _handle_event(self, event: Any) -> None:
        """Handle client events on the Home Assistant event loop."""
        self.hass.loop.call_soon_threadsafe(self._process_event, event)

    @callback
    def _process_event(self, event: Any) -> None:
        """Process an event from the client."""
        if isinstance(event, ZoneStatusUpdated):
            _LOGGER.debug(
                "Zone status event received: zone_id=%s changed_fields=%s",
                event.zone_id,
                event.changed_fields,
            )
        if isinstance(event, ConnectionStateChanged):
            if event.connected:
                self._unavailable_pushed = False
                self._schedule_refresh_after_connect()
            else:
                self._connection_epoch += 1
                # Push the snapshot (one listener update per drop, shared with
                # the link listener): every entity re-reads is_ready and goes
                # unavailable together, instead of showing stale state.
                self._push_unavailable(self._connection_epoch)
            return
        if isinstance(event, CsmSnapshotUpdated):
            self._set_snapshot(self.get_snapshot())
            return
        if isinstance(event, (DomainCsmChanged, TableCsmChanged)):
            if event.csm_domain:
                self._queue_domain_refresh({event.csm_domain})
            return
        self._set_snapshot(self.get_snapshot())

    def _queue_domain_refresh(self, domains: Iterable[str]) -> None:
        """Queue a refresh for the given domains and debounce updates."""
        self._pending_domains.update(_normalize_domains(domains))
        if self._debounce_task is None or self._debounce_task.done():
            self._debounce_task = self._create_background_task(
                self._async_debounced_refresh(), f"{DOMAIN} debounced refresh"
            )

    async def _async_refresh_after_connect(self) -> None:
        """Refresh after a reconnect; a failure is logged, the reconnect stands."""
        try:
            await self.async_refresh_now()
        except AUTH_ERRORS as err:
            _LOGGER.warning("Refresh after reconnect auth failure: %s", err)
            self.start_reauth_once()
        except REFRESH_AFTER_CONNECT_ERRORS as err:
            _LOGGER.debug("Refresh after reconnect failed: %s", err)

    async def _async_debounced_refresh(self) -> None:
        """Refresh pending domains after a short debounce delay."""
        await asyncio.sleep(self._debounce_seconds)
        async with self._refresh_lock:
            while self._pending_domains:
                domains = set(self._pending_domains)
                self._pending_domains.clear()
                try:
                    await self._async_refresh_domains_locked(domains)
                except AUTH_ERRORS as err:
                    self._pending_domains.clear()
                    _LOGGER.warning("Domain refresh auth failure: %s", err)
                    self.start_reauth_once()
                    break
                except DEBOUNCED_REFRESH_ERRORS as err:
                    # The link is down: drop the queue; the reconnect refresh
                    # reloads everything.
                    self._pending_domains.clear()
                    _LOGGER.debug("Domain refresh stopped: %s", err)
                    break
        self._set_snapshot(self.get_snapshot())

    def _set_snapshot(self, snapshot: PanelSnapshot | None) -> None:
        """Update coordinator data and track snapshot version."""
        self._snapshot_seq += 1
        self.async_set_updated_data(snapshot or PanelSnapshot.empty())


def _log_command_failure(
    kind: str, target_id: int, action: str, err: Exception
) -> None:
    """Log a rejected arm/disarm/bypass, with the panel error code when known."""
    panel_code = panel_error_code(err)
    if panel_code is not None:
        _LOGGER.warning(
            "%s %s %s failed: %s (panel error %s)",
            kind,
            target_id,
            action,
            err,
            panel_code,
        )
    else:
        _LOGGER.warning("%s %s %s failed: %s", kind, target_id, action, err)


def _connection_state(event: Elke27Event) -> bool | None:
    """Return True/False for connect/disconnect events, None otherwise."""
    if event.event_type is EventType.DISCONNECTED:
        return False
    if event.event_type is EventType.READY:
        return True
    if event.event_type is EventType.CONNECTION:
        connected = event.data.get("connected")
        if isinstance(connected, bool):
            return connected
    return None


def _normalize_domains(domains: Iterable[str] | str | None) -> set[str]:
    if domains is None:
        return set()
    if isinstance(domains, str):
        return {domains}
    return {str(domain) for domain in domains if domain}


def _panel_name_from_entry(panel: object | None) -> str | None:
    if isinstance(panel, dict):
        name = panel.get("panel_name") or panel.get("name")
        return str(name) if name else None
    return None


# Any library or network failure during setup: let Home Assistant retry.
SETUP_RETRY_ERRORS: tuple[type[Exception], ...] = (
    Elke27Error,
    E27Error,
    TimeoutError,
    OSError,
    ConfigEntryNotReady,
    HomeAssistantError,
)
