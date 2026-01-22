from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import cast

import pytest

from elke27_lib.client import Elke27Client
from test.helpers.internal import get_kernel, get_private, set_private


@pytest.mark.live_e27
@pytest.mark.asyncio
async def test_live_system_r_u_alive(live_e27_client: Elke27Client) -> None:
    kernel = get_kernel(live_e27_client)
    stop_keepalive = cast(Callable[[], None], get_private(kernel, "_stop_keepalive"))
    stop_keepalive()
    set_private(kernel, "_keepalive_interval_s", 1.0)
    set_private(kernel, "_keepalive_timeout_s", 2.0)
    set_private(kernel, "_keepalive_max_missed", 2)
    set_private(kernel, "_keepalive_enabled", True)

    fired = asyncio.Event()
    result_box: dict[str, bool] = {}
    original = cast(Callable[[], Awaitable[bool]], get_private(kernel, "_send_keepalive_request"))

    async def _wrapped_keepalive() -> bool:
        ok = await original()
        result_box["ok"] = ok
        fired.set()
        return ok

    set_private(kernel, "_send_keepalive_request", _wrapped_keepalive)
    start_keepalive = cast(Callable[[], None], get_private(kernel, "_start_keepalive"))
    start_keepalive()

    await asyncio.wait_for(fired.wait(), timeout=10.0)
    assert result_box.get("ok") is True
