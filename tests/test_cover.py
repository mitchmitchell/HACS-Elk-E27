# ruff: noqa: S101, FBT001
"""Tests for the Elke27 garage door cover platform."""

from __future__ import annotations

import dataclasses
import importlib.util
from pathlib import Path
import sys
from types import MappingProxyType
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

_HAS_DEPS = all(
    importlib.util.find_spec(name) is not None
    for name in ("homeassistant", "elke27_lib")
)
pytestmark = pytest.mark.skipif(not _HAS_DEPS, reason="deps missing")

if _HAS_DEPS:
    sys.path.insert(0, str(Path(__file__).parents[1]))

    from elke27_lib import AreaState, PanelInfo, PanelSnapshot
    from elke27_lib.types import BarrierState

    from custom_components.elke27 import cover as cover_mod
    from custom_components.elke27.coordinator import (
        BARRIER_OUTCOME_UNKNOWN_MESSAGE,
        BARRIER_PERMISSION_MESSAGE,
    )
    from homeassistant.components.cover import CoverDeviceClass, CoverEntityFeature
    from homeassistant.exceptions import HomeAssistantError
    from tests.conftest import make_coordinator


def _snapshot(barriers: dict[int, Any], **kwargs: Any) -> PanelSnapshot:
    values: dict[str, Any] = {
        "panel": PanelInfo(mac="00:11:22:33:44:55"),
        "barriers": MappingProxyType(barriers),
    }
    values.update(
        {
            k: MappingProxyType(v) if isinstance(v, dict) else v
            for k, v in kwargs.items()
        }
    )
    return dataclasses.replace(PanelSnapshot.empty(), **values)


def _barrier(status: str | None, **kw: Any) -> BarrierState:
    from elke27_lib.types import barrier_is_open  # noqa: PLC0415

    return BarrierState(
        barrier_id=kw.pop("barrier_id", 1),
        name=kw.pop("name", "Test Door"),
        status=status,
        is_open=barrier_is_open(status),
        **kw,
    )


def _coordinator(snapshot: PanelSnapshot, client: Any = None) -> Any:
    coord = make_coordinator(MagicMock())
    coord.data = snapshot
    coord._client = client  # noqa: SLF001
    coord.async_set_updated_data = MagicMock()
    return coord


def _entry() -> Any:
    entry = MagicMock()
    entry.entry_id = "test-entry"
    entry.title = "Test panel"
    entry.data = {}
    return entry


def _client(set_side_effect: Any = None) -> Any:
    client = MagicMock()
    client.async_set_barrier = AsyncMock(side_effect=set_side_effect)
    client.async_refresh_barrier = AsyncMock(return_value=None)
    client.get_snapshot = MagicMock(return_value=PanelSnapshot.empty())
    return client


@pytest.mark.parametrize(
    ("status", "closed"),
    [
        ("OPEN", False),
        ("open", False),
        ("CLOSE", True),
        ("CLOSED", True),
        ("close", True),
        ("STOPPED", None),
        ("stopped", None),
        ("ON", None),
        ("weird", None),
        (None, None),
    ],
)
def test_state_mapping(status: str | None, closed: bool | None) -> None:
    """Panel status maps to is_closed; unknown values stay None."""
    assert cover_mod.barrier_is_closed(_barrier(status)) is closed


@pytest.mark.parametrize("count", [0, 1, 4])
async def test_setup_creates_one_cover_per_barrier(count: int) -> None:
    """One cover per barrier; none when the panel has none."""
    barriers = {
        i: _barrier("CLOSE", barrier_id=i, name=f"Door {i}")
        for i in range(1, count + 1)
    }
    coord = _coordinator(_snapshot(barriers))
    entry = _entry()
    entry.runtime_data.coordinator = coord
    added: list[Any] = []
    await cover_mod.async_setup_entry(MagicMock(), entry, added.extend)
    assert len(added) == count
    for ent in added:
        assert ent.device_class == CoverDeviceClass.GARAGE
        assert ent.supported_features == (
            CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE
        )
        assert ":barrier:" not in ent.unique_id
        assert ent.is_opening is False
        assert ent.is_closing is False
    base = cover_mod.unique_base(coord, entry)
    assert {ent.unique_id for ent in added} == {
        f"{base}:{i}" for i in range(1, count + 1)
    }


