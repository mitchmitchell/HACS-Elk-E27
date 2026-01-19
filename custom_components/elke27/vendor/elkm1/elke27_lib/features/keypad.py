"""
elke27_lib/features/keypad.py

Feature module: keypad
"""

from __future__ import annotations

from typing import Any

from elke27_lib.handlers.keypad import (
    make_keypad_get_attribs_handler,
    make_keypad_get_configured_handler,
    make_keypad_get_table_info_handler,
)

ROUTE_KEYPAD_GET_CONFIGURED = ("keypad", "get_configured")
ROUTE_KEYPAD_GET_ATTRIBS = ("keypad", "get_attribs")
ROUTE_KEYPAD_GET_TABLE_INFO = ("keypad", "get_table_info")


def register(elk) -> None:
    elk.register_handler(
        ROUTE_KEYPAD_GET_CONFIGURED,
        make_keypad_get_configured_handler(elk.state, elk.emit, elk.now),
    )
    elk.register_handler(
        ROUTE_KEYPAD_GET_ATTRIBS,
        make_keypad_get_attribs_handler(elk.state, elk.emit, elk.now),
    )
    elk.register_handler(
        ROUTE_KEYPAD_GET_TABLE_INFO,
        make_keypad_get_table_info_handler(elk.state, elk.emit, elk.now),
    )

    elk.register_request(
        ROUTE_KEYPAD_GET_CONFIGURED,
        build_keypad_get_configured_payload,
    )
    elk.register_request(
        ROUTE_KEYPAD_GET_ATTRIBS,
        build_keypad_get_attribs_payload,
    )
    elk.register_request(
        ROUTE_KEYPAD_GET_TABLE_INFO,
        build_keypad_get_table_info_payload,
    )


def build_keypad_get_configured_payload(*, block_id: int = 1, **kwargs: Any) -> dict:
    if not isinstance(block_id, int) or block_id < 1:
        raise ValueError(f"build_keypad_get_configured_payload: block_id must be int >= 1 (got {block_id!r})")
    return {"block_id": block_id}


def build_keypad_get_attribs_payload(*, keypad_id: int, **kwargs: Any) -> dict:
    if not isinstance(keypad_id, int) or keypad_id < 1:
        raise ValueError(f"build_keypad_get_attribs_payload: keypad_id must be int >= 1 (got {keypad_id!r})")
    return {"keypad_id": keypad_id}


def build_keypad_get_table_info_payload(**kwargs: Any) -> dict:
    return {}
