"""
elke27_lib/features/tstat.py

Feature module: tstat
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from elke27_lib.kernel import E27Kernel

from elke27_lib.handlers.tstat import (
    make_tstat_get_status_handler,
    make_tstat_get_table_info_handler,
)

ROUTE_TSTAT_GET_STATUS = ("tstat", "get_status")
ROUTE_TSTAT_GET_TABLE_INFO = ("tstat", "get_table_info")
ROUTE_TSTAT_TABLE_INFO = ("tstat", "table_info")


def register(elk: E27Kernel) -> None:
    elk.register_handler(
        ROUTE_TSTAT_GET_STATUS,
        make_tstat_get_status_handler(elk.state, elk.emit, elk.now),
    )
    elk.register_handler(
        ROUTE_TSTAT_GET_TABLE_INFO,
        make_tstat_get_table_info_handler(elk.state, elk.emit, elk.now),
    )
    elk.register_handler(
        ROUTE_TSTAT_TABLE_INFO,
        make_tstat_get_table_info_handler(elk.state, elk.emit, elk.now),
    )
    elk.register_request(
        ROUTE_TSTAT_GET_STATUS,
        build_tstat_get_status_payload,
    )
    elk.register_request(
        ROUTE_TSTAT_GET_TABLE_INFO,
        build_tstat_get_table_info_payload,
    )


def build_tstat_get_status_payload(*, tstat_id: int, **_kwargs: Any) -> Mapping[str, Any]:
    if tstat_id < 1:
        raise ValueError(
            f"build_tstat_get_status_payload: tstat_id must be int >= 1 (got {tstat_id!r})"
        )
    return {"tstat_id": tstat_id}


def build_tstat_get_table_info_payload(**_kwargs: Any) -> Mapping[str, Any]:
    return {}
