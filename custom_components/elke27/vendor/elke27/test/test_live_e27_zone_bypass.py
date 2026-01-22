"""
Live E27 test: zone bypass via zone_set_status.

Run:
  source ~/elk-e27-env-vars.sh
  pytest -q -m live_e27 test/test_live_e27_zone_bypass.py -s
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


def _get_pin() -> int:
    pin_str = (os.environ.get("ELKE27_PIN") or "").strip()
    if not pin_str:
        pytest.skip("ELKE27_PIN not set; skipping zone bypass test.")
    if not pin_str.isdigit():
        pytest.fail("ELKE27_PIN must be numeric.")
    pin_val = int(pin_str)
    if pin_val <= 0:
        pytest.fail("ELKE27_PIN must be a positive integer.")
    return pin_val


@pytest.mark.live_e27
@pytest.mark.asyncio
async def test_live_zone_bypass(live_e27_client: Elke27Client) -> None:
    zone_id = _env_zone_id()
    pin = _get_pin()

    status_result = await live_e27_client.async_execute(
        "zone_get_status",
        zone_id=zone_id,
        timeout_s=_LIVE_TIMEOUT_S,
    )
    if not status_result.ok:
        if extract_error_code(status_result.error) == E27ErrorCode.ELKERR_NOT_READY:
            pytest.skip("Panel reported not-ready for zone_get_status.")
        pytest.fail(f"zone_get_status failed: {describe_error(status_result.error)}")
    assert_payload_shape("zone_get_status", status_result.data)

    set_result = await live_e27_client.async_execute(
        "zone_set_status",
        zone_id=zone_id,
        pin=pin,
        bypassed=True,
        timeout_s=_LIVE_TIMEOUT_S,
    )
    if not set_result.ok:
        pytest.fail(f"zone_set_status bypass failed: {describe_error(set_result.error)}")
    assert_payload_shape("zone_set_status", set_result.data)

    status_result = await live_e27_client.async_execute(
        "zone_get_status",
        zone_id=zone_id,
        timeout_s=_LIVE_TIMEOUT_S,
    )
    if status_result.ok and isinstance(status_result.data, dict):
        assert_payload_shape("zone_get_status", status_result.data)
        assert bool(status_result.data.get("BYPASSED")) is True
