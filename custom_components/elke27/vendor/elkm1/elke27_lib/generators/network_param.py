"""Network parameter request generators."""

from __future__ import annotations

ResponseKey = tuple[str, str]


def generator_network_param_get_ssid() -> tuple[dict, ResponseKey]:
    return {}, ("network", "get_ssid")


def generator_network_param_get_rssi() -> tuple[dict, ResponseKey]:
    return {}, ("network", "get_rssi")
