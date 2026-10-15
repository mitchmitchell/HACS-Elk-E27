# ruff: noqa: S101, TC001, TC002, FBT001
"""Tests for the Elke27 garage door cover platform (loaded entry, public services)."""

from __future__ import annotations

import dataclasses
import re
from types import MappingProxyType
from typing import Any
from unittest.mock import patch

from elke27_lib import PanelSnapshot
from elke27_lib.errors import (
    Elke27AuthError,
    Elke27CryptoError,
    Elke27LinkRequiredError,
    Elke27PermissionError,
)
from elke27_lib.types import BarrierState, barrier_is_open
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    snapshot_platform,
)
from pytest_homeassistant_custom_component.syrupy import HomeAssistantSnapshotExtension
from syrupy.assertion import SnapshotAssertion

from custom_components.elke27 import cover as cover_mod
from custom_components.elke27.const import DOMAIN
from custom_components.elke27.coordinator import BARRIER_PERMISSION_MESSAGE
from homeassistant.components.cover import DOMAIN as COVER_DOMAIN
from homeassistant.config_entries import SOURCE_REAUTH
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_CLOSE_COVER,
    SERVICE_OPEN_COVER,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from tests.conftest import ClientHarness, panel_snapshot

DOOR = "cover.test_panel_garage"


def _barrier(
    barrier_id: int, status: str | None, name: str | None = "Garage"
) -> BarrierState:
    return BarrierState(
        barrier_id=barrier_id, name=name, status=status, is_open=barrier_is_open(status)
    )


def _with_barriers(snapshot: PanelSnapshot, *barriers: BarrierState) -> PanelSnapshot:
    return dataclasses.replace(
        snapshot, barriers=MappingProxyType({b.barrier_id: b for b in barriers})
    )


async def _load(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    mock_client: ClientHarness,
    *barriers: BarrierState,
) -> None:
    """Load the entry; the barriers appear only through the barrier domain refresh."""
    mock_client.snapshot = panel_snapshot()

    async def _refresh(domain: str) -> None:
        if domain == "barrier":
            mock_client.snapshot = _with_barriers(mock_client.snapshot, *barriers)

    mock_client.client.async_refresh_domain_config.side_effect = _refresh
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def _call(hass: HomeAssistant, service: str, entity_id: str = DOOR) -> None:
    await hass.services.async_call(
        COVER_DOMAIN, service, {ATTR_ENTITY_ID: entity_id}, blocking=True
    )


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
    assert cover_mod.barrier_is_closed(_barrier(1, status)) is closed


async def test_covers_after_first_refresh(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: ClientHarness,
    entity_registry: er.EntityRegistry,
    snapshot: SnapshotAssertion,
) -> None:
    """Setup primes the barrier domain; states, names and unique IDs are pinned."""
    with patch("custom_components.elke27.PLATFORMS", [Platform.COVER]):
        await _load(
            hass,
            mock_config_entry,
            mock_client,
            _barrier(1, "CLOSE"),
            _barrier(2, "OPEN", name=None),
            _barrier(3, "STOPPED", name="Shop"),
        )
    await snapshot_platform(
        hass,
        entity_registry,
        snapshot.use_extension(HomeAssistantSnapshotExtension),
        mock_config_entry.entry_id,
    )


async def test_no_barriers_no_covers(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: ClientHarness
) -> None:
    """A panel without barriers gets no cover entities."""
    await _load(hass, mock_config_entry, mock_client)
    assert hass.states.async_entity_ids(COVER_DOMAIN) == []


@pytest.mark.parametrize(
    ("service", "is_open"), [(SERVICE_OPEN_COVER, True), (SERVICE_CLOSE_COVER, False)]
)
async def test_command_sent_once_then_status_read(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: ClientHarness,
    service: str,
    is_open: bool,
) -> None:
    """Open/close call the library exactly once, then read status once."""
    await _load(hass, mock_config_entry, mock_client, _barrier(1, "CLOSE"))
    await _call(hass, service)
    mock_client.client.async_set_barrier.assert_awaited_once_with(1, open=is_open)
    mock_client.client.async_refresh_barrier.assert_awaited_once_with(1)


async def test_outcome_unknown_not_retried(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: ClientHarness
) -> None:
    """Unknown outcome raises the check-the-door error, no resend, one resync read."""
    errors: Any = pytest.importorskip("elke27_lib.errors")
    await _load(hass, mock_config_entry, mock_client, _barrier(1, "OPEN"))
    mock_client.client.async_set_barrier.side_effect = (
        errors.Elke27CommandOutcomeUnknownError(("barrier", "set_status"))
    )
    with pytest.raises(HomeAssistantError, match="check the door"):
        await _call(hass, SERVICE_CLOSE_COVER)
    mock_client.client.async_set_barrier.assert_awaited_once()
    mock_client.client.async_refresh_barrier.assert_awaited_once_with(1)


async def test_permission_refusal_message(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: ClientHarness
) -> None:
    """A permission refusal names automation authority and is not retried."""
    await _load(hass, mock_config_entry, mock_client, _barrier(1, "OPEN"))
    mock_client.client.async_set_barrier.side_effect = Elke27PermissionError()
    with pytest.raises(HomeAssistantError, match=re.escape(BARRIER_PERMISSION_MESSAGE)):
        await _call(hass, SERVICE_CLOSE_COVER)
    mock_client.client.async_set_barrier.assert_awaited_once()


@pytest.mark.parametrize(
    "error", [Elke27LinkRequiredError, Elke27AuthError, Elke27CryptoError]
)
async def test_auth_error_starts_reauth(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: ClientHarness,
    error: type[Exception],
) -> None:
    """A link/auth/crypto failure on a garage command starts reauth, not a resend."""
    await _load(hass, mock_config_entry, mock_client, _barrier(1, "OPEN"))
    mock_client.client.async_set_barrier.side_effect = error()
    with pytest.raises(HomeAssistantError):
        await _call(hass, SERVICE_CLOSE_COVER)
    await hass.async_block_till_done()
    mock_client.client.async_set_barrier.assert_awaited_once()
    mock_client.client.async_refresh_barrier.assert_not_awaited()
    flows = [
        flow
        for flow in hass.config_entries.flow.async_progress()
        if flow["handler"] == DOMAIN and flow["context"]["source"] == SOURCE_REAUTH
    ]
    assert len(flows) == 1


async def test_stopped_is_unknown(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: ClientHarness
) -> None:
    """STOPPED shows as unknown."""
    await _load(hass, mock_config_entry, mock_client, _barrier(1, "STOPPED"))
    state = hass.states.get(DOOR)
    assert state is not None
    assert state.state == STATE_UNKNOWN


async def test_unavailable_when_barrier_missing(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: ClientHarness
) -> None:
    """The entity goes unavailable when its barrier leaves the snapshot."""
    await _load(hass, mock_config_entry, mock_client, _barrier(1, "OPEN"))
    assert hass.states.get(DOOR).state == "open"
    coordinator = mock_config_entry.runtime_data.coordinator
    coordinator.async_set_updated_data(_with_barriers(mock_client.snapshot))
    await hass.async_block_till_done()
    assert hass.states.get(DOOR).state == STATE_UNAVAILABLE
