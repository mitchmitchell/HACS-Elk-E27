# ruff: noqa: S101, D103, TC001, TC002
"""
Alarm panel service and state tests on a loaded entry (mocked elke27 client).

From Tester's feature-map coverage pass (2026-10-17). The all-zero code test
pins #90 (fixed by #94).
"""

from __future__ import annotations

import asyncio
from typing import Any

from elke27_lib import AreaState, ArmMode, ZoneState
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.const import ATTR_CODE, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from tests.conftest import (
    ClientHarness,
    FakeClient,
    Setup,
    command_result,
    link_event,
    settle,
    snapshot_with,
)

ALARM = "alarm_control_panel.house"


def _area(**kw: Any) -> AreaState:
    return AreaState(area_id=1, name="House", ready=kw.pop("ready", True), **kw)


# --- Alarm state ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("area_kw", "expected"),
    [
        ({"arm_mode": ArmMode.DISARMED}, "disarmed"),
        ({"arm_mode": None}, "disarmed"),
        ({"arm_mode": ArmMode.ARMED_AWAY}, "armed_away"),
        ({"arm_mode": ArmMode.ARMED_STAY}, "armed_home"),
        ({"arm_mode": ArmMode.ARMED_NIGHT}, "armed_night"),
        ({"arm_mode": ArmMode.ARMED_AWAY, "alarm_active": True}, "triggered"),
        ({"arm_mode": ArmMode.DISARMED, "alarm_active": True}, "triggered"),
    ],
)
async def test_alarm_state_published_to_hass(
    hass: HomeAssistant, setup: Setup, area_kw: dict, expected: str
) -> None:
    client = FakeClient(command_result(refresh=True))
    coordinator = await setup(client)
    snap = snapshot_with(client.snapshot, areas={1: _area(**area_kw)})
    client.snapshot = snap
    coordinator.async_set_updated_data(snap)
    await hass.async_block_till_done()
    assert hass.states.get(ALARM).state == expected


# --- Arm custom bypass (not verified) ----------------------------------------


async def test_custom_bypass_service_bypasses_open_zone_then_arms(
    hass: HomeAssistant, setup: Setup
) -> None:
    client = FakeClient(command_result(refresh=True))
    client.snapshot = snapshot_with(
        client.snapshot,
        areas={1: _area(ready=False)},
        zones={
            1: ZoneState(zone_id=1, name="Front Door", area_id=1, open=True),
            2: ZoneState(zone_id=2, name="Hall", area_id=1, open=False),
        },
    )
    await setup(client)
    await hass.services.async_call(
        "alarm_control_panel",
        "alarm_arm_custom_bypass",
        {"entity_id": ALARM, ATTR_CODE: "1234"},
        blocking=True,
    )
    writes = [(k, p) for k, p in client.calls if k in {"bypass", "arm"}]
    assert writes[0][0] == "bypass"
    assert writes[0][1]["zone_id"] == 1
    assert [k for k, _ in writes] == ["bypass", "arm"]


async def test_custom_bypass_rejects_non_numeric_code_before_send(
    hass: HomeAssistant, setup: Setup
) -> None:
    client = FakeClient(command_result(refresh=True))
    await setup(client)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            "alarm_control_panel",
            "alarm_arm_custom_bypass",
            {"entity_id": ALARM, ATTR_CODE: "12ab"},
            blocking=True,
        )
    assert client.keys({"bypass", "arm"}) == []


# --- Disarm clears bypasses (not verified) -----------------------------------


async def test_disarm_of_bypassed_armed_area_follows_panel_state(
    hass: HomeAssistant, setup: Setup
) -> None:
    client = FakeClient(command_result(refresh=True))
    client.snapshot = snapshot_with(
        client.snapshot,
        areas={1: _area(arm_mode=ArmMode.ARMED_AWAY)},
        zones={1: ZoneState(zone_id=1, name="Front Door", area_id=1, bypassed=True)},
    )
    coordinator = await setup(client)
    assert hass.states.get(ALARM).state == "armed_away"
    await hass.services.async_call(
        "alarm_control_panel",
        "alarm_disarm",
        {"entity_id": ALARM, ATTR_CODE: "1234"},
        blocking=True,
    )
    assert client.keys({"disarm"}) == ["disarm"]
    # The panel disarms (and clears bypasses); HA follows the next snapshot.
    snap = snapshot_with(
        client.snapshot,
        areas={1: _area(arm_mode=ArmMode.DISARMED)},
        zones={1: ZoneState(zone_id=1, name="Front Door", area_id=1, bypassed=False)},
    )
    coordinator.async_set_updated_data(snap)
    await hass.async_block_till_done()
    assert hass.states.get(ALARM).state == "disarmed"


# --- Code validation (#90) ---------------------------------------------------


@pytest.mark.parametrize("code", ["0000", "000000"])
async def test_all_zero_code_rejected_before_send(
    hass: HomeAssistant, setup: Setup, code: str
) -> None:
    client = FakeClient(command_result(refresh=True))
    await setup(client)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            "alarm_control_panel",
            "alarm_disarm",
            {"entity_id": ALARM, ATTR_CODE: code},
            blocking=True,
        )
    assert client.keys({"disarm"}) == []


async def test_padded_numeric_code_is_stripped(
    hass: HomeAssistant, setup: Setup
) -> None:
    client = FakeClient(command_result(refresh=True))
    await setup(client)
    await hass.services.async_call(
        "alarm_control_panel",
        "alarm_disarm",
        {"entity_id": ALARM, ATTR_CODE: " 1234 "},
        blocking=True,
    )
    disarms = [p for k, p in client.calls if k == "disarm"]
    assert len(disarms) == 1
    assert "1234" in disarms[0].values()


# --- Disarm while the link is down (#80: HA skips unavailable entities) ---------


async def test_disarm_after_link_drop_sends_nothing(
    hass: HomeAssistant,
    setup: Setup,
    mock_client: ClientHarness,
    mock_config_entry: MockConfigEntry,
) -> None:
    client = FakeClient(command_result(refresh=True))
    coordinator = await setup(client)

    async def _unreachable(*_args: object, **_kwargs: object) -> None:
        await asyncio.Event().wait()

    # The link drops: the client is no longer ready and reports DISCONNECTED; the
    # panel stays unreachable, so the reconnect loop just waits.
    mock_client.client.async_connect.side_effect = _unreachable
    mock_client.client.is_ready = False
    listener = mock_client.client.subscribe.call_args.args[0]
    listener(link_event(connected=False))
    await settle()
    # The next coordinator update after the drop publishes the new availability.
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(ALARM).state == STATE_UNAVAILABLE
    await hass.services.async_call(
        "alarm_control_panel",
        "alarm_disarm",
        {"entity_id": ALARM, ATTR_CODE: "1234"},
        blocking=True,
    )
    assert client.keys({"disarm"}) == []
    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
