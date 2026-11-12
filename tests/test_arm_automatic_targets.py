# ruff: noqa: S101, TC001, D107
"""arm_automatic rejects bad targets before any panel command runs."""

from __future__ import annotations

import dataclasses
from types import MappingProxyType

from elke27_lib import AreaState, PanelSnapshot, ZoneState
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.elke27.const import (
    CONF_INTEGRATION_ID,
    CONF_LINK_KEYS_JSON,
    DOMAIN,
)
from homeassistant.const import ATTR_CODE, CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er
from tests.conftest import (
    HOST_2,
    LINK_KEYS_JSON,
    PANEL_SERIAL_2,
    PORT,
    FakeClient,
    Setup,
    command_result,
    entity_ids,
    panel_snapshot,
)

ALARM = "alarm_control_panel.house"
CODE = "1234"
# Bad targets must sort after the valid panel entity_id; the service sorts targets.
MISSING = "alarm_control_panel.zzz_does_not_exist"
NON_ELKE27 = "alarm_control_panel.zzz_demo_alarm"


def _no_bypass_or_arm(client: FakeClient) -> None:
    assert not any(kind in {"bypass", "arm"} for kind, _params in client.calls)


def _two_area_panel_snapshot() -> PanelSnapshot:
    """Open zones in both areas so automatic arming would bypass before arm."""
    return dataclasses.replace(
        panel_snapshot(),
        areas=MappingProxyType(
            {
                1: AreaState(area_id=1, name="House", ready=False),
                2: AreaState(area_id=2, name="Garage", ready=True),
            }
        ),
        zones=MappingProxyType(
            {
                1: ZoneState(zone_id=1, name="Front Door", area_id=1, open=True),
                2: ZoneState(zone_id=2, name="Back Door", area_id=1, open=True),
                4: ZoneState(zone_id=4, name="Garage Door", area_id=2, open=True),
            }
        ),
    )


class OpenZonesClient(FakeClient):
    """Two-area snapshot so automatic arming would bypass before arm."""

    def __init__(self) -> None:
        super().__init__(command_result(refresh=True))
        self.snapshot = _two_area_panel_snapshot()


@pytest.fixture
async def two_area_client(hass: HomeAssistant, setup: Setup) -> FakeClient:
    """Load a panel with two areas from the panel snapshot."""
    client = OpenZonesClient()
    await setup(client)
    assert len(entity_ids(hass, "alarm_control_panel")) == 2  # noqa: PLR2004
    return client


async def test_rejects_missing_entity_with_valid_area(
    hass: HomeAssistant, two_area_client: FakeClient
) -> None:
    """A missing entity id fails validation before the valid area is touched."""
    two_area_client.calls.clear()
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN,
            "alarm_arm_automatic",
            {"entity_id": [ALARM, MISSING], "mode": "away", ATTR_CODE: CODE},
            blocking=True,
        )
    assert err.value.translation_key == "entity_invalid_area"
    assert (err.value.translation_placeholders or {})["entity_id"] == MISSING
    _no_bypass_or_arm(two_area_client)


async def test_rejects_non_elke27_entity_with_valid_area(
    hass: HomeAssistant, two_area_client: FakeClient
) -> None:
    """A non-Elke27 alarm entity fails validation before arming starts."""
    er.async_get(hass).async_get_or_create(
        "alarm_control_panel",
        "demo",
        "demo:test",
        suggested_object_id="zzz_demo_alarm",
    )
    two_area_client.calls.clear()
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN,
            "alarm_arm_automatic",
            {"entity_id": [ALARM, NON_ELKE27], "mode": "away", ATTR_CODE: CODE},
            blocking=True,
        )
    assert err.value.translation_key == "entity_invalid_area"
    assert (err.value.translation_placeholders or {})["entity_id"] == NON_ELKE27
    _no_bypass_or_arm(two_area_client)


async def test_rejects_unloaded_entry_with_valid_area(
    hass: HomeAssistant,
    two_area_client: FakeClient,
) -> None:
    """An entity on an unloaded config entry is rejected before bypass or arm."""
    entry_b = MockConfigEntry(
        domain=DOMAIN,
        title="Panel B",
        unique_id=PANEL_SERIAL_2,
        data={
            CONF_HOST: HOST_2,
            CONF_PORT: PORT,
            CONF_LINK_KEYS_JSON: LINK_KEYS_JSON,
            CONF_INTEGRATION_ID: "987654321098",
        },
    )
    entry_b.add_to_hass(hass)
    unloaded_panel = (
        er.async_get(hass)
        .async_get_or_create(
            "alarm_control_panel",
            DOMAIN,
            f"{PANEL_SERIAL_2}:1",
            suggested_object_id="panel_b_house",
            config_entry=entry_b,
        )
        .entity_id
    )
    two_area_client.calls.clear()
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN,
            "alarm_arm_automatic",
            {
                "entity_id": [ALARM, unloaded_panel],
                "mode": "away",
                ATTR_CODE: CODE,
            },
            blocking=True,
        )
    assert err.value.translation_key == "config_entry_not_loaded"
    assert (err.value.translation_placeholders or {})["entity_id"] == unloaded_panel
    _no_bypass_or_arm(two_area_client)
