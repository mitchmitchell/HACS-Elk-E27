# ruff: noqa: S101, SLF001, TC001, TC002, TC003
"""Tests for the Elke27 config flow (manual path)."""

from __future__ import annotations

from collections.abc import Generator
import dataclasses
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, create_autospec, patch

from elke27_lib import LinkKeys
from elke27_lib.client import Elke27Client
from elke27_lib.errors import (
    E27Timeout,
    Elke27AuthError,
    Elke27ConnectionError,
    Elke27LinkRequiredError,
    Elke27ProtocolError,
    InvalidCredentials,
)
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
import voluptuous as vol

from custom_components.elke27.config_flow import (
    CONF_ACCESS_CODE,
    CONF_PANEL,
    CONF_PANEL_INFO,
    CONF_PASSPHRASE,
    CONF_RESCAN,
    CONF_SETUP_METHOD,
    SETUP_METHOD_MANUAL,
    Elke27ConfigFlow,
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
from homeassistant.helpers.selector import SelectSelector
from tests.conftest import (
    HOST,
    HOST_2,
    INTEGRATION_SERIAL,
    PANEL_MAC,
    PANEL_SERIAL,
    PANEL_SERIAL_2,
    PORT,
    panel_snapshot,
)

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


RAW_MAC = "AABBCC112233"
FORMATTED_RAW_MAC = "aa:bb:cc:11:22:33"


async def test_manual_create_normalizes_mac_unique_id(
    hass: HomeAssistant, flow_client: Any
) -> None:
    """Create stores a formatted MAC unique_id even when the panel reports raw MAC."""
    snapshot = panel_snapshot()
    flow_client.get_snapshot.return_value = dataclasses.replace(
        snapshot,
        panel=dataclasses.replace(snapshot.panel, mac=RAW_MAC),
    )
    result = await _start_manual(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_HOST: HOST, CONF_ACCESS_CODE: ACCESS_CODE, CONF_PASSPHRASE: PASSPHRASE},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == FORMATTED_RAW_MAC


async def test_reauth_accepts_formatted_mac_when_panel_reports_raw_mac(
    hass: HomeAssistant, flow_client: Any
) -> None:
    """Reauth matches when the entry MAC is formatted and the panel returns raw MAC."""
    mock_config_entry = MockConfigEntry(
        domain=DOMAIN,
        title="Test Panel",
        unique_id=FORMATTED_RAW_MAC,
        data={
            CONF_HOST: HOST,
            CONF_PORT: PORT,
            CONF_LINK_KEYS_JSON: LINK_KEYS.to_json(),
            CONF_INTEGRATION_SERIAL: INTEGRATION_SERIAL,
        },
    )
    mock_config_entry.add_to_hass(hass)
    snapshot = panel_snapshot()
    flow_client.get_snapshot.return_value = dataclasses.replace(
        snapshot,
        panel=dataclasses.replace(snapshot.panel, mac=RAW_MAC.upper()),
    )
    result = await mock_config_entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ACCESS_CODE: ACCESS_CODE, CONF_PASSPHRASE: PASSPHRASE}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"


async def test_manual_create_uses_panel_serial_unique_id_when_no_mac(
    hass: HomeAssistant, flow_client: Any
) -> None:
    """No-MAC panels use panel hardware serial as the config entry unique_id."""
    snapshot = panel_snapshot()
    flow_client.get_snapshot.return_value = dataclasses.replace(
        snapshot,
        panel=dataclasses.replace(snapshot.panel, mac=None, serial=PANEL_SERIAL),
    )
    result = await _start_manual(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_HOST: HOST, CONF_ACCESS_CODE: ACCESS_CODE, CONF_PASSPHRASE: PASSPHRASE},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == PANEL_SERIAL


