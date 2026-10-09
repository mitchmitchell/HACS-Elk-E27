"""Hub wrapper for the Elke27 client lifecycle."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING, Any

from elke27_lib import (
    ArmMode,
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
    Elke27InvalidArgument,
    Elke27LinkRequiredError,
    Elke27PinRequiredError,
)

from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError

from .const import READY_TIMEOUT
from .identity import build_client_identity

if TYPE_CHECKING:
    from collections.abc import Callable

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
    ) -> None:
        """Initialize the hub wrapper."""
        self._hass = hass
        self._host = host
        self._port = port
        self._link_keys_json = link_keys_json
        self._integration_serial = integration_serial
        self._panel_name = panel_name
        self._client: Elke27Client | None = None
        self._connection_unsubscribe: Callable[[], None] | None = None
        self._connect_lock = asyncio.Lock()
        self._reconnect_task: asyncio.Task[None] | None = None
        self._reconnect_attempts = 0
        self._stopping = False
        self._unavailable_logged = False
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
            link_keys = LinkKeys.from_json(self._link_keys_json)
            client = Elke27Client(ClientConfig())
            client.set_client_identity(build_client_identity(self._integration_serial))
            self._client = client

            def _raise_not_ready() -> None:
                msg = "The client did not become ready before timeout"
                raise ConfigEntryNotReady(msg)

            try:
                await client.async_connect(self._host, self._port, link_keys)
                ready = await client.wait_ready(timeout_s=READY_TIMEOUT)
                if not ready:
                    _raise_not_ready()
                self._connection_unsubscribe = client.subscribe(
                    self._handle_connection_event
                )
                self._resubscribe_typed_callbacks()
                if self._unavailable_logged:
                    _LOGGER.info("Panel connection restored")
                    self._unavailable_logged = False
            except Exception:
                with contextlib.suppress(Exception):
                    await client.async_disconnect()
                self._client = None
                raise

    async def async_disconnect(self) -> None:
        """Disconnect the client and unregister event handlers."""
        self._stopping = True
        if self._reconnect_task is not None:
            self._reconnect_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reconnect_task
            self._reconnect_task = None
        await self._async_disconnect()

    async def _async_disconnect(self) -> None:
        """Disconnect the client and unregister event handlers."""
        was_connected = self._client is not None
        if self._connection_unsubscribe is not None:
            self._connection_unsubscribe()
            self._connection_unsubscribe = None
        if self._client is not None:
            await self._client.async_disconnect()
        self._client = None
        self._clear_typed_subscriptions()
        if was_connected:
            self._log_unavailable()

    def get_snapshot(self) -> PanelSnapshot | None:
        """Return the latest client snapshot."""
        client = self._client
        if client is None:
            return None
        return client.get_snapshot()

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
        await client.async_set_output(output_id, on=state)
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
            self._hass.async_create_background_task(
                self._async_refresh_light_later(light_id),
                f"elke27 light {light_id} status refresh",
            )
        return ok

    async def _async_refresh_light(self, light_id: int) -> None:
        """Request a light's status; failures are logged, not raised."""
        try:
            await self._async_execute("light_get_status", light_id=light_id)
        except Exception as err:  # noqa: BLE001
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
        self, area_id: int, snapshot: PanelSnapshot | None, pin: str | None
    ) -> None:
        """Bypass open, non-bypassed zones assigned to the given area."""
        for zone in area_faulted_zones(snapshot, area_id):
            try:
                bypassed = await self.async_set_zone_bypass(
                    zone.zone_id, bypassed=True, pin=pin
                )
            except Elke27PinRequiredError:
                raise
            except HomeAssistantError as err:
                raise ZoneBypassFailedError(zone, str(err)) from err
            if not bypassed:
                raise ZoneBypassFailedError(zone, "bypass was not acknowledged.")

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
        except (Elke27Error, Elke27InvalidArgument) as err:
            _log_command_failure("Zone", zone_id, "bypass", err)
            raise HomeAssistantError(_error_message(err)) from err
        return True

    async def _async_execute(self, command_key: str, **params: Any) -> bool:
        """Run a raw elke27 command for domains without a public client method."""
        client = self._client
        if client is None:
            return False
        result = await client.async_execute(command_key, **params)
        if not result.ok:
            if result.error is not None:
                raise result.error
            return False
        return True

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
        except (Elke27Error, Elke27InvalidArgument) as err:
            _log_command_failure("Area", area_id, "arming", err)
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
                unsubscribe()
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
        except (Elke27Error, Elke27InvalidArgument) as err:
            _log_command_failure("Area", area_id, "disarm", err)
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
        self._reconnect_task = self._hass.async_create_task(
            self._async_reconnect_loop()
        )

    @callback
    def _cancel_reconnect(self) -> None:
        """Cancel any scheduled reconnection attempts."""
        if self._reconnect_task is None:
            return
        if not self._reconnect_task.done():
            self._reconnect_task.cancel()
        self._reconnect_task = None
        self._reconnect_attempts = 0

    def _log_unavailable(self) -> None:
        """Log the panel as unavailable once."""
        if self._unavailable_logged:
            return
        _LOGGER.info("Panel connection lost")
        self._unavailable_logged = True

    async def _async_reconnect_loop(self) -> None:
        """Reconnect with exponential backoff until successful or stopped."""
        while not self._stopping:
            _LOGGER.debug("Reconnect attempt %s starting", self._reconnect_attempts + 1)
            try:
                await self._async_connect()
            except Elke27LinkRequiredError:
                _LOGGER.exception("Reconnect aborted")
                return
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("Reconnect attempt failed: %s", err)
            else:
                self._reconnect_attempts = 0
                return
            self._reconnect_attempts += 1
            delay = min(300, 2**self._reconnect_attempts)
            _LOGGER.debug(
                "Reconnect attempt %s sleeping for %s seconds",
                self._reconnect_attempts,
                delay,
            )
            await asyncio.sleep(delay)


