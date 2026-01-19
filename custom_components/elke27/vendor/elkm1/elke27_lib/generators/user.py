"""User domain request generators."""

from __future__ import annotations

from typing import Tuple


ResponseKey = Tuple[str, str]


def generator_user_get_configured(*, block_id: int = 1) -> tuple[dict, ResponseKey]:
    if not isinstance(block_id, int) or block_id < 1:
        raise ValueError(f"block_id must be an int >= 1 (got {block_id!r})")
    return {"block_id": block_id}, ("user", "get_configured")


def generator_user_get_attribs(*, user_id: int) -> tuple[dict, ResponseKey]:
    if not isinstance(user_id, int) or user_id < 1:
        raise ValueError(f"user_id must be an int >= 1 (got {user_id!r})")
    return {"user_id": user_id}, ("user", "get_attribs")
