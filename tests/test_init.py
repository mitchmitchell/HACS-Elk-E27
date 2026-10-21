# ruff: noqa: S101, SLF001, TC001, ARG001
"""Tests for Elke27 setup and unload."""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from unittest.mock import AsyncMock, patch

from elke27_lib import ArmMode
from elke27_lib.errors import (
    E27Error,
    E27Timeout,
    Elke27AuthError,
    Elke27ConnectionError,
    Elke27CryptoError,
    Elke27LinkRequiredError,
    Elke27ProtocolError,
    Elke27TimeoutError,
)
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.elke27 import async_remove_entry
from custom_components.elke27.alarm_control_panel import _normalize_code
from custom_components.elke27.const import (
    CONF_INTEGRATION_ID,
    CONF_LINK_KEYS_JSON,
    DOMAIN,
    ISSUE_RECONNECT_FAILED,
    RECONNECT_NON_TRANSPORT_ISSUE_THRESHOLD,
)
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import (
    device_registry as dr,
    entity_registry as er,
    issue_registry as ir,
)
from tests.conftest import (
    HOST,
    HOST_2,
    INTEGRATION_ID,
    LINK_KEYS_JSON,
    PANEL_SERIAL,
    PANEL_SERIAL_2,
    PORT,
    ClientHarness,
    FakeClient,
    Setup,
    command_result,
    disconnect_link,
    panel_snapshot,
    script_connect,
    setup_entry,
)


