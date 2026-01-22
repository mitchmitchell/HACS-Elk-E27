from __future__ import annotations

from elke27_lib.generators.registry import COMMANDS


def test_registry_handlers_use_canonical_names() -> None:
    for key, spec in COMMANDS.items():
        assert spec.handler.__name__ == f"handler_{key}"
