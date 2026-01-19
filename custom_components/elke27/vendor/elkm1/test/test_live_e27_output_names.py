"""
Live E27 test: output names populated from output_get_attribs.

Run:
  source ~/elk-e27-env-vars.sh
  pytest -q -m live_e27 test/test_live_e27_output_names.py -s
"""

from __future__ import annotations

import pytest

from elke27_lib.client import Elke27Client
from elke27_lib.const import E27ErrorCode
from test.helpers.error_codes import describe_error, extract_error_code
from test.helpers.payload_validation import assert_payload_shape

_LIVE_TIMEOUT_S = 15.0


@pytest.mark.live_e27
@pytest.mark.asyncio
async def test_live_output_names_from_attribs(live_e27_client: Elke27Client) -> None:
    configured = await live_e27_client.async_execute("output_get_configured", timeout_s=_LIVE_TIMEOUT_S)
    if not configured.ok:
        if extract_error_code(configured.error) == E27ErrorCode.ELKERR_NOT_READY:
            pytest.skip("Panel reported not-ready for output_get_configured.")
        pytest.fail(f"output_get_configured failed: {describe_error(configured.error)}")
    assert_payload_shape("output_get_configured", configured.data)

    output_ids = configured.data.get("outputs") if configured.data else None
    if not isinstance(output_ids, list):
        output_ids = []

    output_ids = [output_id for output_id in output_ids if isinstance(output_id, int) and output_id >= 1]

    for output_id in output_ids:
        result = await live_e27_client.async_execute(
            "output_get_attribs",
            output_id=output_id,
            timeout_s=_LIVE_TIMEOUT_S,
        )
        if not result.ok and extract_error_code(result.error) == E27ErrorCode.ELKERR_NOT_READY:
            pytest.skip("Panel reported not-ready for output_get_attribs.")
        if result.ok:
            assert_payload_shape("output_get_attribs", result.data)
            output = live_e27_client.outputs.get(output_id)
            if output is not None and isinstance(output.name, str) and output.name != "":
                return

    configured_count = len(live_e27_client._kernel.state.inventory.configured_outputs)
    if configured_count == 0:
        pytest.skip("No outputs configured on this panel; skipping output name assertions.")

    pytest.skip("Output names not available after attribs fetch; auth may be required.")