async def test_remove_entry_deletes_duplicate_unique_id_repair(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Removing an entry clears its duplicate unique_id repair issue."""
    mock_config_entry.add_to_hass(hass)
    issue_id = f"duplicate_unique_id_{mock_config_entry.entry_id}"
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=False,
        severity=ir.IssueSeverity.ERROR,
        translation_key="duplicate_unique_id",
    )
    await async_remove_entry(hass, mock_config_entry)
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None


async def test_two_macless_entries_have_separate_devices_and_entity_ids(
    hass: HomeAssistant, mock_client: ClientHarness
) -> None:
    """Two MAC-less panels stay on separate devices with distinct entity unique IDs."""
    snapshot_a = panel_snapshot()
    mock_client.snapshot = dataclasses.replace(
        snapshot_a,
        panel=dataclasses.replace(
            snapshot_a.panel, mac=None, serial=PANEL_SERIAL, panel_name="Panel A"
        ),
    )
    entry_a = MockConfigEntry(
        domain=DOMAIN,
        title="Panel A",
        unique_id=PANEL_SERIAL,
        data={
            "host": HOST,
            "port": PORT,
            CONF_LINK_KEYS_JSON: LINK_KEYS_JSON,
            CONF_INTEGRATION_ID: INTEGRATION_ID,
        },
    )
    entry_a.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry_a.entry_id)
    await hass.async_block_till_done()

    snapshot_b = panel_snapshot()
    mock_client.snapshot = dataclasses.replace(
        snapshot_b,
        panel=dataclasses.replace(
            snapshot_b.panel, mac=None, serial=PANEL_SERIAL_2, panel_name="Panel B"
        ),
    )
    entry_b = MockConfigEntry(
        domain=DOMAIN,
        title="Panel B",
        unique_id=PANEL_SERIAL_2,
        data={
            "host": HOST_2,
            "port": PORT,
            CONF_LINK_KEYS_JSON: LINK_KEYS_JSON,
            CONF_INTEGRATION_ID: INTEGRATION_ID,
        },
    )
    entry_b.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry_b.entry_id)
    await hass.async_block_till_done()

    device_registry = dr.async_get(hass)
    elke27_devices = [
        device
        for device in device_registry.devices
        if device.identifiers
        and any(identifier[0] == DOMAIN for identifier in device.identifiers)
        and device.via_device_id is None
    ]
    assert len(elke27_devices) == 2  # noqa: PLR2004
    # E2: each panel also has one area device linked to it.
    assert {
        device.via_device_id
        for device in device_registry.devices
        if device.via_device_id is not None
    } == {device.id for device in elke27_devices}
    device_keys = {next(iter(device.identifiers))[1] for device in elke27_devices}
    assert device_keys == {PANEL_SERIAL, PANEL_SERIAL_2}

    entity_registry = er.async_get(hass)
    area_entities = [
        entity
        for entity in entity_registry.entities.values()
        if entity.platform == DOMAIN and entity.domain == "alarm_control_panel"
    ]
    assert len(area_entities) == 2  # noqa: PLR2004
    assert area_entities[0].unique_id != area_entities[1].unique_id
    assert PANEL_SERIAL in area_entities[0].unique_id
    assert PANEL_SERIAL_2 in area_entities[1].unique_id

    issues = ir.async_get(hass).issues
    assert not any(
        issue.translation_key == "duplicate_unique_id"
        for issue in issues.values()
        if issue.domain == DOMAIN
    )


async def test_two_panels_without_mac_or_serial_use_entry_id_identity(
    hass: HomeAssistant, mock_client: ClientHarness
) -> None:
    """No MAC and no serial: host:port dedupe, devices and entities use entry id."""
    snapshot_a = panel_snapshot()
    mock_client.snapshot = dataclasses.replace(
        snapshot_a,
        panel=dataclasses.replace(
            snapshot_a.panel, mac=None, serial=None, panel_name="Panel A"
        ),
    )
    entry_a = MockConfigEntry(
        domain=DOMAIN,
        title="Panel A",
        unique_id=None,
        data={
            "host": HOST,
            "port": PORT,
            CONF_LINK_KEYS_JSON: LINK_KEYS_JSON,
            CONF_INTEGRATION_ID: INTEGRATION_ID,
        },
    )
    entry_a.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry_a.entry_id)
    await hass.async_block_till_done()

    snapshot_b = panel_snapshot()
    mock_client.snapshot = dataclasses.replace(
        snapshot_b,
        panel=dataclasses.replace(
            snapshot_b.panel, mac=None, serial=None, panel_name="Panel B"
        ),
    )
    entry_b = MockConfigEntry(
        domain=DOMAIN,
        title="Panel B",
        unique_id=None,
        data={
            "host": HOST_2,
            "port": PORT,
            CONF_LINK_KEYS_JSON: LINK_KEYS_JSON,
            CONF_INTEGRATION_ID: INTEGRATION_ID,
        },
    )
    entry_b.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry_b.entry_id)
    await hass.async_block_till_done()

    assert entry_a.unique_id is None
    assert entry_b.unique_id is None

    device_registry = dr.async_get(hass)
    elke27_devices = [
        device
        for device in device_registry.devices
        if device.identifiers
        and any(identifier[0] == DOMAIN for identifier in device.identifiers)
        and device.via_device_id is None
    ]
    assert len(elke27_devices) == 2  # noqa: PLR2004
    # E2: each panel also has one area device linked to it.
    assert {
        device.via_device_id
        for device in device_registry.devices
        if device.via_device_id is not None
    } == {device.id for device in elke27_devices}
    device_keys = {next(iter(device.identifiers))[1] for device in elke27_devices}
    assert device_keys == {entry_a.entry_id, entry_b.entry_id}

    entity_registry = er.async_get(hass)
    area_entities = [
        entity
        for entity in entity_registry.entities.values()
        if entity.platform == DOMAIN and entity.domain == "alarm_control_panel"
    ]
    assert len(area_entities) == 2  # noqa: PLR2004
    entity_bases = {entity.unique_id.split(":")[0] for entity in area_entities}
    assert entity_bases == {entry_a.entry_id, entry_b.entry_id}

    issues = ir.async_get(hass).issues
    assert not any(
        issue.translation_key == "duplicate_unique_id"
        for issue in issues.values()
        if issue.domain == DOMAIN
    )


async def test_setup_and_unload(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: ClientHarness
) -> None:
    """Setup connects; unload stops the coordinator and disconnects once."""
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_config_entry.state is ConfigEntryState.LOADED
    mock_client.client.async_connect.assert_awaited_once()
    assert hass.states.get("alarm_control_panel.house") is not None

    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_config_entry.state is ConfigEntryState.NOT_LOADED
    mock_client.client.async_disconnect.assert_awaited_once()


# The failed unload leaves the coordinator running, stale-check timer included.
@pytest.mark.parametrize("expected_lingering_timers", [True])
async def test_unload_platform_failure_keeps_client(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: ClientHarness
) -> None:
    """If a platform fails to unload, the client is not torn down."""
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    with patch.object(
        hass.config_entries, "async_unload_platforms", AsyncMock(return_value=False)
    ):
        assert not await hass.config_entries.async_unload(mock_config_entry.entry_id)
    assert mock_config_entry.state is ConfigEntryState.FAILED_UNLOAD
    mock_client.client.async_disconnect.assert_not_awaited()
    hub = mock_config_entry.runtime_data.coordinator
    assert hub.client is mock_client.client


async def test_setup_failure_after_connect_disconnects(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: ClientHarness
) -> None:
    """
    An unexpected failure after connecting still disconnects the client.

    The coordinator's first refresh handles unexpected errors itself (setup is
    retried); the integration adds no catch-all of its own.
    """
    mock_client.client.async_refresh_csm.side_effect = RuntimeError("boom")
    mock_config_entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY
    mock_client.client.async_disconnect.assert_awaited()


def _reauth_flows(hass: HomeAssistant) -> list[dict]:
    return [
        flow
        for flow in hass.config_entries.flow.async_progress()
        if flow["handler"] == DOMAIN and flow["context"]["source"] == SOURCE_REAUTH
    ]


def _reconnect_issue_id(entry_id: str) -> str:
    return f"{ISSUE_RECONNECT_FAILED}_{entry_id}"


HUB_LOGGER = "custom_components.elke27.coordinator"


def _reconnect_failure_log_records(
    caplog: pytest.LogCaptureFixture,
) -> list[logging.LogRecord]:
    return [
        record
        for record in caplog.records
        if record.name == HUB_LOGGER
        and "Reconnect attempt failed" in record.getMessage()
    ]


_TRANSIENT_ERRORS = [
    Elke27ConnectionError("down"),
    Elke27TimeoutError("slow"),
    E27Timeout("raw timeout"),
    OSError("unreachable"),
]


@pytest.mark.parametrize(
    ("stage", "error"),
    [
        *(
            (stage, error)
            for stage in ("connect", "refresh_csm", "refresh_domain")
            for error in _TRANSIENT_ERRORS
        ),
        ("connect", Elke27ProtocolError("garbled")),
        ("refresh_csm", Elke27ProtocolError("garbled")),
    ],
)
async def test_setup_transient_errors_retry(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: ClientHarness,
    error: Exception,
    stage: str,
) -> None:
    """Library and network errors at any setup stage retry and disconnect."""
    target = {
        "connect": mock_client.client.async_connect,
        "refresh_csm": mock_client.client.async_refresh_csm,
        "refresh_domain": mock_client.client.async_refresh_domain_config,
    }[stage]
    target.side_effect = error
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY
    assert not _reauth_flows(hass)
    if stage != "connect":
        mock_client.client.async_disconnect.assert_awaited()


@pytest.mark.parametrize(
    "error",
    [
        Elke27LinkRequiredError("relink"),
        Elke27AuthError("bad credentials"),
        Elke27CryptoError("link invalid"),
    ],
)
@pytest.mark.parametrize("stage", ["connect", "refresh_csm"])
async def test_setup_auth_errors_start_reauth(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: ClientHarness,
    error: Exception,
    stage: str,
) -> None:
    """A link the panel no longer accepts fails setup and starts reauth."""
    target = {
        "connect": mock_client.client.async_connect,
        "refresh_csm": mock_client.client.async_refresh_csm,
    }[stage]
    target.side_effect = error
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_config_entry.state is ConfigEntryState.SETUP_ERROR
    flows = _reauth_flows(hass)
    assert len(flows) == 1
    assert flows[0]["context"]["entry_id"] == mock_config_entry.entry_id


async def test_setup_bad_link_keys_start_reauth(
    hass: HomeAssistant, mock_client: ClientHarness
) -> None:
    """Unreadable stored link keys start reauth without connecting."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="00:11:22:33:44:55",
        data={
            "host": "192.0.2.10",
            "port": 2101,
            CONF_LINK_KEYS_JSON: "not a link key mapping",
            "integration_id": "123456789012",
        },
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert len(_reauth_flows(hass)) == 1
    mock_client.client.async_connect.assert_not_awaited()


