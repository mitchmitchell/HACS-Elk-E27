from __future__ import annotations

from elke27_lib.kernel import E27Kernel


def test_no_retry_after_timeout() -> None:
    kernel = E27Kernel(request_timeout_s=0.01, request_max_retries=1)
    sent: list[int] = []

    class _Session:
        def send_json(self, msg, *, priority=None, on_sent=None, on_fail=None):
            sent.append(msg.get("seq"))
            if on_sent is not None:
                on_sent(0.0)

    kernel._session = _Session()
    kernel._log_outbound = lambda *args, **kwargs: None  # type: ignore[assignment]

    kernel._send_request_with_seq(
        1,
        "zone",
        "get_status",
        {"zone_id": 1},
        pending=True,
        opaque=None,
        expected_route=("zone", "get_status"),
    )
    assert sent == [1]
    kernel._on_reply_timeout(1)
    assert sent == [1]
