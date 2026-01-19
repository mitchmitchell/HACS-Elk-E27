"""Control domain request generators."""

from __future__ import annotations

from typing import Tuple


ResponseKey = Tuple[str, str]


def generator_control_get_version_info() -> tuple[dict, ResponseKey]:
    return {}, ("control", "get_version_info")


def generator_control_get_table_info() -> tuple[dict, ResponseKey]:
    return {}, ("control", "get_table_info")


def generator_control_authenticate(*, pin: int) -> tuple[dict, ResponseKey]:
    if not isinstance(pin, int):
        raise ValueError("pin must be an integer")
    if not (0 <= pin <= 999999):
        raise ValueError("pin must be in range 0..999999")
    return {"pin": pin}, ("control", "authenticate")