async def test_setup_domain_refresh_auth_error_starts_reauth(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: ClientHarness
) -> None:
    """An auth failure while priming domains fails setup and starts reauth."""
    mock_client.client.async_refresh_domain_config.side_effect = Elke27AuthError(
        "not allowed"
    )
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_config_entry.state is ConfigEntryState.SETUP_ERROR
    assert len(_reauth_flows(hass)) == 1


async def test_setup_domain_refresh_rejection_is_tolerated(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: ClientHarness
) -> None:
    """A panel refusing one domain refresh does not block setup."""
    mock_client.client.async_refresh_domain_config.side_effect = Elke27ProtocolError(
        "refused"
    )
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_config_entry.state is ConfigEntryState.LOADED


async def test_command_auth_error_starts_reauth_once(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: ClientHarness
) -> None:
    """An auth failure from a panel command starts reauth only once."""
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    hub = mock_config_entry.runtime_data.coordinator
    mock_client.client.async_arm_area.side_effect = Elke27AuthError("bad")

    with pytest.raises(HomeAssistantError):
        await hub.async_arm_area(1, ArmMode.ARMED_AWAY, "1234")
    await hass.async_block_till_done()
    assert len(_reauth_flows(hass)) == 1

    with pytest.raises(HomeAssistantError):
        await hub.async_arm_area(1, ArmMode.ARMED_AWAY, "1234")
    await hass.async_block_till_done()
    assert len(_reauth_flows(hass)) == 1


