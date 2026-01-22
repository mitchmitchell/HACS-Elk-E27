"""Bus I/O domain request generators."""

from __future__ import annotations

ResponseKey = tuple[str, str]


def generator_bus_ios_get_trouble() -> tuple[dict[str, object], ResponseKey]:
    return {}, ("bus_io_dev", "get_trouble")
