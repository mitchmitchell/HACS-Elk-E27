"""
Live E27 test: keypad enumeration via keypads_get_configured + keypads_get_attribs.

Run:
  source ~/elk-e27-env-vars.sh
  pytest -q -m live_e27 test/test_live_e27_keypads_download.py -s
"""

from __future__ import annotations

from typing import cast

import pytest

from elke27_lib.client import Elke27Client
from elke27_lib.const import E27ErrorCode
from test.helpers.error_codes import describe_error, extract_error_code
from test.helpers.payload_validation import assert_payload_shape

_LIVE_TIMEOUT_S = 15.0


@pytest.mark.live_e27
@pytest.mark.asyncio
async def test_live_keypads_download(live_e27_client: Elke27Client) -> None:
    table_info = await live_e27_client.async_execute(
        "keypad_get_table_info", timeout_s=_LIVE_TIMEOUT_S
    )
    if not table_info.ok:
        error_code = extract_error_code(table_info.error)
        if error_code == E27ErrorCode.ELKERR_NOT_READY:
            pytest.skip("Panel reported not-ready for keypad_get_table_info.")
        if error_code == E27ErrorCode.ELKERR_INVALID_TABLE:
            pytest.skip("Keypad table not allocated on this panel.")
        pytest.fail(f"keypad_get_table_info failed: {describe_error(table_info.error)}")
    table_elements = table_info.data.get("table_elements") if table_info.data else None
    if not isinstance(table_elements, int) or table_elements < 1:
        pytest.skip("Keypad table not allocated on this panel.")

    configured = await live_e27_client.async_execute(
        "keypad_get_configured", timeout_s=_LIVE_TIMEOUT_S
    )
    if not configured.ok:
        error_code = extract_error_code(configured.error)
        if error_code == E27ErrorCode.ELKERR_NOT_READY:
            pytest.skip("Panel reported not-ready for keypad_get_configured.")
        if error_code == E27ErrorCode.ELKERR_INVALID_TABLE:
            pytest.fail(
                "keypad_get_configured failed with invalid_table despite allocated keypad table."
            )
        pytest.fail(f"keypad_get_configured failed: {describe_error(configured.error)}")
    assert_payload_shape("keypad_get_configured", configured.data)

    keypad_ids = configured.data.get("keypads") if configured.data else None
    if not isinstance(keypad_ids, list):
        pytest.skip("No configured keypads reported.")

    keypad_ids_list = cast(list[object], keypad_ids)
    keypad_ids = [
        keypad_id for keypad_id in keypad_ids_list if isinstance(keypad_id, int) and keypad_id >= 1
    ]
    if not keypad_ids:
        pytest.skip("No configured keypads reported.")

    for keypad_id in keypad_ids:
        result = await live_e27_client.async_execute(
            "keypad_get_attribs",
            keypad_id=keypad_id,
            timeout_s=_LIVE_TIMEOUT_S,
        )
        if not result.ok and extract_error_code(result.error) == E27ErrorCode.ELKERR_NOT_READY:
            pytest.skip("Panel reported not-ready for keypad_get_attribs.")
        if result.ok:
            assert_payload_shape("keypad_get_attribs", result.data)
            keypad = live_e27_client.state.keypads.get(keypad_id)
            if keypad is not None:
                return

    pytest.skip("Keypad attribs unavailable; auth may be required.")