def _backoff_delays(sleep_mock: AsyncMock) -> list[float]:
    """Backoff sleeps only (the patch is global; ignore other zero sleeps)."""
    return [c.args[0] for c in sleep_mock.await_args_list if c.args and c.args[0]]


async def test_reconnect_auth_failure_starts_reauth(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: ClientHarness,
    fast_backoff: AsyncMock,
) -> None:
    """If the panel rejects the link while reconnecting, reauth starts."""
    await setup_entry(hass, mock_config_entry)
    connects_before = mock_client.client.async_connect.await_count
    script_connect(mock_client, [Elke27LinkRequiredError("relink")])
    await disconnect_link(mock_client)
    await hass.async_block_till_done()
    assert mock_client.client.async_connect.await_count == connects_before + 1
    assert _backoff_delays(fast_backoff) == []
    assert len(_reauth_flows(hass)) == 1


async def test_reconnect_auth_failure_clears_repair_issue(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: ClientHarness,
    issue_registry: ir.IssueRegistry,
    fast_backoff: AsyncMock,
) -> None:
    """Auth refusal during reconnect clears a reconnect repair issue."""
    await setup_entry(hass, mock_config_entry)
    issue_id = _reconnect_issue_id(mock_config_entry.entry_id)
    script_connect(
        mock_client,
        [Elke27ProtocolError("p")] * RECONNECT_NON_TRANSPORT_ISSUE_THRESHOLD
        + [Elke27LinkRequiredError("relink")],
    )
    await disconnect_link(mock_client)
    await hass.async_block_till_done()
    assert issue_registry.async_get_issue(DOMAIN, issue_id) is None
    assert len(_reauth_flows(hass)) == 1


async def test_reconnect_backoff_jitter_scales_base_delay(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: ClientHarness
) -> None:
    """Reconnect sleep applies jitter between 0.8x and 1.2x of the base backoff."""
    await setup_entry(hass, mock_config_entry)
    script_connect(
        mock_client,
        [Elke27ConnectionError("down"), Elke27ConnectionError("still down"), None],
    )
    sleep_mock = AsyncMock()
    with (
        patch("custom_components.elke27.reconnect.asyncio.sleep", sleep_mock),
        patch(
            "custom_components.elke27.reconnect.random.uniform",
            side_effect=[0.8, 1.2],
        ) as uniform_mock,
    ):
        await disconnect_link(mock_client)
    uniform_mock.assert_any_call(0.8, 1.2)
    assert _backoff_delays(sleep_mock) == pytest.approx([2 * 0.8, 4 * 1.2])


async def test_reconnect_transport_failure_retries_with_backoff(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: ClientHarness,
    fast_backoff: AsyncMock,
) -> None:
    """Transport failures retry with exponential backoff until connect succeeds."""
    await setup_entry(hass, mock_config_entry)
    connects_before = mock_client.client.async_connect.await_count
    script_connect(
        mock_client, [Elke27ConnectionError("down"), Elke27TimeoutError("slow"), None]
    )
    await disconnect_link(mock_client)
    assert mock_client.client.async_connect.await_count == connects_before + 3
    assert _backoff_delays(fast_backoff) == [2, 4]


