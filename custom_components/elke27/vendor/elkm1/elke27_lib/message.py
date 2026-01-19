"""Message builders used in tests and offline tooling."""

from __future__ import annotations

from typing import Any, Mapping

from elke27_lib.features.area import build_area_get_status_payload
from elke27_lib.features.control import build_control_authenticate_payload


def _wrap_request(*, seq: int, domain: str, name: str, payload: Mapping[str, Any], session_id: int | None = None) -> dict:
    msg: dict[str, Any] = {"seq": seq}
    if session_id is not None:
        msg["session_id"] = session_id
    msg[domain] = {name: dict(payload)}
    return msg


def build_area_get_status(*, seq: int, session_id: int, area_id: int) -> dict:
    payload = build_area_get_status_payload(area_id=area_id)
    return _wrap_request(seq=seq, session_id=session_id, domain="area", name="get_status", payload=payload)


def build_authenticate(*, seq: int, pin: int | str) -> dict:
    payload = build_control_authenticate_payload(pin=pin)
    return _wrap_request(seq=seq, session_id=None, domain="control", name="authenticate", payload=payload)


__all__ = ["build_area_get_status", "build_authenticate"]
