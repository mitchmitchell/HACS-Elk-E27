# ruff: noqa: S101, TC001, TC003, D103, ARG001, PLC0415, FBT001
"""Tests for E1a: panel MAC from discovery as identity."""

from __future__ import annotations

from collections.abc import Generator
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, create_autospec, patch

from elke27_lib import DiscoveredPanel, LinkKeys
from elke27_lib.client import Elke27Client
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.elke27.config_flow import (
    CONF_ACCESS_CODE,
    CONF_PANEL,
    CONF_PASSPHRASE,
    CONF_SETUP_METHOD,
    SETUP_METHOD_MANUAL,
)
from custom_components.elke27.const import (
    CONF_INTEGRATION_SERIAL,
    CONF_LINK_KEYS_JSON,
    CONF_PANEL_MAC,
    CONF_PANEL_MAC_FILLED,
    DOMAIN,
)
from custom_components.elke27.entity import entry_identity_key
from custom_components.elke27.panel_mac import (
    async_apply_discovered_mac,
    async_verify_panel_mac,
    mismatch_issue_id,
    normalize_panel_mac,
    panel_mac_from_discovered,
)
from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import issue_registry as ir
from tests.conftest import HOST, INTEGRATION_SERIAL, LINK_KEYS_JSON, PORT

MAC = "2CCF67CA3B20"
MAC_FMT = "2c:cf:67:ca:3b:20"
OTHER_MAC = "AABBCCDDEEFF"
LINK_KEYS = LinkKeys(tempkey_hex="aa", linkkey_hex="bb", linkhmac_hex="cc")


def _discovered(mac: str | None = MAC, host: str = HOST) -> DiscoveredPanel:
    return DiscoveredPanel(host=host, port=PORT, panel_name="E27 (3B20)", panel_mac=mac)


def _entry(
    *,
    mac: str | None = None,
    unique_id: str | None = None,
    host: str = HOST,
    **extra: Any,
) -> MockConfigEntry:
    data: dict[str, Any] = {
        CONF_HOST: host,
        CONF_PORT: PORT,
        CONF_LINK_KEYS_JSON: LINK_KEYS_JSON,
        CONF_INTEGRATION_SERIAL: INTEGRATION_SERIAL,
        **extra,
    }
    if mac is not None:
        data[CONF_PANEL_MAC] = mac
    return MockConfigEntry(domain=DOMAIN, title="Panel", unique_id=unique_id, data=data)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2c:cf:67:ca:3b:20", MAC),
        ("2C-CF-67-CA-3B-20", MAC),
        ("2ccf67ca3b20", MAC),
        ("2ccf.67ca.3b20", MAC),
        ("000000000000", None),
        ("2ccf67ca3b", None),
        ("gggggggggggg", None),
        (None, None),
        (12, None),
    ],
)
def test_normalize_panel_mac(raw: Any, expected: str | None) -> None:
    assert normalize_panel_mac(raw) == expected


def test_panel_mac_from_discovered_shapes() -> None:
    assert panel_mac_from_discovered(None) is None
    assert panel_mac_from_discovered({"panel_mac": "2c:cf:67:ca:3b:20"}) == MAC
    assert panel_mac_from_discovered({"mac": "2ccf67ca3b20"}) == MAC
    assert (
        panel_mac_from_discovered(SimpleNamespace(panel_mac="2c:cf:67:ca:3b:20")) == MAC
    )
    assert panel_mac_from_discovered(_discovered(None)) is None


# --- config flow -------------------------------------------------------------


@pytest.fixture
def flow_client() -> Generator[Any]:
    client: Any = create_autospec(Elke27Client, instance=True)
    client.async_link.return_value = LINK_KEYS
    client.wait_ready.return_value = True
    import dataclasses

    from tests.conftest import panel_snapshot

    snap = panel_snapshot()
    # Library 0.3.12+: mac is None in the snapshot (only discovery knows it).
    client.get_snapshot.return_value = dataclasses.replace(
        snap, panel=dataclasses.replace(snap.panel, mac=None)
    )
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


async def _manual(hass: HomeAssistant) -> dict[str, Any]:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_SETUP_METHOD: SETUP_METHOD_MANUAL}
    )
    return await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_HOST: HOST, CONF_ACCESS_CODE: "1234", CONF_PASSPHRASE: "pw"},
    )


async def test_manual_entry_with_discovery_reply_stores_mac(
    hass: HomeAssistant, flow_client: Any, mock_discover_host: AsyncMock
) -> None:
    mock_discover_host.return_value = _discovered("2c:cf:67:ca:3b:20")
    result = await _manual(hass)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    entry = result["result"]
    assert entry.data[CONF_PANEL_MAC] == MAC
    assert CONF_PANEL_MAC_FILLED not in entry.data
    assert entry.unique_id == MAC_FMT
    mock_discover_host.assert_awaited_once()
    assert mock_discover_host.await_args.args[0] == HOST


