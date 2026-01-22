"""
elke27_lib/features/system.py

Feature module: system
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from elke27_lib.kernel import E27Kernel

from elke27_lib.handlers.system import (
    make_system_file_info_handler,
    make_system_get_attribs_handler,
    make_system_get_cutoffs_handler,
    make_system_get_debug_flags_handler,
    make_system_get_debug_string_handler,
    make_system_get_sounders_handler,
    make_system_get_system_time_handler,
    make_system_get_table_info_handler,
    make_system_get_trouble_handler,
    make_system_get_troubles_handler,
    make_system_get_update_handler,
    make_system_r_u_alive_handler,
    make_system_reconfig_handler,
    make_system_reset_smokes_handler,
    make_system_set_attribs_handler,
    make_system_set_cutoffs_handler,
    make_system_set_debug_flags_handler,
    make_system_set_run_handler,
    make_system_set_system_key_handler,
    make_system_set_system_time_handler,
    make_system_start_updt_handler,
)

ROUTE_SYSTEM_GET_TROUBLE = ("system", "get_trouble")
ROUTE_SYSTEM_GET_TROUBLES = ("system", "get_troubles")
ROUTE_SYSTEM_GET_TABLE_INFO = ("system", "get_table_info")
ROUTE_SYSTEM_GET_ATTRIBS = ("system", "get_attribs")
ROUTE_SYSTEM_SET_ATTRIBS = ("system", "set_attribs")
ROUTE_SYSTEM_GET_CUTOFFS = ("system", "get_cutoffs")
ROUTE_SYSTEM_SET_CUTOFFS = ("system", "set_cutoffs")
ROUTE_SYSTEM_GET_SOUNDERS = ("system", "get_sounders")
ROUTE_SYSTEM_GET_SYSTEM_TIME = ("system", "get_system_time")
ROUTE_SYSTEM_SET_SYSTEM_TIME = ("system", "set_system_time")
ROUTE_SYSTEM_SET_SYSTEM_KEY = ("system", "set_system_key")
ROUTE_SYSTEM_FILE_INFO = ("system", "file_info")
ROUTE_SYSTEM_GET_DEBUG_FLAGS = ("system", "get_debug_flags")
ROUTE_SYSTEM_SET_DEBUG_FLAGS = ("system", "set_debug_flags")
ROUTE_SYSTEM_GET_DEBUG_STRING = ("system", "get_debug_string")
ROUTE_SYSTEM_R_U_ALIVE = ("system", "r_u_alive")
ROUTE_SYSTEM_RESET_SMOKES = ("system", "reset_smokes")
ROUTE_SYSTEM_SET_RUN = ("system", "set_run")
ROUTE_SYSTEM_START_UPDT = ("system", "start_updt")
ROUTE_SYSTEM_RECONFIG = ("system", "reconfig")
ROUTE_SYSTEM_GET_UPDATE = ("system", "get_update")


def register(elk: E27Kernel) -> None:
    elk.register_handler(
        ROUTE_SYSTEM_GET_TROUBLE,
        make_system_get_trouble_handler(elk.state, elk.emit, elk.now),
    )
    elk.register_handler(
        ROUTE_SYSTEM_GET_TROUBLES,
        make_system_get_troubles_handler(elk.state, elk.emit, elk.now),
    )
    elk.register_handler(
        ROUTE_SYSTEM_GET_TABLE_INFO,
        make_system_get_table_info_handler(elk.state, elk.emit, elk.now),
    )
    elk.register_handler(
        ROUTE_SYSTEM_GET_ATTRIBS,
        make_system_get_attribs_handler(elk.state, elk.emit, elk.now),
    )
    elk.register_handler(
        ROUTE_SYSTEM_SET_ATTRIBS,
        make_system_set_attribs_handler(elk.state, elk.emit, elk.now),
    )
    elk.register_handler(
        ROUTE_SYSTEM_GET_CUTOFFS,
        make_system_get_cutoffs_handler(elk.state, elk.emit, elk.now),
    )
    elk.register_handler(
        ROUTE_SYSTEM_SET_CUTOFFS,
        make_system_set_cutoffs_handler(elk.state, elk.emit, elk.now),
    )
    elk.register_handler(
        ROUTE_SYSTEM_GET_SOUNDERS,
        make_system_get_sounders_handler(elk.state, elk.emit, elk.now),
    )
    elk.register_handler(
        ROUTE_SYSTEM_GET_SYSTEM_TIME,
        make_system_get_system_time_handler(elk.state, elk.emit, elk.now),
    )
    elk.register_handler(
        ROUTE_SYSTEM_SET_SYSTEM_TIME,
        make_system_set_system_time_handler(elk.state, elk.emit, elk.now),
    )
    elk.register_handler(
        ROUTE_SYSTEM_SET_SYSTEM_KEY,
        make_system_set_system_key_handler(elk.state, elk.emit, elk.now),
    )
    elk.register_handler(
        ROUTE_SYSTEM_FILE_INFO,
        make_system_file_info_handler(elk.state, elk.emit, elk.now),
    )
    elk.register_handler(
        ROUTE_SYSTEM_GET_DEBUG_FLAGS,
        make_system_get_debug_flags_handler(elk.state, elk.emit, elk.now),
    )
    elk.register_handler(
        ROUTE_SYSTEM_SET_DEBUG_FLAGS,
        make_system_set_debug_flags_handler(elk.state, elk.emit, elk.now),
    )
    elk.register_handler(
        ROUTE_SYSTEM_GET_DEBUG_STRING,
        make_system_get_debug_string_handler(elk.state, elk.emit, elk.now),
    )
    elk.register_handler(
        ROUTE_SYSTEM_R_U_ALIVE,
        make_system_r_u_alive_handler(elk.state, elk.emit, elk.now),
    )
    elk.register_handler(
        ROUTE_SYSTEM_RESET_SMOKES,
        make_system_reset_smokes_handler(elk.state, elk.emit, elk.now),
    )
    elk.register_handler(
        ROUTE_SYSTEM_SET_RUN,
        make_system_set_run_handler(elk.state, elk.emit, elk.now),
    )
    elk.register_handler(
        ROUTE_SYSTEM_START_UPDT,
        make_system_start_updt_handler(elk.state, elk.emit, elk.now),
    )
    elk.register_handler(
        ROUTE_SYSTEM_RECONFIG,
        make_system_reconfig_handler(elk.state, elk.emit, elk.now),
    )
    elk.register_handler(
        ROUTE_SYSTEM_GET_UPDATE,
        make_system_get_update_handler(elk.state, elk.emit, elk.now),
    )

    elk.register_request(
        ROUTE_SYSTEM_GET_TROUBLE,
        build_system_get_trouble_payload,
    )
    elk.register_request(
        ROUTE_SYSTEM_GET_TROUBLES,
        build_system_get_trouble_payload,
    )
    elk.register_request(
        ROUTE_SYSTEM_GET_TABLE_INFO,
        build_system_get_table_info_payload,
    )
    elk.register_request(
        ROUTE_SYSTEM_GET_ATTRIBS,
        build_system_get_attribs_payload,
    )
    elk.register_request(
        ROUTE_SYSTEM_SET_ATTRIBS,
        build_system_set_attribs_payload,
    )
    elk.register_request(
        ROUTE_SYSTEM_GET_CUTOFFS,
        build_system_get_cutoffs_payload,
    )
    elk.register_request(
        ROUTE_SYSTEM_SET_CUTOFFS,
        build_system_set_cutoffs_payload,
    )
    elk.register_request(
        ROUTE_SYSTEM_GET_SOUNDERS,
        build_system_get_sounders_payload,
    )
    elk.register_request(
        ROUTE_SYSTEM_GET_SYSTEM_TIME,
        build_system_get_system_time_payload,
    )
    elk.register_request(
        ROUTE_SYSTEM_SET_SYSTEM_TIME,
        build_system_set_system_time_payload,
    )
    elk.register_request(
        ROUTE_SYSTEM_SET_SYSTEM_KEY,
        build_system_set_system_key_payload,
    )
    elk.register_request(
        ROUTE_SYSTEM_FILE_INFO,
        build_system_file_info_payload,
    )
    elk.register_request(
        ROUTE_SYSTEM_GET_DEBUG_FLAGS,
        build_system_get_debug_flags_payload,
    )
    elk.register_request(
        ROUTE_SYSTEM_SET_DEBUG_FLAGS,
        build_system_set_debug_flags_payload,
    )
    elk.register_request(
        ROUTE_SYSTEM_GET_DEBUG_STRING,
        build_system_get_debug_string_payload,
    )
    elk.register_request(
        ROUTE_SYSTEM_R_U_ALIVE,
        build_system_r_u_alive_payload,
    )
    elk.register_request(
        ROUTE_SYSTEM_RESET_SMOKES,
        build_system_reset_smokes_payload,
    )
    elk.register_request(
        ROUTE_SYSTEM_SET_RUN,
        build_system_set_run_payload,
    )
    elk.register_request(
        ROUTE_SYSTEM_START_UPDT,
        build_system_start_updt_payload,
    )
    elk.register_request(
        ROUTE_SYSTEM_RECONFIG,
        build_system_reconfig_payload,
    )
    elk.register_request(
        ROUTE_SYSTEM_GET_UPDATE,
        build_system_get_update_payload,
    )


def build_system_get_trouble_payload(**_kwargs: Any) -> Mapping[str, Any]:
    return {}


def build_system_get_table_info_payload(**_kwargs: Any) -> Mapping[str, Any]:
    return {}


def build_system_get_attribs_payload(**_kwargs: Any) -> Mapping[str, Any]:
    return {}


def build_system_set_attribs_payload(**kwargs: Any) -> Mapping[str, Any]:
    if not kwargs:
        raise ValueError("build_system_set_attribs_payload requires at least one attribute.")
    return dict(kwargs)


def build_system_get_cutoffs_payload(**_kwargs: Any) -> Mapping[str, Any]:
    return {}


def build_system_set_cutoffs_payload(**kwargs: Any) -> Mapping[str, Any]:
    if not kwargs:
        raise ValueError("build_system_set_cutoffs_payload requires at least one cutoff value.")
    return dict(kwargs)


def build_system_get_sounders_payload(
    *, sounder_id: int | None = None, **_kwargs: Any
) -> Mapping[str, Any]:
    payload: dict[str, object] = {}
    if sounder_id is not None:
        if sounder_id < 0:
            raise ValueError(
                f"build_system_get_sounders_payload: sounder_id must be int >= 0 (got {sounder_id!r})"
            )
        payload["sounder_id"] = sounder_id
    return payload


def build_system_get_system_time_payload(**_kwargs: Any) -> Mapping[str, Any]:
    return {}


def build_system_set_system_time_payload(
    *,
    tz_offset: int,
    city_index: int,
    gmt_seconds: int,
    dst_active: bool,
    **_kwargs: Any,
) -> Mapping[str, Any]:
    if city_index < 0:
        raise ValueError("build_system_set_system_time_payload: city_index must be int >= 0")
    if gmt_seconds < 0:
        raise ValueError("build_system_set_system_time_payload: gmt_seconds must be int >= 0")
    return {
        "tz_offset": tz_offset,
        "city_index": city_index,
        "gmt_seconds": gmt_seconds,
        "dst_active": dst_active,
    }


def build_system_set_system_key_payload(*, key: int, **_kwargs: Any) -> Mapping[str, Any]:
    if key < 0:
        raise ValueError("build_system_set_system_key_payload: key must be int >= 0")
    return {"key": key}


def build_system_file_info_payload(
    *,
    file_list: bool | None = None,
    file_num: int | None = None,
    **_kwargs: Any,
) -> Mapping[str, Any]:
    payload: dict[str, object] = {}
    if file_list is not None:
        payload["file_list"] = file_list
    if file_num is not None:
        if file_num < 0:
            raise ValueError("build_system_file_info_payload: file_num must be int >= 0")
        payload["file_num"] = file_num
    if not payload:
        raise ValueError("build_system_file_info_payload requires file_list or file_num")
    return payload


def build_system_get_debug_flags_payload(**_kwargs: Any) -> Mapping[str, Any]:
    return {}


def build_system_set_debug_flags_payload(
    *,
    dbug: list[int] | None = None,
    dbug_id: int | None = None,
    dbug_not_id: int | None = None,
    **_kwargs: Any,
) -> Mapping[str, Any]:
    payload: dict[str, object] = {}
    if dbug is not None:
        payload["dbug"] = dbug
    if dbug_id is not None:
        if dbug_id < 0:
            raise ValueError("build_system_set_debug_flags_payload: dbug_id must be int >= 0")
        payload["dbug_id"] = dbug_id
    if dbug_not_id is not None:
        if dbug_not_id < 0:
            raise ValueError("build_system_set_debug_flags_payload: dbug_not_id must be int >= 0")
        payload["dbug_not_id"] = dbug_not_id
    if not payload:
        raise ValueError("build_system_set_debug_flags_payload requires at least one field.")
    return payload


def build_system_get_debug_string_payload(*, dbug_id: int, **_kwargs: Any) -> Mapping[str, Any]:
    if dbug_id < 0:
        raise ValueError("build_system_get_debug_string_payload: dbug_id must be int >= 0")
    return {"dbug_id": dbug_id}


def build_system_r_u_alive_payload(**_kwargs: Any) -> Mapping[str, Any]:
    return {}


def build_system_reset_smokes_payload(**_kwargs: Any) -> Mapping[str, Any]:
    return {"reset_smokes": True}


def build_system_set_run_payload(*, app: str, **_kwargs: Any) -> Mapping[str, Any]:
    if not app.strip():
        raise ValueError("build_system_set_run_payload: app must be a non-empty string")
    return {"app": app}


def build_system_start_updt_payload(
    *, device_id: str, ft: int, **_kwargs: Any
) -> Mapping[str, Any]:
    if not device_id.strip():
        raise ValueError("build_system_start_updt_payload: device_id must be a non-empty string")
    if ft < 0:
        raise ValueError("build_system_start_updt_payload: ft must be int >= 0")
    return {"device_id": device_id, "ft": ft}


def build_system_reconfig_payload(**_kwargs: Any) -> Mapping[str, Any]:
    return {}


def build_system_get_update_payload(**_kwargs: Any) -> Mapping[str, Any]:
    return {}
