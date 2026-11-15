# ruff: noqa: S101, SLF001, PLR2004
"""elke27 client snapshot after area_get_status (pin 35ff15f, path a)."""

from __future__ import annotations

import asyncio

from elke27_lib.client import Elke27Client
from elke27_lib.states import AreaState
from elke27_lib.types import DelayStateEntry, DelayStateExit
import pytest


@pytest.mark.asyncio
async def test_area_get_status_reply_updates_client_delay_state() -> None:
    """Inbound area.get_status updates kernel state and client snapshot delay_state."""
    client = Elke27Client(skip_connect_bootstrap=True)
    client._kernel.load_features_blocking(None)
    loop = asyncio.get_running_loop()
    client.bind_kernel_loop_thread_for_tests(loop)
    client._event_loop = loop
    state = client.state
    state.areas[1] = AreaState(
        area_id=1, name="Main", arm_state="DISARMED", arm_cmd_state="DISARMED"
    )
    state.table_info_by_domain["area"] = {"table_elements": 1}
    state.panel.app_version = "0.0.6.4"
    client._kernel._on_message(
        {
            "seq": 11,
            "area": {
                "get_status": {
                    "area_id": 1,
                    "arm_state": "DISARMED",
                    "arm_cmd_state": "ARMED_AWAY",
                    "ee_timer": 30,
                    "alarm_zone": "",
                }
            },
        }
    )
    await asyncio.sleep(0.05)
    exit_area = client.get_snapshot().areas[1]
    assert isinstance(exit_area.delay_state, DelayStateExit)
    assert exit_area.delay_state.seconds == 30
    assert exit_area.arming

    client._kernel._on_message(
        {
            "seq": 12,
            "area": {
                "get_status": {
                    "area_id": 1,
                    "arm_state": "ARMED_AWAY",
                    "arm_cmd_state": "ARMED_AWAY",
                    "ee_timer": 20,
                    "alarm_zone": "005",
                }
            },
        }
    )
    await asyncio.sleep(0.05)
    entry_area = client.get_snapshot().areas[1]
    assert isinstance(entry_area.delay_state, DelayStateEntry)
    assert entry_area.delay_state.seconds == 20
    assert entry_area.delay_state.zone == "005"
    assert entry_area.entry_delay
