"""Tstat request generators."""

from __future__ import annotations

ResponseKey = tuple[str, str]


def generator_tstat_get_table_info() -> tuple[dict[str, object], ResponseKey]:
    return {}, ("tstat", "get_table_info")


def generator_tstat_get_status(*, tstat_id: int) -> tuple[dict[str, object], ResponseKey]:
    if tstat_id < 1:
        raise ValueError(f"tstat_id must be an int >= 1 (got {tstat_id!r})")
    return {"tstat_id": tstat_id}, ("tstat", "get_status")
