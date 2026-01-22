"""
Live E27 test: control_authenticate via v2 async_execute.

Run:
  source ~/elk-e27-env-vars.sh
  pytest -q -m live_e27 test/test_live_e27_authenticate.py -s
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from typing import cast

import pytest

from elke27_lib.client import Elke27Client
from elke27_lib.const import E27ErrorCode
from test.helpers.error_codes import describe_error, extract_error_code
from test.helpers.internal import get_kernel, get_private, set_private
from test.helpers.payload_validation import assert_payload_shape


def _get_pin() -> str:
    pin_str = (os.environ.get("ELKE27_PIN") or "").strip()
    if not pin_str:
        pytest.skip("ELKE27_PIN not set; skipping authenticate test.")
    if not pin_str.isdigit():
        pytest.fail("ELKE27_PIN must be numeric.")
    if int(pin_str) <= 0:
        pytest.fail("ELKE27_PIN must be a positive integer.")
    return pin_str


@pytest.mark.live_e27
@pytest.mark.asyncio
async def test_live_control_authenticate(live_e27_client: Elke27Client) -> None:
    log_level = str(os.environ.get("LOG_LEVEL", "INFO") or "INFO").upper()
    logging.basicConfig(level=getattr(logging, log_level, logging.INFO), force=True)
    logging.getLogger("elke27_lib.session").setLevel(logging.DEBUG)

    pin = int(_get_pin())
    kernel = get_kernel(live_e27_client)
    original_on_message = cast(
        Callable[[dict[str, object]], object], get_private(kernel, "_on_message")
    )
    enable_raw_log = log_level == "DEBUG"
    if enable_raw_log:

        def _log_on_message(msg: dict[str, object]) -> object:
            logging.getLogger(__name__).debug("raw inbound msg=%r", msg)
            return original_on_message(msg)

        set_private(kernel, "_on_message", _log_on_message)
    try:
        result = await live_e27_client.async_execute("control_authenticate", pin=pin)
        if not result.ok:
            if extract_error_code(result.error) == E27ErrorCode.ELKERR_NOT_READY:
                pytest.skip("Panel reported not-ready for control_authenticate.")
            pytest.fail(f"control_authenticate failed: {describe_error(result.error)}")
        assert_payload_shape("control_authenticate", result.data)
    finally:
        if enable_raw_log:
            set_private(kernel, "_on_message", original_on_message)
