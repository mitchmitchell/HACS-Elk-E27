"""Log domain request generators."""

from __future__ import annotations

ResponseKey = tuple[str, str]


def generator_log_get_index() -> tuple[dict[str, object], ResponseKey]:
    return {}, ("log", "get_index")


def generator_log_get_table_info() -> tuple[dict[str, object], ResponseKey]:
    return {}, ("log", "get_table_info")


def generator_log_get_trouble() -> tuple[dict[str, object], ResponseKey]:
    return {}, ("log", "get_trouble")


def generator_log_get_attribs() -> tuple[dict[str, object], ResponseKey]:
    return {}, ("log", "get_attribs")


def generator_log_set_attribs(
    *,
    log_flags: dict[str, object] | None = None,
    sdlog_flags: dict[str, object] | None = None,
) -> tuple[dict[str, object], ResponseKey]:
    _ = (log_flags, sdlog_flags)
    raise ValueError("log_set_attribs is disabled to protect panel logs")


def generator_log_get_list(
    *, start: int, date: int, cnt: int
) -> tuple[dict[str, object], ResponseKey]:
    return {"start": start, "date": date, "cnt": cnt}, ("log", "get_list")


def generator_log_get_log(*, log_id: int) -> tuple[dict[str, object], ResponseKey]:
    return {"log_id": log_id}, ("log", "get_log")


def generator_log_clear(*, block_id: int) -> tuple[dict[str, object], ResponseKey]:
    _ = block_id
    raise ValueError("log_clear is disabled to protect panel logs")


def generator_log_realloc(*, table_elements: int) -> tuple[dict[str, object], ResponseKey]:
    _ = table_elements
    raise ValueError("log_realloc is disabled to protect panel logs")
