# ruff: noqa: S101 -- assert statements in pytest tests
# ruff: noqa: D103 -- pytest fixtures omit docstrings
# ruff: noqa: TC001 -- Home Assistant typing imports at runtime
# ruff: noqa: TC002 -- pytest HA plugin imports at runtime
"""
Climate thermostat entities use Home Assistant native temperature attributes.

CI pins Home Assistant 2026.10.4; reload-based tests simulate the 2026.11 native
climate API until pytest-homeassistant-custom-component ships a 2026.11 pin.
"""

from __future__ import annotations

import dataclasses
import importlib
from importlib.metadata import version
import logging
from types import MappingProxyType
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

from elke27_lib import PanelInfo, PanelSnapshot, ThermostatState
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

import custom_components.elke27.climate as elke27_climate
from custom_components.elke27.climate import Elke27Thermostat
from homeassistant.components.climate import ClimateEntity
from homeassistant.core import HomeAssistant
from tests.conftest import ClientHarness, entity_ids, snapshot_with

if TYPE_CHECKING:
    from collections.abc import Iterator

_DEPRECATED_CLASS_MEMBERS = frozenset(
    {
        "_attr_temperature_unit",
        "current_temperature",
        "target_temperature_low",
        "target_temperature_high",
    },
)
_NATIVE_CLASS_MEMBERS = frozenset(
    {
        "_attr_native_temperature_unit",
        "native_current_temperature",
        "native_target_temperature_low",
        "native_target_temperature_high",
    },
)

_DISPLAY_CURRENT_TEMPERATURE_C = 21.1
_DISPLAY_TARGET_LOW_C = 20.0
_DISPLAY_TARGET_HIGH_C = 24.4
_PANEL_CURRENT_F = 70.0
_PANEL_HEAT_SETPOINT_F = 68.0
_PANEL_COOL_SETPOINT_F = 76.0


def _homeassistant_core_version() -> str:
    return version("homeassistant")


def _snapshot(thermostats: dict[int, ThermostatState]) -> PanelSnapshot:
    return dataclasses.replace(
        PanelSnapshot.empty(),
        panel=PanelInfo(mac="00:11:22:33:44:55"),
        thermostats=MappingProxyType(thermostats),
    )


def _build_thermostat(
    climate_module: Any,
    tstat: ThermostatState,
) -> Any:
    """Build a thermostat entity for direct property checks."""
    coordinator = MagicMock()
    coordinator.data = _snapshot({tstat.tstat_id: tstat})
    coordinator.is_ready = True
    entry = MagicMock()
    entry.entry_id = "entry-1"
    entry.unique_id = None
    entry.title = "Panel"
    entry.data = {"host": "192.0.2.10"}
    return climate_module.Elke27Thermostat(coordinator, entry, tstat.tstat_id, tstat)


@pytest.fixture
def native_climate_module() -> Iterator[Any]:
    """Reload climate.py while ClimateEntity looks like HA 2026.11+."""
    injected = "native_current_temperature" not in ClimateEntity.__dict__
    if injected:
        ClimateEntity.native_current_temperature = property(lambda _: None)
    reloaded = importlib.reload(elke27_climate)
    try:
        yield reloaded
    finally:
        importlib.reload(elke27_climate)
        if injected:
            del ClimateEntity.native_current_temperature
        importlib.reload(elke27_climate)


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


def test_reloaded_native_branch_has_no_legacy_overrides(
    native_climate_module: Any,
) -> None:
    """When HA exposes native climate temperatures, Elke27 skips the legacy shim."""
    class_members = set(native_climate_module.Elke27Thermostat.__dict__)
    assert class_members.isdisjoint(_DEPRECATED_CLASS_MEMBERS)
    assert class_members >= _NATIVE_CLASS_MEMBERS


def test_reloaded_native_branch_reads_panel_fahrenheit(
    native_climate_module: Any,
) -> None:
    """Native properties return panel Fahrenheit on the simulated HA 2026.11 branch."""
    tstat = ThermostatState(
        tstat_id=1,
        temperature=int(_PANEL_CURRENT_F),
        heat_setpoint=int(_PANEL_HEAT_SETPOINT_F),
        cool_setpoint=int(_PANEL_COOL_SETPOINT_F),
    )
    entity = _build_thermostat(native_climate_module, tstat)
    assert entity.native_current_temperature == _PANEL_CURRENT_F
    assert entity.native_target_temperature_low == _PANEL_HEAT_SETPOINT_F
    assert entity.native_target_temperature_high == _PANEL_COOL_SETPOINT_F


@pytest.mark.usefixtures("tstat_loaded")
def test_climate_platform_import_no_deprecated_temperature_warnings(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Loading climate entities must not log deprecated temperature member warnings."""
    deprecated = [
        record.getMessage()
        for record in caplog.records
        if "provides the deprecated" in record.getMessage()
    ]
    assert deprecated == [], (
        f"deprecated temperature warnings on HA {_homeassistant_core_version()}: "
        f"{deprecated}"
    )


@pytest.mark.usefixtures("tstat_loaded")
async def test_climate_state_reports_native_temperatures(hass: HomeAssistant) -> None:
    """State attributes expose setpoints converted from native Fahrenheit."""
    (entity,) = entity_ids(hass, "climate")
    state = hass.states.get(entity)
    assert state is not None
    assert state.attributes["current_temperature"] == _DISPLAY_CURRENT_TEMPERATURE_C
    assert state.attributes["target_temp_low"] == _DISPLAY_TARGET_LOW_C
    assert state.attributes["target_temp_high"] == _DISPLAY_TARGET_HIGH_C


@pytest.fixture(autouse=True)
def caplog_deprecation_warnings(caplog: pytest.LogCaptureFixture) -> None:
    """Capture integration deprecation warnings while climate entities load."""
    caplog.set_level(logging.WARNING)
