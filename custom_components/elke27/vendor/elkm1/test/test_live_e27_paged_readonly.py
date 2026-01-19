"""
Live E27 test: paged read-only command (zone_get_configured).

Run:
  source ~/elk-e27-env-vars.sh
  pytest -q -m live_e27 test/test_live_e27_paged_readonly.py -s
"""

from __future__ import annotations

import pytest

from elke27_lib.client import Elke27Client
from elke27_lib.const import E27ErrorCode
from test.helpers.error_codes import describe_error, extract_error_code
from test.helpers.payload_validation import assert_payload_shape


_LIVE_TIMEOUT_S = 60.0


@pytest.mark.live_e27
@pytest.mark.asyncio
async def test_live_paged_zone_get_configured(live_e27_client: Elke27Client) -> None:
    result = await live_e27_client.async_execute("zone_get_configured", timeout_s=_LIVE_TIMEOUT_S)
    if not result.ok:
        if extract_error_code(result.error) == E27ErrorCode.ELKERR_NOT_READY:
            pytest.skip("Panel reported not-ready for zone_get_configured.")
        pytest.fail(f"zone_get_configured failed: {describe_error(result.error)}")
    assert_payload_shape("zone_get_configured", result.data)

    assert isinstance(result.data, dict)
    zones = result.data.get("zones")
    block_count = result.data.get("block_count")
    assert isinstance(zones, list)
    assert len(zones) > 0
    assert isinstance(block_count, int)
    if block_count <= 1:
        pytest.skip("zone_get_configured returned a single block; paging not exercised.")
