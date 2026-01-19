"""
elke27_lib/features/control.py

Feature module: control

Responsibilities:
- Register inbound handlers for control.*
- Register outbound request builders for control.* routes

Current scope:
- ("control","get_version_info")
- ("control","authenticate")
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from elke27_lib.handlers.control import (
    make_control_authenticate_handler,
    make_control_get_trouble_handler,
    make_control_get_version_info_handler,
)

ROUTE_CONTROL_GET_VERSION_INFO = ("control", "get_version_info")
ROUTE_CONTROL_AUTHENTICATE = ("control", "authenticate")
ROUTE_CONTROL_GET_TROUBLE = ("control", "get_trouble")


def register(elk) -> None:
    # Inbound handler
    elk.register_handler(
        ROUTE_CONTROL_GET_VERSION_INFO,
        make_control_get_version_info_handler(elk.state, elk.emit, elk.now),
    )
    elk.register_handler(
        ROUTE_CONTROL_AUTHENTICATE,
        make_control_authenticate_handler(elk.state, elk.emit, elk.now),
    )
    elk.register_handler(
        ROUTE_CONTROL_GET_TROUBLE,
        make_control_get_trouble_handler(elk.state, elk.emit, elk.now),
    )

    # Outbound request builder (payload only; kernel builds seq/session_id/envelope)
    elk.register_request(
        ROUTE_CONTROL_GET_VERSION_INFO,
        build_control_get_version_info_payload,
    )
    elk.register_request(
        ROUTE_CONTROL_AUTHENTICATE,
        build_control_authenticate_payload,
    )
    elk.register_request(
        ROUTE_CONTROL_GET_TROUBLE,
        build_control_get_trouble_payload,
    )


def build_control_get_version_info_payload(**kwargs: Any) -> dict:
    # No parameters required for v0
    return {}


def build_control_authenticate_payload(*, pin: int | str, **kwargs: Any) -> dict:
    if isinstance(pin, str):
        if not pin.isdigit():
            raise ValueError("pin must be an integer or numeric string")
        pin_value = int(pin)
    elif isinstance(pin, int):
        pin_value = pin
    else:
        raise ValueError("pin must be an integer or numeric string")
    if not (0 <= pin_value <= 999999):
        raise ValueError("pin must be in range 0..999999")
    return {"pin": pin_value}


def build_control_get_trouble_payload(**kwargs: Any) -> Mapping[str, Any]:
    return {}