async def test_two_macless_panels_with_different_serials_can_be_added(
    hass: HomeAssistant, flow_client: Any
) -> None:
    """Two MAC-less panels with different panel serials are separate config entries."""
    snapshot_a = panel_snapshot()
    flow_client.get_snapshot.return_value = dataclasses.replace(
        snapshot_a,
        panel=dataclasses.replace(snapshot_a.panel, mac=None, serial=PANEL_SERIAL),
    )
    result = await _start_manual(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_HOST: HOST, CONF_ACCESS_CODE: ACCESS_CODE, CONF_PASSPHRASE: PASSPHRASE},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == PANEL_SERIAL

    snapshot_b = panel_snapshot()
    flow_client.get_snapshot.return_value = dataclasses.replace(
        snapshot_b,
        panel=dataclasses.replace(snapshot_b.panel, mac=None, serial=PANEL_SERIAL_2),
    )
    result = await _start_manual(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_HOST: HOST_2,
            CONF_ACCESS_CODE: ACCESS_CODE,
            CONF_PASSPHRASE: PASSPHRASE,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == PANEL_SERIAL_2
    assert len(hass.config_entries.async_entries(DOMAIN)) == 2  # noqa: PLR2004


async def test_two_macless_panels_without_serial_at_different_hosts(
    hass: HomeAssistant, flow_client: Any
) -> None:
    """MAC-less panels without serial dedupe on host:port only (both can be added)."""
    snapshot = panel_snapshot()
    flow_client.get_snapshot.return_value = dataclasses.replace(
        snapshot,
        panel=dataclasses.replace(snapshot.panel, mac=None, serial=None),
    )
    result = await _start_manual(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_HOST: HOST, CONF_ACCESS_CODE: ACCESS_CODE, CONF_PASSPHRASE: PASSPHRASE},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id is None

    result = await _start_manual(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_HOST: HOST_2,
            CONF_ACCESS_CODE: ACCESS_CODE,
            CONF_PASSPHRASE: PASSPHRASE,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id is None
    assert len(hass.config_entries.async_entries(DOMAIN)) == 2  # noqa: PLR2004


async def test_reauth_succeeds_when_entry_unique_id_is_none_no_mac_panel(
    hass: HomeAssistant, flow_client: Any
) -> None:
    """Legacy entries with unique_id unset reauth and receive the panel serial."""
    snapshot = panel_snapshot()
    flow_client.get_snapshot.return_value = dataclasses.replace(
        snapshot,
        panel=dataclasses.replace(snapshot.panel, mac=None, serial=PANEL_SERIAL),
    )
    mock_config_entry = MockConfigEntry(
        domain=DOMAIN,
        title="Test Panel",
        unique_id=None,
        data={
            CONF_HOST: HOST,
            CONF_PORT: PORT,
            CONF_LINK_KEYS_JSON: LINK_KEYS.to_json(),
            CONF_INTEGRATION_SERIAL: INTEGRATION_SERIAL,
        },
    )
    mock_config_entry.add_to_hass(hass)
    result = await mock_config_entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ACCESS_CODE: ACCESS_CODE, CONF_PASSPHRASE: PASSPHRASE}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert mock_config_entry.unique_id == PANEL_SERIAL


async def test_reauth_legacy_none_accepts_live_mac_when_stored_panel_had_no_mac(
    hass: HomeAssistant, flow_client: Any
) -> None:
    """Reauth succeeds when stored panel_info had no MAC but live reports one."""
    snapshot = panel_snapshot()
    flow_client.get_snapshot.return_value = snapshot
    mock_config_entry = MockConfigEntry(
        domain=DOMAIN,
        title="Test Panel",
        unique_id=None,
        data={
            CONF_HOST: HOST,
            CONF_PORT: PORT,
            CONF_LINK_KEYS_JSON: LINK_KEYS.to_json(),
            CONF_INTEGRATION_SERIAL: INTEGRATION_SERIAL,
        },
        options={
            CONF_PANEL_INFO: dataclasses.asdict(
                dataclasses.replace(snapshot.panel, mac=None, serial=PANEL_SERIAL)
            )
        },
    )
    mock_config_entry.add_to_hass(hass)
    result = await mock_config_entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ACCESS_CODE: ACCESS_CODE, CONF_PASSPHRASE: PASSPHRASE}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert mock_config_entry.unique_id == PANEL_MAC


async def test_reauth_legacy_none_matches_stored_panel_info(
    hass: HomeAssistant, flow_client: Any
) -> None:
    """Legacy reauth succeeds when live panel identity matches stored panel_info."""
    snapshot = panel_snapshot()
    flow_client.get_snapshot.return_value = snapshot
    mock_config_entry = MockConfigEntry(
        domain=DOMAIN,
        title="Test Panel",
        unique_id=None,
        data={
            CONF_HOST: HOST,
            CONF_PORT: PORT,
            CONF_LINK_KEYS_JSON: LINK_KEYS.to_json(),
            CONF_INTEGRATION_SERIAL: INTEGRATION_SERIAL,
        },
        options={CONF_PANEL_INFO: dataclasses.asdict(snapshot.panel)},
    )
    mock_config_entry.add_to_hass(hass)
    result = await mock_config_entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ACCESS_CODE: ACCESS_CODE, CONF_PASSPHRASE: PASSPHRASE}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert mock_config_entry.unique_id == PANEL_MAC


