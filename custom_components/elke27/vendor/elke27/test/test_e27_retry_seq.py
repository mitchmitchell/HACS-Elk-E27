from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

from elke27_lib.kernel import E27Kernel
from test.helpers.internal import get_private


def test_no_retry_after_timeout() -> None:
    kernel = E27Kernel(request_timeout_s=0.01, request_max_retries=1)
    sent: list[int] = []

    class _Session:
        def send_json(
            self,
            msg: dict[str, Any],
            *,
            priority: object = None,
            on_sent: Callable[[float], None] | None = None,
            on_fail: Callable[[BaseException], None] | None = None,
        ) -> None:
            _ = priority, on_fail
            seq = msg.get("seq")
            if isinstance(seq, int):
                sent.append(seq)
            if on_sent is not None:
                on_sent(0.0)

    def _noop(*_args: object, **_kwargs: object) -> None:
        return None

    kernel_any = cast(Any, kernel)
    kernel_any._session = _Session()
    kernel_any._log_outbound = _noop

    kernel.send_request_with_seq(
        1,
        "zone",
        "get_status",
        {"zone_id": 1},
        pending=True,
        opaque=None,
        expected_route=("zone", "get_status"),
    )
    assert sent == [1]
    on_reply_timeout = cast(Callable[[int], None], get_private(kernel, "_on_reply_timeout"))
    on_reply_timeout(1)
    assert sent == [1]
