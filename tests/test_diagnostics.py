# ruff: noqa: S101, TC001, TC002, ARG001
"""Tests for Elke27 diagnostics."""

from __future__ import annotations

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.elke27.const import (
    CONF_LINK_KEYS_JSON,
    CONF_PANEL_MAC,
    CONF_PANEL_MAC_FILLED,
)
from custom_components.elke27.diagnostics import async_get_config_entry_diagnostics
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


async def test_diagnostics_expose_panel_mac_identity(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: ClientHarness
) -> None:
    """Diagnostics show panel_mac, panel_mac_filled and unique_id, not secrets."""
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
    assert result["panel_mac"] == "2CCF67CA3B20"
    assert result["panel_mac_filled"] is True
    assert result["unique_id"] == "2c:cf:67:ca:3b:20"
    assert CONF_LINK_KEYS_JSON not in result
    assert "linkkey" not in repr(result).lower()
