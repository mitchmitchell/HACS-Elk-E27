"""Tstat request generators."""

from __future__ import annotations

from typing import Tuple


ResponseKey = Tuple[str, str]


def generator_tstat_get_table_info() -> tuple[dict, ResponseKey]:
    return {}, ("tstat", "get_table_info")


def generator_tstat_get_status(*, tstat_id: int) -> tuple[dict, ResponseKey]:
    if not isinstance(tstat_id, int) or tstat_id < 1:
        raise ValueError(f"tstat_id must be an int >= 1 (got {tstat_id!r})")
    return {"tstat_id": tstat_id}, ("tstat", "get_status")
