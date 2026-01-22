"""
elke27_lib/features/area.py

Feature module: area

Converts the existing area handlers into Pattern-1 feature registration:
- Registers inbound handlers for area.get_status / area.set_status / area.__root__
- Registers outbound request builders (read-only sequence of implementation):
    - area.get_status (builder only)
    - (optional later) other area.get_* builders

Notes:
- We are NOT implementing any outbound writes. No set_* builders are registered.
- We DO consume inbound area.set_status messages as ingest-only status updates (Option A).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from elke27_lib.dispatcher import PagedTransferKey

if TYPE_CHECKING:
    from elke27_lib.kernel import E27Kernel

from elke27_lib.handlers.area import (
    make_area___root___handler,
    make_area_configured_merge,
    make_area_get_attribs_handler,
    make_area_get_configured_handler,
    make_area_get_status_handler,
    make_area_get_table_info_handler,
    make_area_get_troubles_handler,
    make_area_set_status_handler,
)

ROUTE_AREA_GET_STATUS = ("area", "get_status")
ROUTE_AREA_GET_ATTRIBS = ("area", "get_attribs")
ROUTE_AREA_GET_CONFIGURED = ("area", "get_configured")
ROUTE_AREA_GET_TABLE_INFO = ("area", "get_table_info")
ROUTE_AREA_TABLE_INFO = ("area", "table_info")
ROUTE_AREA_GET_TROUBLES = ("area", "get_troubles")
ROUTE_AREA_GET_TROUBLE = ("area", "get_trouble")
ROUTE_AREA_SET_STATUS = ("area", "set_status")  # inbound-only (no outbound builder)
ROUTE_AREA_ROOT = ("area", "__root__")


def register(elk: E27Kernel) -> None:
    def request_configured_block(block_id: int, transfer_key: PagedTransferKey) -> None:
        elk.request(
            ROUTE_AREA_GET_CONFIGURED,
            block_id=block_id,
            opaque=transfer_key,
        )

    elk.register_paged(
        ROUTE_AREA_GET_CONFIGURED,
        merge_fn=make_area_configured_merge(elk.state),
        request_block=request_configured_block,
    )
    # -------------------------
    # Inbound handlers
    # -------------------------
    elk.register_handler(
        ROUTE_AREA_GET_STATUS,
        make_area_get_status_handler(elk.state, elk.emit, elk.now),
    )
    elk.register_handler(
        ROUTE_AREA_GET_ATTRIBS,
        make_area_get_attribs_handler(elk.state, elk.emit, elk.now),
    )
    elk.register_handler(
        ROUTE_AREA_GET_CONFIGURED,
        make_area_get_configured_handler(
            elk.state,
            elk.emit,
            elk.now,
        ),
    )
    elk.register_handler(
        ROUTE_AREA_GET_TABLE_INFO,
        make_area_get_table_info_handler(elk.state, elk.emit, elk.now),
    )
    elk.register_handler(
        ROUTE_AREA_TABLE_INFO,
        make_area_get_table_info_handler(elk.state, elk.emit, elk.now),
    )
    elk.register_handler(
        ROUTE_AREA_GET_TROUBLES,
        make_area_get_troubles_handler(elk.state, elk.emit, elk.now),
    )
    elk.register_handler(
        ROUTE_AREA_GET_TROUBLE,
        make_area_get_troubles_handler(elk.state, elk.emit, elk.now),
    )
    elk.register_handler(
        ROUTE_AREA_SET_STATUS,
        make_area_set_status_handler(elk.state, elk.emit, elk.now),
    )
    elk.register_handler(
        ROUTE_AREA_ROOT,
        make_area___root___handler(elk.state, elk.emit, elk.now),
    )

    # -------------------------
    # Outbound request builders (GET-only in this phase)
    # -------------------------
    elk.register_request(
        ROUTE_AREA_GET_STATUS,
        build_area_get_status_payload,
    )
    elk.register_request(
        ROUTE_AREA_GET_ATTRIBS,
        build_area_get_attribs_payload,
    )
    elk.register_request(
        ROUTE_AREA_GET_CONFIGURED,
        build_area_get_configured_payload,
    )
    elk.register_request(
        ROUTE_AREA_GET_TABLE_INFO,
        build_area_get_table_info_payload,
    )
    elk.register_request(
        ROUTE_AREA_GET_TROUBLES,
        build_area_get_troubles_payload,
    )


def build_area_get_status_payload(*, area_id: int, **_kwargs: Any) -> Mapping[str, Any]:
    # Strict-ish validation here is fine (builders are programmer-facing).
    if area_id < 1:
        raise ValueError(
            f"build_area_get_status_payload: area_id must be int >= 1 (got {area_id!r})"
        )
    return {"area_id": area_id}


def build_area_get_attribs_payload(*, area_id: int, **_kwargs: Any) -> Mapping[str, Any]:
    if area_id < 1:
        raise ValueError(
            f"build_area_get_attribs_payload: area_id must be int >= 1 (got {area_id!r})"
        )
    return {"area_id": area_id}


def build_area_get_configured_payload(*, block_id: int = 1, **_kwargs: Any) -> Mapping[str, Any]:
    if block_id < 1:
        raise ValueError(
            f"build_area_get_configured_payload: block_id must be int >= 1 (got {block_id!r})"
        )
    return {"block_id": block_id}


def build_area_get_troubles_payload(*, area_id: int, **_kwargs: Any) -> Mapping[str, Any]:
    if area_id < 1:
        raise ValueError(
            f"build_area_get_troubles_payload: area_id must be int >= 1 (got {area_id!r})"
        )
    return {"area_id": area_id}


def build_area_get_table_info_payload(**_kwargs: Any) -> Mapping[str, Any]:
    return {}
