"""Rule domain request generators."""

from __future__ import annotations

ResponseKey = tuple[str, str]


def generator_rule_get_rules(*, block_id: int = 1) -> tuple[dict[str, object], ResponseKey]:
    if block_id < 0:
        raise ValueError(f"block_id must be an int >= 0 (got {block_id!r})")
    return {"block_id": block_id}, ("rule", "get_rules")
