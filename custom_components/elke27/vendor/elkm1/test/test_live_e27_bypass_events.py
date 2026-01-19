"""
Live E27 test: observe bypass + unbypass events from external sources (e.g., Elk app).

Run:
  source ~/elk-e27-env-vars.sh
  pytest -q -m live_e27 test/test_live_e27_bypass_events.py -s
"""

from __future__ import annotations

import asyncio

import pytest

from elke27_lib.client import Elke27Client
from elke27_lib.events import ZoneStatusUpdated

_LIVE_TIMEOUT_S = 30.0


@pytest.mark.live_e27
@pytest.mark.asyncio
async def test_live_zone_bypass_event_cycle(live_e27_client: Elke27Client) -> None:
    """
    Wait for any zone to be bypassed, then unbypassed, within 30 seconds.
    """
    loop = asyncio.get_running_loop()
    bypass_event = asyncio.Event()
    unbypass_event = asyncio.Event()
    bypassed_zone_id: int | None = None

    def _on_evt(evt) -> None:
        nonlocal bypassed_zone_id
        if not isinstance(evt, ZoneStatusUpdated):
            return
        if "bypassed" not in evt.changed_fields:
            return
        zone = live_e27_client._kernel.state.zones.get(evt.zone_id)
        if zone is None or not isinstance(zone.bypassed, bool):
            return
        if zone.bypassed:
            if bypassed_zone_id is None:
                bypassed_zone_id = evt.zone_id
                loop.call_soon_threadsafe(bypass_event.set)
        elif bypassed_zone_id == evt.zone_id:
            loop.call_soon_threadsafe(unbypass_event.set)

    unsubscribe = live_e27_client.subscribe_typed(_on_evt)
    try:
        deadline = loop.time() + _LIVE_TIMEOUT_S
        await asyncio.wait_for(bypass_event.wait(), timeout=_LIVE_TIMEOUT_S)
        remaining = deadline - loop.time()
        if remaining <= 0:
            pytest.fail("Timed out before receiving unbypass event.")
        await asyncio.wait_for(unbypass_event.wait(), timeout=remaining)
    finally:
        unsubscribe()
