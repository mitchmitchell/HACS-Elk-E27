"""Loop module that does nothing blocking."""

import asyncio
import time


async def tick() -> float:
    await asyncio.sleep(0)
    return time.monotonic()


async def offload() -> None:
    await asyncio.to_thread(time.sleep, 0)
