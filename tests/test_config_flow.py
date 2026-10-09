# ruff: noqa: S101, TC001, TC002, TC003
"""Tests for the Elke27 config flow (manual path)."""

from __future__ import annotations

from collections.abc import Generator
from typing import Any
from unittest.mock import AsyncMock, create_autospec, patch

from elke27_lib import LinkKeys
from elke27_lib.client import Elke27Client
from elke27_lib.errors import (
    Elke27ConnectionError,
    Elke27LinkRequiredError,
    Elke27ProtocolError,
    InvalidCredentials,
)
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.elke27.config_flow import (
    CONF_ACCESS_CODE,
    CONF_PASSPHRASE,
    CONF_SETUP_METHOD,
    SETUP_METHOD_MANUAL,
)
from custom_components.elke27.const import (
    CONF_INTEGRATION_SERIAL,
    CONF_LINK_KEYS_JSON,
    DOMAIN,
)
from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from tests.conftest import HOST, INTEGRATION_SERIAL, PANEL_MAC, PORT, panel_snapshot

ACCESS_CODE = "908172"
PASSPHRASE = "secret-passphrase"
LINK_KEYS = LinkKeys(tempkey_hex="aa", linkkey_hex="bb", linkhmac_hex="cc")


@pytest.fixture
def flow_client() -> Generator[Any]:
    """Patch the client and identity used by the config flow."""
    client: Any = create_autospec(Elke27Client, instance=True)
    client.async_link.return_value = LINK_KEYS
    client.wait_ready.return_value = True
    client.get_snapshot.return_value = panel_snapshot()
    with (
        patch(
            "custom_components.elke27.config_flow._create_client", return_value=client
        ),
        patch(
            "custom_components.elke27.config_flow.async_get_integration_serial",
            AsyncMock(return_value=INTEGRATION_SERIAL),
        ),
        patch("custom_components.elke27.async_setup_entry", return_value=True),
    ):
        yield client


async def _start_manual(hass: HomeAssistant) -> dict[str, Any]:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_SETUP_METHOD: SETUP_METHOD_MANUAL}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "manual"
    return result


async def test_manual_creates_entry_without_storing_codes(
    hass: HomeAssistant, flow_client: Any
) -> None:
    """Manual setup links, creates the entry, and never stores the codes."""
    result = await _start_manual(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_HOST: HOST, CONF_ACCESS_CODE: ACCESS_CODE, CONF_PASSPHRASE: PASSPHRASE},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Test Panel"
    data = result["data"]
    assert data[CONF_HOST] == HOST
    assert data[CONF_PORT] == PORT
    assert data[CONF_LINK_KEYS_JSON] == LINK_KEYS.to_json()
    assert data[CONF_INTEGRATION_SERIAL] == INTEGRATION_SERIAL
    assert result["result"].unique_id == PANEL_MAC
    stored = repr(data) + repr(result["options"])
    assert ACCESS_CODE not in stored
    assert PASSPHRASE not in stored
    flow_client.async_disconnect.assert_awaited_once()


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (InvalidCredentials("bad"), "invalid_auth"),
        (Elke27ConnectionError("down"), "cannot_connect"),
        (Elke27LinkRequiredError("relink"), "link_required"),
        (Elke27ProtocolError("other"), "unknown"),
    ],
)
async def test_manual_errors_then_recover(
    hass: HomeAssistant, flow_client: Any, error: Exception, reason: str
) -> None:
    """Link errors show on the form and the user can try again."""
    result = await _start_manual(hass)
    flow_client.async_link.side_effect = error
    user_input = {
        CONF_HOST: HOST,
        CONF_ACCESS_CODE: ACCESS_CODE,
        CONF_PASSPHRASE: PASSPHRASE,
    }
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": reason}
    flow_client.async_disconnect.assert_awaited()

    flow_client.async_link.side_effect = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_manual_not_ready(hass: HomeAssistant, flow_client: Any) -> None:
    """A panel that never becomes ready shows cannot_connect."""
    flow_client.wait_ready.return_value = False
    result = await _start_manual(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_HOST: HOST, CONF_ACCESS_CODE: ACCESS_CODE, CONF_PASSPHRASE: PASSPHRASE},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_manual_aborts_when_host_configured(
    hass: HomeAssistant, flow_client: Any, mock_config_entry: MockConfigEntry
) -> None:
    """A host that is already configured aborts before linking."""
    mock_config_entry.add_to_hass(hass)
    result = await _start_manual(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_HOST: HOST, CONF_ACCESS_CODE: ACCESS_CODE, CONF_PASSPHRASE: PASSPHRASE},
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    flow_client.async_link.assert_not_awaited()
