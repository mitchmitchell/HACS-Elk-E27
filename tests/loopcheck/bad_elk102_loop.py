import socket


def f() -> None:
    sock = socket.socket()
    sock.connect(("h", 1))
