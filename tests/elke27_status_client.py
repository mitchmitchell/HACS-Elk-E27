# ruff: noqa: S101, SLF001, SIM108, TC003
"""Mock client whose get_snapshot reflects elke27 status-reply handling."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from elke27_lib.client import Elke27Client
from elke27_lib.states import AreaState as KernelArea, ZoneState as KernelZone


def _seed_two_area_panel(live: Elke27Client) -> None:
    state = live.state
    state.panel.app_version = "0.0.6.4"
    state.table_info_by_domain["area"] = {"table_elements": 2}
    state.table_info_by_domain["zone"] = {"table_elements": 6}
    state.areas[1] = KernelArea(area_id=1, name="House", arm_state="DISARMED")
    state.areas[2] = KernelArea(area_id=2, name="Garage", arm_state="DISARMED")
    for zid, name, area_id, violated, bypassed in (
        (1, "Front Door", 1, True, False),
        (2, "Back Door", 1, True, False),
        (3, "Bypassed", 1, True, True),
        (4, "Garage Door", 2, True, False),
        (5, "No Area", None, True, False),
        (6, "Closed", 1, False, False),
    ):
        state.zones[zid] = KernelZone(
            zone_id=zid,
            name=name,
            area_id=area_id,
            violated=violated,
            bypassed=bypassed,
        )


async def _dispatch(live: Elke27Client, msg: Mapping[str, Any]) -> None:
    live._kernel._on_message(dict(msg))
    await asyncio.sleep(0)


def _resolve_reply(
    reply: Mapping[str, Any] | Callable[[], Mapping[str, Any]] | str | None,
    *,
    default: Mapping[str, Any] | str,
) -> Mapping[str, Any] | str:
    if reply is None:
        return default if isinstance(default, str) else dict(default)
    if callable(reply):
        value = reply()
    else:
        value = reply
    return value


def make_status_client(
    *,
    area_reply: Mapping[str, Any] | Callable[[], Mapping[str, Any]] | None = None,
    zones_reply: Mapping[str, Any]
    | Callable[[], Mapping[str, Any] | str]
    | str
    | None = "99",
    live: Elke27Client | None = None,
) -> MagicMock:
    """Return a mock client backed by elke27 kernel status handlers."""
    if live is None:
        live = Elke27Client(skip_connect_bootstrap=True)
        live._kernel.load_features_blocking(None)
        _seed_two_area_panel(live)
    seq = {"n": 0}
    client = MagicMock()

    async def _execute(command_key: str, **_params: Any) -> Any:
        if live._event_loop is None:
            loop = asyncio.get_running_loop()
            live.bind_kernel_loop_thread_for_tests(loop)
            live._event_loop = loop
        seq["n"] += 1
        if command_key == "area_get_status":
            payload = _resolve_reply(
                area_reply,
                default={"area_id": 1, "arm_state": "DISARMED"},
            )
            assert isinstance(payload, dict)
            await _dispatch(
                live,
                {"seq": seq["n"], "area": {"get_status": payload}},
            )
            return MagicMock(ok=True, data=payload, error=None)
        if command_key == "zone_get_all_zones_status":
            raw = _resolve_reply(zones_reply, default="99")
            if isinstance(raw, str):
                payload = {"status": raw}
            else:
                payload = dict(raw)
            await _dispatch(
                live,
                {
                    "seq": seq["n"],
                    "zone": {"get_all_zones_status": payload},
                },
            )
            return MagicMock(ok=True, data=payload, error=None)
        return MagicMock(ok=True, data={}, error=None)

    client.async_execute = AsyncMock(side_effect=_execute)
    client.get_snapshot = live.get_snapshot
    client.async_arm_area = AsyncMock(return_value=MagicMock(ok=True))
    client.async_disarm_area = AsyncMock(return_value=MagicMock(ok=True))
    client.async_disconnect = AsyncMock(return_value=None)
    return client
