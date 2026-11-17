# ruff: noqa: S101, TC001, FBT001
"""Arm night feature flag, service, and error translations."""

from __future__ import annotations

from typing import Any

from elke27_lib import AreaState, ArmMode
from elke27_lib.errors import (
    Elke27CommandOutcomeUnknownError,
    Elke27InvalidArgument,
    Elke27NightArmFirmwareTooOldError,
    Elke27PanelApplicationFirmwareUnknownError,
    Elke27StayInstantRequiredError,
)
import pytest

from custom_components.elke27.errors import arm_night_command_error
from homeassistant.components.alarm_control_panel import AlarmControlPanelEntityFeature
from homeassistant.const import ATTR_CODE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from tests.conftest import FakeClient, Setup, command_result, snapshot_with

ALARM = "alarm_control_panel.house"


def _area(**kw: Any) -> AreaState:
    return AreaState(area_id=1, name="House", ready=kw.pop("ready", True), **kw)


@pytest.mark.parametrize(
    ("night_arm_supported", "expects_night"),
    [
        pytest.param(True, True, id="supported"),
        pytest.param(False, False, id="firmware_too_old"),
        pytest.param(False, False, id="firmware_unknown"),
    ],
)
async def test_arm_night_feature_on_off(
    hass: HomeAssistant,
    setup: Setup,
    night_arm_supported: bool,
    expects_night: bool,
) -> None:
    """ARM_NIGHT is in supported_features only when night_arm_supported is True."""
    client = FakeClient(command_result(refresh=True))
    snap = snapshot_with(
        client.snapshot,
        areas={1: _area(night_arm_supported=night_arm_supported)},
    )
    client.snapshot = snap
    await setup(client)
    await hass.async_block_till_done()
    state = hass.states.get(ALARM)
    assert state is not None
    features = state.attributes.get("supported_features", 0)
    has_night = bool(features & AlarmControlPanelEntityFeature.ARM_NIGHT)
    assert has_night is expects_night


async def test_arm_night_service_sends_armed_night(
    hass: HomeAssistant, setup: Setup
) -> None:
    """alarm_arm_night calls the client with ARMED_NIGHT."""
    client = FakeClient(command_result(refresh=True))
    snap = snapshot_with(
        client.snapshot,
        areas={1: _area(night_arm_supported=True)},
    )
    client.snapshot = snap
    await setup(client)
    await hass.services.async_call(
        "alarm_control_panel",
        "alarm_arm_night",
        {"entity_id": ALARM, ATTR_CODE: "1234"},
        blocking=True,
    )
    arms = [params for kind, params in client.calls if kind == "arm"]
    assert len(arms) == 1
    assert arms[0]["mode"] is ArmMode.ARMED_NIGHT
    assert arms[0]["pin"] == "1234"


@pytest.mark.parametrize(
    ("side_effect", "translation_key"),
    [
        (
            Elke27StayInstantRequiredError(),
            "night_arm_stay_instant_required",
        ),
        (
            Elke27PanelApplicationFirmwareUnknownError(
                "Panel application firmware version is unknown."
            ),
            "night_arm_firmware_unknown",
        ),
        (
            Elke27NightArmFirmwareTooOldError(
                "Arm night requires panel application firmware 0.0.5.37 or later.",
                required=(0, 0, 5, 37),
                actual=(0, 0, 5, 36),
            ),
            "night_arm_firmware_too_old",
        ),
        (
            Elke27CommandOutcomeUnknownError(("area", "set_arm_state")),
            "night_arm_outcome_unknown",
        ),
    ],
)
async def test_arm_night_error_translations(
    setup: Setup,
    side_effect: BaseException,
    translation_key: str,
) -> None:
    """Night-arm failures from elke27 map to integration translation keys."""
    client = FakeClient(command_result(refresh=True))
    snap = snapshot_with(
        client.snapshot,
        areas={1: _area(night_arm_supported=True)},
    )
    client.snapshot = snap
    coordinator = await setup(client)
    client.write_result = side_effect
    with pytest.raises(HomeAssistantError) as ctx:
        await coordinator.async_arm_area(1, ArmMode.ARMED_NIGHT, "1234")
    assert ctx.value.translation_key == translation_key


def test_arm_night_command_error_maps_typed_firmware_errors() -> None:
    """Typed elke27 firmware errors map to the night-arm translation keys."""
    unknown = arm_night_command_error(
        Elke27PanelApplicationFirmwareUnknownError(
            "Panel application firmware version is unknown."
        )
    )
    assert unknown is not None
    assert unknown.translation_key == "night_arm_firmware_unknown"

    too_old = arm_night_command_error(
        Elke27NightArmFirmwareTooOldError(
            "Arm night requires panel application firmware 0.0.5.37 or later.",
            required=(0, 0, 5, 37),
            actual=(0, 0, 5, 36),
        )
    )
    assert too_old is not None
    assert too_old.translation_key == "night_arm_firmware_too_old"


@pytest.mark.parametrize(
    "err",
    [
        Exception("Panel application firmware version is unknown."),
        Exception("Arm night requires panel application firmware 0.5.37 or newer."),
        Elke27InvalidArgument("Panel application firmware version is unknown."),
        Elke27InvalidArgument(
            "Arm night requires panel application firmware 0.5.37 or newer."
        ),
    ],
)
def test_arm_night_command_error_ignores_matching_message(
    err: BaseException,
) -> None:
    """A matching message on a non-typed exception does not map."""
    assert arm_night_command_error(err) is None
