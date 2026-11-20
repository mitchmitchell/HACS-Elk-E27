# ruff: noqa: S101, TC001
"""An arm whose outcome is unknown reports arm_outcome_unknown in every mode."""

from __future__ import annotations

from typing import Any

from elke27_lib import AreaState, ArmMode
from elke27_lib.errors import Elke27CommandOutcomeUnknownError
import pytest

from homeassistant.components.alarm_control_panel import AlarmControlPanelEntityFeature
from homeassistant.const import ATTR_CODE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from tests.conftest import FakeClient, Setup, command_result, snapshot_with

ALARM = "alarm_control_panel.house"


def _area(**kw: Any) -> AreaState:
    return AreaState(area_id=1, name="House", ready=True, **kw)


@pytest.mark.parametrize(
    ("service", "mode"),
    [
        pytest.param("alarm_arm_away", ArmMode.ARMED_AWAY, id="away"),
        pytest.param("alarm_arm_home", ArmMode.ARMED_STAY, id="stay"),
        pytest.param("alarm_arm_night", ArmMode.ARMED_NIGHT, id="night"),
    ],
)
async def test_arm_outcome_unknown_reaches_the_service_call(
    hass: HomeAssistant, setup: Setup, service: str, mode: ArmMode
) -> None:
    """The arm service raises arm_outcome_unknown and sends the arm once."""
    client = FakeClient(command_result(refresh=True))
    client.snapshot = snapshot_with(
        client.snapshot, areas={1: _area(night_arm_supported=True)}
    )
    await setup(client)
    client.write_result = Elke27CommandOutcomeUnknownError(("area", "set_arm_state"))
    with pytest.raises(HomeAssistantError) as ctx:
        await hass.services.async_call(
            "alarm_control_panel",
            service,
            {"entity_id": ALARM, ATTR_CODE: "1234"},
            blocking=True,
        )
    assert ctx.value.translation_key == "arm_outcome_unknown"
    arms = [params for kind, params in client.calls if kind == "arm"]
    assert len(arms) == 1
    assert arms[0]["mode"] is mode


async def test_features_without_area_in_snapshot(
    hass: HomeAssistant, setup: Setup
) -> None:
    """An area missing from the snapshot drops ARM_NIGHT instead of raising."""
    client = FakeClient(command_result(refresh=True))
    client.snapshot = snapshot_with(
        client.snapshot, areas={1: _area(night_arm_supported=True)}
    )
    coordinator = await setup(client)
    entity = hass.data["entity_components"]["alarm_control_panel"].get_entity(ALARM)
    assert entity is not None
    assert entity.supported_features & AlarmControlPanelEntityFeature.ARM_NIGHT
    coordinator.async_set_updated_data(snapshot_with(coordinator.data, areas={}))
    await hass.async_block_till_done()
    assert not entity.supported_features & AlarmControlPanelEntityFeature.ARM_NIGHT
