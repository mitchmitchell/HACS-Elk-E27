"""Command registry for outbound request generators (read-only)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal, Optional

from elke27_lib.errors import Elke27ProtocolError
from elke27_lib.handlers.all_handlers import HANDLERS, HandlerFn
from elke27_lib.permissions import ALL_PERMISSION_KEYS, PermissionLevel, permission_for_generator

from .all_generators import GENERATORS, GeneratorFn

MergeStrategy = Callable[..., object] | str


@dataclass(frozen=True)
class CommandSpec:
    key: str
    domain: str
    command: str
    generator: GeneratorFn
    handler: HandlerFn
    min_permission: PermissionLevel
    response_mode: Literal["single", "paged_blocks"] = "single"
    block_field: Optional[str] = None
    block_count_field: Optional[str] = None
    first_block: int = 1
    merge_strategy: Optional[MergeStrategy] = None
    requires_automation_authority: bool = False
    area_scoped: bool = False


_DOMAIN_PREFIXES = sorted(
    {
        "network_param",
        "user_value",
        "usergroup",
        "bus_ios",
        "net_dev",
        "win_cover",
        "cs_param",
        "keyfob",
        "wiegand",
        "zwave",
        "barrier",
        "control",
        "system",
        "keypad",
        "output",
        "register",
        "rule",
        "task",
        "timer",
        "tstat",
        "light",
        "lock",
        "area",
        "cell",
        "flag",
        "log",
        "network",
        "user",
        "wltx",
        "zone",
        "repeater",
    },
    key=len,
    reverse=True,
)


def _split_domain_command(key: str) -> tuple[str, str]:
    if key.startswith("network_param_"):
        return "network", key[len("network_param_") :]
    for domain in _DOMAIN_PREFIXES:
        prefix = f"{domain}_"
        if key.startswith(prefix):
            return domain, key[len(prefix) :]
    if "_" in key:
        domain, command = key.split("_", 1)
        return domain, command
    raise Elke27ProtocolError(f"Cannot derive domain/command for {key!r}")


_COMMAND_OVERRIDES: dict[str, dict[str, object]] = {
    "area_get_configured": {
        "response_mode": "paged_blocks",
        "block_field": "block_id",
        "block_count_field": "block_count",
        "merge_strategy": "area_configured",
    },
    "zone_get_configured": {
        "response_mode": "paged_blocks",
        "block_field": "block_id",
        "block_count_field": "block_count",
        "merge_strategy": "zone_configured",
    },
    "output_get_configured": {
        "response_mode": "paged_blocks",
        "block_field": "block_id",
        "block_count_field": "block_count",
        "merge_strategy": "output_configured",
    },
    "output_get_all_outputs_status": {
        "response_mode": "paged_blocks",
        "block_field": "block_id",
        "block_count_field": "block_count",
        "merge_strategy": "output_all_status",
    },
    "rule_get_rules": {
        "response_mode": "paged_blocks",
        "block_field": "block_id",
        "block_count_field": "block_count",
        "first_block": 0,
        "merge_strategy": "rule_blocks",
    },
    "user_get_configured": {
        "response_mode": "paged_blocks",
        "block_field": "block_id",
        "block_count_field": "block_count",
        "merge_strategy": "user_configured",
    },
    "keypad_get_configured": {
        "response_mode": "paged_blocks",
        "block_field": "block_id",
        "block_count_field": "block_count",
        "merge_strategy": "keypad_configured",
    },
    "area_get_status": {"area_scoped": True},
    "area_set_status": {"area_scoped": True},
    "area_set_arm_state": {"area_scoped": True},
}


def _build_spec(key: str) -> CommandSpec:
    domain, command = _split_domain_command(key)
    generator = GENERATORS[key]
    handler = HANDLERS[key]
    min_permission = permission_for_generator(key)
    overrides = _COMMAND_OVERRIDES.get(key, {})
    return CommandSpec(
        key=key,
        domain=domain,
        command=command,
        generator=generator,
        handler=handler,
        min_permission=min_permission,
        **overrides,
    )


COMMANDS: dict[str, CommandSpec] = {key: _build_spec(key) for key in sorted(ALL_PERMISSION_KEYS)}
