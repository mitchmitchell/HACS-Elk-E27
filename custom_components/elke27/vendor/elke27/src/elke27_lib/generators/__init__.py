"""Outbound request generators and registry."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:

    class CommandSpec(Protocol):
        pass

    COMMANDS: dict[str, CommandSpec]


def __getattr__(name: str) -> Any:
    if name in {"COMMANDS", "CommandSpec"}:
        module = importlib.import_module(".registry", __name__)
        return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["CommandSpec", "COMMANDS"]
