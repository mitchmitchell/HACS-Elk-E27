# ruff: noqa: S101, TC001, PLR2004
"""Tests for E1: the flow-generated integration ID sent as the client sn."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.elke27 import identity
from custom_components.elke27.const import (
    CONF_INTEGRATION_ID,
    CONF_LINK_KEYS_JSON,
    DOMAIN,
)
from custom_components.elke27.identity import (
    build_client_identity,
    generate_integration_id,
    is_valid_integration_id,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from tests.conftest import HOST, LINK_KEYS_JSON, PANEL_MAC, PORT


def test_generated_ids_are_12_digits_without_leading_zero() -> None:
    """Every generated ID is 12 ASCII digits and never starts with zero."""
    for _ in range(2000):
        value = generate_integration_id()
        assert len(value) == 12
        assert value.isdigit()
        assert value[0] != "0"
        assert is_valid_integration_id(value)


@pytest.mark.parametrize(
    ("draw", "expected"),
    [(0, "100000000000"), (9 * 10**11 - 1, "999999999999")],
)
def test_generated_id_range_bounds(draw: int, expected: str) -> None:
    """The lowest and highest draws map to the ends of the 12-digit range."""
    with patch.object(identity.secrets, "randbelow", return_value=draw) as rb:
        assert generate_integration_id() == expected
    rb.assert_called_once_with(9 * 10**11)


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        123456789012,
        "12345678901",
        "1234567890123",
        "012345678901",
        "12345678901a",
        "1234-5678901",
        " 12345678901",
        "\uff11" * 12,  # fullwidth digits
        "12345678901\u00b2",  # superscript two
        "aabbccddeeff",
    ],
)
def test_invalid_integration_ids_rejected(value: Any) -> None:
    """Wrong length, non-digits, non-ASCII digits and leading zeros fail."""
    assert not is_valid_integration_id(value)


def test_build_client_identity_passes_id_unchanged() -> None:
    """The integration ID is sent as the client sn, unchanged."""
    assert build_client_identity("314159265358") == {
        "mn": "222",
        "sn": "314159265358",
    }


@pytest.mark.parametrize(
    "data_extra",
    [
        {},  # pre-E1 entry: no integration ID at all
        {"integration_serial": "123456789012"},  # legacy key is not adopted
        {CONF_INTEGRATION_ID: "012345678901"},
        {CONF_INTEGRATION_ID: "12345"},
        {CONF_INTEGRATION_ID: "aabbccddeeff"},
    ],
)
async def test_setup_rejects_entry_without_valid_integration_id(
    hass: HomeAssistant, data_extra: dict[str, Any]
) -> None:
    """
    A VERSION 2 entry without a valid ID fails setup and never connects.

    VERSION 1 (0.1.x) entries are upgraded in place instead; see
    test_legacy_migration.py.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        unique_id=PANEL_MAC,
        data={
            CONF_HOST: HOST,
            CONF_PORT: PORT,
            CONF_LINK_KEYS_JSON: LINK_KEYS_JSON,
            **data_extra,
        },
    )
    entry.add_to_hass(hass)
    with patch("custom_components.elke27.coordinator.Elke27Client") as client_cls:
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert entry.reason is not None
    assert "Delete the integration" in entry.reason
    client_cls.assert_not_called()
    # Nothing is written back: no backfill, no migration.
    assert CONF_INTEGRATION_ID not in entry.data or entry.data == {
        CONF_HOST: HOST,
        CONF_PORT: PORT,
        CONF_LINK_KEYS_JSON: LINK_KEYS_JSON,
        **data_extra,
    }
