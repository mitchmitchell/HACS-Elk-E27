# ruff: noqa: S101, PLR2004, TC001, ARG001, D101, D103, D107, TRY003, EM101
"""Real zone_bypass service tests for pre-validation, rollback, and locking."""

from __future__ import annotations

import asyncio
import dataclasses
from types import MappingProxyType
from typing import Any
from unittest.mock import AsyncMock

from elke27_lib import AreaState, ArmMode, PanelSnapshot, ZoneState
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.elke27.const import (
    CONF_INTEGRATION_ID,
    CONF_LINK_KEYS_JSON,
    DOMAIN,
)
from custom_components.elke27.coordinator import Elke27DataUpdateCoordinator
from custom_components.elke27.models import Elke27RuntimeData
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_CODE, CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er
from tests.conftest import (
    HOST_2,
    LINK_KEYS_JSON,
    PANEL_MAC,
    PANEL_SERIAL_2,
    PORT,
    ClientHarness,
    FakeClient,
    Setup,
    command_result,
    entity_ids,
    panel_snapshot,
)

CODE = "1234"
KEY = PANEL_MAC


def _three_zone_snapshot(*, zone2_bypassed: bool = False) -> PanelSnapshot:
    return dataclasses.replace(
        panel_snapshot(),
        areas=MappingProxyType(
            {
                1: AreaState(
                    area_id=1, name="House", ready=True, arm_mode=ArmMode.DISARMED
                )
            }
        ),
        zones=MappingProxyType(
            {
                1: ZoneState(
                    zone_id=1, name="Zone A", area_id=1, open=False, bypassed=False
                ),
                2: ZoneState(
                    zone_id=2,
                    name="Zone B",
                    area_id=1,
                    open=False,
                    bypassed=zone2_bypassed,
                ),
                3: ZoneState(
                    zone_id=3, name="Zone C", area_id=1, open=False, bypassed=False
                ),
            }
        ),
    )


def _all_bypassed_snapshot() -> PanelSnapshot:
    base = _three_zone_snapshot()
    return dataclasses.replace(
        base,
        zones=MappingProxyType(
            {
                zone_id: dataclasses.replace(zone, bypassed=True)
                for zone_id, zone in base.zones.items()
            }
        ),
    )


def _two_area_snapshot() -> PanelSnapshot:
    return dataclasses.replace(
        panel_snapshot(),
        areas=MappingProxyType(
            {
                1: AreaState(
                    area_id=1, name="House", ready=False, arm_mode=ArmMode.DISARMED
                ),
                2: AreaState(
                    area_id=2, name="Garage", ready=True, arm_mode=ArmMode.DISARMED
                ),
            }
        ),
        zones=MappingProxyType(
            {
                1: ZoneState(zone_id=1, name="Front Door", area_id=1, open=True),
                4: ZoneState(zone_id=4, name="Garage Door", area_id=2, open=False),
            }
        ),
    )


class ThreeZoneClient(FakeClient):
    def __init__(self, *, snapshot: PanelSnapshot | None = None) -> None:
        super().__init__(command_result(refresh=True))
        self.snapshot = snapshot or _three_zone_snapshot()


class TwoAreaClient(FakeClient):
    def __init__(self) -> None:
        super().__init__(command_result(refresh=True))
        self.snapshot = _two_area_snapshot()


def _bypass_calls(client: FakeClient) -> list[tuple[int, bool]]:
    return [
        (params["zone_id"], params["bypassed"])
        for kind, params in client.calls
        if kind == "bypass"
    ]


def _panel_bypass_calls(mock_client: ClientHarness) -> list[tuple[int, bool]]:
    out: list[tuple[int, bool]] = []
    mock = mock_client.client.async_set_zone_bypass
    for call in mock.await_args_list:
        zone_id = call.args[0]
        bypassed = call.kwargs["bypassed"]
        out.append((zone_id, bypassed))
    return out


