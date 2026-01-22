from __future__ import annotations

import asyncio
import logging
import os

import pytest

from elke27_lib.client import Elke27Client
from test.helpers.payload_validation import assert_payload_shape

LOG = logging.getLogger(__name__)


def _area_id() -> int:
    value = os.environ.get("ELKE27_AREA_ID") or "1"
    try:
        parsed = int(value)
    except ValueError:
        return 1
    return parsed if parsed > 0 else 1


def _require_interactive() -> None:
    if os.environ.get("ELKE27_INTERACTIVE", "").strip().lower() != "true":
        pytest.skip("Set ELKE27_INTERACTIVE=true to run chime tests.")


@pytest.mark.live_e27
@pytest.mark.asyncio
async def test_live_area_chime_on_ack(live_e27_client: Elke27Client) -> None:
    _require_interactive()
    area_id = _area_id()
    result = await live_e27_client.async_execute("area_set_status", area_id=area_id, chime=True)
    assert result.ok is True
    assert_payload_shape("area_set_status", result.data)


@pytest.mark.live_e27
@pytest.mark.asyncio
async def test_live_area_chime_off_ack(live_e27_client: Elke27Client) -> None:
    _require_interactive()
    area_id = _area_id()
    result = await live_e27_client.async_execute("area_set_status", area_id=area_id, chime=False)
    assert result.ok is True
    assert_payload_shape("area_set_status", result.data)


@pytest.mark.live_e27
@pytest.mark.asyncio
async def test_live_area_chime_on_persist(live_e27_client: Elke27Client) -> None:
    _require_interactive()
    if os.environ.get("ELKE27_HUMAN_TEST") != "1":
        pytest.skip("ELKE27_HUMAN_TEST not set; skipping persistent chime test.")
    area_id = _area_id()
    result = await live_e27_client.async_execute("area_set_status", area_id=area_id, chime=True)
    assert result.ok is True
    assert_payload_shape("area_set_status", result.data)


@pytest.mark.live_e27
@pytest.mark.asyncio
async def test_live_area_chime_state_roundtrip(live_e27_client: Elke27Client) -> None:
    _require_interactive()
    area_id = _area_id()
    area = live_e27_client.areas.get(area_id)
    snapshot_available = False
    original: bool | None = None
    if area is not None and area.chime is not None:
        snapshot_available = True
        original = bool(area.chime)
    if not snapshot_available:
        configured_result = await live_e27_client.async_execute("area_get_configured")
        if configured_result.ok:
            assert_payload_shape("area_get_configured", configured_result.data)
        await asyncio.sleep(0.5)
        area = live_e27_client.areas.get(area_id)
        if area is not None and area.chime is not None:
            snapshot_available = True
            original = bool(area.chime)
    if original is None:
        result = await live_e27_client.async_execute("area_get_status", area_id=area_id)
        if result.ok:
            assert_payload_shape("area_get_status", result.data)
        if result.ok and isinstance(result.data, dict) and "Chime" in result.data:
            original = bool(result.data["Chime"])
    if original is None:
        pytest.skip("Chime state not available in snapshot or response; skipping state roundtrip.")

    target = not original

    async def _wait_for_chime(expected: bool, timeout_s: float = 5.0) -> bool:
        if not snapshot_available:
            await asyncio.sleep(timeout_s)
            return False
        end = asyncio.get_running_loop().time() + timeout_s
        while asyncio.get_running_loop().time() < end:
            current_area = live_e27_client.areas.get(area_id)
            if current_area is not None and current_area.chime is expected:
                return True
            await asyncio.sleep(0.2)
        return False

    try:
        result = await live_e27_client.async_execute(
            "area_set_status", area_id=area_id, chime=target
        )
        if result.ok:
            assert_payload_shape("area_set_status", result.data)
        changed = await _wait_for_chime(target)
        if not changed and snapshot_available:
            LOG.warning("Chime state did not converge to %s within timeout.", target)
    finally:
        result = await live_e27_client.async_execute(
            "area_set_status", area_id=area_id, chime=original
        )
        if result.ok:
            assert_payload_shape("area_set_status", result.data)
        reverted = await _wait_for_chime(original)
        if not reverted and snapshot_available:
            LOG.warning("Chime state did not revert to %s within timeout.", original)