async def test_reconnect_protocol_error_retries(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: ClientHarness,
    fast_backoff: AsyncMock,
) -> None:
    """Protocol errors keep retrying with backoff and never start reauth."""
    await setup_entry(hass, mock_config_entry)
    connects_before = mock_client.client.async_connect.await_count
    script_connect(mock_client, [Elke27ProtocolError("protocol"), None])
    await disconnect_link(mock_client)
    await hass.async_block_till_done()
    assert mock_client.client.async_connect.await_count == connects_before + 2
    assert _backoff_delays(fast_backoff) == [2]
    assert len(_reauth_flows(hass)) == 0


async def test_reconnect_streak_counts_through_transport_error(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: ClientHarness,
    issue_registry: ir.IssueRegistry,
    caplog: pytest.LogCaptureFixture,
    fast_backoff: AsyncMock,
) -> None:
    """Transport between protocol errors does not reset the non-transport streak."""
    await setup_entry(hass, mock_config_entry)
    caplog.set_level(logging.DEBUG, logger=HUB_LOGGER)
    script_connect(
        mock_client,
        [
            Elke27ProtocolError("one"),
            Elke27ConnectionError("down"),
            Elke27ProtocolError("two"),
            Elke27ProtocolError("three"),
        ],
    )
    await disconnect_link(mock_client)
    issue_id = _reconnect_issue_id(mock_config_entry.entry_id)
    assert issue_registry.async_get_issue(DOMAIN, issue_id) is not None
    failure_logs = _reconnect_failure_log_records(caplog)
    warnings = [record for record in failure_logs if record.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert len(_reauth_flows(hass)) == 0
    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)


async def test_reconnect_non_transport_logs_warning_once_then_debug(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: ClientHarness,
    caplog: pytest.LogCaptureFixture,
    fast_backoff: AsyncMock,
) -> None:
    """The first failure in a non-transport streak warns; later ones are debug."""
    await setup_entry(hass, mock_config_entry)
    caplog.set_level(logging.DEBUG, logger=HUB_LOGGER)
    script_connect(
        mock_client, [Elke27ProtocolError("first"), Elke27ProtocolError("second"), None]
    )
    await disconnect_link(mock_client)
    failure_logs = _reconnect_failure_log_records(caplog)
    warnings = [record for record in failure_logs if record.levelno == logging.WARNING]
    debug_logs = [record for record in failure_logs if record.levelno == logging.DEBUG]
    assert len(warnings) == 1
    assert len(debug_logs) == 1


async def test_reconnect_raw_error_logs_traceback_once(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: ClientHarness,
    caplog: pytest.LogCaptureFixture,
    fast_backoff: AsyncMock,
) -> None:
    """A raw (non-elke27) library error has a traceback only on the first failure."""
    await setup_entry(hass, mock_config_entry)
    caplog.set_level(logging.DEBUG, logger=HUB_LOGGER)
    script_connect(mock_client, [E27Error("boom"), E27Error("again"), None])
    await disconnect_link(mock_client)
    failure_logs = _reconnect_failure_log_records(caplog)
    warnings = [record for record in failure_logs if record.levelno == logging.WARNING]
    debug_logs = [record for record in failure_logs if record.levelno == logging.DEBUG]
    assert len(warnings) == 1
    assert warnings[0].exc_info is not None
    assert len(debug_logs) == 1
    assert debug_logs[0].exc_info is None


async def test_remove_entry_clears_reconnect_repair_issue(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: ClientHarness,
    issue_registry: ir.IssueRegistry,
    fast_backoff: AsyncMock,
) -> None:
    """Removing an entry clears reconnect repair state even if unload failed."""
    await setup_entry(hass, mock_config_entry)
    script_connect(
        mock_client,
        [Elke27ProtocolError("p")] * RECONNECT_NON_TRANSPORT_ISSUE_THRESHOLD,
    )
    await disconnect_link(mock_client)
    issue_id = _reconnect_issue_id(mock_config_entry.entry_id)
    assert issue_registry.async_get_issue(DOMAIN, issue_id) is not None

    await async_remove_entry(hass, mock_config_entry)
    assert issue_registry.async_get_issue(DOMAIN, issue_id) is None
    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)


