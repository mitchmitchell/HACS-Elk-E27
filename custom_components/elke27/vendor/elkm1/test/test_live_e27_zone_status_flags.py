"""
Live E27 test: zone status flags from zone_get_status.

Run:
  source ~/elk-e27-env-vars.sh
  pytest -q -m live_e27 test/test_live_e27_zone_status_flags.py -s
"""

from __future__ import annotations

import os

import pytest

from elke27_lib.client import Elke27Client
from elke27_lib.const import E27ErrorCode
from test.helpers.error_codes import describe_error, extract_error_code
from test.helpers.payload_validation import assert_payload_shape


_LIVE_TIMEOUT_S = 15.0


def _env_zone_id() -> int:
    for name in ("ELKE27_ZONE_ID", "E27_ZONE_ID"):
        value = os.environ.get(name)
        if value and value.strip().isdigit():
            zone_id = int(value)
            if zone_id >= 1:
                return zone_id
    return 1


@pytest.mark.live_e27
@pytest.mark.asyncio
async def test_live_zone_status_flags(live_e27_client: Elke27Client) -> None:
    zone_id = _env_zone_id()
    result = await live_e27_client.async_execute("zone_get_status", zone_id=zone_id, timeout_s=_LIVE_TIMEOUT_S)
    if not result.ok:
        if extract_error_code(result.error) == E27ErrorCode.ELKERR_NOT_READY:
            pytest.skip("Panel reported not-ready for zone_get_status.")
        pytest.fail(f"zone_get_status failed: {describe_error(result.error)}")
    assert_payload_shape("zone_get_status", result.data)

    zone = live_e27_client._kernel.state.zones.get(zone_id)
    assert zone is not None
    for field in ("bypassed", "trouble", "tamper", "alarm", "low_battery"):
        value = getattr(zone, field, None)
        assert isinstance(value, bool)