async def test_reauth_legacy_none_rejects_mismatched_stored_panel_info(
    hass: HomeAssistant, flow_client: Any
) -> None:
    """Legacy reauth aborts when live panel identity differs from stored panel_info."""
    snapshot = panel_snapshot()
    other = dataclasses.replace(
        snapshot,
        panel=dataclasses.replace(
            snapshot.panel,
            mac="66:77:88:99:aa:bb",
            serial="other-panel-serial",
        ),
    )
    flow_client.get_snapshot.return_value = other
    mock_config_entry = MockConfigEntry(
        domain=DOMAIN,
        title="Test Panel",
        unique_id=None,
        data={
            CONF_HOST: HOST,
            CONF_PORT: PORT,
            CONF_LINK_KEYS_JSON: LINK_KEYS.to_json(),
            CONF_INTEGRATION_SERIAL: INTEGRATION_SERIAL,
        },
        options={CONF_PANEL_INFO: dataclasses.asdict(snapshot.panel)},
    )
    mock_config_entry.add_to_hass(hass)
    result = await mock_config_entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ACCESS_CODE: ACCESS_CODE, CONF_PASSPHRASE: PASSPHRASE}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_panel"
    assert mock_config_entry.unique_id is None


async def test_manual_aborts_same_mac_at_different_host(
    hass: HomeAssistant,
    flow_client: Any,  # noqa: ARG001
    mock_config_entry: MockConfigEntry,
) -> None:
    """Re-adding the same MAC panel at a new address aborts already_configured."""
    mock_config_entry.add_to_hass(hass)
    other_host = "192.0.2.99"
    result = await _start_manual(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_HOST: other_host,
            CONF_ACCESS_CODE: ACCESS_CODE,
            CONF_PASSPHRASE: PASSPHRASE,
        },
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_manual_aborts_already_configured_legacy_none_unique_id_no_mac(
    hass: HomeAssistant, flow_client: Any
) -> None:
    """Re-adding a no-MAC panel with a legacy entry aborts already_configured."""
    snapshot = panel_snapshot()
    flow_client.get_snapshot.return_value = dataclasses.replace(
        snapshot,
        panel=dataclasses.replace(snapshot.panel, mac=None, serial=PANEL_SERIAL),
    )
    legacy = MockConfigEntry(
        domain=DOMAIN,
        title="Legacy Panel",
        unique_id=None,
        data={
            CONF_HOST: HOST,
            CONF_PORT: PORT,
            CONF_LINK_KEYS_JSON: LINK_KEYS.to_json(),
            CONF_INTEGRATION_SERIAL: INTEGRATION_SERIAL,
        },
    )
    legacy.add_to_hass(hass)
    result = await _start_manual(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_HOST: HOST, CONF_ACCESS_CODE: ACCESS_CODE, CONF_PASSPHRASE: PASSPHRASE},
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


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


@pytest.mark.parametrize(
    "error",
    [OSError("unreachable"), TimeoutError("slow"), E27Timeout("raw timeout")],
)
async def test_manual_cannot_connect_os_timeout_errors(
    hass: HomeAssistant, flow_client: Any, error: Exception
) -> None:
    """OS, timeout and raw E27 errors show cannot_connect."""
    result = await _start_manual(hass)
    flow_client.async_link.side_effect = error
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_HOST: HOST, CONF_ACCESS_CODE: ACCESS_CODE, CONF_PASSPHRASE: PASSPHRASE},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}
    flow_client.async_disconnect.assert_awaited()


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


async def test_manual_auth_error_is_invalid_auth(
    hass: HomeAssistant, flow_client: Any
) -> None:
    """elke27 maps rejected credentials to Elke27AuthError: show invalid_auth."""
    result = await _start_manual(hass)
    flow_client.async_link.side_effect = Elke27AuthError("rejected")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_HOST: HOST, CONF_ACCESS_CODE: ACCESS_CODE, CONF_PASSPHRASE: PASSPHRASE},
    )
    assert result["errors"] == {"base": "invalid_auth"}