def test_stopped_is_unknown() -> None:
    """STOPPED shows as unknown; no extra state attributes are exposed."""
    coord = _coordinator(_snapshot({1: _barrier("STOPPED")}))
    ent = cover_mod.Elke27BarrierCover(coord, _entry(), 1, _barrier("STOPPED"))
    assert ent.is_closed is None
    assert ent.extra_state_attributes is None


def test_unnamed_barrier_uses_translated_name() -> None:
    """No panel name: translation_key with the barrier id as placeholder."""
    coord = _coordinator(_snapshot({3: _barrier("OPEN", barrier_id=3, name=None)}))
    ent = cover_mod.Elke27BarrierCover(
        coord, _entry(), 3, _barrier("OPEN", barrier_id=3, name=None)
    )
    assert ent.translation_key == "garage_door"
    assert ent.translation_placeholders == {"barrier_id": "3"}
    assert not hasattr(ent, "_attr_name")


def test_device_is_panel_without_area() -> None:
    """No area_id means the panel device."""
    coord = _coordinator(_snapshot({1: _barrier("OPEN")}))
    info = cover_mod.device_info_for_barrier(coord, _entry(), _barrier("OPEN"))
    assert info.get("via_device") is None


def test_device_is_area_when_barrier_has_area() -> None:
    """A barrier with an area_id that has a device attaches to it."""
    coord = _coordinator(
        _snapshot({1: _barrier("OPEN")}, areas={2: AreaState(area_id=2, name="Garage")})
    )
    barrier = MagicMock(area_id=2, status="OPEN", name="Door")
    info = cover_mod.device_info_for_barrier(coord, _entry(), barrier)
    assert info["name"] == "Garage"
    assert info["model"] == "Area"


@pytest.mark.parametrize(
    ("method", "is_open"), [("async_open_cover", True), ("async_close_cover", False)]
)
async def test_command_sent_once_then_status_read(method: str, is_open: bool) -> None:
    """Open/close call the library exactly once, then read status once."""
    client = _client()
    coord = _coordinator(_snapshot({1: _barrier("CLOSE")}), client)
    ent = cover_mod.Elke27BarrierCover(coord, _entry(), 1, _barrier("CLOSE"))
    await getattr(ent, method)()
    client.async_set_barrier.assert_awaited_once_with(1, open=is_open)
    client.async_refresh_barrier.assert_awaited_once_with(1)


async def test_outcome_unknown_not_retried() -> None:
    """Unknown outcome raises the check-the-door error, no resend, one resync read."""
    errors = pytest.importorskip("elke27_lib.errors")
    cls = getattr(errors, "Elke27CommandOutcomeUnknownError", None)
    if cls is None:
        pytest.skip("elke27 without OutcomeUnknown")
    err = cls(("barrier", "set_status"))
    client = _client(err)
    coord = _coordinator(_snapshot({1: _barrier("OPEN")}), client)
    ent = cover_mod.Elke27BarrierCover(coord, _entry(), 1, _barrier("OPEN"))
    with pytest.raises(HomeAssistantError, match="check the door"):
        await ent.async_close_cover()
    assert BARRIER_OUTCOME_UNKNOWN_MESSAGE.startswith("Garage door")
    client.async_set_barrier.assert_awaited_once()
    client.async_refresh_barrier.assert_awaited_once_with(1)


async def test_permission_refusal_message() -> None:
    """A permission refusal names automation authority and is not retried."""
    errors = pytest.importorskip("elke27_lib.errors")
    err = errors.Elke27PermissionError()
    client = _client(err)
    coord = _coordinator(_snapshot({1: _barrier("OPEN")}), client)
    ent = cover_mod.Elke27BarrierCover(coord, _entry(), 1, _barrier("OPEN"))
    with pytest.raises(HomeAssistantError) as exc:
        await ent.async_close_cover()
    assert str(exc.value) == BARRIER_PERMISSION_MESSAGE
    client.async_set_barrier.assert_awaited_once()


async def test_disconnected_raises_without_sending() -> None:
    """No client: HomeAssistantError, nothing queued."""
    coord = _coordinator(_snapshot({1: _barrier("OPEN")}), None)
    ent = cover_mod.Elke27BarrierCover(coord, _entry(), 1, _barrier("OPEN"))
    with pytest.raises(HomeAssistantError, match="not sent"):
        await ent.async_open_cover()


def test_unavailable_when_barrier_missing() -> None:
    """Entity is unavailable when the barrier leaves the snapshot."""
    coord = _coordinator(_snapshot({}))
    ent = cover_mod.Elke27BarrierCover(coord, _entry(), 1, _barrier("OPEN"))
    assert ent.available is False
