"""Thread module: blocking is allowed here."""

import socket
import time
from asyncio import run_coroutine_threadsafe


def pump(sock: socket.socket) -> bytes:
    time.sleep(0)
    return sock.recv(1)


def hand_back(coro, loop):
    return run_coroutine_threadsafe(coro, loop)
