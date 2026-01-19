"""
Live E27 test: system status snapshot via system_get_trouble.

Run:
  source ~/elk-e27-env-vars.sh
  pytest -q -m live_e27 test/test_live_e27_system_status.py -s
"""

from __future__ import annotations

import pytest

from elke27_lib.client import Elke27Client
from elke27_lib.const import E27ErrorCode
from test.helpers.error_codes import describe_error, extract_error_code
from test.helpers.payload_validation import assert_payload_shape


_LIVE_TIMEOUT_S = 15.0


def _extract_error_code(payload: object) -> E27ErrorCode | None:
    if isinstance(payload, dict):
        direct = payload.get("error_code")
        if isinstance(direct, int):
            try:
                return E27ErrorCode(direct)
            except ValueError:
                return None
        for key in ("get_trouble", "get_troubles"):
            nested = payload.get(key)
            if isinstance(nested, dict):
                nested_code = nested.get("error_code")
                if isinstance(nested_code, int):
                    try:
                        return E27ErrorCode(nested_code)
                    except ValueError:
                        return None
    return None


@pytest.mark.live_e27
@pytest.mark.asyncio
async def test_live_system_status_snapshot(live_e27_client: Elke27Client) -> None:
    result = await live_e27_client.async_execute("system_get_trouble", timeout_s=_LIVE_TIMEOUT_S)
    if not result.ok:
        if extract_error_code(result.error) == E27ErrorCode.ELKERR_NOT_READY:
            pytest.skip("Panel reported not-ready for system_get_trouble.")
        pytest.fail(f"system_get_trouble failed: {describe_error(result.error)}")
    assert_payload_shape("system_get_trouble", result.data)

    error_code = _extract_error_code(result.data)
    if error_code == E27ErrorCode.ELKERR_NOT_READY:
        pytest.skip("Panel reported not-ready for system_get_trouble.")

    status = live_e27_client._kernel.state.system_status
    assert isinstance(status, dict)
    if not status:
        pytest.skip("system_get_trouble response was empty.")
