"""Generated generator stubs and re-exports for all permission keys."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

from elke27_lib.permissions import ALL_PERMISSION_KEYS, canonical_generator_key

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

GeneratorFn = Callable[..., tuple[dict[str, object], tuple[str, str]]]


def _collect_generators(module: object) -> dict[str, GeneratorFn]:
    found: dict[str, GeneratorFn] = {}
    for name in dir(module):
        if not name.startswith("generator_"):
            continue
        fn = getattr(module, name)
        if callable(fn):
            key = canonical_generator_key(fn.__name__)
            found[key] = cast(GeneratorFn, fn)
    return found


def _stub_generator(key: str) -> GeneratorFn:
    def _stub(*_args: Any, **_kwargs: Any) -> tuple[dict[str, object], tuple[str, str]]:
        raise NotImplementedError(f"{key} not implemented in library yet")

    _stub.__name__ = f"generator_{key}"
    return _stub


_EXISTING: dict[str, GeneratorFn] = {}
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
    _EXISTING.update(_collect_generators(_module))


GENERATORS: dict[str, GeneratorFn] = {}
for _key in sorted(ALL_PERMISSION_KEYS):
    fn = _EXISTING.get(_key) or _stub_generator(_key)
    GENERATORS[_key] = fn
    globals()[f"generator_{_key}"] = fn


__all__ = ["GENERATORS"]
