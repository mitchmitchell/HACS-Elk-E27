"""Area domain request generators."""

from __future__ import annotations

from typing import Literal

ResponseKey = tuple[str, str]
ArmState = Literal["DISARMED", "ARMED_AWAY", "ARMED_STAY"]

_ARM_STATES: set[str] = {"DISARMED", "ARMED_AWAY", "ARMED_STAY"}


def generator_area_get_table_info() -> tuple[dict, ResponseKey]:
    return {}, ("area", "get_table_info")


def generator_area_get_configured(*, block_id: int = 1) -> tuple[dict, ResponseKey]:
    if not isinstance(block_id, int) or block_id < 1:
        raise ValueError(f"block_id must be an int >= 1 (got {block_id!r})")
    return {"block_id": block_id}, ("area", "get_configured")


def generator_area_get_status(*, area_id: int) -> tuple[dict, ResponseKey]:
    if not isinstance(area_id, int) or area_id < 1:
        raise ValueError(f"area_id must be an int >= 1 (got {area_id!r})")
    return {"area_id": area_id}, ("area", "get_status")


def generator_area_get_attribs(*, area_id: int) -> tuple[dict, ResponseKey]:
    if not isinstance(area_id, int) or area_id < 1:
        raise ValueError(f"area_id must be an int >= 1 (got {area_id!r})")
    return {"area_id": area_id}, ("area", "get_attribs")


def generator_area_set_status(*, area_id: int, chime: bool) -> tuple[dict, ResponseKey]:
    if not isinstance(area_id, int) or area_id < 1:
        raise ValueError(f"area_id must be an int >= 1 (got {area_id!r})")
    if not isinstance(chime, bool):
        raise ValueError(f"chime must be bool (got {chime!r})")
    return {"area_id": area_id, "Chime": chime}, ("area", "set_status")


def generator_area_set_arm_state(*, area_id: int, arm_state: ArmState | str, pin: int) -> tuple[dict, ResponseKey]:
    if not isinstance(area_id, int) or area_id < 1:
        raise ValueError(f"area_id must be an int >= 1 (got {area_id!r})")
    if not isinstance(arm_state, str) or arm_state not in _ARM_STATES:
        raise ValueError(f"arm_state must be one of {_ARM_STATES!r} (got {arm_state!r})")
    if not isinstance(pin, int) or pin <= 0:
        raise ValueError(f"pin must be a positive int (got {pin!r})")
    return {"area_id": area_id, "arm_state": arm_state, "pin": pin}, ("area", "set_arm_state")
