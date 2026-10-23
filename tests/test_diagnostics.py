# ruff: noqa: S101, TC001, TC002, ARG001, PLR2004
"""Tests for Elke27 diagnostics."""

from __future__ import annotations

import dataclasses
from importlib.metadata import version as package_version

from elke27_lib import ArmMode, CommandResult
from elke27_lib.client import Result
from freezegun.api import FrozenDateTimeFactory
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from custom_components.elke27.const import (
    CONF_INTEGRATION_ID,
    CONF_LINK_KEYS_JSON,
    CONF_PANEL_MAC,
    CONF_PANEL_MAC_FILLED,
)
from custom_components.elke27.coordinator import (
    STALE_CHECK_INTERVAL,
    STALE_REFRESH_AFTER,
)
from custom_components.elke27.diagnostics import async_get_config_entry_diagnostics
from homeassistant.components.diagnostics import REDACTED
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from tests.conftest import ClientHarness


async def test_diagnostics_without_secrets(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: ClientHarness
) -> None:
    """Diagnostics describe the entry and snapshot, without link keys."""
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    result = await async_get_config_entry_diagnostics(hass, mock_config_entry)
    assert result["entry_id"] == mock_config_entry.entry_id
    assert result["link_keys_present"] is True
    assert "1" in result["snapshot"]["areas"]
    assert CONF_LINK_KEYS_JSON not in result
    assert "linkkey" not in repr(result).lower()


async def test_diagnostics_redact_identity(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: ClientHarness
) -> None:
    """MAC, serial-or-MAC unique_id, integration ID and host are redacted."""
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    hass.config_entries.async_update_entry(
        mock_config_entry,
        data={
            **mock_config_entry.data,
            CONF_PANEL_MAC: "2CCF67CA3B20",
            CONF_PANEL_MAC_FILLED: True,
        },
        unique_id="2c:cf:67:ca:3b:20",
    )

    result = await async_get_config_entry_diagnostics(hass, mock_config_entry)
    assert result[CONF_PANEL_MAC] == REDACTED
    assert result["unique_id"] == REDACTED
    assert result[CONF_INTEGRATION_ID] == REDACTED
    assert result[CONF_HOST] == REDACTED
    assert result["panel_mac_filled"] is True
    text = repr(result)
    for secret in (
        "2CCF67CA3B20",
        "2c:cf:67:ca:3b:20",
        mock_config_entry.data[CONF_INTEGRATION_ID],
        mock_config_entry.data[CONF_HOST],
    ):
        assert secret not in text
    assert "integration_serial" not in result
    assert "snapshot_available" not in result
    assert CONF_LINK_KEYS_JSON not in result


async def test_diagnostics_d5_fields_read_properties_only(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: ClientHarness,
    freezer: FrozenDateTimeFactory,
) -> None:
    """D5 fields come from coordinator/supervisor properties, no client calls."""
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    coordinator = mock_config_entry.runtime_data.coordinator
    # Two commands whose library refresh failed: two post-command fallback reads.
    mock_client.client.async_arm_area.return_value = CommandResult(
        route="test", status_refresh_ok=False
    )
    mock_client.client.async_disarm_area.return_value = CommandResult(
        route="test", status_refresh_ok=False
    )
    mock_client.client.async_execute.return_value = Result.success(
        {"arm_state": "DISARMED"}
    )
    assert await coordinator.async_arm_area(1, ArmMode.ARMED_AWAY, "1234")
    assert await coordinator.async_disarm_area(1, "1234")
    # A snapshot that stays stale: the stale timer runs one automatic read.
    mock_client.snapshot = dataclasses.replace(mock_client.snapshot, stale=True)
    steps = int(STALE_REFRESH_AFTER / STALE_CHECK_INTERVAL.total_seconds()) + 1
    for _ in range(steps):
        freezer.tick(STALE_CHECK_INTERVAL)
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
    mock_client.client.reset_mock()

    result = await async_get_config_entry_diagnostics(hass, mock_config_entry)

    called = [c for c in mock_client.client.mock_calls if not c[0].startswith("__")]
    assert called == []
    assert result["client_present"] is True
    assert result["client_ready"] is True
    assert result["last_update_success"] is True
    assert result["reconnect_attempts"] == 0
    assert result["repairs_issue_active"] is False
    assert result["reauth_requested"] is False
    assert result["snapshot_stale"] is True
    assert result["stale_area_ids"] == []
    assert result["stale_zone_ids"] == []
    assert result["fallback_refresh_count"] == 2
    assert result["last_fallback_at"] is not None
    assert result["stale_auto_refresh_count"] == 1
    assert result["integration_version"] == "0.2.1"
    assert result["elke27_version"] == package_version("elke27")
    assert result["area_device_count"] == len(mock_client.snapshot.areas)


async def test_diagnostics_link_down_with_client_present(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: ClientHarness
) -> None:
    """A client that is not ready reports client_ready False, client_present True."""
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    mock_client.client.is_ready = False

    result = await async_get_config_entry_diagnostics(hass, mock_config_entry)

    assert result["client_present"] is True
    assert result["client_ready"] is False


async def test_diagnostics_entry_not_loaded(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    mock_config_entry: MockConfigEntry,
    mock_client: ClientHarness,
) -> None:
    """A setup_error entry downloads redacted entry data, no runtime fields."""
    data = dict(mock_config_entry.data)
    data.pop(CONF_INTEGRATION_ID)
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(mock_config_entry, data=data)
    assert await async_setup_component(hass, "diagnostics", {})
    assert not await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_config_entry.state is ConfigEntryState.SETUP_ERROR

    result = await get_diagnostics_for_config_entry(
        hass, hass_client, mock_config_entry
    )
    assert result["entry_id"] == mock_config_entry.entry_id
    assert result["entry_state"] == ConfigEntryState.SETUP_ERROR.value
    assert result[CONF_HOST] == REDACTED
    assert result["unique_id"] == REDACTED
    assert result["link_keys_present"] is True
    assert "linkkey" not in repr(result).lower()
    for runtime_key in ("client_ready", "snapshot", "reconnect_attempts"):
        assert runtime_key not in result
