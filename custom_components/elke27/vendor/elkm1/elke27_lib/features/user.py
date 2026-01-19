"""
elke27_lib/features/user.py

Feature module: user
"""

from __future__ import annotations

from typing import Any

from elke27_lib.handlers.user import (
    make_user_get_attribs_handler,
    make_user_get_configured_handler,
)


ROUTE_USER_GET_CONFIGURED = ("user", "get_configured")
ROUTE_USER_GET_ATTRIBS = ("user", "get_attribs")


def register(elk) -> None:
    elk.register_handler(
        ROUTE_USER_GET_CONFIGURED,
        make_user_get_configured_handler(elk.state, elk.emit, elk.now),
    )
    elk.register_handler(
        ROUTE_USER_GET_ATTRIBS,
        make_user_get_attribs_handler(elk.state, elk.emit, elk.now),
    )

    elk.register_request(
        ROUTE_USER_GET_CONFIGURED,
        build_user_get_configured_payload,
    )
    elk.register_request(
        ROUTE_USER_GET_ATTRIBS,
        build_user_get_attribs_payload,
    )


def build_user_get_configured_payload(*, block_id: int = 1, **kwargs: Any) -> dict:
    if not isinstance(block_id, int) or block_id < 1:
        raise ValueError(f"build_user_get_configured_payload: block_id must be int >= 1 (got {block_id!r})")
    return {"block_id": block_id}


def build_user_get_attribs_payload(*, user_id: int, **kwargs: Any) -> dict:
    if not isinstance(user_id, int) or user_id < 1:
        raise ValueError(f"build_user_get_attribs_payload: user_id must be int >= 1 (got {user_id!r})")
    return {"user_id": user_id}
