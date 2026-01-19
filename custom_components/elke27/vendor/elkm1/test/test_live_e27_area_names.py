"""
Live E27 test: area names populated from area_get_attribs.

Run:
  source ~/elk-e27-env-vars.sh
  pytest -q -m live_e27 test/test_live_e27_area_names.py -s
"""

from __future__ import annotations

import pytest

from elke27_lib.client import Elke27Client
from elke27_lib.const import E27ErrorCode
from test.helpers.error_codes import describe_error, extract_error_code
from test.helpers.payload_validation import assert_payload_shape


@pytest.mark.live_e27
@pytest.mark.asyncio
async def test_live_area_names_from_attribs(live_e27_client: Elke27Client) -> None:
    configured = await live_e27_client.async_execute("area_get_configured")
    if not configured.ok:
        if extract_error_code(configured.error) == E27ErrorCode.ELKERR_NOT_READY:
            pytest.skip("Panel reported not-ready for area_get_configured.")
        pytest.fail(f"area_get_configured failed: {describe_error(configured.error)}")
    assert_payload_shape("area_get_configured", configured.data)

    configured_ids = configured.data.get("areas") if configured.data else None
    if not isinstance(configured_ids, list):
        pytest.skip("No configured areas reported.")
    configured_ids = sorted(area_id for area_id in configured_ids if isinstance(area_id, int) and area_id >= 1)
    if not configured_ids:
        pytest.skip("No configured areas reported.")

    names: list[tuple[int, str, int | None]] = []
    for area_id in configured_ids:
        result = await live_e27_client.async_execute("area_get_attribs", area_id=area_id)
        if not result.ok:
            error_code = extract_error_code(result.error)
            if error_code == E27ErrorCode.ELKERR_NOT_READY:
                pytest.skip("Panel reported not-ready for area_get_attribs.")
            names.append((area_id, "", int(error_code) if error_code is not None else None))
            continue
        assert_payload_shape("area_get_attribs", result.data)
        name = ""
        if result.data and isinstance(result.data.get("name"), str):
            name = result.data.get("name", "")
        names.append((area_id, name, None))

    print("area names:", names)
    missing = [area_id for area_id, name, _ in names if not name]
    if missing:
        pytest.fail(f"Missing area names for ids: {missing}")