@pytest.mark.parametrize(
    "failures",
    [
        RECONNECT_NON_TRANSPORT_ISSUE_THRESHOLD - 1,
        RECONNECT_NON_TRANSPORT_ISSUE_THRESHOLD,
    ],
)
async def test_reconnect_repair_issue_after_non_transport_threshold(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: ClientHarness,
    issue_registry: ir.IssueRegistry,
    fast_backoff: AsyncMock,
    failures: int,
) -> None:
    """Two non-transport failures raise no issue; the third does."""
    await setup_entry(hass, mock_config_entry)
    script_connect(mock_client, [Elke27ProtocolError("protocol")] * failures)
    await disconnect_link(mock_client)
    issue_id = _reconnect_issue_id(mock_config_entry.entry_id)
    issue = issue_registry.async_get_issue(DOMAIN, issue_id)
    assert (issue is not None) is (failures >= RECONNECT_NON_TRANSPORT_ISSUE_THRESHOLD)
    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)


async def test_reconnect_transport_failures_never_raise_issue(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: ClientHarness,
    issue_registry: ir.IssueRegistry,
    fast_backoff: AsyncMock,
) -> None:
    """Ten transport failures in a row raise no Repairs issue; backoff caps at 60 s."""
    await setup_entry(hass, mock_config_entry)
    script_connect(mock_client, [Elke27ConnectionError("down")] * 10)
    await disconnect_link(mock_client)
    issue_id = _reconnect_issue_id(mock_config_entry.entry_id)
    assert issue_registry.async_get_issue(DOMAIN, issue_id) is None
    delays = _backoff_delays(fast_backoff)
    assert delays == [min(60, 2**n) for n in range(1, 11)]
    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)


async def test_reconnect_repair_issue_cleared_on_success(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: ClientHarness,
    issue_registry: ir.IssueRegistry,
    fast_backoff: AsyncMock,
) -> None:
    """A successful reconnect clears the repair issue."""
    await setup_entry(hass, mock_config_entry)
    script_connect(mock_client, [Elke27ProtocolError("protocol")] * 3 + [None])
    issue_id = _reconnect_issue_id(mock_config_entry.entry_id)
    await disconnect_link(mock_client)
    assert issue_registry.async_get_issue(DOMAIN, issue_id) is None
    assert len(_reauth_flows(hass)) == 0


async def test_reconnect_repair_issue_cleared_on_unload(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: ClientHarness,
    issue_registry: ir.IssueRegistry,
    fast_backoff: AsyncMock,
) -> None:
    """Unload removes the reconnect repair issue."""
    await setup_entry(hass, mock_config_entry)
    script_connect(
        mock_client,
        [Elke27ProtocolError("p")] * RECONNECT_NON_TRANSPORT_ISSUE_THRESHOLD,
    )
    await disconnect_link(mock_client)
    issue_id = _reconnect_issue_id(mock_config_entry.entry_id)
    assert issue_registry.async_get_issue(DOMAIN, issue_id) is not None

    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert issue_registry.async_get_issue(DOMAIN, issue_id) is None


async def test_connect_cancellation_disconnects_client(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: ClientHarness
) -> None:
    """A cancelled connect tears down the half-open client."""
    connect_started = asyncio.Event()
    release_connect = asyncio.Event()

    async def _slow_connect(*_args: object, **_kwargs: object) -> None:
        connect_started.set()
        await release_connect.wait()

    mock_client.client.async_connect.side_effect = _slow_connect
    mock_config_entry.add_to_hass(hass)
    setup_task = asyncio.create_task(
        hass.config_entries.async_setup(mock_config_entry.entry_id)
    )
    await connect_started.wait()
    setup_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await setup_task
    mock_client.client.async_disconnect.assert_awaited()


async def test_reconnect_listener_removed_on_unload(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: ClientHarness
) -> None:
    """The supervisor reports reconnects straight to the coordinator (D3)."""
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    hub = mock_config_entry.runtime_data.coordinator
    assert hub.reconnect._on_reconnected == hub._handle_reconnected

    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert hub.reconnect.task is None or hub.reconnect.task.done()


def _connection_lost(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [
        r
        for r in caplog.records
        if r.getMessage() == "Panel connection lost"
        and r.name == "custom_components.elke27.coordinator"
    ]


@pytest.mark.parametrize("retries", [2, 6])
async def test_link_drop_logs_warning_once(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: ClientHarness,
    fast_backoff: AsyncMock,
    caplog: pytest.LogCaptureFixture,
    retries: int,
) -> None:
    """A link drop is logged once at WARNING, however many retries fail (#80, #83)."""
    await setup_entry(hass, mock_config_entry)
    script_connect(mock_client, [Elke27ConnectionError("down")] * retries)
    await disconnect_link(mock_client)
    await disconnect_link(mock_client)
    assert [r.levelno for r in _connection_lost(caplog)] == [logging.WARNING]
    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)


