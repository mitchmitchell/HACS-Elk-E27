"""
Live E27 test: arm STAY then disarm.

Safety:
- Use a non-critical area.
- Have a keypad available.
- Alarm notifications may trigger.
"""

from __future__ import annotations

import asyncio
import os
from getpass import getpass

import pytest

from elke27_lib.client import Elke27Client
from elke27_lib.const import E27ErrorCode
from test.helpers.error_codes import extract_error_code
from test.helpers.payload_validation import assert_payload_shape


def _area_id() -> int:
    value = os.environ.get("ELKE27_AREA_ID") or "1"
    try:
        parsed = int(value)
    except ValueError:
        return 1
    return parsed if parsed > 0 else 1


def _get_pin() -> int:
    pin_str = (os.environ.get("ELKE27_PIN") or "").strip()
    if not pin_str:
        pin_str = getpass("Enter panel PIN to arm/disarm (will not echo): ").strip()
    if not pin_str.isdigit():
        pytest.fail("PIN must be digits.")
    pin_val = int(pin_str)
    if pin_val <= 0:
        pytest.fail("PIN must be a positive integer.")
    return pin_val


def _is_not_ready_error(result) -> bool:
    return extract_error_code(result.error) == E27ErrorCode.ELKERR_NOT_READY


def _require_interactive() -> None:
    if os.environ.get("ELKE27_INTERACTIVE", "").strip().lower() != "true":
        pytest.skip("Set ELKE27_INTERACTIVE=true to run arm/disarm tests.")


@pytest.mark.live_e27
@pytest.mark.asyncio
async def test_live_arm_stay_then_disarm(live_e27_client: Elke27Client) -> None:
    _require_interactive()
    area_id = _area_id()
    pin = _get_pin()

    result = await live_e27_client.async_execute(
        "area_set_arm_state",
        area_id=area_id,
        arm_state="DISARMED",
        pin=pin,
    )
    assert result.ok is True
    assert_payload_shape("area_set_arm_state", result.data)

    result = await live_e27_client.async_execute(
        "area_set_arm_state",
        area_id=area_id,
        arm_state="ARMED_STAY",
        pin=pin,
    )
    if not result.ok and _is_not_ready_error(result):
        pytest.skip("Panel reported not-ready for ARMED_STAY.")
    assert result.ok is True
    assert_payload_shape("area_set_arm_state", result.data)

    if os.environ.get("ELKE27_PIN", "").strip():
        await asyncio.sleep(2)
    else:
        input("Panel armed STAY. Press Enter to DISARM...")

    result = await live_e27_client.async_execute(
        "area_set_arm_state",
        area_id=area_id,
        arm_state="DISARMED",
        pin=pin,
    )
    assert result.ok is True
    assert_payload_shape("area_set_arm_state", result.data)