class ZoneBypassFailedError(HomeAssistantError):
    """Bypass could not be applied to a zone."""

    def __init__(self, zone: ZoneState, reason: str) -> None:
        """Initialize with the zone that failed and the panel reason."""
        self.zone = zone
        self.reason = reason
        super().__init__(f"{zone_bypass_label(zone)}: {reason}")


def area_faulted_zones(snapshot: PanelSnapshot | None, area_id: int) -> list[ZoneState]:
    """Return open, non-bypassed zones assigned to the given area."""
    if snapshot is None:
        return []
    return [zone for zone in snapshot.faulted_zones if zone.area_id == area_id]


def zone_bypass_label(zone: ZoneState) -> str:
    """Return a user-facing label for a zone."""
    name = (zone.name or "").strip()
    if name:
        return name
    return f"Zone {zone.zone_id}"


def _validated_pin(pin: str) -> str:
    """Return the user code as a digit string, or raise if it is not numeric."""
    value = str(pin).strip()
    if not value.isdigit():
        msg = "Code must be numeric."
        raise HomeAssistantError(msg)
    return value


def _library_arm_mode(mode: Any) -> ArmMode:
    """Map an integration arm request to the elke27 arm mode."""
    # The E27 has Away and Stay arming only; it has no Night mode.
    if mode in (ArmMode.ARMED_STAY, ArmMode.ARMED_AWAY):
        return mode
    # Custom bypass: the entity bypasses open zones first, then arms away.
    if isinstance(mode, str) and mode.upper() == "ARMED_CUSTOM_BYPASS":
        return ArmMode.ARMED_AWAY
    msg = "Arm mode is not supported."
    raise HomeAssistantError(msg)


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


def _error_message(err: Exception) -> str:
    """Return a user-facing message for a library error."""
    return getattr(err, "user_message", None) or str(err) or type(err).__name__


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