async def test_second_link_drop_logs_second_warning_and_restore_is_info(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: ClientHarness,
    fast_backoff: AsyncMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Drop, reconnect, drop again: two WARNINGs and one INFO "restored" (#83)."""
    caplog.set_level(logging.DEBUG)
    await setup_entry(hass, mock_config_entry)
    script_connect(mock_client, [Elke27ConnectionError("down"), None])
    await disconnect_link(mock_client)
    restored = [
        r for r in caplog.records if r.getMessage() == "Panel connection restored"
    ]
    assert [r.levelno for r in restored] == [logging.INFO]
    script_connect(mock_client, [])
    await disconnect_link(mock_client)
    levels = [r.levelno for r in _connection_lost(caplog)]
    assert levels == [logging.WARNING, logging.WARNING]
    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)


async def test_link_drop_warning_visible_at_default_level(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: ClientHarness,
    fast_backoff: AsyncMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """At HA's default WARNING level the drop is still recorded (#83)."""
    caplog.set_level(logging.WARNING)
    await setup_entry(hass, mock_config_entry)
    script_connect(mock_client, [])
    await disconnect_link(mock_client)
    assert len(_connection_lost(caplog)) == 1
    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)


async def test_unload_does_not_log_connection_lost(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: ClientHarness,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An intentional disconnect on unload is not logged as a lost connection."""
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert "Panel connection lost" not in caplog.text


async def test_reconnect_task_cancelled_on_unload(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: ClientHarness
) -> None:
    """The reconnect loop is an entry task, so unload cancels it."""
    await setup_entry(hass, mock_config_entry)
    marks = script_connect(mock_client, [])
    await disconnect_link(mock_client)
    assert marks == ["blocked"]

    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert marks == ["blocked", "cancelled"]


async def test_second_disconnect_starts_no_second_loop(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: ClientHarness
) -> None:
    """A disconnect while a reconnect is already running starts no second loop."""
    await setup_entry(hass, mock_config_entry)
    marks = script_connect(mock_client, [])
    await disconnect_link(mock_client)
    await disconnect_link(mock_client)
    assert marks == ["blocked"]
    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)


@pytest.mark.parametrize("code", ["abc", "12a4", ""])
def test_non_numeric_alarm_code_is_validation_error(code: str) -> None:
    """A non-numeric alarm code is a user input error, not a panel failure."""
    with pytest.raises(ServiceValidationError):
        _normalize_code(code)


def test_numeric_alarm_code_is_stripped() -> None:
    """Surrounding whitespace is removed from a numeric code."""
    assert _normalize_code(" 1234 ") == "1234"


ALARM = "alarm_control_panel.house"


async def _disarm(hass: HomeAssistant, code: str) -> None:
    await hass.services.async_call(
        "alarm_control_panel",
        "alarm_disarm",
        {"entity_id": ALARM, "code": code},
        blocking=True,
    )


@pytest.mark.parametrize(
    ("code", "key"),
    [
        ("0", "code_all_zero"),
        ("0000", "code_all_zero"),
        (" 000000 ", "code_all_zero"),
        ("0" * 5000, "code_all_zero"),
        ("\u00b2", "code_not_numeric"),
        ("\uff11\uff12\uff13\uff14", "code_not_numeric"),
    ],
)
async def test_disarm_service_refuses_invalid_code_before_sending(
    hass: HomeAssistant, setup: Setup, code: str, key: str
) -> None:
    """An all-zero or non-ASCII code is a 400 validation error, never sent (#90)."""
    client = FakeClient(command_result(refresh=True))
    await setup(client)
    with pytest.raises(ServiceValidationError) as err:
        await _disarm(hass, code)
    assert err.value.translation_key == key
    assert client.keys({"disarm"}) == []


async def test_disarm_service_sends_code_with_zeros_but_not_all_zero(
    hass: HomeAssistant, setup: Setup
) -> None:
    """Leading or embedded zeros alone are not refused: "1000" reaches the client."""
    client = FakeClient(command_result(refresh=True))
    await setup(client)
    await _disarm(hass, "1000")
    assert [(key, params.get("pin")) for key, params in client.calls] == [
        ("disarm", "1000")
    ]
