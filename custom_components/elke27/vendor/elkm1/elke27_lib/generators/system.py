"""System domain request generators."""

from __future__ import annotations

ResponseKey = tuple[str, str]


def generator_system_get_trouble() -> tuple[dict, ResponseKey]:
    return {}, ("system", "get_trouble")


def generator_system_get_troubles() -> tuple[dict, ResponseKey]:
    return {}, ("system", "get_troubles")


def generator_system_get_table_info() -> tuple[dict, ResponseKey]:
    return {}, ("system", "get_table_info")


def generator_system_get_attribs() -> tuple[dict, ResponseKey]:
    return {}, ("system", "get_attribs")


def generator_system_set_attribs(**kwargs: object) -> tuple[dict, ResponseKey]:
    if not kwargs:
        raise ValueError("set_attribs requires at least one attribute.")
    return dict(kwargs), ("system", "set_attribs")


def generator_system_get_cutoffs() -> tuple[dict, ResponseKey]:
    return {}, ("system", "get_cutoffs")


def generator_system_set_cutoffs(**kwargs: object) -> tuple[dict, ResponseKey]:
    if not kwargs:
        raise ValueError("set_cutoffs requires at least one cutoff value.")
    return dict(kwargs), ("system", "set_cutoffs")


def generator_system_get_sounders(*, sounder_id: int | None = None) -> tuple[dict, ResponseKey]:
    payload: dict[str, object] = {}
    if sounder_id is not None:
        if not isinstance(sounder_id, int) or sounder_id < 0:
            raise ValueError(f"sounder_id must be a non-negative int (got {sounder_id!r})")
        payload["sounder_id"] = sounder_id
    return payload, ("system", "get_sounders")


def generator_system_get_system_time() -> tuple[dict, ResponseKey]:
    return {}, ("system", "get_system_time")


def generator_system_set_system_time(
    *,
    tz_offset: int,
    city_index: int,
    gmt_seconds: int,
    dst_active: bool,
) -> tuple[dict, ResponseKey]:
    if not isinstance(tz_offset, int):
        raise ValueError("tz_offset must be int")
    if not isinstance(city_index, int) or city_index < 0:
        raise ValueError("city_index must be a non-negative int")
    if not isinstance(gmt_seconds, int) or gmt_seconds < 0:
        raise ValueError("gmt_seconds must be a non-negative int")
    if not isinstance(dst_active, bool):
        raise ValueError("dst_active must be bool")
    return {
        "tz_offset": tz_offset,
        "city_index": city_index,
        "gmt_seconds": gmt_seconds,
        "dst_active": dst_active,
    }, ("system", "set_system_time")


def generator_system_set_system_key(*, key: int) -> tuple[dict, ResponseKey]:
    if not isinstance(key, int) or key < 0:
        raise ValueError("key must be a non-negative int")
    return {"key": key}, ("system", "set_system_key")


def generator_system_file_info(
    *,
    file_list: bool | None = None,
    file_num: int | None = None,
) -> tuple[dict, ResponseKey]:
    payload: dict[str, object] = {}
    if file_list is not None:
        if not isinstance(file_list, bool):
            raise ValueError("file_list must be bool")
        payload["file_list"] = file_list
    if file_num is not None:
        if not isinstance(file_num, int) or file_num < 0:
            raise ValueError("file_num must be a non-negative int")
        payload["file_num"] = file_num
    if not payload:
        raise ValueError("file_info requires file_list or file_num")
    return payload, ("system", "file_info")


def generator_system_get_debug_flags() -> tuple[dict, ResponseKey]:
    return {}, ("system", "get_debug_flags")


def generator_system_set_debug_flags(
    *,
    dbug: list[int] | None = None,
    dbug_id: int | None = None,
    dbug_not_id: int | None = None,
) -> tuple[dict, ResponseKey]:
    payload: dict[str, object] = {}
    if dbug is not None:
        if not isinstance(dbug, list) or not all(isinstance(v, int) for v in dbug):
            raise ValueError("dbug must be a list of ints")
        payload["dbug"] = dbug
    if dbug_id is not None:
        if not isinstance(dbug_id, int) or dbug_id < 0:
            raise ValueError("dbug_id must be a non-negative int")
        payload["dbug_id"] = dbug_id
    if dbug_not_id is not None:
        if not isinstance(dbug_not_id, int) or dbug_not_id < 0:
            raise ValueError("dbug_not_id must be a non-negative int")
        payload["dbug_not_id"] = dbug_not_id
    if not payload:
        raise ValueError("set_debug_flags requires at least one field")
    return payload, ("system", "set_debug_flags")


def generator_system_get_debug_string(*, dbug_id: int) -> tuple[dict, ResponseKey]:
    if not isinstance(dbug_id, int) or dbug_id < 0:
        raise ValueError("dbug_id must be a non-negative int")
    return {"dbug_id": dbug_id}, ("system", "get_debug_string")


def generator_system_r_u_alive() -> tuple[dict, ResponseKey]:
    return True, ("system", "r_u_alive")


def generator_system_reset_smokes() -> tuple[dict, ResponseKey]:
    return {"reset_smokes": True}, ("system", "reset_smokes")


def generator_system_set_run(*, app: str) -> tuple[dict, ResponseKey]:
    if not isinstance(app, str) or not app.strip():
        raise ValueError("app must be a non-empty string")
    return {"app": app}, ("system", "set_run")


def generator_system_start_updt(*, device_id: str, ft: int) -> tuple[dict, ResponseKey]:
    if not isinstance(device_id, str) or not device_id.strip():
        raise ValueError("device_id must be a non-empty string")
    if not isinstance(ft, int) or ft < 0:
        raise ValueError("ft must be a non-negative int")
    return {"device_id": device_id, "ft": ft}, ("system", "start_updt")


def generator_system_reconfig() -> tuple[dict, ResponseKey]:
    return {}, ("system", "reconfig")


def generator_system_get_update() -> tuple[dict, ResponseKey]:
    return {}, ("system", "get_update")