def _zone_entities(hass: HomeAssistant) -> dict[int, str]:
    mapping: dict[int, str] = {}
    for entity_id in entity_ids(hass, "binary_sensor"):
        entry = er.async_get(hass).async_get(entity_id)
        assert entry is not None
        zone_id = int(entry.unique_id.rsplit(":", 1)[-1])
        mapping[zone_id] = entity_id
    return mapping


def _patch_bypass(
    client: FakeClient, mock_client: ClientHarness, side_effect: Any
) -> None:
    mock_client.client.async_set_zone_bypass = AsyncMock(side_effect=side_effect)


@pytest.fixture
async def three_zones(
    hass: HomeAssistant, setup: Setup, mock_client: ClientHarness
) -> tuple[FakeClient, ClientHarness]:
    client = ThreeZoneClient()
    await setup(client)
    assert len(_zone_entities(hass)) == 3
    return client, mock_client


@pytest.fixture
async def two_areas(
    hass: HomeAssistant, setup: Setup, mock_client: ClientHarness
) -> tuple[FakeClient, ClientHarness]:
    client = TwoAreaClient()
    await setup(client)
    assert len(entity_ids(hass, "alarm_control_panel")) == 2
    return client, mock_client


@pytest.mark.parametrize(
    ("bad_entity", "call_order"),
    [
        ("binary_sensor.aaa_demo_not_elke27", "first"),
        ("binary_sensor.house_zone_b_blocker", "middle"),
        ("binary_sensor.zzz_demo_not_elke27", "last"),
    ],
    ids=["invalid_first", "invalid_middle", "invalid_last"],
)
async def test_invalid_target_sends_nothing(
    hass: HomeAssistant,
    three_zones: tuple[FakeClient, ClientHarness],
    bad_entity: str,
    call_order: str,
) -> None:
    client, _mock = three_zones
    zones = _zone_entities(hass)
    object_id = bad_entity.split(".", 1)[1]
    er.async_get(hass).async_get_or_create(
        "binary_sensor",
        "demo",
        f"demo:{object_id}",
        suggested_object_id=object_id,
    )
    order = [zones[1], zones[2], zones[3]]
    if call_order == "first":
        order = [bad_entity, *order]
    elif call_order == "middle":
        order = [zones[1], bad_entity, zones[2], zones[3]]
    else:
        order = [*order, bad_entity]
    client.calls.clear()
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN,
            "zone_bypass",
            {"entity_id": order, "code": CODE, "bypass": True},
            blocking=True,
        )
    assert err.value.translation_key == "entity_invalid_zone"
    assert _bypass_calls(client) == []


async def test_unknown_zone_in_list_sends_nothing(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    three_zones: tuple[FakeClient, ClientHarness],
) -> None:
    client, _mock = three_zones
    missing = (
        er.async_get(hass)
        .async_get_or_create(
            "binary_sensor",
            DOMAIN,
            f"{KEY}:99",
            config_entry=mock_config_entry,
            suggested_object_id="house_missing_zone",
        )
        .entity_id
    )
    zones = _zone_entities(hass)
    client.calls.clear()
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN,
            "zone_bypass",
            {"entity_id": [zones[1], missing, zones[3]], "code": CODE, "bypass": True},
            blocking=True,
        )
    assert err.value.translation_key == "zone_not_found"
    assert _bypass_calls(client) == []


async def test_panel_refusal_rolls_back_prior_zones(
    hass: HomeAssistant,
    three_zones: tuple[FakeClient, ClientHarness],
) -> None:
    client, mock_client = three_zones
    zones = _zone_entities(hass)
    base = client.async_set_zone_bypass

    async def _bypass(zone_id: int, **params: Any) -> Any:
        if zone_id == 2 and params.get("bypassed") is True:
            raise HomeAssistantError("panel refused zone 2")
        return await base(zone_id, **params)

    client.calls.clear()
    _patch_bypass(client, mock_client, _bypass)
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            DOMAIN,
            "zone_bypass",
            {"entity_id": [zones[1], zones[2], zones[3]], "code": CODE, "bypass": True},
            blocking=True,
        )
    assert err.value.translation_key == "zone_bypass_failed_rolled_back"
    ph = err.value.translation_placeholders or {}
    assert ph.get("rolled_back_zone_numbers") == "1"
    assert "still_bypassed_zone_numbers" not in ph
    assert err.value.__cause__ is not None
    assert _panel_bypass_calls(mock_client) == [(1, True), (2, True), (1, False)]


