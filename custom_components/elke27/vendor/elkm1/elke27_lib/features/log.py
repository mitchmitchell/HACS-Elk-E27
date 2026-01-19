"""
elke27_lib/features/log.py

Feature module: log

Responsibilities:
- Register inbound handlers for log.*
- Register outbound request builders for log.* routes
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from elke27_lib.handlers.log import (
    make_log_clear_handler,
    make_log_get_attribs_handler,
    make_log_get_index_handler,
    make_log_get_list_handler,
    make_log_get_log_handler,
    make_log_get_table_info_handler,
    make_log_get_trouble_handler,
    make_log_realloc_handler,
    make_log_set_attribs_handler,
)

ROUTE_LOG_CLEAR = ("log", "clear")
ROUTE_LOG_GET_ATTRIBS = ("log", "get_attribs")
ROUTE_LOG_GET_INDEX = ("log", "get_index")
ROUTE_LOG_GET_LIST = ("log", "get_list")
ROUTE_LOG_GET_LOG = ("log", "get_log")
ROUTE_LOG_GET_TABLE_INFO = ("log", "get_table_info")
ROUTE_LOG_GET_TROUBLE = ("log", "get_trouble")
ROUTE_LOG_REALLOC = ("log", "realloc")
ROUTE_LOG_SET_ATTRIBS = ("log", "set_attribs")


def register(elk) -> None:
    elk.register_handler(
        ROUTE_LOG_GET_TROUBLE,
        make_log_get_trouble_handler(elk.state, elk.emit, elk.now),
    )
    elk.register_handler(
        ROUTE_LOG_GET_INDEX,
        make_log_get_index_handler(elk.state, elk.emit, elk.now),
    )
    elk.register_handler(
        ROUTE_LOG_GET_TABLE_INFO,
        make_log_get_table_info_handler(elk.state, elk.emit, elk.now),
    )
    elk.register_handler(
        ROUTE_LOG_GET_ATTRIBS,
        make_log_get_attribs_handler(elk.state, elk.emit, elk.now),
    )
    elk.register_handler(
        ROUTE_LOG_SET_ATTRIBS,
        make_log_set_attribs_handler(elk.state, elk.emit, elk.now),
    )
    elk.register_handler(
        ROUTE_LOG_GET_LIST,
        make_log_get_list_handler(elk.state, elk.emit, elk.now),
    )
    elk.register_handler(
        ROUTE_LOG_GET_LOG,
        make_log_get_log_handler(elk.state, elk.emit, elk.now),
    )
    elk.register_handler(
        ROUTE_LOG_CLEAR,
        make_log_clear_handler(elk.state, elk.emit, elk.now),
    )
    elk.register_handler(
        ROUTE_LOG_REALLOC,
        make_log_realloc_handler(elk.state, elk.emit, elk.now),
    )

    elk.register_request(
        ROUTE_LOG_GET_TROUBLE,
        build_log_get_trouble_payload,
    )
    elk.register_request(
        ROUTE_LOG_GET_INDEX,
        build_log_get_index_payload,
    )
    elk.register_request(
        ROUTE_LOG_GET_TABLE_INFO,
        build_log_get_table_info_payload,
    )
    elk.register_request(
        ROUTE_LOG_GET_ATTRIBS,
        build_log_get_attribs_payload,
    )
    elk.register_request(
        ROUTE_LOG_GET_LIST,
        build_log_get_list_payload,
    )
    elk.register_request(
        ROUTE_LOG_GET_LOG,
        build_log_get_log_payload,
    )
    # NOTE: log_set_attribs/log_clear/log_realloc are intentionally not registered.
    # These commands can mutate or clear panel logs and are disabled by default.


def build_log_get_trouble_payload(**kwargs: Any) -> Mapping[str, Any]:
    return {}


def build_log_get_index_payload(**kwargs: Any) -> Mapping[str, Any]:
    return {}


def build_log_get_table_info_payload(**kwargs: Any) -> Mapping[str, Any]:
    return {}


def build_log_get_attribs_payload(**kwargs: Any) -> Mapping[str, Any]:
    return {}


def build_log_get_list_payload(*, start: int, date: int, cnt: int, **kwargs: Any) -> Mapping[str, Any]:
    if not all(isinstance(value, int) for value in (start, date, cnt)):
        raise ValueError("build_log_get_list_payload requires integer start, date, and cnt")
    return {"start": start, "date": date, "cnt": cnt}


def build_log_get_log_payload(*, log_id: int, **kwargs: Any) -> Mapping[str, Any]:
    if not isinstance(log_id, int):
        raise ValueError("build_log_get_log_payload requires integer log_id")
    return {"log_id": log_id}

