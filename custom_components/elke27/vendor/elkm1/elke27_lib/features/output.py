"""
elke27_lib/features/output.py

Feature module: output
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from elke27_lib.handlers.output import (
    make_output_configured_merge,
    make_output_get_all_outputs_status_handler,
    make_output_get_attribs_handler,
    make_output_get_configured_handler,
    make_output_get_status_handler,
    make_output_get_table_info_handler,
)

ROUTE_OUTPUT_GET_STATUS = ("output", "get_status")
ROUTE_OUTPUT_GET_ALL_OUTPUTS_STATUS = ("output", "get_all_outputs_status")
ROUTE_OUTPUT_GET_TABLE_INFO = ("output", "get_table_info")
ROUTE_OUTPUT_TABLE_INFO = ("output", "table_info")
ROUTE_OUTPUT_GET_ATTRIBS = ("output", "get_attribs")
ROUTE_OUTPUT_GET_CONFIGURED = ("output", "get_configured")


def register(elk) -> None:
    elk.register_handler(
        ROUTE_OUTPUT_GET_STATUS,
        make_output_get_status_handler(elk.state, elk.emit, elk.now),
    )
    elk.register_handler(
        ROUTE_OUTPUT_GET_CONFIGURED,
        make_output_get_configured_handler(elk.state, elk.emit, elk.now),
    )
    elk.register_handler(
        ROUTE_OUTPUT_GET_ALL_OUTPUTS_STATUS,
        make_output_get_all_outputs_status_handler(elk.state, elk.emit, elk.now),
    )
    elk.register_handler(
        ROUTE_OUTPUT_GET_ATTRIBS,
        make_output_get_attribs_handler(elk.state, elk.emit, elk.now),
    )
    elk.register_handler(
        ROUTE_OUTPUT_GET_TABLE_INFO,
        make_output_get_table_info_handler(elk.state, elk.emit, elk.now),
    )
    elk.register_handler(
        ROUTE_OUTPUT_TABLE_INFO,
        make_output_get_table_info_handler(elk.state, elk.emit, elk.now),
    )

    elk.register_paged(
        ROUTE_OUTPUT_GET_CONFIGURED,
        merge_fn=make_output_configured_merge(elk.state),
        request_block=lambda block_id, transfer_key: elk.request(
            ROUTE_OUTPUT_GET_CONFIGURED,
            block_id=block_id,
            opaque=transfer_key,
        ),
    )

    elk.register_request(
        ROUTE_OUTPUT_GET_STATUS,
        build_output_get_status_payload,
    )
    elk.register_request(
        ROUTE_OUTPUT_GET_CONFIGURED,
        build_output_get_configured_payload,
    )
    elk.register_request(
        ROUTE_OUTPUT_GET_ALL_OUTPUTS_STATUS,
        build_output_get_all_outputs_status_payload,
    )
    elk.register_request(
        ROUTE_OUTPUT_GET_ATTRIBS,
        build_output_get_attribs_payload,
    )
    elk.register_request(
        ROUTE_OUTPUT_GET_TABLE_INFO,
        build_output_get_table_info_payload,
    )


def build_output_get_status_payload(*, output_id: int, **kwargs: Any) -> Mapping[str, Any]:
    if not isinstance(output_id, int) or output_id < 1:
        raise ValueError(f"build_output_get_status_payload: output_id must be int >= 1 (got {output_id!r})")
    return {"output_id": output_id}


def build_output_get_all_outputs_status_payload(**kwargs: Any) -> bool:
    return True


def build_output_get_attribs_payload(*, output_id: int, **kwargs: Any) -> Mapping[str, Any]:
    if not isinstance(output_id, int) or output_id < 1:
        raise ValueError(f"build_output_get_attribs_payload: output_id must be int >= 1 (got {output_id!r})")
    return {"output_id": output_id}


def build_output_get_table_info_payload(**kwargs: Any) -> Mapping[str, Any]:
    return {}


def build_output_get_configured_payload(*, block_id: int = 1, **kwargs: Any) -> Mapping[str, Any]:
    if not isinstance(block_id, int) or block_id < 1:
        raise ValueError(f"build_output_get_configured_payload: block_id must be int >= 1 (got {block_id!r})")
    return {"block_id": block_id}
