# ruff: noqa: S101, D103, TC001, TC002, ARG001, PLR2004
"""Climate thermostat entities use Home Assistant native temperature attributes."""

from __future__ import annotations

from importlib.metadata import version
import logging

from elke27_lib import ThermostatState
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.elke27.climate import Elke27Thermostat
from homeassistant.components.climate import ClimateEntity
from homeassistant.core import HomeAssistant
from tests.conftest import ClientHarness, entity_ids, snapshot_with

_DEPRECATED_CLASS_MEMBERS = frozenset(
    {
        "_attr_temperature_unit",
        "current_temperature",
        "target_temperature_low",
        "target_temperature_high",
    }
)
_NATIVE_CLASS_MEMBERS = frozenset(
    {
        "_attr_native_temperature_unit",
        "native_current_temperature",
        "native_target_temperature_low",
        "native_target_temperature_high",
    }
)


def _homeassistant_core_version() -> str:
    return version("homeassistant")


@pytest.fixture
async def tstat_loaded(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: ClientHarness
) -> ClientHarness:
    mock_client.snapshot = snapshot_with(
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
    return mock_client


def test_elke27_thermostat_uses_native_temperature_members() -> None:
    """Subclass overrides native temperature members, not the deprecated API."""
    class_members = set(Elke27Thermostat.__dict__)
    assert class_members >= _NATIVE_CLASS_MEMBERS
    if "native_current_temperature" in ClimateEntity.__dict__:
        assert class_members.isdisjoint(_DEPRECATED_CLASS_MEMBERS)


@pytest.mark.usefixtures("tstat_loaded")
def test_climate_platform_import_no_deprecated_temperature_warnings(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Loading climate entities must not trigger deprecated temperature warnings."""
    deprecated = [
        record.getMessage()
        for record in caplog.records
        if "provides the deprecated" in record.getMessage()
    ]
    assert deprecated == [], (
        f"deprecated temperature warnings on HA {_homeassistant_core_version()}: "
        f"{deprecated}"
    )


async def test_climate_state_reports_native_temperatures(
    hass: HomeAssistant, tstat_loaded: ClientHarness
) -> None:
    """State attributes expose setpoints converted from native Fahrenheit."""
    (entity,) = entity_ids(hass, "climate")
    state = hass.states.get(entity)
    assert state is not None
    assert state.attributes["current_temperature"] == 21.1
    assert state.attributes["target_temp_low"] == 20.0
    assert state.attributes["target_temp_high"] == 24.4


@pytest.fixture(autouse=True)
def caplog_deprecation_warnings(caplog: pytest.LogCaptureFixture) -> None:
    """Capture integration deprecation warnings while climate entities load."""
    caplog.set_level(logging.WARNING)
