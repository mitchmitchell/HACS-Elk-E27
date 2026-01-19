from __future__ import annotations

import re
from typing import Any

from elke27_lib.const import E27ErrorCode

_ERROR_CODE_RE = re.compile(r"error_code=(\d+)")


def extract_error_code(err: Any) -> int | None:
    if err is None:
        return None
    code = getattr(err, "error_code", None)
    if isinstance(code, int):
        return code
    match = _ERROR_CODE_RE.search(str(err))
    if match:
        return int(match.group(1))
    return None


def error_code_name(code: int | None) -> str:
    if code is None:
        return "UNKNOWN"
    try:
        return E27ErrorCode(code).name
    except ValueError:
        return f"UNKNOWN_{code}"


def describe_error(err: Any) -> str:
    code = extract_error_code(err)
    if code is None:
        return str(err)
    return f"{err} ({error_code_name(code)})"
