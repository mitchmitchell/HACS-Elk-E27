# ruff: noqa: S101, TC001, TC002, ARG001, PLR2004
"""Tests for Elke27 diagnostics."""

from __future__ import annotations

from importlib.metadata import version as package_version

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.elke27.const import (
    CONF_INTEGRATION_ID,
    CONF_LINK_KEYS_JSON,
    CONF_PANEL_MAC,
    CONF_PANEL_MAC_FILLED,
)
from custom_components.elke27.diagnostics import async_get_config_entry_diagnostics
from homeassistant.components.diagnostics import REDACTED
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
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
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: ClientHarness
) -> None:
    """D5 fields come from coordinator/supervisor properties, no client calls."""
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    coordinator = mock_config_entry.runtime_data.coordinator
    coordinator._fallback_refresh_count = 2  # noqa: SLF001
    coordinator._stale_auto_refresh_count = 3  # noqa: SLF001
    mock_client.client.reset_mock()

    result = await async_get_config_entry_diagnostics(hass, mock_config_entry)

    called = [c for c in mock_client.client.mock_calls if not c[0].startswith("__")]
    assert called == []
    assert result["connected"] is True
    assert result["client_ready"] is True
    assert result["last_update_success"] is True
    assert result["reconnect_attempts"] == 0
    assert result["repairs_issue_active"] is False
    assert result["reauth_requested"] is False
    assert result["snapshot_stale"] is False
    assert result["stale_area_ids"] == []
    assert result["stale_zone_ids"] == []
    assert result["fallback_refresh_count"] == 2
    assert result["last_fallback_at"] is None
    assert result["stale_auto_refresh_count"] == 3
    assert result["integration_version"] == "0.2.0"
    assert result["elke27_version"] == package_version("elke27")
    assert result["area_device_count"] == len(mock_client.snapshot.areas)
