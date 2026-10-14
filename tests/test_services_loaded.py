# ruff: noqa: S101
"""Service tests that load the config entry and call the public service."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from custom_components.elke27.const import DOMAIN
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er

if TYPE_CHECKING:
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from homeassistant.core import HomeAssistant
    from tests.conftest import ClientHarness


@pytest.mark.parametrize("unique_id", ["noseparator", "base:abc"])
async def test_bypass_rejects_unparseable_unique_id(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: ClientHarness,
    unique_id: str,
) -> None:
    """The zone_bypass service rejects a zone whose unique ID has no numeric id."""
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    entity = er.async_get(hass).async_get_or_create(
        "binary_sensor",
        DOMAIN,
        unique_id,
        config_entry=mock_config_entry,
        suggested_object_id="bad_zone",
    )
    calls_before = len(mock_client.client.mock_calls)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "zone_bypass",
            {"entity_id": [entity.entity_id], "code": "1234", "bypass": True},
            blocking=True,
        )
    assert mock_client.client.mock_calls[calls_before:] == []
