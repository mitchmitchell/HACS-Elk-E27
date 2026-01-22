"""
elke27_lib/features/rule.py

Feature module: rule
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from elke27_lib.kernel import E27Kernel

from elke27_lib.handlers.rule import make_rule_get_rules_handler

ROUTE_RULE_GET_RULES = ("rule", "get_rules")


def register(elk: E27Kernel) -> None:
    elk.register_handler(
        ROUTE_RULE_GET_RULES,
        make_rule_get_rules_handler(elk.state, elk.emit, elk.now),
    )
    elk.register_request(
        ROUTE_RULE_GET_RULES,
        build_rule_get_rules_payload,
    )


def build_rule_get_rules_payload(*, block_id: int = 1, **_kwargs: Any) -> dict[str, object]:
    if block_id < 0:
        raise ValueError(
            f"build_rule_get_rules_payload: block_id must be int >= 0 (got {block_id!r})"
        )
    return {"block_id": block_id}
