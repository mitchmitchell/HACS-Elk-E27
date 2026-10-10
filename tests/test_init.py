# ruff: noqa: S101, SLF001, TC001, ARG001
"""Tests for Elke27 setup and unload."""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, patch

from elke27_lib import ArmMode
from elke27_lib.errors import (
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
    CONF_LINK_KEYS_JSON,
    DOMAIN,
    ISSUE_RECONNECT_FAILED,
    RECONNECT_NON_TRANSPORT_ISSUE_THRESHOLD,
)
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import issue_registry as ir
from tests.conftest import ClientHarness


def _reconnect_issue_id(entry_id: str) -> str:
    return f"{ISSUE_RECONNECT_FAILED}_{entry_id}"


HUB_LOGGER = "custom_components.elke27.hub"


def _reconnect_failure_log_records(
    caplog: pytest.LogCaptureFixture,
) -> list[logging.LogRecord]:
    return [
        record
        for record in caplog.records
        if record.name == HUB_LOGGER
        and "Reconnect attempt failed" in record.getMessage()
    ]


async def test_setup_and_unload(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: ClientHarness
) -> None:
    """Setup connects; unload stops the coordinator and disconnects once."""
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_config_entry.state is ConfigEntryState.LOADED
    mock_client.client.async_connect.assert_awaited_once()
    assert hass.states.get("alarm_control_panel.test_panel_house") is not None

    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_config_entry.state is ConfigEntryState.NOT_LOADED
    mock_client.client.async_disconnect.assert_awaited_once()


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
    hub = mock_config_entry.runtime_data.hub
    assert hub.client is mock_client.client


async def test_setup_failure_after_connect_disconnects(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: ClientHarness
) -> None:
    """A failure after connecting still disconnects the client."""
    mock_client.client.async_refresh_csm.side_effect = RuntimeError("boom")
    mock_config_entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_config_entry.state is ConfigEntryState.SETUP_ERROR
    mock_client.client.async_disconnect.assert_awaited()


