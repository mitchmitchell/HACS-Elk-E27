"""Elke27 v2 public API surface."""

import importlib
from typing import Any

from .errors import (
    Elke27AuthError,
    Elke27ConnectionError,
    Elke27CryptoError,
    Elke27DisconnectedError,
    Elke27Error,
    Elke27InvalidArgument,
    Elke27LinkRequiredError,
    Elke27PermissionError,
    Elke27PinRequiredError,
    Elke27ProtocolError,
    Elke27TimeoutError,
    Elke27TransientError,
)
from .redact import redact_for_diagnostics
from .types import (
    AreaState,
    ArmMode,
    ClientConfig,
    CsmSnapshot,
    DiscoveredPanel,
    Elke27Event,
    EventType,
    LinkKeys,
    OutputDefinition,
    OutputState,
    PanelInfo,
    PanelSnapshot,
    TableInfo,
    ZoneDefinition,
    ZoneState,
)


def __getattr__(name: str) -> Any:
    if name == "Elke27Client":
        module = importlib.import_module(".client", __name__)
        return module.Elke27Client
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "ClientConfig",
    "DiscoveredPanel",
    "LinkKeys",
    "PanelSnapshot",
    "CsmSnapshot",
    "PanelInfo",
    "TableInfo",
    "AreaState",
    "ZoneState",
    "OutputState",
    "ZoneDefinition",
    "OutputDefinition",
    "ArmMode",
    "Elke27Event",
    "EventType",
    "Elke27Error",
    "Elke27TransientError",
    "Elke27ConnectionError",
    "Elke27TimeoutError",
    "Elke27DisconnectedError",
    "Elke27AuthError",
    "Elke27LinkRequiredError",
    "Elke27PinRequiredError",
    "Elke27PermissionError",
    "Elke27ProtocolError",
    "Elke27CryptoError",
    "Elke27InvalidArgument",
    "redact_for_diagnostics",
]
