"""Zone domain request generators."""

from __future__ import annotations

ResponseKey = tuple[str, str]


def generator_zone_get_table_info() -> tuple[dict[str, object], ResponseKey]:
    return {}, ("zone", "get_table_info")


def generator_zone_get_configured(*, block_id: int = 1) -> tuple[dict[str, object], ResponseKey]:
    if block_id < 1:
        raise ValueError(f"block_id must be an int >= 1 (got {block_id!r})")
    return {"block_id": block_id}, ("zone", "get_configured")


def generator_zone_get_all_zones_status() -> tuple[dict[str, object], ResponseKey]:
    return {}, ("zone", "get_all_zones_status")


def generator_zone_get_status(*, zone_id: int) -> tuple[dict[str, object], ResponseKey]:
    if zone_id < 1:
        raise ValueError(f"zone_id must be an int >= 1 (got {zone_id!r})")
    return {"zone_id": zone_id}, ("zone", "get_status")


def generator_zone_get_attribs(*, zone_id: int) -> tuple[dict[str, object], ResponseKey]:
    if zone_id < 1:
        raise ValueError(f"zone_id must be an int >= 1 (got {zone_id!r})")
    return {"zone_id": zone_id}, ("zone", "get_attribs")


def generator_zone_get_defs(*, block_id: int = 1) -> tuple[dict[str, object], ResponseKey]:
    if block_id < 1:
        raise ValueError(f"block_id must be an int >= 1 (got {block_id!r})")
    return {"block_id": block_id}, ("zone", "get_defs")


def generator_zone_get_def_flags(*, definition: str) -> tuple[dict[str, object], ResponseKey]:
    if not definition.strip():
        raise ValueError("definition must be a non-empty string")
    return {"definition": definition}, ("zone", "get_def_flags")


def generator_zone_set_status(
    *, zone_id: int, pin: int | str, bypassed: bool
) -> tuple[dict[str, object], ResponseKey]:
    if zone_id < 1:
        raise ValueError(f"zone_id must be an int >= 1 (got {zone_id!r})")
    return {"zone_id": zone_id, "pin": pin, "BYPASSED": bypassed}, ("zone", "set_status")
