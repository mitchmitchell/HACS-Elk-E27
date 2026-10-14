"""Reconnect supervisor for the Elke27 hub (ADR-0125 D2)."""

from __future__ import annotations

import asyncio
import logging
import random
from typing import TYPE_CHECKING, Any

from elke27_lib.errors import Elke27Error

from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import issue_registry as ir

from .const import (
    DOMAIN,
    ISSUE_RECONNECT_FAILED,
    RECONNECT_NON_TRANSPORT_ISSUE_THRESHOLD,
)
from .errors import AUTH_ERRORS, COMMAND_ERRORS, is_reconnect_retryable

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Coroutine

    from .models import Elke27ConfigEntry


# D2 is a pure move: keep logging under the hub's logger name so existing
# logger configuration and log filters see no change.
_LOGGER = logging.getLogger(f"{__package__}.hub")

# What a reconnect attempt may fail with: the library's errors, raw timeouts and
# OS errors, and not-ready. Anything else is a bug and ends the loop (HA logs it)
# instead of retrying forever.
RECONNECT_ERRORS: tuple[type[Exception], ...] = (*COMMAND_ERRORS, ConfigEntryNotReady)


class ReconnectSupervisor:
    """
    Run the reconnect loop, the Repairs issue and reauth-once for one entry.

    The supervisor holds no client reference: it only calls ``connect``, which
    sends no panel writes, so a failed or queued command is never replayed.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry: Elke27ConfigEntry | None,
        *,
        connect: Callable[[], Awaitable[None]],
        is_stopping: Callable[[], bool],
        on_reconnected: Callable[[], None],
    ) -> None:
        """Initialize the supervisor."""
        self._hass = hass
        self._entry = entry
        self._connect = connect
        self._is_stopping = is_stopping
        self._on_reconnected = on_reconnected
        self._reauth_requested = False
        self._reconnect_task: asyncio.Task[None] | None = None
        self._reconnect_attempts = 0
        self._reconnect_non_transport_streak = 0
        self._issue_active = False

    @property
    def attempts(self) -> int:
        """Return the number of failed attempts in the current outage."""
        return self._reconnect_attempts

    @property
    def issue_active(self) -> bool:
        """Return True while this supervisor has a Repairs issue raised."""
        return self._issue_active

    @property
    def reauth_requested(self) -> bool:
        """Return True once reauth was started (reset only on reload)."""
        return self._reauth_requested

    @property
    def task(self) -> asyncio.Task[None] | None:
        """Return the running reconnect task, if any."""
        return self._reconnect_task

    @callback
    def start(self) -> None:
        """Schedule reconnection attempts when the panel disconnects."""
        if self._is_stopping():
            return
        if self._reconnect_task is not None and not self._reconnect_task.done():
            return
        _LOGGER.debug("Creating reconnect task")
        self._reconnect_task = self._create_background_task(
            self._async_run(), "elke27 reconnect"
        )

    def _create_background_task(
        self, coro: Coroutine[Any, Any, None], name: str
    ) -> asyncio.Task[None]:
        """Create a task that is cancelled when the config entry unloads."""
        if self._entry is not None:
            return self._entry.async_create_background_task(self._hass, coro, name)
        return self._hass.async_create_background_task(coro, name)

    @callback
    def cancel(self) -> None:
        """Cancel any scheduled reconnection attempts."""
        if self._reconnect_task is None:
            return
        if not self._reconnect_task.done():
            self._reconnect_task.cancel()
        self._reconnect_task = None
        self._reconnect_attempts = 0

    async def async_stop(self) -> None:
        """Cancel the reconnect task and wait for it to finish (unload path)."""
        task = self._reconnect_task
        self._reconnect_task = None
        if task is not None and not task.done():
            task.cancel()
            # Wait for the loop to finish without swallowing a cancellation
            # of this call itself.
            await asyncio.wait([task])

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
    def clear_issue(self) -> None:
        """Remove the reconnect repair issue when the link is healthy or unloading."""
        issue_id = self._reconnect_repair_issue_id()
        if issue_id is None:
            return
        ir.async_delete_issue(self._hass, DOMAIN, issue_id)
        self._issue_active = False

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
        self._issue_active = True

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

    async def _async_run(self) -> None:
        """Reconnect with exponential backoff until successful or stopped."""
        while not self._is_stopping():
            _LOGGER.debug("Reconnect attempt %s starting", self._reconnect_attempts + 1)
            try:
                await self._connect()
            except AUTH_ERRORS as err:
                # The panel no longer accepts the link: retrying cannot help.
                _LOGGER.warning("Reconnect stopped; relink required: %s", err)
                self._reconnect_attempts = 0
                self._reconnect_non_transport_streak = 0
                self.clear_issue()
                self.start_reauth_once()
                return
            except RECONNECT_ERRORS as err:
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
                self.clear_issue()
                self._on_reconnected()
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
