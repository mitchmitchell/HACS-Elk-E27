# ruff: noqa: S101, D103, TC001, TC002, ARG001
"""
Climate service tests on a loaded entry.

Commands reach the mocked client through the real coordinator (Tester
feature-map coverage pass, 2026-10-17).
"""

from __future__ import annotations

import dataclasses
from types import MappingProxyType
from typing import Any

from elke27_lib import ThermostatState
from elke27_lib.client import Result
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.core import HomeAssistant
from tests.conftest import ClientHarness


def _with(snapshot: Any, **kw: Any) -> Any:
    return dataclasses.replace(
        snapshot, **{k: MappingProxyType(v) for k, v in kw.items()}
    )


def _ids(hass: HomeAssistant, domain: str) -> list[str]:
    return [s.entity_id for s in hass.states.async_all(domain)]


@pytest.fixture
async def tstat_loaded(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: ClientHarness
) -> ClientHarness:
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
    mock_client.client.async_execute.reset_mock()
    mock_client.client.async_execute.return_value = Result.success({})
    return mock_client


@pytest.mark.parametrize(
    ("hvac", "sent"),
    [("off", "OFF"), ("heat", "HEAT"), ("cool", "COOL"), ("heat_cool", "AUTO")],
)
async def test_hvac_mode_service_maps_to_panel(
    hass: HomeAssistant, tstat_loaded: ClientHarness, hvac: str, sent: str
) -> None:
    (entity,) = _ids(hass, "climate")
    await hass.services.async_call(
        "climate",
        "set_hvac_mode",
        {"entity_id": entity, "hvac_mode": hvac},
        blocking=True,
    )
    tstat_loaded.client.async_execute.assert_awaited_once_with(
        "tstat_set_status", tstat_id=1, mode=sent
    )


@pytest.mark.parametrize(("fan", "sent"), [("auto", "AUTO"), ("on", "ON")])
async def test_fan_mode_service_maps_to_panel(
    hass: HomeAssistant, tstat_loaded: ClientHarness, fan: str, sent: str
) -> None:
    (entity,) = _ids(hass, "climate")
    await hass.services.async_call(
        "climate", "set_fan_mode", {"entity_id": entity, "fan_mode": fan}, blocking=True
    )
    tstat_loaded.client.async_execute.assert_awaited_once_with(
        "tstat_set_status", tstat_id=1, fan_mode=sent
    )


async def test_climate_state_reports_mode_and_fan(
    hass: HomeAssistant, tstat_loaded: ClientHarness
) -> None:
    (entity,) = _ids(hass, "climate")
    state = hass.states.get(entity)
    assert state.state == "heat"
    assert state.attributes["fan_mode"] == "auto"
