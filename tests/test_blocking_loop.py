# ruff: noqa: S101, TC001, TC002, TC003, ARG001
"""Regression tests for Home Assistant event-loop blocking detection."""

from __future__ import annotations

import builtins
from collections.abc import AsyncGenerator, Callable
import importlib
from importlib.metadata import version as package_version
import os
from pathlib import Path
import sys
import threading
from typing import Any
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.elke27.diagnostics import async_get_config_entry_diagnostics
from custom_components.elke27.panel_mac import async_discover_panel_mac
from homeassistant.core import HomeAssistant
from homeassistant.util.loop import protect_loop

BLOCKING_LOG = "Detected blocking call"
EXPECTED_ELKE27_VERSION = package_version("elke27")


def _import_allowed(mapped_args: dict[str, Any]) -> bool:
    """Mirror homeassistant.block_async_io: allow imports already in sys.modules."""
    args = mapped_args.get("args")
    return bool(args and args[0] in sys.modules)


_PYTEST_SKIPPED_GUARDS: tuple[
    tuple[Callable[..., object], object, str, Callable[[dict[str, Any]], bool] | None],
    ...,
] = (
    (builtins.open, builtins, "open", None),
    (importlib.import_module, importlib, "import_module", _import_allowed),
    (os.listdir, os, "listdir", None),
    (os.scandir, os, "scandir", None),
    (Path.open, Path, "open", None),
    (Path.read_text, Path, "read_text", None),
    (Path.read_bytes, Path, "read_bytes", None),
    (Path.write_text, Path, "write_text", None),
    (Path.write_bytes, Path, "write_bytes", None),
)


@pytest.fixture
async def production_loop_guards(hass: HomeAssistant) -> AsyncGenerator[None]:
    """
    Apply block_async_io guards that pytest skips for import_module and file I/O.

    Home Assistant sets ``_IN_TESTS`` during pytest, so the real loop detector does
    not wrap these calls unless we install them here.
    """
    _ = hass
    loop_thread_id = threading.get_ident()
    restored: list[tuple[object, str, object]] = []
    for original, obj, name, check_allowed in _PYTEST_SKIPPED_GUARDS:
        current = getattr(obj, name)
        if getattr(current, "__wrapped__", None) is original:
            continue
        protected = protect_loop(
            original,
            loop_thread_id=loop_thread_id,
            strict=False,
            strict_core=False,
            check_allowed=check_allowed,
        )
        setattr(obj, name, protected)
        restored.append((obj, name, current))
    yield
    for obj, name, previous in restored:
        setattr(obj, name, previous)


async def test_diagnostics_elke27_version_no_blocking_io(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    production_loop_guards: None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Diagnostics must resolve elke27_version off the event loop."""
    mock_config_entry.add_to_hass(hass)
    caplog.clear()

    result = await async_get_config_entry_diagnostics(hass, mock_config_entry)

    assert result["elke27_version"] == EXPECTED_ELKE27_VERSION
    assert not any(BLOCKING_LOG in record.message for record in caplog.records)


@pytest.mark.real_discover_host
async def test_discover_panel_mac_no_blocking_io(
    hass: HomeAssistant,
    socket_enabled: None,
    production_loop_guards: None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Unicast discovery must not import or block on the Home Assistant loop."""
    caplog.clear()
    with patch("custom_components.elke27.panel_mac.DISCOVER_HOST_TIMEOUT", 0.25):
        await async_discover_panel_mac("192.0.2.1")

    assert not any(BLOCKING_LOG in record.message for record in caplog.records)
