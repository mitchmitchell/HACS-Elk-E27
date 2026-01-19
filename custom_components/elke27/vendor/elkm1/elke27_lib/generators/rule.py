"""Rule domain request generators."""

from __future__ import annotations

from typing import Tuple


ResponseKey = Tuple[str, str]


def generator_rule_get_rules(*, block_id: int = 1) -> tuple[dict, ResponseKey]:
    if not isinstance(block_id, int) or block_id < 0:
        raise ValueError(f"block_id must be an int >= 0 (got {block_id!r})")
    return {"block_id": block_id}, ("rule", "get_rules")
