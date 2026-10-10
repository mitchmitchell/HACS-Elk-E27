"""Data update coordinator for the Elke27 integration."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING, Any

from elke27_lib import PanelSnapshot
from elke27_lib.errors import Elke27Error
from elke27_lib.events import (
    ConnectionStateChanged,
    CsmSnapshotUpdated,
    DomainCsmChanged,
    TableCsmChanged,
    ZoneStatusUpdated,
)

from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import DOMAIN

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine, Iterable

    from homeassistant.config_entries import ConfigEntry

    from .hub import Elke27Hub

_LOGGER = logging.getLogger(__name__)


class Elke27DataUpdateCoordinator(DataUpdateCoordinator[PanelSnapshot | None]):
    """Coordinate Elke27 snapshot updates and CSM refreshes."""

    def __init__(
        self,
        hass: HomeAssistant,
        hub: Elke27Hub,
        entry: ConfigEntry,
        *,
        debounce_seconds: float = 0.3,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(hass, _LOGGER, name=DOMAIN, config_entry=entry)
        self._hub = hub
        self._debounce_seconds = debounce_seconds
        self._pending_domains: set[str] = set()
        self._refresh_lock = asyncio.Lock()
        self._debounce_task: asyncio.Task[None] | None = None
        self._unsubscribe: Callable[[], None] | None = None
        self._unsubscribe_reconnect: Callable[[], None] | None = None

    async def async_start(self) -> None:
        """Subscribe to hub events and seed snapshot data."""
        if self._unsubscribe is not None:
            self._unsubscribe()
        self._unsubscribe = self._hub.subscribe_typed(self._handle_event)
        if self._unsubscribe_reconnect is None:
            self._unsubscribe_reconnect = self._hub.add_reconnect_listener(
                self._handle_reconnected
            )
        self._set_snapshot(self._hub.get_snapshot())

    @callback
    def _handle_reconnected(self) -> None:
        """Refresh and push an update after an automatic reconnect."""
        # Push right away so entities become available even on a quiet panel,
        # then refresh the CSM for anything missed while disconnected.
        self._set_snapshot(self._hub.get_snapshot())
        self._create_task(self._async_refresh_after_connect(), "refresh")

    async def async_stop(self) -> None:
        """Stop coordinating updates and clean up resources."""
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        if self._unsubscribe_reconnect is not None:
            self._unsubscribe_reconnect()
            self._unsubscribe_reconnect = None
        if self._debounce_task is not None:
            self._debounce_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._debounce_task
            self._debounce_task = None

    async def async_refresh_now(self) -> None:
        """Perform a full CSM refresh and update the snapshot."""
        await self._hub.refresh_csm()
        self._set_snapshot(self._hub.get_snapshot())

    def _handle_event(self, event: Any) -> None:
        """Handle hub events on the Home Assistant event loop."""
        self.hass.loop.call_soon_threadsafe(self._process_event, event)

    @callback
    def _process_event(self, event: Any) -> None:
        """Process an event from the hub."""
        if isinstance(event, ZoneStatusUpdated):
            _LOGGER.debug(
                "Zone status event received: zone_id=%s changed_fields=%s",
                event.zone_id,
                event.changed_fields,
            )
        if isinstance(event, ConnectionStateChanged):
            if event.connected:
                self._create_task(self._async_refresh_after_connect(), "refresh")
            else:
                # Push an update so entities re-read availability right away
                # instead of showing stale state until the next panel event.
                self._set_snapshot(self._hub.get_snapshot())
            return
        if isinstance(event, CsmSnapshotUpdated):
            self._set_snapshot(self._hub.get_snapshot())
            return
        if isinstance(event, (DomainCsmChanged, TableCsmChanged)):
            if event.csm_domain:
                self._queue_domain_refresh({event.csm_domain})
            return
        self._set_snapshot(self._hub.get_snapshot())

    def _queue_domain_refresh(self, domains: Iterable[str]) -> None:
        """Queue a refresh for the given domains and debounce updates."""
        self._pending_domains.update(_normalize_domains(domains))
        if self._debounce_task is None or self._debounce_task.done():
            self._debounce_task = self._create_task(
                self._async_debounced_refresh(), "debounced refresh"
            )

    def _create_task(
        self, coro: Coroutine[Any, Any, None], name: str
    ) -> asyncio.Task[None]:
        """Create a task tied to the config entry, so unload cancels it."""
        return self.config_entry.async_create_task(self.hass, coro, f"{DOMAIN} {name}")

    async def _async_refresh_after_connect(self) -> None:
        """Refresh after a reconnect; a failure is logged, the reconnect stands."""
        try:
            await self.async_refresh_now()
        except (Elke27Error, HomeAssistantError) as err:
            _LOGGER.debug("Refresh after reconnect failed: %s", err)

    async def _async_debounced_refresh(self) -> None:
        """Refresh pending domains after a short debounce delay."""
        await asyncio.sleep(self._debounce_seconds)
        async with self._refresh_lock:
            while self._pending_domains:
                domains = set(self._pending_domains)
                self._pending_domains.clear()
                results = await asyncio.gather(
                    *(self._hub.refresh_domain_config(domain) for domain in domains),
                    return_exceptions=True,
                )
                for domain, result in zip(domains, results, strict=True):
                    if isinstance(result, Exception):
                        _LOGGER.debug(
                            "Domain refresh failed for %s: %s", domain, result
                        )
        self._set_snapshot(self._hub.get_snapshot())

    def _set_snapshot(self, snapshot: PanelSnapshot | None) -> None:
        """Update coordinator data and track snapshot version."""
        self.async_set_updated_data(snapshot)


def _normalize_domains(domains: Iterable[str] | str | None) -> set[str]:
    if domains is None:
        return set()
    if isinstance(domains, str):
        return {domains}
    return {str(domain) for domain in domains if domain}