async def test_failed_rollback_reports_still_bypassed(
    hass: HomeAssistant,
    three_zones: tuple[FakeClient, ClientHarness],
) -> None:
    client, mock_client = three_zones
    zones = _zone_entities(hass)
    base = client.async_set_zone_bypass

    async def _bypass(zone_id: int, **params: Any) -> Any:
        if zone_id == 2 and params.get("bypassed"):
            raise HomeAssistantError("panel refused zone 2")
        if zone_id == 1 and params.get("bypassed") is False:
            raise HomeAssistantError("rollback refused zone 1")
        return await base(zone_id, **params)

    client.calls.clear()
    _patch_bypass(client, mock_client, _bypass)
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            DOMAIN,
            "zone_bypass",
            {"entity_id": [zones[1], zones[2], zones[3]], "code": CODE, "bypass": True},
            blocking=True,
        )
    assert err.value.translation_key == "zone_bypass_failed_still_bypassed"
    ph = err.value.translation_placeholders or {}
    assert ph.get("still_bypassed_zone_numbers") == "1"
    assert _panel_bypass_calls(mock_client) == [(1, True), (2, True), (1, False)]


async def test_already_bypassed_zone_is_skipped(
    hass: HomeAssistant,
    setup: Setup,
) -> None:
    client = ThreeZoneClient(snapshot=_three_zone_snapshot(zone2_bypassed=True))
    await setup(client)
    zones = _zone_entities(hass)
    client.calls.clear()
    await hass.services.async_call(
        DOMAIN,
        "zone_bypass",
        {"entity_id": [zones[1], zones[2], zones[3]], "code": CODE, "bypass": True},
        blocking=True,
    )
    assert _bypass_calls(client) == [(1, True), (3, True)]


async def test_duplicate_targets_send_once(
    hass: HomeAssistant,
    three_zones: tuple[FakeClient, ClientHarness],
) -> None:
    client, _mock = three_zones
    zones = _zone_entities(hass)
    client.calls.clear()
    await hass.services.async_call(
        DOMAIN,
        "zone_bypass",
        {"entity_id": [zones[1], zones[1]], "code": CODE, "bypass": True},
        blocking=True,
    )
    assert _bypass_calls(client) == [(1, True)]


async def test_single_zone_unacknowledged_has_no_restore_clause(
    hass: HomeAssistant,
    three_zones: tuple[FakeClient, ClientHarness],
) -> None:
    _client, _mock_client = three_zones
    zones = _zone_entities(hass)
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    coordinator = entry.runtime_data.coordinator

    async def _never_ack(
        _zone_id: int, *, bypassed: bool, pin: str | None = None
    ) -> bool:
        return False

    coordinator.async_set_zone_bypass = _never_ack
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            DOMAIN,
            "zone_bypass",
            {"entity_id": [zones[1]], "code": CODE, "bypass": True},
            blocking=True,
        )
    assert err.value.translation_key == "zone_bypass_not_acknowledged"
    assert "rolled_back_zone_numbers" not in (err.value.translation_placeholders or {})


