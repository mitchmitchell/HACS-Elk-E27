"""User domain request generators."""

from __future__ import annotations

ResponseKey = tuple[str, str]


def generator_user_get_configured(*, block_id: int = 1) -> tuple[dict[str, object], ResponseKey]:
    if block_id < 1:
        raise ValueError(f"block_id must be an int >= 1 (got {block_id!r})")
    return {"block_id": block_id}, ("user", "get_configured")


def generator_user_get_attribs(*, user_id: int) -> tuple[dict[str, object], ResponseKey]:
    if user_id < 1:
        raise ValueError(f"user_id must be an int >= 1 (got {user_id!r})")
    return {"user_id": user_id}, ("user", "get_attribs")
