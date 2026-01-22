"""Keypad domain request generators."""

from __future__ import annotations

ResponseKey = tuple[str, str]


def generator_keypad_get_configured(*, block_id: int = 1) -> tuple[dict[str, object], ResponseKey]:
    if block_id < 1:
        raise ValueError(f"block_id must be an int >= 1 (got {block_id!r})")
    return {"block_id": block_id}, ("keypad", "get_configured")


def generator_keypad_get_attribs(*, keypad_id: int) -> tuple[dict[str, object], ResponseKey]:
    if keypad_id < 1:
        raise ValueError(f"keypad_id must be an int >= 1 (got {keypad_id!r})")
    return {"keypad_id": keypad_id}, ("keypad", "get_attribs")


def generator_keypad_get_table_info() -> tuple[dict[str, object], ResponseKey]:
    return {}, ("keypad", "get_table_info")
