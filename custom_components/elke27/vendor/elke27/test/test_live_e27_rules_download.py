"""
Live E27 test: rule_get_rules download.

Run:
  source ~/elk-e27-env-vars.sh
  pytest -q -m live_e27 test/test_live_e27_rules_download.py -s
"""

from __future__ import annotations

import pytest

from elke27_lib.client import Elke27Client
from elke27_lib.const import E27ErrorCode
from test.helpers.error_codes import describe_error, extract_error_code
from test.helpers.payload_validation import assert_payload_shape

_LIVE_TIMEOUT_S = 30.0


@pytest.mark.live_e27
@pytest.mark.asyncio
async def test_live_rules_download(live_e27_client: Elke27Client) -> None:
    result = await live_e27_client.async_execute("rule_get_rules", timeout_s=_LIVE_TIMEOUT_S)
    if not result.ok:
        if extract_error_code(result.error) == E27ErrorCode.ELKERR_NOT_READY:
            pytest.skip("Panel reported not-ready for rule_get_rules.")
        pytest.fail(f"rule_get_rules failed: {describe_error(result.error)}")
    assert_payload_shape("rule_get_rules", result.data)

    rules = live_e27_client.state.rules
    if not rules:
        pytest.skip("No rules configured on this panel.")