async def test_manual_entry_without_reply_stores_nothing(
    hass: HomeAssistant, flow_client: Any, mock_discover_host: AsyncMock
) -> None:
    result = await _manual(hass)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    entry = result["result"]
    assert CONF_PANEL_MAC not in entry.data
    assert entry.unique_id is None


async def test_manual_entry_discovery_error_is_no_reply(
    hass: HomeAssistant, flow_client: Any, mock_discover_host: AsyncMock
) -> None:
    mock_discover_host.side_effect = OSError("boom")
    result = await _manual(hass)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert CONF_PANEL_MAC not in result["result"].data


async def test_duplicate_add_aborted_by_mac_unique_id(
    hass: HomeAssistant, flow_client: Any, mock_discover_host: AsyncMock
) -> None:
    _entry(mac=MAC, unique_id=MAC_FMT, host="192.0.2.99").add_to_hass(hass)
    mock_discover_host.return_value = _discovered()
    result = await _manual(hass)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_discovery_flow_stores_mac_from_discovered_panel(
    hass: HomeAssistant, flow_client: Any, mock_discover_host: AsyncMock
) -> None:
    panel = SimpleNamespace(
        panel_host=HOST,
        port=PORT,
        panel_name="E27 (3B20)",
        panel_mac="2c:cf:67:ca:3b:20",
    )
    scanner = MagicMock()
    scanner.async_scan = AsyncMock(return_value=[panel])
    with patch(
        "custom_components.elke27.config_flow.AIOELKDiscovery", return_value=scanner
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_SETUP_METHOD: "discover"}
        )
        assert result["step_id"] == "discover"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_ACCESS_CODE: "1234", CONF_PASSPHRASE: "pw"}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].data[CONF_PANEL_MAC] == MAC
    assert result["result"].unique_id == MAC_FMT
    mock_discover_host.assert_not_awaited()
    assert CONF_PANEL in result["result"].data


async def test_reauth_with_different_discovered_mac_aborts(
    hass: HomeAssistant, flow_client: Any, mock_discover_host: AsyncMock
) -> None:
    entry = _entry(mac=MAC, unique_id=MAC_FMT)
    entry.add_to_hass(hass)
    mock_discover_host.return_value = _discovered(OTHER_MAC)
    result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ACCESS_CODE: "1234", CONF_PASSPHRASE: "pw"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_panel"


async def test_reauth_of_018_entry_fills_mac(
    hass: HomeAssistant, flow_client: Any, mock_discover_host: AsyncMock
) -> None:
    entry = _entry()
    entry.add_to_hass(hass)
    mock_discover_host.return_value = _discovered()
    result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ACCESS_CODE: "1234", CONF_PASSPHRASE: "pw"}
    )
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_PANEL_MAC] == MAC
    assert entry.data[CONF_PANEL_MAC_FILLED] is True
    assert entry.unique_id == MAC_FMT


@pytest.mark.parametrize("filled", [False, True])
async def test_reauth_no_reply_keeps_stored_mac_entry(
    hass: HomeAssistant, flow_client: Any, filled: bool
) -> None:
    """No discovery reply on reauth must not abort wrong_panel for a MAC entry."""
    extra = {CONF_PANEL_MAC_FILLED: True} if filled else {}
    entry = _entry(mac=MAC, unique_id=MAC_FMT, **extra)
    entry.add_to_hass(hass)
    result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ACCESS_CODE: "1234", CONF_PASSPHRASE: "pw"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_PANEL_MAC] == MAC
    assert entry.unique_id == MAC_FMT
    assert bool(entry.data.get(CONF_PANEL_MAC_FILLED)) is filled


# --- connect-time verify / fill ---------------------------------------------


async def test_connect_verify_match_clears_issue(
    hass: HomeAssistant, mock_discover_host: AsyncMock
) -> None:
    entry = _entry(mac=MAC, unique_id=MAC_FMT)
    entry.add_to_hass(hass)
    issues = ir.async_get(hass)
    async_apply_discovered_mac(hass, entry, OTHER_MAC)
    assert issues.async_get_issue(DOMAIN, mismatch_issue_id(entry.entry_id))
    mock_discover_host.return_value = _discovered()
    await async_verify_panel_mac(hass, entry)
    assert issues.async_get_issue(DOMAIN, mismatch_issue_id(entry.entry_id)) is None


