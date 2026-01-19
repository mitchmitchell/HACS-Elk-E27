"""
elke27_lib/features/zone.py

Feature module: zone

Responsibilities:
- Register inbound handlers for zone.get_configured and zone.get_all_zones_status
- Register outbound request builders for the same routes
"""

from __future__ import annotations

from typing import Any, Mapping

from elke27_lib.handlers.zone import (
    make_zone_get_all_zones_status_handler,
    make_zone_get_status_handler,
    make_zone_get_attribs_handler,
    make_zone_configured_merge,
    make_zone_get_configured_handler,
    make_zone_get_def_flags_handler,
    make_zone_get_defs_handler,
    make_zone_get_table_info_handler,
    make_zone_set_status_handler,
)


ROUTE_ZONE_GET_CONFIGURED = ("zone", "get_configured")
ROUTE_ZONE_GET_ATTRIBS = ("zone", "get_attribs")
ROUTE_ZONE_GET_STATUS = ("zone", "get_status")
ROUTE_ZONE_GET_ALL_ZONES_STATUS = ("zone", "get_all_zones_status")
ROUTE_ZONE_GET_TABLE_INFO = ("zone", "get_table_info")
ROUTE_ZONE_TABLE_INFO = ("zone", "table_info")
ROUTE_ZONE_GET_DEFS = ("zone", "get_defs")
ROUTE_ZONE_GET_DEF_FLAGS = ("zone", "get_def_flags")
ROUTE_ZONE_SET_STATUS = ("zone", "set_status")


def register(elk) -> None:
    elk.register_paged(
        ROUTE_ZONE_GET_CONFIGURED,
        merge_fn=make_zone_configured_merge(elk.state),
        request_block=lambda block_id, transfer_key: elk.request(
            ROUTE_ZONE_GET_CONFIGURED,
            block_id=block_id,
            opaque=transfer_key,
        ),
    )
    # Inbound handlers
    elk.register_handler(
        ROUTE_ZONE_GET_CONFIGURED,
        make_zone_get_configured_handler(
            elk.state,
            elk.emit,
            elk.now,
        ),
    )
    elk.register_handler(
        ROUTE_ZONE_GET_ATTRIBS,
        make_zone_get_attribs_handler(elk.state, elk.emit, elk.now),
    )
    elk.register_handler(
        ROUTE_ZONE_GET_STATUS,
        make_zone_get_status_handler(elk.state, elk.emit, elk.now),
    )
    elk.register_handler(
        ROUTE_ZONE_GET_ALL_ZONES_STATUS,
        make_zone_get_all_zones_status_handler(elk.state, elk.emit, elk.now),
    )
    elk.register_handler(
        ROUTE_ZONE_GET_TABLE_INFO,
        make_zone_get_table_info_handler(elk.state, elk.emit, elk.now),
    )
    elk.register_handler(
        ROUTE_ZONE_TABLE_INFO,
        make_zone_get_table_info_handler(elk.state, elk.emit, elk.now),
    )
    elk.register_handler(
        ROUTE_ZONE_GET_DEFS,
        make_zone_get_defs_handler(elk.state, elk.emit, elk.now),
    )
    elk.register_handler(
        ROUTE_ZONE_GET_DEF_FLAGS,
        make_zone_get_def_flags_handler(elk.state, elk.emit, elk.now),
    )
    elk.register_handler(
        ROUTE_ZONE_SET_STATUS,
        make_zone_set_status_handler(elk.state, elk.emit, elk.now),
    )

    # Outbound request builders
    elk.register_request(
        ROUTE_ZONE_GET_CONFIGURED,
        build_zone_get_configured_payload,
    )
    elk.register_request(
        ROUTE_ZONE_GET_ATTRIBS,
        build_zone_get_attribs_payload,
    )
    elk.register_request(
        ROUTE_ZONE_GET_STATUS,
        build_zone_get_status_payload,
    )
    elk.register_request(
        ROUTE_ZONE_GET_ALL_ZONES_STATUS,
        build_zone_get_all_zones_status_payload,
    )
    elk.register_request(
        ROUTE_ZONE_GET_TABLE_INFO,
        build_zone_get_table_info_payload,
    )
    elk.register_request(
        ROUTE_ZONE_GET_DEFS,
        build_zone_get_defs_payload,
    )
    elk.register_request(
        ROUTE_ZONE_GET_DEF_FLAGS,
        build_zone_get_def_flags_payload,
    )
    elk.register_request(
        ROUTE_ZONE_SET_STATUS,
        build_zone_set_status_payload,
    )


def build_zone_get_configured_payload(*, block_id: int = 1, **kwargs: Any) -> Mapping[str, Any]:
    if not isinstance(block_id, int) or block_id < 1:
        raise ValueError(f"build_zone_get_configured_payload: block_id must be int >= 1 (got {block_id!r})")
    return {"block_id": block_id}


def build_zone_get_all_zones_status_payload(**kwargs: Any) -> bool:
    return True


def build_zone_get_attribs_payload(*, zone_id: int, **kwargs: Any) -> Mapping[str, Any]:
    if not isinstance(zone_id, int) or zone_id < 1:
        raise ValueError(f"build_zone_get_attribs_payload: zone_id must be int >= 1 (got {zone_id!r})")
    return {"zone_id": zone_id}


def build_zone_get_status_payload(*, zone_id: int, **kwargs: Any) -> Mapping[str, Any]:
    if not isinstance(zone_id, int) or zone_id < 1:
        raise ValueError(f"build_zone_get_status_payload: zone_id must be int >= 1 (got {zone_id!r})")
    return {"zone_id": zone_id}


def build_zone_get_table_info_payload(**kwargs: Any) -> Mapping[str, Any]:
    return {}


def build_zone_get_defs_payload(*, block_id: int, **kwargs: Any) -> Mapping[str, Any]:
    if not isinstance(block_id, int) or block_id < 1:
        raise ValueError(f"build_zone_get_defs_payload: block_id must be int >= 1 (got {block_id!r})")
    return {"block_id": block_id}


def build_zone_get_def_flags_payload(*, definition: str, **kwargs: Any) -> Mapping[str, Any]:
    if not isinstance(definition, str) or not definition.strip():
        raise ValueError("build_zone_get_def_flags_payload: definition must be a non-empty string")
    return {"definition": definition}


def build_zone_set_status_payload(*, zone_id: int, pin: int | str, bypassed: bool, **kwargs: Any) -> Mapping[str, Any]:
    if not isinstance(zone_id, int) or zone_id < 1:
        raise ValueError(f"build_zone_set_status_payload: zone_id must be int >= 1 (got {zone_id!r})")
    if not isinstance(bypassed, bool):
        raise ValueError(f"build_zone_set_status_payload: bypassed must be bool (got {bypassed!r})")
    return {"zone_id": zone_id, "pin": pin, "BYPASSED": bypassed}
