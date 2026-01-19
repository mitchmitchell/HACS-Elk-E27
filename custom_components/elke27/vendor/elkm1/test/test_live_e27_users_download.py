"""
Live E27 test: user enumeration via user_get_configured + user_get_attribs.

Run:
  source ~/elk-e27-env-vars.sh
  pytest -q -m live_e27 test/test_live_e27_users_download.py -s
"""

from __future__ import annotations

import logging
import os

import pytest

from elke27_lib.client import Elke27Client
from elke27_lib.const import E27ErrorCode
from test.helpers.error_codes import describe_error, extract_error_code
from test.helpers.payload_validation import assert_payload_shape


_LIVE_TIMEOUT_S = 15.0
LOG = logging.getLogger(__name__)


def _get_pin() -> int:
    pin_str = (os.environ.get("ELKE27_PIN") or "").strip()
    if not pin_str:
        pytest.skip("ELKE27_PIN not set; skipping auth-required test.")
    if not pin_str.isdigit():
        pytest.fail("ELKE27_PIN must be numeric.")
    pin_val = int(pin_str)
    if pin_val <= 0:
        pytest.fail("ELKE27_PIN must be a positive integer.")
    return pin_val


@pytest.mark.live_e27
@pytest.mark.asyncio
async def test_live_users_download(live_e27_client: Elke27Client) -> None:
    log_level = str(os.environ.get("LOG_LEVEL", "INFO") or "INFO").upper()
    logging.basicConfig(level=getattr(logging, log_level, logging.INFO), force=True)
    logging.getLogger("elke27_lib.session").setLevel(logging.DEBUG)

    pin = _get_pin()
    kernel = live_e27_client._kernel
    original_on_message = kernel._on_message
    enable_raw_log = log_level == "DEBUG"
    if enable_raw_log:
        def _log_on_message(msg):
            LOG.debug("raw inbound msg=%r", msg)
            return original_on_message(msg)

        kernel._on_message = _log_on_message
    try:
        auth_result = await live_e27_client.async_execute("control_authenticate", pin=pin)
        LOG.debug(
            "control.authenticate result ok=%s data=%r error=%s",
            auth_result.ok,
            auth_result.data,
            auth_result.error,
        )
        if not auth_result.ok:
            pytest.fail(f"control.authenticate failed: {describe_error(auth_result.error)}")
        assert_payload_shape("control_authenticate", auth_result.data)

        configured = await live_e27_client.async_execute("user_get_configured", timeout_s=_LIVE_TIMEOUT_S)
        if not configured.ok:
            if extract_error_code(configured.error) == E27ErrorCode.ELKERR_NOT_READY:
                pytest.skip("Panel reported not-ready for user_get_configured.")
            pytest.fail(f"user_get_configured failed: {describe_error(configured.error)}")
        assert_payload_shape("user_get_configured", configured.data)

        user_ids = configured.data.get("users") if configured.data else None
        if not isinstance(user_ids, list):
            pytest.skip("No configured users reported.")

        user_ids = [user_id for user_id in user_ids if isinstance(user_id, int) and user_id >= 1]
        if not user_ids:
            pytest.skip("No configured users reported.")

        last_error = None
        for user_id in user_ids:
            auth_result = await live_e27_client.async_execute("control_authenticate", pin=pin)
            LOG.debug(
                "control.authenticate (per-user) ok=%s data=%r error=%s",
                auth_result.ok,
                auth_result.data,
                auth_result.error,
            )
            if not auth_result.ok:
                pytest.fail(f"control.authenticate failed: {describe_error(auth_result.error)}")
            assert_payload_shape("control_authenticate", auth_result.data)

            result = await live_e27_client.async_execute(
                "user_get_attribs",
                user_id=user_id,
                pin=str(pin),
                timeout_s=_LIVE_TIMEOUT_S,
            )
            if not result.ok:
                code = extract_error_code(result.error)
                if code == E27ErrorCode.ELKERR_NOT_READY:
                    pytest.skip("Panel reported not-ready for user_get_attribs.")
                if code == E27ErrorCode.ELKERR_NOAUTH:
                    pytest.fail(f"user_get_attribs rejected after authenticate: {describe_error(result.error)}")
                last_error = result.error
                continue
            assert_payload_shape("user_get_attribs", result.data)
            if result.ok:
                user = live_e27_client._kernel.state.users.get(user_id)
                if user is not None:
                    return

        if last_error is not None:
            pytest.fail(f"User attribs unavailable after authenticate: {describe_error(last_error)}")
        pytest.skip("User attribs unavailable; auth may be required.")
    finally:
        if enable_raw_log:
            kernel._on_message = original_on_message