async def test_connect_verify_mismatch_raises_repairs_and_keeps_identity(
    hass: HomeAssistant, mock_discover_host: AsyncMock, caplog: pytest.LogCaptureFixture
) -> None:
    entry = _entry(mac=MAC, unique_id=MAC_FMT)
    entry.add_to_hass(hass)
    mock_discover_host.return_value = _discovered(OTHER_MAC)
    await async_verify_panel_mac(hass, entry)
    issue = ir.async_get(hass).async_get_issue(
        DOMAIN, mismatch_issue_id(entry.entry_id)
    )
    assert issue is not None
    assert issue.translation_key == "panel_mac_mismatch"
    assert issue.translation_placeholders["discovered"] == "aa:bb:cc:dd:ee:ff"
    assert entry.data[CONF_PANEL_MAC] == MAC
    assert entry.unique_id == MAC_FMT
    assert "keeping the existing identity" in caplog.text


async def test_connect_verify_no_reply_keeps_stored_mac(
    hass: HomeAssistant, mock_discover_host: AsyncMock
) -> None:
    entry = _entry(mac=MAC, unique_id=MAC_FMT)
    entry.add_to_hass(hass)
    await async_verify_panel_mac(hass, entry)
    assert entry.data[CONF_PANEL_MAC] == MAC
    assert not ir.async_get(hass).issues


async def test_connect_fill_on_018_entry(
    hass: HomeAssistant, mock_discover_host: AsyncMock
) -> None:
    entry = _entry()
    entry.add_to_hass(hass)
    mock_discover_host.return_value = _discovered()
    await async_verify_panel_mac(hass, entry)
    assert entry.data[CONF_PANEL_MAC] == MAC
    assert entry.data[CONF_PANEL_MAC_FILLED] is True
    assert entry.unique_id == MAC_FMT
    assert entry.version == 1


async def test_connect_fill_skipped_when_unique_id_taken(
    hass: HomeAssistant, mock_discover_host: AsyncMock
) -> None:
    other = _entry(mac=MAC, unique_id=MAC_FMT, host="192.0.2.99")
    other.add_to_hass(hass)
    entry = _entry()
    entry.add_to_hass(hass)
    mock_discover_host.return_value = _discovered()
    await async_verify_panel_mac(hass, entry)
    assert CONF_PANEL_MAC not in entry.data
    assert entry.unique_id is None
    issue = ir.async_get(hass).async_get_issue(
        DOMAIN, f"duplicate_unique_id_{entry.entry_id}"
    )
    assert issue is not None
    assert issue.translation_key == "duplicate_unique_id"


async def test_connect_fill_keeps_existing_serial_unique_id(
    hass: HomeAssistant, mock_discover_host: AsyncMock
) -> None:
    entry = _entry(unique_id="real-serial-1")
    entry.add_to_hass(hass)
    mock_discover_host.return_value = _discovered()
    await async_verify_panel_mac(hass, entry)
    assert entry.data[CONF_PANEL_MAC] == MAC
    assert entry.unique_id == "real-serial-1"


# --- identity tiers -----------------------------------------------------------


def _hub_coord(mac: str | None = None) -> tuple[Any, Any]:
    panel = SimpleNamespace(mac=mac, serial=None, panel_name="Panel")
    coordinator = SimpleNamespace(panel_name="Panel", data=SimpleNamespace(panel=panel))
    return coordinator, coordinator


def test_identity_key_stored_mac_is_tier_one() -> None:
    _hub, coord = _hub_coord()
    entry = _entry(mac=MAC)
    assert entry_identity_key(coord, entry) == MAC_FMT  # type: ignore[arg-type]


def test_identity_key_ignores_filled_mac() -> None:
    """A filled-in MAC must not move an existing entry's entity identity."""
    _hub, coord = _hub_coord()
    entry = _entry(mac=MAC, **{CONF_PANEL_MAC_FILLED: True})
    assert entry_identity_key(coord, entry) == entry.entry_id  # type: ignore[arg-type]


async def test_hub_connect_schedules_mac_check(
    hass: HomeAssistant, mock_client: Any, mock_discover_host: AsyncMock
) -> None:
    entry = _entry()
    entry.add_to_hass(hass)
    mock_discover_host.return_value = _discovered()
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    mock_discover_host.assert_awaited()
    assert entry.data[CONF_PANEL_MAC] == MAC
    assert entry.unique_id == MAC_FMT


async def test_reauth_discovery_entry_rediscovers_and_rejects_new_mac(
    hass: HomeAssistant, flow_client: Any, mock_discover_host: AsyncMock
) -> None:
    """Reauth ignores the saved discovery record's MAC and checks the live one."""
    entry = _entry(
        mac=MAC,
        unique_id=MAC_FMT,
        **{CONF_PANEL: {"host": HOST, "port": PORT, "mac": MAC_FMT}},
    )
    entry.add_to_hass(hass)
    mock_discover_host.return_value = _discovered(OTHER_MAC)
    result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ACCESS_CODE: "1234", CONF_PASSPHRASE: "pw"}
    )
    mock_discover_host.assert_awaited_once()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_panel"
    assert entry.data[CONF_PANEL_MAC] == MAC


