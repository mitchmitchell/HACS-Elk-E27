# ruff: noqa: S101, D103, PLR2004, TC001, TC002, ARG001
"""
Feature-map coverage expansion (Tester, 2026-10-17, hardware-free).

Maps to grokbots/dev/feature-map/README.md rows that were not verified or
PARTIAL on hardware. Everything runs against a mocked elke27 client.
xfail rows (all strict) describe the expected behaviour for open issues
(#89, #90, #91); drop a marker when its fix lands.
"""

from __future__ import annotations

import contextlib
import dataclasses
from types import MappingProxyType
from typing import Any
from unittest.mock import AsyncMock

from elke27_lib import AreaState, ArmMode, LightState, ThermostatState, ZoneState
from elke27_lib.client import Result
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.const import ATTR_CODE, STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from tests.conftest import ClientHarness, FakeClient, Setup, command_result, settle

ALARM = "alarm_control_panel.house"


def _area(**kw: Any) -> AreaState:
    return AreaState(area_id=1, name="House", ready=kw.pop("ready", True), **kw)


def _with(snapshot: Any, **kw: Any) -> Any:
    return dataclasses.replace(
        snapshot, **{k: MappingProxyType(v) for k, v in kw.items()}
    )


def _ids(hass: HomeAssistant, domain: str) -> list[str]:
    return [s.entity_id for s in hass.states.async_all(domain)]


# --- States + attributes (PARTIAL: needs arming on hardware) -----------------


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
    snap = _with(client.snapshot, areas={1: _area(**area_kw)})
    client.snapshot = snap
    coordinator.async_set_updated_data(snap)
    await hass.async_block_till_done()
    assert hass.states.get(ALARM).state == expected


async def test_attributes_list_faulted_zones_and_ready(
    hass: HomeAssistant, setup: Setup
) -> None:
    client = FakeClient(command_result(refresh=True))
    coordinator = await setup(client)
    snap = _with(
        client.snapshot,
        areas={1: _area(ready=False, arm_mode=ArmMode.DISARMED)},
        zones={
            1: ZoneState(zone_id=1, name="Front Door", area_id=1, open=True),
            2: ZoneState(zone_id=2, name="Back Door", area_id=1, open=False),
        },
    )
    coordinator.async_set_updated_data(snap)
    await hass.async_block_till_done()
    attrs = hass.states.get(ALARM).attributes
    assert attrs["ready"] is False
    assert attrs["faulted_zone_ids"] == [1]
    assert attrs["faulted_zones"] == ["Front Door"]


# --- Arm custom bypass (not verified) ----------------------------------------