async def test_two_panels_dedupe_same_zone_number(
    hass: HomeAssistant,
    setup: Setup,
) -> None:
    client_a = ThreeZoneClient()
    await setup(client_a)
    entry_a = hass.config_entries.async_entries(DOMAIN)[0]
    coord_a = entry_a.runtime_data.coordinator
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
    coord_b = Elke27DataUpdateCoordinator(hass, entry_b)
    assert coord_a.client is not None
    object.__setattr__(coord_b, "_client", coord_a.client)
    coord_b.data = _three_zone_snapshot()
    coord_b.panel_device_id = "panel-b-device"
    entry_b.runtime_data = Elke27RuntimeData(coordinator=coord_b)
    entry_b.mock_state(hass, ConfigEntryState.LOADED)
    zone_reg = er.async_get(hass)
    zones_b: dict[int, str] = {}
    for zone_id in (1, 2, 3):
        zones_b[zone_id] = zone_reg.async_get_or_create(
            "binary_sensor",
            DOMAIN,
            f"{entry_b.entry_id}:{zone_id}",
            config_entry=entry_b,
            suggested_object_id=f"panel_b_zone_{zone_id}",
        ).entity_id
    zones_a = _zone_entities(hass)
    calls_a: list[tuple[int, bool]] = []
    calls_b: list[tuple[int, bool]] = []
    orig_a = coord_a.async_set_zone_bypass
    orig_b = coord_b.async_set_zone_bypass

    async def _a(zone_id: int, *, bypassed: bool, pin: str | None = None) -> bool:
        calls_a.append((zone_id, bypassed))
        return await orig_a(zone_id, bypassed=bypassed, pin=pin)

    async def _b(zone_id: int, *, bypassed: bool, pin: str | None = None) -> bool:
        calls_b.append((zone_id, bypassed))
        return await orig_b(zone_id, bypassed=bypassed, pin=pin)

    coord_a.async_set_zone_bypass = _a  # type: ignore[method-assign]
    coord_b.async_set_zone_bypass = _b  # type: ignore[method-assign]

    await hass.services.async_call(
        DOMAIN,
        "zone_bypass",
        {"entity_id": [zones_a[1], zones_b[1]], "code": CODE, "bypass": True},
        blocking=True,
    )
    assert calls_a == [(1, True)]
    assert calls_b == [(1, True)]


async def test_unbypass_failed_rebypass_rollback_reports_still_unbypassed(
    hass: HomeAssistant,
    setup: Setup,
    mock_client: ClientHarness,
) -> None:
    client = ThreeZoneClient(snapshot=_all_bypassed_snapshot())
    await setup(client)
    zones = _zone_entities(hass)
    base = client.async_set_zone_bypass

    async def _bypass(zone_id: int, **params: Any) -> Any:
        if zone_id == 2 and params.get("bypassed") is False:
            raise HomeAssistantError("panel refused zone 2 un-bypass")
        if zone_id == 1 and params.get("bypassed") is True:
            raise HomeAssistantError("rollback refused zone 1 re-bypass")
        return await base(zone_id, **params)

    client.calls.clear()
    _patch_bypass(client, mock_client, _bypass)
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            DOMAIN,
            "zone_bypass",
            {
                "entity_id": [zones[1], zones[2], zones[3]],
                "code": CODE,
                "bypass": False,
            },
            blocking=True,
        )
    assert err.value.translation_key == "zone_bypass_failed_still_unbypassed"
    ph = err.value.translation_placeholders or {}
    assert ph.get("still_unbypassed_zone_numbers") == "1"


async def test_unbypass_refusal_re_bypasses_prior_zones(
    hass: HomeAssistant,
    setup: Setup,
    mock_client: ClientHarness,
) -> None:
    client = ThreeZoneClient(snapshot=_all_bypassed_snapshot())
    await setup(client)
    zones = _zone_entities(hass)
    base = client.async_set_zone_bypass
    refusal = HomeAssistantError("panel refused zone 2 un-bypass")

    async def _bypass(zone_id: int, **params: Any) -> Any:
        if zone_id == 2 and params.get("bypassed") is False:
            raise refusal
        return await base(zone_id, **params)

    client.calls.clear()
    _patch_bypass(client, mock_client, _bypass)
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            DOMAIN,
            "zone_bypass",
            {
                "entity_id": [zones[1], zones[2], zones[3]],
                "code": CODE,
                "bypass": False,
            },
            blocking=True,
        )
    assert err.value.translation_key == "zone_bypass_failed_rolled_back"
    assert err.value.__cause__ is refusal
    assert _panel_bypass_calls(mock_client) == [(1, False), (2, False), (1, True)]


