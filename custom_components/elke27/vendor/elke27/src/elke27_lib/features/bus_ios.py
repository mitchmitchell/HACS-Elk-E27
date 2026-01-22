"""
elke27_lib/features/bus_ios.py

Feature module: bus_ios

Responsibilities:
- Register inbound handlers for bus_io_dev.*
- Register outbound request builders for bus_io_dev.* routes
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from elke27_lib.kernel import E27Kernel

from elke27_lib.handlers.bus_ios import make_bus_ios_get_trouble_handler

ROUTE_BUS_IOS_GET_TROUBLE = ("bus_io_dev", "get_trouble")


def register(elk: E27Kernel) -> None:
    elk.register_handler(
        ROUTE_BUS_IOS_GET_TROUBLE,
        make_bus_ios_get_trouble_handler(elk.state, elk.emit, elk.now),
    )

    elk.register_request(
        ROUTE_BUS_IOS_GET_TROUBLE,
        build_bus_ios_get_trouble_payload,
    )


def build_bus_ios_get_trouble_payload(**_kwargs: Any) -> Mapping[str, Any]:
    return {}
