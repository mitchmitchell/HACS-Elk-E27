from __future__ import annotations

import asyncio

import pytest

from elke27_lib.client import Elke27Client


@pytest.mark.live_e27
@pytest.mark.asyncio
async def test_live_system_r_u_alive(live_e27_client: Elke27Client) -> None:
    kernel = live_e27_client._kernel
    kernel._stop_keepalive()
    kernel._keepalive_interval_s = 1.0
    kernel._keepalive_timeout_s = 2.0
    kernel._keepalive_max_missed = 2
    kernel._keepalive_enabled = True

    fired = asyncio.Event()
    result_box: dict[str, bool] = {}
    original = kernel._send_keepalive_request

    async def _wrapped_keepalive() -> bool:
        ok = await original()
        result_box["ok"] = ok
        fired.set()
        return ok

    kernel._send_keepalive_request = _wrapped_keepalive  # type: ignore[assignment]
    kernel._start_keepalive()

    await asyncio.wait_for(fired.wait(), timeout=10.0)
    assert result_box.get("ok") is True
