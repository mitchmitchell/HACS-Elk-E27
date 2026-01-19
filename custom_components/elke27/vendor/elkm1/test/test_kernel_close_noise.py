import pytest

from elke27_lib import session as session_mod
from elke27_lib.kernel import E27Kernel


class _FakeSession:
    def __init__(self) -> None:
        self.info = type("_Info", (), {"session_id": 1})()

    def close(self) -> None:
        return None


@pytest.mark.asyncio
async def test_explicit_close_suppresses_io_disconnect():
    kernel = E27Kernel()
    kernel._session = _FakeSession()
    events = []

    def _emit_connection_state(*, connected: bool, reason=None, error_type=None):
        events.append((connected, reason, error_type))

    kernel._emit_connection_state = _emit_connection_state  # type: ignore[assignment]

    await kernel.close()
    assert events == [(False, "closed", None)]

    kernel._on_session_disconnected(session_mod.SessionIOError("bad fd"))
    assert events == [(False, "closed", None)]
