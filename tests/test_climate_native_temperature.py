# ruff: noqa: S101 -- assert statements in pytest tests
# ruff: noqa: TC001 -- Home Assistant typing imports at runtime
# ruff: noqa: TC002 -- pytest HA plugin imports at runtime
"""Climate thermostat temperature reporting through Home Assistant state."""

from __future__ import annotations

from elke27_lib import ThermostatState
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.core import HomeAssistant
from homeassistant.util.unit_system import METRIC_SYSTEM, US_CUSTOMARY_SYSTEM
from tests.conftest import ClientHarness, entity_ids, snapshot_with

_PANEL_CURRENT_F = 70
_PANEL_HEAT_SETPOINT_F = 68
_PANEL_COOL_SETPOINT_F = 76
_METRIC_CURRENT_C = 21.1
_METRIC_HEAT_SETPOINT_C = 20.0
_METRIC_COOL_SETPOINT_C = 24.4

_HALL_TSTAT = ThermostatState(
    tstat_id=1,
    name="Hall",
    temperature=_PANEL_CURRENT_F,
    heat_setpoint=_PANEL_HEAT_SETPOINT_F,
    cool_setpoint=_PANEL_COOL_SETPOINT_F,
    mode="HEAT",
    fan_mode="AUTO",
)


async def _load_climate(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: ClientHarness,
) -> None:
    mock_client.snapshot = snapshot_with(
        mock_client.snapshot,
        thermostats={1: _HALL_TSTAT},
    )
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()


def _climate_temperature_attributes(hass: HomeAssistant) -> dict[str, float]:
    (entity_id,) = entity_ids(hass, "climate")
    state = hass.states.get(entity_id)
    assert state is not None
    return {
        "current_temperature": state.attributes["current_temperature"],
        "target_temp_low": state.attributes["target_temp_low"],
        "target_temp_high": state.attributes["target_temp_high"],
    }


async def test_climate_state_converts_panel_fahrenheit_to_celsius(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: ClientHarness,
) -> None:
    """Metric Home Assistant shows panel Fahrenheit setpoints converted to Celsius."""
    hass.config.units = METRIC_SYSTEM
    await _load_climate(hass, mock_config_entry, mock_client)
    temperatures = _climate_temperature_attributes(hass)
    assert temperatures == {
        "current_temperature": _METRIC_CURRENT_C,
        "target_temp_low": _METRIC_HEAT_SETPOINT_C,
        "target_temp_high": _METRIC_COOL_SETPOINT_C,
    }


async def test_climate_state_keeps_panel_fahrenheit_for_us_customary(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: ClientHarness,
) -> None:
    """US customary Home Assistant keeps panel Fahrenheit on the climate entity."""
    hass.config.units = US_CUSTOMARY_SYSTEM
    await _load_climate(hass, mock_config_entry, mock_client)
    temperatures = _climate_temperature_attributes(hass)
    assert temperatures == {
        "current_temperature": float(_PANEL_CURRENT_F),
        "target_temp_low": float(_PANEL_HEAT_SETPOINT_F),
        "target_temp_high": float(_PANEL_COOL_SETPOINT_F),
    }
