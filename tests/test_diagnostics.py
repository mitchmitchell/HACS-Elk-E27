# ruff: noqa: S101, TC001, TC002, ARG001
"""Tests for Elke27 diagnostics."""

from __future__ import annotations

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.elke27.const import CONF_LINK_KEYS_JSON
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
    assert result["snapshot_available"] is True
    assert "1" in result["snapshot"]["areas"]
    assert CONF_LINK_KEYS_JSON not in result
    assert "linkkey" not in repr(result).lower()