async def test_reauth_018_discovery_entry_fills_live_not_saved_mac(
    hass: HomeAssistant, flow_client: Any, mock_discover_host: AsyncMock
) -> None:
    """A 0.1.8 discovery entry is filled from the live reply, not CONF_PANEL."""
    entry = _entry(
        **{CONF_PANEL: {"host": HOST, "port": PORT, "mac": "aa:bb:cc:dd:ee:ff"}}
    )
    entry.add_to_hass(hass)
    result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ACCESS_CODE: "1234", CONF_PASSPHRASE: "pw"}
    )
    assert result["reason"] == "reauth_successful"
    assert CONF_PANEL_MAC not in entry.data
    mock_discover_host.assert_awaited_once()


@pytest.mark.parametrize("placeholder", ["00000001", "0", "FFFFFFFF"])
async def test_connect_fill_replaces_placeholder_serial_unique_id(
    hass: HomeAssistant, mock_discover_host: AsyncMock, placeholder: str
) -> None:
    entry = _entry(unique_id=placeholder)
    entry.add_to_hass(hass)
    mock_discover_host.return_value = _discovered()
    await async_verify_panel_mac(hass, entry)
    assert entry.unique_id == MAC_FMT
    assert entry.data[CONF_PANEL_MAC] == MAC


async def test_duplicate_add_aborted_by_other_entry_stored_mac(
    hass: HomeAssistant, flow_client: Any, mock_discover_host: AsyncMock
) -> None:
    """An entry with CONF_PANEL_MAC but a different unique_id still blocks a re-add."""
    _entry(mac=MAC, unique_id="real-serial-1", host="192.0.2.99").add_to_hass(hass)
    mock_discover_host.return_value = _discovered()
    result = await _manual(hass)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_duplicate_check_does_not_match_on_live_serial_vs_stored_mac(
    hass: HomeAssistant, flow_client: Any, mock_discover_host: AsyncMock
) -> None:
    """No live MAC: another entry's stored MAC is not compared with the serial."""
    _entry(mac=MAC, unique_id=MAC_FMT, host="192.0.2.99").add_to_hass(hass)
    result = await _manual(hass)
    assert result["type"] is FlowResultType.CREATE_ENTRY


@pytest.mark.parametrize("owner_by", ["unique_id", "stored_mac"])
async def test_reauth_fill_skips_mac_owned_by_other_entry(
    hass: HomeAssistant,
    flow_client: Any,
    mock_discover_host: AsyncMock,
    owner_by: str,
) -> None:
    """Reauth must not store a MAC another entry owns (Bugbot 4263971532)."""
    if owner_by == "unique_id":
        other = _entry(unique_id=MAC_FMT, host="192.0.2.99")
    else:
        other = _entry(mac=MAC, unique_id="other-id", host="192.0.2.99")
    other.add_to_hass(hass)
    entry = _entry()
    entry.add_to_hass(hass)
    mock_discover_host.return_value = _discovered()
    result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ACCESS_CODE: "1234", CONF_PASSPHRASE: "pw"}
    )
    assert result["reason"] == "reauth_successful"
    assert CONF_PANEL_MAC not in entry.data
    assert CONF_PANEL_MAC_FILLED not in entry.data
    assert entry.unique_id != MAC_FMT
    issue = ir.async_get(hass).async_get_issue(
        DOMAIN, f"duplicate_unique_id_{entry.entry_id}"
    )
    assert issue is not None


@pytest.mark.parametrize("via_reauth", [False, True])
async def test_connect_fill_skipped_when_stored_mac_taken(
    hass: HomeAssistant,
    flow_client: Any,
    mock_discover_host: AsyncMock,
    via_reauth: bool,
) -> None:
    """Connect-time fill refuses a MAC another entry stores (Bugbot 4264098717)."""
    other = _entry(mac=MAC, unique_id="other-id", host="192.0.2.99")
    other.add_to_hass(hass)
    entry = _entry()
    entry.add_to_hass(hass)
    mock_discover_host.return_value = _discovered()
    issue_id = f"duplicate_unique_id_{entry.entry_id}"
    if via_reauth:
        # Reauth refuses the MAC, then the reload runs the connect-time fill.
        result = await entry.start_reauth_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_ACCESS_CODE: "1234", CONF_PASSPHRASE: "pw"}
        )
        assert result["reason"] == "reauth_successful"
        assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None
    await async_verify_panel_mac(hass, entry)
    assert CONF_PANEL_MAC not in entry.data
    assert CONF_PANEL_MAC_FILLED not in entry.data
    assert entry.unique_id != MAC_FMT
    issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.translation_key == "duplicate_unique_id"