async def test_custom_bypass_service_bypasses_open_zone_then_arms(
    hass: HomeAssistant, setup: Setup
) -> None:
    client = FakeClient(command_result(refresh=True))
    client.snapshot = _with(
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


async def test_disarm_then_panel_clears_bypass_attribute(
    hass: HomeAssistant, setup: Setup
) -> None:
    client = FakeClient(command_result(refresh=True))
    client.snapshot = _with(
        client.snapshot,
        areas={1: _area(arm_mode=ArmMode.ARMED_AWAY)},
        zones={1: ZoneState(zone_id=1, name="Front Door", area_id=1, bypassed=True)},
    )
    coordinator = await setup(client)
    zone = next(e for e in _ids(hass, "binary_sensor") if "front_door" in e)
    assert hass.states.get(zone).attributes["bypassed"] is True
    await hass.services.async_call(
        "alarm_control_panel",
        "alarm_disarm",
        {"entity_id": ALARM, ATTR_CODE: "1234"},
        blocking=True,
    )
    assert client.keys({"disarm"}) == ["disarm"]
    # The panel clears bypasses on disarm; HA must follow the next snapshot.
    snap = _with(
        client.snapshot,
        areas={1: _area(arm_mode=ArmMode.DISARMED)},
        zones={1: ZoneState(zone_id=1, name="Front Door", area_id=1, bypassed=False)},
    )
    coordinator.async_set_updated_data(snap)
    await hass.async_block_till_done()
    assert hass.states.get(zone).attributes["bypassed"] is False
    assert hass.states.get(ALARM).state == "disarmed"


# --- Code validation (#90) ---------------------------------------------------


@pytest.mark.xfail(
    reason="HACS #90: all-zero code should be a clean 400, not sent", strict=True
)
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


# --- #80 disarm while unavailable -------------------------------------------


async def test_disarm_while_unavailable_sends_nothing(
    hass: HomeAssistant, setup: Setup
) -> None:
    client = FakeClient(command_result(refresh=True))
    coordinator = await setup(client)
    coordinator.last_update_success = False
    coordinator.async_update_listeners()
    await hass.async_block_till_done()
    assert hass.states.get(ALARM).state == STATE_UNAVAILABLE
    with contextlib.suppress(HomeAssistantError):
        await hass.services.async_call(
            "alarm_control_panel",
            "alarm_disarm",
            {"entity_id": ALARM, ATTR_CODE: "1234"},
            blocking=True,
        )
    assert client.keys({"disarm"}) == []


# --- HVAC + fan modes --------------------------------------------------------


@pytest.fixture
async def tstat_loaded(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: ClientHarness
) -> Any:
    mock_client.snapshot = _with(
        mock_client.snapshot,
        thermostats={
            1: ThermostatState(
                tstat_id=1,
                name="Hall",
                temperature=70,
                heat_setpoint=68,
                cool_setpoint=76,
                mode="HEAT",
                fan_mode="AUTO",
            )
        },
    )
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    coordinator = mock_config_entry.runtime_data.coordinator
    coordinator.async_set_tstat_status = AsyncMock(return_value=True)
    return coordinator


@pytest.mark.parametrize(
    ("hvac", "sent"),
    [("off", "OFF"), ("heat", "HEAT"), ("cool", "COOL"), ("heat_cool", "AUTO")],
)
async def test_hvac_mode_service_maps_to_panel(
    hass: HomeAssistant, tstat_loaded: Any, hvac: str, sent: str
) -> None:
    (entity,) = _ids(hass, "climate")
    await hass.services.async_call(
        "climate",
        "set_hvac_mode",
        {"entity_id": entity, "hvac_mode": hvac},
        blocking=True,
    )
    tstat_loaded.async_set_tstat_status.assert_awaited_once_with(1, mode=sent)


@pytest.mark.parametrize(("fan", "sent"), [("auto", "AUTO"), ("on", "ON")])
async def test_fan_mode_service_maps_to_panel(
    hass: HomeAssistant, tstat_loaded: Any, fan: str, sent: str
) -> None:
    (entity,) = _ids(hass, "climate")
    await hass.services.async_call(
        "climate", "set_fan_mode", {"entity_id": entity, "fan_mode": fan}, blocking=True
    )
    tstat_loaded.async_set_tstat_status.assert_awaited_once_with(1, fan_mode=sent)


async def test_climate_state_reports_mode_and_fan(
    hass: HomeAssistant, tstat_loaded: Any
) -> None:
    (entity,) = _ids(hass, "climate")
    state = hass.states.get(entity)
    assert state.state == "heat"
    assert state.attributes["fan_mode"] == "auto"


# --- Lights: #89 false off, #91 scale ---------------------------------------


@pytest.fixture
async def light_loaded(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: ClientHarness
) -> Any:
    mock_client.snapshot = _with(
        mock_client.snapshot,
        lights={1: LightState(light_id=1, name="Dimmer", state=True, level=99)},
    )
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    return mock_config_entry.runtime_data.coordinator


def _record(hass: HomeAssistant, entity: str) -> list[str]:
    seen: list[str] = []

    def _cb(event: Any) -> None:
        if event.data["entity_id"] == entity and event.data["new_state"]:
            seen.append(event.data["new_state"].state)

    hass.bus.async_listen("state_changed", _cb)
    return seen


@pytest.mark.xfail(
    reason="HACS #89: transient level-0 get_status reply publishes a false off",
    strict=True,
)
async def test_dim_does_not_publish_transient_off(
    hass: HomeAssistant, light_loaded: Any, mock_client: ClientHarness
) -> None:
    coordinator = light_loaded
    (entity,) = _ids(hass, "light")
    seen = _record(hass, entity)
    base = mock_client.snapshot

    async def _execute(key: str, **params: Any) -> Result[Any]:
        if key == "light_get_status":
            # Z-Wave ramp: the immediate status read reports 0 before the target.
            snap = _with(
                base,
                lights={1: LightState(light_id=1, name="Dimmer", state=False, level=0)},
            )
            mock_client.snapshot = snap
            coordinator.async_set_updated_data(snap)
        return Result.success({})

    mock_client.client.async_execute.side_effect = _execute
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": entity, "brightness": 26}, blocking=True
    )
    final = _with(
        base, lights={1: LightState(light_id=1, name="Dimmer", state=True, level=10)}
    )
    coordinator.async_set_updated_data(final)
    await hass.async_block_till_done()
    await settle()
    assert STATE_OFF not in seen
    assert hass.states.get(entity).state == STATE_ON


@pytest.mark.xfail(
    reason="HACS #91 (PR #92): 'on' without brightness should send level 100",
    strict=True,
)
async def test_on_without_brightness_sends_100(
    hass: HomeAssistant, light_loaded: Any, mock_client: ClientHarness
) -> None:
    mock_client.client.async_execute.return_value = Result.success({})
    (entity,) = _ids(hass, "light")
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": entity}, blocking=True
    )
    sets = [
        c
        for c in mock_client.client.async_execute.call_args_list
        if c.args[0] == "light_set_status"
    ]
    assert sets[0].kwargs["level"] == 100


async def test_turn_off_sends_level_zero_and_one_off(
    hass: HomeAssistant, light_loaded: Any, mock_client: ClientHarness
) -> None:
    mock_client.client.async_execute.return_value = Result.success({})
    (entity,) = _ids(hass, "light")
    await hass.services.async_call(
        "light", "turn_off", {"entity_id": entity}, blocking=True
    )
    sets = [
        c
        for c in mock_client.client.async_execute.call_args_list
        if c.args[0] == "light_set_status"
    ]
    assert len(sets) == 1
    assert sets[0].kwargs["level"] == 0
    assert sets[0].kwargs["status"] == "OFF"