async def test_rollback_keeps_area_lock_while_unbypass_blocked(
    hass: HomeAssistant,
    three_zones: tuple[FakeClient, ClientHarness],
) -> None:
    client, mock_client = three_zones
    zones = _zone_entities(hass)
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    coordinator = entry.runtime_data.coordinator
    original_bypass = coordinator.async_set_zone_bypass
    panel_calls: list[tuple[int, bool]] = []
    rollback_reached = asyncio.Event()
    hold_rollback = asyncio.Event()
    send_index = 0

    async def _coordinator_bypass(
        zone_id: int, *, bypassed: bool, pin: str | None = None
    ) -> bool:
        nonlocal send_index
        if zone_id == 3 and send_index >= 2:
            return await original_bypass(zone_id, bypassed=bypassed, pin=pin)
        send_index += 1
        if send_index == 1:
            panel_calls.append((zone_id, bypassed))
            return await original_bypass(zone_id, bypassed=bypassed, pin=pin)
        if send_index == 2:
            panel_calls.append((zone_id, bypassed))
            raise HomeAssistantError("panel refused zone 2")
        assert send_index == 3
        rollback_reached.set()
        await hold_rollback.wait()
        ok = await original_bypass(zone_id, bypassed=bypassed, pin=pin)
        panel_calls.append((zone_id, bypassed))
        return ok

    coordinator.async_set_zone_bypass = _coordinator_bypass
    client.calls.clear()

    first = asyncio.create_task(
        hass.services.async_call(
            DOMAIN,
            "zone_bypass",
            {"entity_id": [zones[1], zones[2], zones[3]], "code": CODE, "bypass": True},
            blocking=True,
        )
    )
    await rollback_reached.wait()
    second = asyncio.create_task(
        hass.services.async_call(
            DOMAIN,
            "zone_bypass",
            {"entity_id": [zones[3]], "code": CODE, "bypass": True},
            blocking=True,
        )
    )
    for _ in range(20):
        await asyncio.sleep(0)
    assert panel_calls == [(1, True), (2, True)]
    assert _panel_bypass_calls(mock_client) == [(1, True)]
    assert not second.done()
    hold_rollback.set()
    with pytest.raises(HomeAssistantError):
        await first
    await second
    assert _panel_bypass_calls(mock_client) == [
        (1, True),
        (1, False),
        (3, True),
    ]


async def test_two_area_locks_block_arm_automatic_until_done(
    hass: HomeAssistant,
    two_areas: tuple[FakeClient, ClientHarness],
) -> None:
    client, mock_client = two_areas
    zones = _zone_entities(hass)
    alarms = sorted(entity_ids(hass, "alarm_control_panel"))
    first_bypass = asyncio.Event()
    hold = asyncio.Event()
    base = client.async_set_zone_bypass

    async def _bypass(zone_id: int, **params: Any) -> Any:
        if zone_id == 1 and params.get("bypassed"):
            first_bypass.set()
            await hold.wait()
        return await base(zone_id, **params)

    client.calls.clear()
    _patch_bypass(client, mock_client, _bypass)

    bypass_task = asyncio.create_task(
        hass.services.async_call(
            DOMAIN,
            "zone_bypass",
            {"entity_id": [zones[1], zones[4]], "code": CODE, "bypass": True},
            blocking=True,
        )
    )
    await first_bypass.wait()
    arm_task = asyncio.create_task(
        hass.services.async_call(
            DOMAIN,
            "alarm_arm_automatic",
            {"entity_id": alarms[1], "mode": "away", ATTR_CODE: CODE},
            blocking=True,
        )
    )
    for _ in range(20):
        await asyncio.sleep(0)
    assert not arm_task.done()
    assert not any(kind == "arm" for kind, _ in client.calls)
    hold.set()
    await bypass_task
    await arm_task
    assert any(kind == "bypass" for kind, _ in client.calls)
    assert any(kind == "arm" for kind, _ in client.calls)
