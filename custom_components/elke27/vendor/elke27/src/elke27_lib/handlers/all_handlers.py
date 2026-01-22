"""Generated handler stubs and re-exports for all permission keys."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, cast

from elke27_lib.dispatcher import DispatchContext
from elke27_lib.permissions import ALL_PERMISSION_KEYS

from . import (
    area,
    bus_ios,
    control,
    keypad,
    log,
    network_param,
    output,
    rule,
    system,
    tstat,
    user,
    zone,
)

HandlerFn = Callable[[Mapping[str, Any], DispatchContext], bool]


def _collect_handlers(module: object) -> dict[str, HandlerFn]:
    found: dict[str, HandlerFn] = {}
    for name in dir(module):
        if not name.startswith("handler_"):
            continue
        fn = getattr(module, name)
        if callable(fn):
            key = name[len("handler_") :]
            found[key] = cast(HandlerFn, fn)
    return found


def _stub_handler(key: str) -> HandlerFn:
    def _stub(_msg: Mapping[str, Any], _ctx: DispatchContext) -> bool:
        raise NotImplementedError(f"{key} not implemented in library yet")

    _stub.__name__ = f"handler_{key}"
    return _stub


_EXISTING: dict[str, HandlerFn] = {}
for _module in (
    area,
    bus_ios,
    control,
    keypad,
    log,
    network_param,
    output,
    rule,
    system,
    tstat,
    user,
    zone,
):
    _EXISTING.update(_collect_handlers(_module))

HANDLERS: dict[str, HandlerFn] = {}
for _key in sorted(ALL_PERMISSION_KEYS):
    fn = _EXISTING.get(_key) or _stub_handler(_key)
    HANDLERS[_key] = fn
    globals()[f"handler_{_key}"] = fn


__all__ = ["HANDLERS"]
