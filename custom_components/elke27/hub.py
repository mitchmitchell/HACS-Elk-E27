"""Hub wrapper for the Elke27 client lifecycle."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
import logging
import random
from typing import TYPE_CHECKING, Any

from elke27_lib import (
    ClientConfig,
    Elke27Event,
    EventType,
    LinkKeys,
    PanelSnapshot,
    ZoneState,
)
from elke27_lib.client import Elke27Client
from elke27_lib.errors import (
    Elke27Error,
    Elke27LinkRequiredError,
    Elke27PinRequiredError,
)

from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers import issue_registry as ir

from .arming import (  # noqa: F401  # re-exported from hub for compatibility (D1 move)
    _ARMED_MODES,
    _ZONE_STATUS_BYPASSED,
    _ZONE_STATUS_KNOWN,
    _ZONE_STATUS_VIOLATED,
    ARM_STATUS_POLL_INTERVAL,
    ARM_STATUS_POLL_TIMEOUT,
    PANEL_ERROR_ALREADY_ARMED,
    PIN_REQUIRED_REASON,
    ZoneBypassFailedError,
    _arm_mode_from_text,
    _library_arm_mode,
    _validated_pin,
    _zone_with_status_char,
    area_armed_from_status_reply,
    area_arming_from_status_reply,
    area_faulted_zones,
    area_is_armed,
    area_skip_automatic_arm_from_status_reply,
    is_already_armed_refusal,
    is_definitive_refusal,
    snapshot_with_status,
    zone_bypass_label,
)
from .const import (
    DOMAIN,
    ISSUE_RECONNECT_FAILED,
    READY_TIMEOUT,
    RECONNECT_NON_TRANSPORT_ISSUE_THRESHOLD,
)
from .errors import (  # noqa: F401  # re-exported from hub for compatibility (D1 move)
    AUTH_ERRORS,
    COMMAND_ERRORS,
    CONNECTION_MESSAGE,
    NOT_ACCEPTED_MESSAGE,
    TIMEOUT_MESSAGE,
    _error_message,
    is_connection_error,
    is_reconnect_retryable,
    is_timeout_error,
    panel_error_code,
)
from .identity import build_client_identity
from .panel_mac import async_verify_panel_mac

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Coroutine

    from .models import Elke27ConfigEntry


_LOGGER = logging.getLogger(__name__)


# Seconds to wait before re-reading a light's status after a set_status.
LIGHT_REFRESH_DELAY = 3.0


class Elke27Hub:
    """Manage a single Elke27 client instance."""

    def __init__(
        self,
        hass: HomeAssistant,
        host: str,
        port: int,
        link_keys_json: str,
        integration_serial: str,
        panel_name: str | None,
        *,
        entry: Elke27ConfigEntry | None = None,
    ) -> None:
        """Initialize the hub wrapper."""
        self._hass = hass
        # Background tasks are tied to the config entry so unload cancels them.
        self._entry = entry
        self._host = host
        self._port = port
        self._link_keys_json = link_keys_json
        self._integration_serial = integration_serial
        self._panel_name = panel_name
        self._client: Elke27Client | None = None
        self._connection_unsubscribe: Callable[[], None] | None = None
        self._connect_lock = asyncio.Lock()
        self._reauth_requested = False
        self._area_arm_locks: dict[int, asyncio.Lock] = {}
        self._arm_automatic_tasks: dict[int, set[asyncio.Task[Any]]] = {}
        self._disarm_cancelled: set[asyncio.Task[Any]] = set()
        self._disarm_pending: dict[int, int] = {}
        self._disarm_clear: dict[int, asyncio.Event] = {}
        self._reconnect_task: asyncio.Task[None] | None = None
        self._reconnect_attempts = 0
        self._reconnect_non_transport_streak = 0
        self._stopping = False
        self._unavailable_logged = False
        self._reconnect_listeners: list[Callable[[], None]] = []
        self._typed_callbacks: dict[
            Callable[[Any], None], Callable[[], None] | None
        ] = {}

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
        if snapshot is not None and snapshot.panel.panel_name:
            return snapshot.panel.panel_name
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
            client.set_client_identity(build_client_identity(self._integration_serial))
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

    async def async_disconnect(self) -> None:
        """Disconnect the client and unregister event handlers."""
        self._stopping = True
        task = self._reconnect_task
        self._reconnect_task = None
        if task is not None and not task.done():
            task.cancel()
            # Wait for the loop to finish without swallowing a cancellation
            # of this call itself.
            await asyncio.wait([task])
        self._clear_reconnect_repair_issue()
        await self._async_disconnect()

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
            level = 99
        ok = await self._async_execute(
            "light_set_status",
            light_id=light_id,
            status="ON" if state else "OFF",
            level=level,
        )
        if ok:
            # The set_status ack may not carry the new state (Z-Wave devices
            # report it later), so ask the panel for the light's status now and
            # once more after a short delay.
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

        The hub belongs to one config entry, so this is per (entry, area).
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
        try:
            await client.async_set_zone_bypass(
                zone_id, bypassed=bypassed, pin=pin_value
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
            raise HomeAssistantError(_error_message(err)) from err
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
        Older elke27 versions without request_link_check() fall back to their
        own keepalive.
        """
        if not is_timeout_error(err):
            return
        client = self._client
        check = getattr(client, "request_link_check", None)
        if check is None:
            return
        try:
            check()
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
        try:
            await client.async_arm_area(
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
            raise HomeAssistantError(_error_message(err)) from err
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
        try:
            await client.async_disarm_area(
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
            raise HomeAssistantError(_error_message(err)) from err
        return True

    def _handle_connection_event(self, event: Elke27Event) -> None:
        """Handle connection lifecycle events from the client."""
        if self._client is None:
            return
        connected = _connection_state(event)
        if connected is False:
            _LOGGER.debug("Panel disconnect event received; scheduling reconnect")
            self._log_unavailable()
            self._hass.loop.call_soon_threadsafe(self._schedule_reconnect)
        elif connected is True:
            self._hass.loop.call_soon_threadsafe(self._cancel_reconnect)

    @callback
    def _schedule_reconnect(self) -> None:
        """Schedule reconnection attempts when the panel disconnects."""
        if self._stopping:
            return
        if self._reconnect_task is not None and not self._reconnect_task.done():
            return
        _LOGGER.debug("Creating reconnect task")
        self._reconnect_task = self._create_background_task(
            self._async_reconnect_loop(), "elke27 reconnect"
        )

    @callback
    def _schedule_panel_mac_check(self) -> None:
        """Verify (or fill) the entry's panel MAC by unicast discovery."""
        if self._entry is None:
            return
        self._create_background_task(
            async_verify_panel_mac(self._hass, self._entry), "elke27 panel mac check"
        )

    def _create_background_task(
        self, coro: Coroutine[Any, Any, None], name: str
    ) -> asyncio.Task[None]:
        """Create a task that is cancelled when the config entry unloads."""
        if self._entry is not None:
            return self._entry.async_create_background_task(self._hass, coro, name)
        return self._hass.async_create_background_task(coro, name)

    @callback
    def _cancel_reconnect(self) -> None:
        """Cancel any scheduled reconnection attempts."""
        if self._reconnect_task is None:
            return
        if not self._reconnect_task.done():
            self._reconnect_task.cancel()
        self._reconnect_task = None
        self._reconnect_attempts = 0

    def add_reconnect_listener(
        self, listener: Callable[[], None]
    ) -> Callable[[], None]:
        """
        Call listener after each successful automatic reconnect.

        The connected event can fire before callbacks are re-attached on the new
        client, so listeners must not rely on seeing it.
        """
        self._reconnect_listeners.append(listener)

        def _remove() -> None:
            if listener in self._reconnect_listeners:
                self._reconnect_listeners.remove(listener)

        return _remove

    def _notify_reconnected(self) -> None:
        """Tell reconnect listeners the client is connected again."""
        for listener in list(self._reconnect_listeners):
            listener()

    def _log_unavailable(self) -> None:
        """Log the panel as unavailable once."""
        if self._unavailable_logged:
            return
        _LOGGER.info("Panel connection lost")
        self._unavailable_logged = True

    def start_reauth_once(self) -> None:
        """Start a reauth flow at most once until the entry reloads."""
        if self._entry is None or self._reauth_requested:
            return
        self._reauth_requested = True
        self._entry.async_start_reauth(self._hass)

    @callback
    def _reconnect_repair_issue_id(self) -> str | None:
        """Return the repairs issue id for this entry's reconnect failures."""
        if self._entry is None:
            return None
        return f"{ISSUE_RECONNECT_FAILED}_{self._entry.entry_id}"

    @callback
    def _clear_reconnect_repair_issue(self) -> None:
        """Remove the reconnect repair issue when the link is healthy or unloading."""
        issue_id = self._reconnect_repair_issue_id()
        if issue_id is None:
            return
        ir.async_delete_issue(self._hass, DOMAIN, issue_id)

    @callback
    def _ensure_reconnect_repair_issue(self) -> None:
        """Raise a repair issue after repeated non-transport reconnect failures."""
        if self._entry is None:
            return
        issue_id = self._reconnect_repair_issue_id()
        if issue_id is None:
            return
        ir.async_create_issue(
            self._hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key=ISSUE_RECONNECT_FAILED,
            translation_placeholders={"name": self._entry.title},
        )

    def _log_reconnect_failure(self, err: Exception, *, transport: bool) -> None:
        """Log a reconnect failure; non-transport streaks warn once per streak."""
        if transport:
            _LOGGER.debug("Reconnect attempt failed: %s", err)
            return
        first_in_streak = self._reconnect_non_transport_streak == 1
        if first_in_streak:
            if isinstance(err, Elke27Error):
                _LOGGER.warning("Reconnect attempt failed: %s", err)
            else:
                _LOGGER.warning("Reconnect attempt failed: %s", err, exc_info=err)
            return
        _LOGGER.debug("Reconnect attempt failed: %s", err)

    async def _async_reconnect_loop(self) -> None:
        """Reconnect with exponential backoff until successful or stopped."""
        while not self._stopping:
            _LOGGER.debug("Reconnect attempt %s starting", self._reconnect_attempts + 1)
            try:
                await self._async_connect()
            except AUTH_ERRORS as err:
                # The panel no longer accepts the link: retrying cannot help.
                _LOGGER.warning("Reconnect stopped; relink required: %s", err)
                self._reconnect_attempts = 0
                self._reconnect_non_transport_streak = 0
                self._clear_reconnect_repair_issue()
                self.start_reauth_once()
                return
            except Exception as err:  # noqa: BLE001
                transport = is_reconnect_retryable(err)
                if not transport:
                    self._reconnect_non_transport_streak += 1
                    if (
                        self._reconnect_non_transport_streak
                        >= RECONNECT_NON_TRANSPORT_ISSUE_THRESHOLD
                    ):
                        self._ensure_reconnect_repair_issue()
                self._log_reconnect_failure(err, transport=transport)
            else:
                self._reconnect_attempts = 0
                self._reconnect_non_transport_streak = 0
                self._clear_reconnect_repair_issue()
                self._notify_reconnected()
                return
            self._reconnect_attempts += 1
            # Cap the base delay at 300 s before jitter (max sleep is about 360 s).
            base_delay = min(300, 2**self._reconnect_attempts)
            delay = base_delay * random.uniform(0.8, 1.2)  # noqa: S311
            _LOGGER.debug(
                "Reconnect attempt %s sleeping for %s seconds",
                self._reconnect_attempts,
                round(delay, 2),
            )
            await asyncio.sleep(delay)


def _log_command_failure(
    kind: str, target_id: int, action: str, err: Exception
) -> None:
    """Log a rejected arm/disarm/bypass, with the panel error code when known."""
    panel_code = getattr(err, "panel_error_code", None)
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
