# ruff: noqa: TRY003, EM101, TC002
"""Order-sensitive status-read mocks (pre snapshot until the matching read finishes)."""

from __future__ import annotations

from typing import Any, Literal
from unittest.mock import AsyncMock, MagicMock

from elke27_lib import PanelSnapshot


def make_status_read_client(
    *,
    before: PanelSnapshot,
    after: PanelSnapshot,
    area: Any,
    zones: Any,
    mode: Literal["full", "area_only", "zone_only"] = "full",
    strict_snapshot_order: bool = False,
) -> MagicMock:
    """
    Mock elke27 client for status refresh paths.

    ``get_snapshot`` returns ``before`` until the relevant ``async_execute`` calls
    for this ``mode`` have finished, then ``after``. With ``strict_snapshot_order``,
    an early ``get_snapshot`` raises ``AssertionError``.
    """
    progress = {"area": False, "zone": False}
    client = MagicMock()

    def _ready() -> bool:
        if mode == "zone_only":
            return progress["zone"]
        if mode == "area_only":
            return progress["area"]
        return progress["area"] and progress["zone"]

    def _get_snapshot() -> PanelSnapshot:
        if not _ready():
            if strict_snapshot_order:
                raise AssertionError("get_snapshot called before status read finished")
            return before
        return after

    async def _execute(command_key: str, **_params: Any) -> Any:
        if command_key == "area_get_status":
            if mode == "zone_only":
                raise AssertionError(command_key)
            if isinstance(area, Exception):
                raise area
            progress["area"] = True
            return MagicMock(ok=True, data=area, error=None)
        if command_key == "zone_get_all_zones_status":
            if mode == "area_only":
                raise AssertionError(command_key)
            if isinstance(zones, Exception):
                raise zones
            progress["zone"] = True
            return MagicMock(ok=True, data=zones, error=None)
        return MagicMock(ok=True, data={}, error=None)

    client.async_execute = AsyncMock(side_effect=_execute)
    client.get_snapshot = MagicMock(side_effect=_get_snapshot)
    return client
