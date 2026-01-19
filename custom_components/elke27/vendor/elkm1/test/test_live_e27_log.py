"""
Live E27 test: log domain read-only requests.

Run:
  source ~/elk-e27-env-vars.sh
  pytest -q -m live_e27 test/test_live_e27_log.py -s
"""

from __future__ import annotations

import asyncio
import time

import pytest

from elke27_lib.client import Elke27Client
from elke27_lib.const import E27ErrorCode
from test.helpers.error_codes import describe_error, extract_error_code
from test.helpers.payload_validation import assert_payload_shape

_LIVE_TIMEOUT_S = 15.0


async def _execute_or_wait_log_state(
    client: Elke27Client,
    command_key: str,
    state_key: str,
    *,
    timeout_s: float,
    allow_missing: bool = False,
    **params: object,
) -> dict:
    result = await client.async_execute(command_key, timeout_s=timeout_s, **params)
    if result.ok and isinstance(result.data, dict):
        return dict(result.data)

    error_text = describe_error(result.error)
    missing_payload = "missing response payload" in error_text
    if not missing_payload:
        if extract_error_code(result.error) == E27ErrorCode.ELKERR_NOT_READY:
            pytest.skip(f"Panel reported not-ready for {command_key}.")
        pytest.fail(f"{command_key} failed: {error_text}")

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        payload = client._kernel.state.log_status.get(state_key)
        if isinstance(payload, dict):
            return dict(payload)
        await asyncio.sleep(0.1)

    if allow_missing:
        return {}
    pytest.fail(f"{command_key} missing response payload and no broadcast state for {state_key}.")
    return {}


@pytest.mark.live_e27
@pytest.mark.asyncio
async def test_live_log_domain(live_e27_client: Elke27Client) -> None:
    index_payload = await _execute_or_wait_log_state(
        live_e27_client,
        "log_get_index",
        "get_index",
        timeout_s=_LIVE_TIMEOUT_S,
        allow_missing=True,
    )
    if index_payload:
        assert_payload_shape("log_get_index", index_payload)
        print(f"log_get_index: {index_payload}")
    else:
        print("log_get_index: no response or broadcast")

    table_payload = await _execute_or_wait_log_state(
        live_e27_client,
        "log_get_table_info",
        "get_table_info",
        timeout_s=_LIVE_TIMEOUT_S,
    )
    assert_payload_shape("log_get_table_info", table_payload)
    print(f"log_get_table_info: {table_payload}")

    attribs_payload = await _execute_or_wait_log_state(
        live_e27_client,
        "log_get_attribs",
        "get_attribs",
        timeout_s=_LIVE_TIMEOUT_S,
    )
    assert_payload_shape("log_get_attribs", attribs_payload)
    print(f"log_get_attribs: {attribs_payload}")

    trouble_payload = await _execute_or_wait_log_state(
        live_e27_client,
        "log_get_trouble",
        "get_trouble",
        timeout_s=_LIVE_TIMEOUT_S,
    )
    assert_payload_shape("log_get_trouble", trouble_payload)
    print(f"log_get_trouble: {trouble_payload}")

    newest = index_payload.get("newest") if index_payload else None
    if not isinstance(newest, int) or newest <= 0:
        newest = 1

    log_result = await live_e27_client.async_execute(
        "log_get_log",
        log_id=newest,
        timeout_s=_LIVE_TIMEOUT_S,
    )
    if not log_result.ok:
        error_code = extract_error_code(log_result.error)
        if error_code == E27ErrorCode.ELKERR_NOT_READY:
            pytest.skip("Panel reported not-ready for log_get_log.")
        if error_code == E27ErrorCode.ELKERR_INVALID_ID:
            pytest.skip("Panel reported invalid log_id for log_get_log.")
        pytest.fail(f"log_get_log failed: {describe_error(log_result.error)}")
    assert_payload_shape("log_get_log", log_result.data)
    print(f"log_get_log({newest}): {log_result.data}")

    gmt_seconds = None
    if isinstance(log_result.data, dict):
        gmt_seconds = log_result.data.get("gmt_seconds")
    if not isinstance(gmt_seconds, int):
        pytest.skip("log_get_log response missing gmt_seconds for log_get_list.")

    list_result = await live_e27_client.async_execute(
        "log_get_list",
        start=newest,
        date=gmt_seconds,
        cnt=10,
        timeout_s=_LIVE_TIMEOUT_S,
    )
    if not list_result.ok:
        if extract_error_code(list_result.error) == E27ErrorCode.ELKERR_NOT_READY:
            pytest.skip("Panel reported not-ready for log_get_list.")
        pytest.fail(f"log_get_list failed: {describe_error(list_result.error)}")
    assert_payload_shape("log_get_list", list_result.data)
    print(f"log_get_list: {list_result.data}")