def _reauth_flows(hass: HomeAssistant) -> list[dict]:
    return [
        flow
        for flow in hass.config_entries.flow.async_progress()
        if flow["handler"] == DOMAIN and flow["context"]["source"] == SOURCE_REAUTH
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


@pytest.mark.parametrize("link_keys_json", ["", "not a link key mapping"])
async def test_setup_bad_link_keys_start_reauth(
    hass: HomeAssistant, mock_client: ClientHarness, link_keys_json: str
) -> None:
    """Missing or unreadable stored link keys start reauth without connecting."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="00:11:22:33:44:55",
        data={
            "host": "192.0.2.10",
            "port": 2101,
            CONF_LINK_KEYS_JSON: link_keys_json,
            "integration_serial": "123456789012",
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
    hub = mock_config_entry.runtime_data.hub
    mock_client.client.async_arm_area.side_effect = Elke27AuthError("bad")

    with pytest.raises(HomeAssistantError):
        await hub.async_arm_area(1, ArmMode.ARMED_AWAY, "1234")
    await hass.async_block_till_done()
    assert len(_reauth_flows(hass)) == 1

    with pytest.raises(HomeAssistantError):
        await hub.async_arm_area(1, ArmMode.ARMED_AWAY, "1234")
    await hass.async_block_till_done()
    assert len(_reauth_flows(hass)) == 1


async def test_reconnect_auth_failure_starts_reauth(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: ClientHarness
) -> None:
    """If the panel rejects the link while reconnecting, reauth starts."""
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    hub = mock_config_entry.runtime_data.hub

    connects_before = mock_client.client.async_connect.await_count
    mock_client.client.async_connect.side_effect = Elke27LinkRequiredError("relink")
    hub._reconnect_attempts = 3
    await hub._async_reconnect_loop()
    await hass.async_block_till_done()
    assert mock_client.client.async_connect.await_count == connects_before + 1
    assert hub._reconnect_attempts == 0
    assert len(_reauth_flows(hass)) == 1


async def test_reconnect_auth_failure_clears_repair_issue(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: ClientHarness,
    issue_registry: ir.IssueRegistry,
) -> None:
    """Auth refusal during reconnect clears a stale reconnect repair issue."""
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    hub = mock_config_entry.runtime_data.hub
    hub._ensure_reconnect_repair_issue()
    issue_id = _reconnect_issue_id(mock_config_entry.entry_id)
    assert issue_registry.async_get_issue(DOMAIN, issue_id) is not None

    mock_client.client.async_connect.side_effect = Elke27LinkRequiredError("relink")
    await hub._async_reconnect_loop()
    await hass.async_block_till_done()
    assert issue_registry.async_get_issue(DOMAIN, issue_id) is None
    assert len(_reauth_flows(hass)) == 1


async def test_reconnect_auth_failure_starts_reauth_once(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: ClientHarness
) -> None:
    """Repeated reconnect auth failures start reauth only once."""
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    hub = mock_config_entry.runtime_data.hub

    mock_client.client.async_connect.side_effect = Elke27AuthError("bad")
    await hub._async_reconnect_loop()
    await hub._async_reconnect_loop()
    await hass.async_block_till_done()
    assert len(_reauth_flows(hass)) == 1


async def test_reconnect_backoff_jitter_scales_base_delay(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: ClientHarness
) -> None:
    """Reconnect sleep applies jitter between 0.8x and 1.2x of the base backoff."""
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    hub = mock_config_entry.runtime_data.hub
    hub._stopping = False

    mock_client.client.async_connect.side_effect = [
        Elke27ConnectionError("down"),
        Elke27ConnectionError("still down"),
        None,
    ]
    mock_client.client.wait_ready.return_value = True
    sleep_mock = AsyncMock()
    with (
        patch("custom_components.elke27.hub.asyncio.sleep", sleep_mock),
        patch(
            "custom_components.elke27.hub.random.uniform",
            side_effect=[0.8, 1.2],
        ) as uniform_mock,
    ):
        await hub._async_reconnect_loop()
    uniform_mock.assert_any_call(0.8, 1.2)
    sleep_mock.assert_any_await(2 * 0.8)
    sleep_mock.assert_any_await(4 * 1.2)


async def test_reconnect_transport_failure_retries_with_backoff(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: ClientHarness
) -> None:
    """Transport failures retry with exponential backoff until connect succeeds."""
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    hub = mock_config_entry.runtime_data.hub
    hub._stopping = False

    connects_before = mock_client.client.async_connect.await_count
    mock_client.client.async_connect.side_effect = [
        Elke27ConnectionError("down"),
        Elke27TimeoutError("slow"),
        None,
    ]
    mock_client.client.wait_ready.return_value = True
    sleep_mock = AsyncMock()
    with (
        patch("custom_components.elke27.hub.asyncio.sleep", sleep_mock),
        patch("custom_components.elke27.hub.random.uniform", return_value=1.0),
    ):
        await hub._async_reconnect_loop()
    transport_failures = 2
    assert (
        mock_client.client.async_connect.await_count
        == connects_before + transport_failures + 1
    )
    assert sleep_mock.await_count == transport_failures
    sleep_mock.assert_any_await(2)
    sleep_mock.assert_any_await(4)


async def test_reconnect_protocol_error_retries(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: ClientHarness
) -> None:
    """Protocol errors keep retrying with backoff and never start reauth."""
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    hub = mock_config_entry.runtime_data.hub
    hub._stopping = False

    connects_before = mock_client.client.async_connect.await_count
    mock_client.client.async_connect.side_effect = [
        Elke27ProtocolError("protocol"),
        None,
    ]
    mock_client.client.wait_ready.return_value = True
    sleep_mock = AsyncMock()
    with (
        patch("custom_components.elke27.hub.asyncio.sleep", sleep_mock),
        patch("custom_components.elke27.hub.random.uniform", return_value=1.0),
    ):
        await hub._async_reconnect_loop()
    await hass.async_block_till_done()
    assert mock_client.client.async_connect.await_count == connects_before + 2
    sleep_mock.assert_awaited_once_with(2)
    assert len(_reauth_flows(hass)) == 0


async def test_reconnect_streak_counts_through_transport_error(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: ClientHarness,
    issue_registry: ir.IssueRegistry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Transport between protocol errors does not reset the non-transport streak."""
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    hub = mock_config_entry.runtime_data.hub
    hub._stopping = False

    reconnect_errors: list[Exception] = [
        Elke27ProtocolError("one"),
        Elke27ConnectionError("down"),
        Elke27ProtocolError("two"),
        Elke27ProtocolError("three"),
    ]

    async def _connect_with_stop(*_args: object, **_kwargs: object) -> None:
        err = reconnect_errors.pop(0)
        if not reconnect_errors:
            hub._stopping = True
        raise err

    mock_client.client.async_connect.side_effect = _connect_with_stop
    caplog.set_level(logging.DEBUG, logger=HUB_LOGGER)
    with (
        patch("custom_components.elke27.hub.asyncio.sleep", AsyncMock()),
        patch("custom_components.elke27.hub.random.uniform", return_value=1.0),
    ):
        await hub._async_reconnect_loop()
    issue_id = _reconnect_issue_id(mock_config_entry.entry_id)
    assert issue_registry.async_get_issue(DOMAIN, issue_id) is not None
    failure_logs = _reconnect_failure_log_records(caplog)
    warnings = [record for record in failure_logs if record.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert len(_reauth_flows(hass)) == 0


async def test_reconnect_non_transport_logs_warning_once_then_debug(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: ClientHarness,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The first failure in a non-transport streak warns; later ones are debug."""
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    hub = mock_config_entry.runtime_data.hub
    hub._stopping = False

    mock_client.client.async_connect.side_effect = [
        Elke27ProtocolError("first"),
        Elke27ProtocolError("second"),
        None,
    ]
    mock_client.client.wait_ready.return_value = True
    caplog.set_level(logging.DEBUG, logger=HUB_LOGGER)
    with (
        patch("custom_components.elke27.hub.asyncio.sleep", AsyncMock()),
        patch("custom_components.elke27.hub.random.uniform", return_value=1.0),
    ):
        await hub._async_reconnect_loop()
    failure_logs = _reconnect_failure_log_records(caplog)
    warnings = [record for record in failure_logs if record.levelno == logging.WARNING]
    debug_logs = [record for record in failure_logs if record.levelno == logging.DEBUG]
    assert len(warnings) == 1
    assert len(debug_logs) == 1


async def test_reconnect_unknown_error_logs_traceback_once(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: ClientHarness,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Unknown reconnect errors include a traceback only on the first streak failure."""
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    hub = mock_config_entry.runtime_data.hub
    hub._stopping = False

    mock_client.client.async_connect.side_effect = [
        RuntimeError("boom"),
        RuntimeError("again"),
        None,
    ]
    mock_client.client.wait_ready.return_value = True
    caplog.set_level(logging.DEBUG, logger=HUB_LOGGER)
    with (
        patch("custom_components.elke27.hub.asyncio.sleep", AsyncMock()),
        patch("custom_components.elke27.hub.random.uniform", return_value=1.0),
    ):
        await hub._async_reconnect_loop()
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
) -> None:
    """Removing an entry clears reconnect repair state even if unload failed."""
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    hub = mock_config_entry.runtime_data.hub
    hub._ensure_reconnect_repair_issue()
    issue_id = _reconnect_issue_id(mock_config_entry.entry_id)
    assert issue_registry.async_get_issue(DOMAIN, issue_id) is not None

    await async_remove_entry(hass, mock_config_entry)
    assert issue_registry.async_get_issue(DOMAIN, issue_id) is None


async def test_reconnect_repair_issue_after_non_transport_threshold(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: ClientHarness,
    issue_registry: ir.IssueRegistry,
) -> None:
    """Three consecutive non-transport failures create a repair issue."""
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    hub = mock_config_entry.runtime_data.hub
    hub._stopping = False

    mock_client.client.async_connect.side_effect = Elke27ProtocolError("protocol")
    sleep_calls = 0

    async def _stop_after_third_sleep(_delay: float) -> None:
        nonlocal sleep_calls
        sleep_calls += 1
        if sleep_calls >= RECONNECT_NON_TRANSPORT_ISSUE_THRESHOLD:
            hub._stopping = True

    with (
        patch(
            "custom_components.elke27.hub.asyncio.sleep",
            side_effect=_stop_after_third_sleep,
        ),
        patch("custom_components.elke27.hub.random.uniform", return_value=1.0),
    ):
        await hub._async_reconnect_loop()
    issue_id = _reconnect_issue_id(mock_config_entry.entry_id)
    assert issue_registry.async_get_issue(DOMAIN, issue_id) is not None


async def test_reconnect_repair_issue_cleared_on_success(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: ClientHarness,
    issue_registry: ir.IssueRegistry,
) -> None:
    """A successful reconnect clears the repair issue."""
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    hub = mock_config_entry.runtime_data.hub
    hub._stopping = False

    mock_client.client.async_connect.side_effect = [
        Elke27ProtocolError("protocol"),
        Elke27ProtocolError("protocol"),
        Elke27ProtocolError("protocol"),
        None,
    ]
    mock_client.client.wait_ready.return_value = True
    with (
        patch("custom_components.elke27.hub.asyncio.sleep", AsyncMock()),
        patch("custom_components.elke27.hub.random.uniform", return_value=1.0),
    ):
        await hub._async_reconnect_loop()
    issue_id = _reconnect_issue_id(mock_config_entry.entry_id)
    assert issue_registry.async_get_issue(DOMAIN, issue_id) is None


async def test_reconnect_repair_issue_cleared_on_unload(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: ClientHarness,
    issue_registry: ir.IssueRegistry,
) -> None:
    """Unload removes the reconnect repair issue."""
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    hub = mock_config_entry.runtime_data.hub
    hub._ensure_reconnect_repair_issue()
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
    """Unload removes the coordinator's reconnect listener from the hub."""
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    hub = mock_config_entry.runtime_data.hub
    assert len(hub._reconnect_listeners) == 1

    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert hub._reconnect_listeners == []


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
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    hub = mock_config_entry.runtime_data.hub

    mock_client.client.async_connect.side_effect = Elke27ConnectionError("down")
    hub._schedule_reconnect()
    task = hub._reconnect_task
    assert task is not None
    assert task in mock_config_entry._background_tasks
    await hass.async_block_till_done(wait_background_tasks=False)

    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert task.done()


@pytest.mark.parametrize("code", ["abc", "12a4", ""])
def test_non_numeric_alarm_code_is_validation_error(code: str) -> None:
    """A non-numeric alarm code is a user input error, not a panel failure."""
    with pytest.raises(ServiceValidationError):
        _normalize_code(code)


def test_numeric_alarm_code_is_stripped() -> None:
    """Surrounding whitespace is removed from a numeric code."""
    assert _normalize_code(" 1234 ") == "1234"