async def test_reauth_relinks_same_panel(
    hass: HomeAssistant, flow_client: Any, mock_config_entry: MockConfigEntry
) -> None:
    """Reauth relinks with the stored integration serial and reloads the entry."""
    mock_config_entry.add_to_hass(hass)
    result = await mock_config_entry.start_reauth_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"

    flow_client.async_link.side_effect = Elke27AuthError("rejected")
    user_input = {CONF_ACCESS_CODE: ACCESS_CODE, CONF_PASSPHRASE: PASSPHRASE}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}

    flow_client.async_link.side_effect = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert mock_config_entry.data[CONF_LINK_KEYS_JSON] == LINK_KEYS.to_json()
    assert mock_config_entry.data[CONF_INTEGRATION_SERIAL] == INTEGRATION_SERIAL
    assert mock_config_entry.data[CONF_HOST] == HOST
    assert ACCESS_CODE not in repr(mock_config_entry.data)
    link_kwargs = flow_client.async_link.await_args.kwargs
    assert link_kwargs["host"] == HOST


async def test_reauth_succeeds_when_entry_unique_id_is_panel_serial(
    hass: HomeAssistant, flow_client: Any
) -> None:
    """Reauth accepts the panel when the entry was keyed by panel hardware serial."""
    snapshot = panel_snapshot()
    flow_client.get_snapshot.return_value = dataclasses.replace(
        snapshot,
        panel=dataclasses.replace(snapshot.panel, mac=None, serial=PANEL_SERIAL),
    )
    mock_config_entry = MockConfigEntry(
        domain=DOMAIN,
        title="Test Panel",
        unique_id=PANEL_SERIAL,
        data={
            CONF_HOST: HOST,
            CONF_PORT: PORT,
            CONF_LINK_KEYS_JSON: LINK_KEYS.to_json(),
            CONF_INTEGRATION_SERIAL: INTEGRATION_SERIAL,
        },
    )
    mock_config_entry.add_to_hass(hass)
    result = await mock_config_entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ACCESS_CODE: ACCESS_CODE, CONF_PASSPHRASE: PASSPHRASE}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert mock_config_entry.unique_id == PANEL_SERIAL
    flow_client.async_link.assert_awaited_once()


async def test_reauth_rejects_different_panel(
    hass: HomeAssistant, flow_client: Any, mock_config_entry: MockConfigEntry
) -> None:
    """Reauth that links a different panel aborts and leaves the entry alone."""
    mock_config_entry.add_to_hass(hass)
    old_data = dict(mock_config_entry.data)
    other = panel_snapshot()
    flow_client.get_snapshot.return_value = dataclasses.replace(
        other, panel=dataclasses.replace(other.panel, mac="66:77:88:99:aa:bb")
    )
    result = await mock_config_entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ACCESS_CODE: ACCESS_CODE, CONF_PASSPHRASE: PASSPHRASE}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_panel"
    assert dict(mock_config_entry.data) == old_data


def _panel_select(schema: vol.Schema) -> SelectSelector:
    for marker, field in schema.schema.items():
        if marker.schema == CONF_PANEL:
            assert isinstance(field, SelectSelector)
            return field
    msg = "panel select missing from discovery schema"
    raise AssertionError(msg)


async def test_discovery_panel_select_rescan_and_dynamic_labels(
    hass: HomeAssistant,
) -> None:
    """Rescan uses a translated label; panel rows keep host/name labels."""
    flow = Elke27ConfigFlow()
    flow.hass = hass
    flow._discovered_panels = [
        SimpleNamespace(
            panel_name="Kitchen",
            panel_host="192.0.2.20",
            port=2101,
            panel_mac="aa:bb:cc:dd:ee:01",
            panel_model="E27",
        ),
        SimpleNamespace(
            panel_name="Garage",
            panel_host="192.0.2.21",
            port=2101,
            panel_mac="aa:bb:cc:dd:ee:02",
            panel_model="E27",
        ),
    ]
    schema = await flow._async_discovery_schema()
    panel_select = _panel_select(schema)
    select_cfg = panel_select.config.get("select", panel_select.config)
    assert "translation_key" not in select_cfg
    options = select_cfg["options"]
    assert options[0] == {"value": "rescan", "label": "Rescan for panels"}
    assert options[0]["value"] == CONF_RESCAN
    assert options[1]["value"] == "0"
    assert "Kitchen" in options[1]["label"]
    assert "192.0.2.20" in options[1]["label"]
    assert options[2]["value"] == "1"
    assert "Garage" in options[2]["label"]
