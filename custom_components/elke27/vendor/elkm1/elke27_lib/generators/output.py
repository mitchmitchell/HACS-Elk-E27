"""Output domain request generators."""

from __future__ import annotations

from typing import Tuple


ResponseKey = Tuple[str, str]


def generator_output_get_table_info() -> tuple[dict, ResponseKey]:
    return {}, ("output", "get_table_info")


def generator_output_get_configured(*, block_id: int = 1) -> tuple[dict, ResponseKey]:
    if not isinstance(block_id, int) or block_id < 1:
        raise ValueError(f"block_id must be an int >= 1 (got {block_id!r})")
    return {"block_id": block_id}, ("output", "get_configured")


def generator_output_get_status(*, output_id: int) -> tuple[dict, ResponseKey]:
    if not isinstance(output_id, int) or output_id < 1:
        raise ValueError(f"output_id must be an int >= 1 (got {output_id!r})")
    return {"output_id": output_id}, ("output", "get_status")


def generator_output_set_status(*, output_id: int, status: str) -> tuple[dict, ResponseKey]:
    if not isinstance(output_id, int) or output_id < 1:
        raise ValueError(f"output_id must be an int >= 1 (got {output_id!r})")
    if status not in {"ON", "OFF"}:
        raise ValueError(f"status must be 'ON' or 'OFF' (got {status!r})")
    return {"output_id": output_id, "status": status}, ("output", "set_status")


def generator_output_get_attribs(*, output_id: int) -> tuple[dict, ResponseKey]:
    if not isinstance(output_id, int) or output_id < 1:
        raise ValueError(f"output_id must be an int >= 1 (got {output_id!r})")
    return {"output_id": output_id}, ("output", "get_attribs")


def generator_output_get_all_outputs_status(*, block_id: int = 1) -> tuple[dict, ResponseKey]:
    if not isinstance(block_id, int) or block_id < 1:
        raise ValueError(f"block_id must be an int >= 1 (got {block_id!r})")
    return {"block_id": block_id}, ("output", "get_all_outputs_status")
