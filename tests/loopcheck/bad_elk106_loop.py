import threading


class C:
    def __init__(self) -> None:
        self._lock = threading.Lock()

    def f(self) -> None:
        with self._lock:
            pass
