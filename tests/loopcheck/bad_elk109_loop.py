import asyncio


def f(coro) -> None:
    asyncio.run(coro)
