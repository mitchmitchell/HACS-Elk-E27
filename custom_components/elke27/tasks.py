"""Background task helper shared by the coordinator and reconnect supervisor."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import asyncio
    from collections.abc import Coroutine

    from homeassistant.core import HomeAssistant

    from .models import Elke27ConfigEntry


def create_background_task(
    hass: HomeAssistant,
    entry: Elke27ConfigEntry | None,
    coro: Coroutine[Any, Any, None],
    name: str,
) -> asyncio.Task[None]:
    """
    Create a task that is cancelled when the config entry unloads.

    Without an entry (unit tests) the task is tied to Home Assistant instead.
    Background tasks do not hold up startup or async_block_till_done.
    """
    if entry is not None:
        return entry.async_create_background_task(hass, coro, name)
    return hass.async_create_background_task(coro, name)
