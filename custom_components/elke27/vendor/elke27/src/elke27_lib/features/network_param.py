"""
elke27_lib/features/network_param.py

Feature module: network_param (domain key "network" per PDF).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from elke27_lib.kernel import E27Kernel

from elke27_lib.handlers.network_param import (
    make_network_error_handler,
    make_network_param_get_rssi_handler,
    make_network_param_get_ssid_handler,
)

ROUTE_NETWORK_GET_SSID = ("network", "get_ssid")
ROUTE_NETWORK_GET_RSSI = ("network", "get_rssi")
ROUTE_NETWORK_ERROR = ("network", "error")


def register(elk: E27Kernel) -> None:
    elk.register_handler(
        ROUTE_NETWORK_GET_SSID,
        make_network_param_get_ssid_handler(elk.state, elk.emit, elk.now),
    )
    elk.register_handler(
        ROUTE_NETWORK_GET_RSSI,
        make_network_param_get_rssi_handler(elk.state, elk.emit, elk.now),
    )
    elk.register_handler(
        ROUTE_NETWORK_ERROR,
        make_network_error_handler(elk.state, elk.emit, elk.now),
    )

    elk.register_request(
        ROUTE_NETWORK_GET_SSID,
        build_network_get_ssid_payload,
    )
    elk.register_request(
        ROUTE_NETWORK_GET_RSSI,
        build_network_get_rssi_payload,
    )


def build_network_get_ssid_payload(**_kwargs: Any) -> Mapping[str, Any]:
    return {}


def build_network_get_rssi_payload(**_kwargs: Any) -> Mapping[str, Any]:
    return {}
