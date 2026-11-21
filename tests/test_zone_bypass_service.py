"""Real zone_bypass service tests for pre-validation, rollback, and locking."""

from __future__ import annotations

import asyncio
import contextvars
import dataclasses
import logging
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from custom_components.elke27.coordinator import Elke27DataUpdateCoordinator
    from homeassistant.core import HomeAssistant
from unittest.mock import AsyncMock, patch

from elke27_lib import AreaState, ArmMode, PanelSnapshot, ZoneState
from elke27_lib.errors import Elke27PanelError, Elke27PinRequiredError
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.elke27.const import (
    CONF_INTEGRATION_ID,
    CONF_LINK_KEYS_JSON,
    DOMAIN,
)
from custom_components.elke27.errors import code_rejected_error
from homeassistant.const import ATTR_CODE, CONF_HOST, CONF_PORT
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
    snapshot_with,
)

CODE = "1234"
KEY = PANEL_MAC
_ALARM_FOR_CUSTOM_BYPASS = "alarm_control_panel.house"
_REJECTED_PANEL_CODES = (11004, 11008, 11037)
_CUSTOM_BYPASS_REJECT = object()
PANEL_REFUSED_ZONE_2 = "panel refused zone 2"
PANEL_REFUSED_ZONE_3 = "panel A refused zone 3"


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


def _four_zone_two_area_snapshot() -> PanelSnapshot:
    return dataclasses.replace(
        panel_snapshot(),
        areas=MappingProxyType(
            {
                1: AreaState(
                    area_id=1, name="House", ready=True, arm_mode=ArmMode.DISARMED
                ),
                2: AreaState(
                    area_id=2, name="Garage", ready=True, arm_mode=ArmMode.DISARMED
                ),
            }
        ),
        zones=MappingProxyType(
            {
                1: ZoneState(zone_id=1, name="Aaa One", area_id=1, open=False),
                2: ZoneState(zone_id=2, name="Ccc Two", area_id=2, open=False),
                3: ZoneState(zone_id=3, name="Bbb Three", area_id=2, open=False),
                4: ZoneState(zone_id=4, name="Ddd Four", area_id=1, open=False),
            }
        ),
    )


class FourZoneClient(FakeClient):
    def __init__(self) -> None:
        super().__init__(command_result(refresh=True))
        self.snapshot = _four_zone_two_area_snapshot()


class _CustomBypassScriptedClient(FakeClient):
    def __init__(
        self,
        outcomes: list[BaseException | object | None],
        rejected_code: int,
    ) -> None:
        super().__init__(command_result(refresh=True))
        self.rejected_code = rejected_code
        self.outcomes = list(outcomes)
        self.snapshot = snapshot_with(
            panel_snapshot(),
            areas={1: AreaState(area_id=1, name="House", ready=False)},
            zones={
                1: ZoneState(zone_id=1, name="Front Door", area_id=1, open=True),
                2: ZoneState(zone_id=2, name="Back Door", area_id=1, open=True),
            },
        )

    async def _write(self, kind: str, **params: Any) -> Any:
        self.calls.append((kind, params))
        outcome = self.outcomes.pop(0) if self.outcomes else None
        if outcome is _CUSTOM_BYPASS_REJECT:
            raise Elke27PanelError(self.rejected_code, "not authorized")
        if outcome is not None:
            raise outcome
        return self.write_result


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


def _panel_b_snapshot() -> PanelSnapshot:
    return dataclasses.replace(
        _three_zone_snapshot(),
        panel=dataclasses.replace(
            panel_snapshot().panel,
            mac=None,
            serial=PANEL_SERIAL_2,
            panel_name="Panel B",
        ),
    )


def _zone_entity_for_serial(
    hass: HomeAssistant, panel_serial: str, zone_id: int
) -> str:
    for entity_id in entity_ids(hass, "binary_sensor"):
        entry = er.async_get(hass).async_get(entity_id)
        if entry is not None and entry.unique_id == f"{panel_serial}:{zone_id}":
            return entity_id
    msg = f"zone {zone_id} on {panel_serial}"
    raise AssertionError(msg)


async def _setup_second_panel(
    hass: HomeAssistant, mock_client: ClientHarness, snapshot: PanelSnapshot
) -> ClientHarness:
    client_b = ThreeZoneClient(snapshot=snapshot)
    harness_b = ClientHarness()
    client_b.install(harness_b.client)
    harness_b.snapshot = snapshot
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
    with patch(
        "custom_components.elke27.coordinator.Elke27Client",
        return_value=harness_b.client,
    ):
        assert await hass.config_entries.async_setup(entry_b.entry_id)
        await hass.async_block_till_done()
    return harness_b


def _client_bypass_calls(harness: ClientHarness) -> list[tuple[int, bool]]:
    out: list[tuple[int, bool]] = []
    mock = harness.client.async_set_zone_bypass
    for call in mock.await_args_list:
        zone_id = call.args[0]
        bypassed = call.kwargs["bypassed"]
        out.append((zone_id, bypassed))
    return out


def _snapshot_with_crosslock_zones(snapshot: PanelSnapshot) -> PanelSnapshot:
    zones = dict(snapshot.zones)
    zones[91] = ZoneState(
        zone_id=91, name="Cross A", area_id=1, open=False, bypassed=False
    )
    zones[92] = ZoneState(
        zone_id=92, name="Cross B", area_id=1, open=False, bypassed=False
    )
    return dataclasses.replace(snapshot, zones=MappingProxyType(zones))


def _assert_entity_on_entry(hass: HomeAssistant, entity_id: str, entry_id: str) -> None:
    reg_entry = er.async_get(hass).async_get(entity_id)
    assert reg_entry is not None
    assert reg_entry.config_entry_id == entry_id


def _rename_zone_entity(hass: HomeAssistant, entity_id: str, object_id: str) -> str:
    new_id = f"binary_sensor.{object_id}"
    er.async_get(hass).async_update_entity(entity_id, new_entity_id=new_id)
    return new_id


def _crosslock_zone_entities(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    panel_serial: str,
    *,
    slug_91: str,
    slug_92: str,
) -> tuple[str, str]:
    entity_91 = _rename_zone_entity(
        hass, _zone_entity_for_serial(hass, panel_serial, 91), slug_91
    )
    entity_92 = _rename_zone_entity(
        hass, _zone_entity_for_serial(hass, panel_serial, 92), slug_92
    )
    _assert_entity_on_entry(hass, entity_91, entry.entry_id)
    _assert_entity_on_entry(hass, entity_92, entry.entry_id)
    return entity_91, entity_92


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


async def _call_zone_bypass(
    hass: HomeAssistant, entity_ids: list[str], *, bypass: bool = True
) -> None:
    await hass.services.async_call(
        DOMAIN,
        "zone_bypass",
        {"entity_id": entity_ids, "code": CODE, "bypass": bypass},
        blocking=True,
    )


def _zone_bypassed_snapshot(
    snapshot: PanelSnapshot, zone_id: int, *, bypassed: bool
) -> PanelSnapshot:
    zones = dict(snapshot.zones)
    zones[zone_id] = dataclasses.replace(zones[zone_id], bypassed=bypassed)
    return dataclasses.replace(snapshot, zones=MappingProxyType(zones))


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
            suggested_object_id="house_zone_b_missing",
        )
        .entity_id
    )
    zones = _zone_entities(hass)
    call_order = sorted([zones[3], missing, zones[1]])
    assert call_order == [zones[1], missing, zones[3]]
    client.calls.clear()
    with pytest.raises(ServiceValidationError) as err:
        await _call_zone_bypass(hass, call_order)
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
            raise HomeAssistantError(PANEL_REFUSED_ZONE_2)
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
            raise HomeAssistantError(PANEL_REFUSED_ZONE_2)
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
    assert err.value.translation_key == "zone_bypass_failed_still_bypassed_no_restore"
    ph = err.value.translation_placeholders or {}
    assert ph.get("still_bypassed_zone_numbers") == "1"
    assert "rolled_back_zone_numbers" not in ph
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


async def test_two_prior_zones_roll_back_in_reverse(
    hass: HomeAssistant,
    three_zones: tuple[FakeClient, ClientHarness],
) -> None:
    client, mock_client = three_zones
    zones = _zone_entities(hass)
    panel_msg = "panel refused zone 3"
    base = client.async_set_zone_bypass
    calls: list[tuple[int, bool]] = []

    async def _bypass(zone_id: int, **params: Any) -> Any:
        bypassed = params.get("bypassed")
        calls.append((zone_id, bypassed))
        if zone_id == 3 and bypassed:
            raise HomeAssistantError(panel_msg)
        return await base(zone_id, **params)

    client.calls.clear()
    _patch_bypass(client, mock_client, _bypass)
    with pytest.raises(HomeAssistantError) as err:
        await _call_zone_bypass(hass, [zones[1], zones[2], zones[3]])
    assert err.value.translation_key == "zone_bypass_failed_rolled_back"
    assert calls == [(1, True), (2, True), (3, True), (2, False), (1, False)]
    ph = err.value.translation_placeholders or {}
    assert set(ph["rolled_back_zone_numbers"].split(", ")) == {"1", "2"}
    assert panel_msg in ph["reason"]


async def test_pin_required_mid_list_rolls_back(
    hass: HomeAssistant,
    three_zones: tuple[FakeClient, ClientHarness],
) -> None:
    client, mock_client = three_zones
    zones = _zone_entities(hass)
    pin_msg = "pin required"
    library_error = Elke27PinRequiredError(pin_msg)
    base = client.async_set_zone_bypass
    calls: list[tuple[int, bool]] = []

    async def _bypass(zone_id: int, **params: Any) -> Any:
        bypassed = params.get("bypassed")
        calls.append((zone_id, bypassed))
        if zone_id == 2 and bypassed:
            raise library_error
        return await base(zone_id, **params)

    client.calls.clear()
    _patch_bypass(client, mock_client, _bypass)
    with pytest.raises(HomeAssistantError) as err:
        await _call_zone_bypass(hass, [zones[1], zones[2]])
    assert err.value.translation_key == "pin_required_bypass"
    assert err.value.translation_domain == DOMAIN
    assert err.value.translation_placeholders in (None, {})
    assert err.value.__cause__ is library_error
    assert calls == [(1, True), (2, True), (1, False)]


async def test_unacknowledged_mid_list_with_failed_rollback(
    hass: HomeAssistant,
    three_zones: tuple[FakeClient, ClientHarness],
) -> None:
    _client, _mock = three_zones
    zones = _zone_entities(hass)
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    coordinator = entry.runtime_data.coordinator
    original_bypass = coordinator.async_set_zone_bypass
    calls: list[tuple[int, bool]] = []

    async def _stub(zone_id: int, *, bypassed: bool, pin: str | None = None) -> bool:
        calls.append((zone_id, bypassed))
        if zone_id == 2 and bypassed:
            return False
        if zone_id == 1 and not bypassed:
            raise HomeAssistantError("rollback refused zone 1")
        return await original_bypass(zone_id, bypassed=bypassed, pin=pin)

    coordinator.async_set_zone_bypass = _stub
    with pytest.raises(HomeAssistantError) as err:
        await _call_zone_bypass(hass, [zones[1], zones[2]])
    assert (
        err.value.translation_key
        == "zone_bypass_failed_not_acknowledged_still_bypassed_no_restore"
    )
    ph = err.value.translation_placeholders or {}
    assert ph.get("still_bypassed_zone_numbers") == "1"
    assert calls == [(1, True), (2, True), (1, False)]


async def test_unacknowledged_lists_rolled_back_zones(
    hass: HomeAssistant,
    three_zones: tuple[FakeClient, ClientHarness],
) -> None:
    _client, _mock = three_zones
    zones = _zone_entities(hass)
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    coordinator = entry.runtime_data.coordinator
    original_bypass = coordinator.async_set_zone_bypass

    async def _stub(zone_id: int, *, bypassed: bool, pin: str | None = None) -> bool:
        if zone_id == 2 and bypassed:
            return False
        return await original_bypass(zone_id, bypassed=bypassed, pin=pin)

    coordinator.async_set_zone_bypass = _stub
    with pytest.raises(HomeAssistantError) as err:
        await _call_zone_bypass(hass, [zones[1], zones[2]])
    assert (
        err.value.translation_key == "zone_bypass_failed_not_acknowledged_rolled_back"
    )
    ph = err.value.translation_placeholders or {}
    assert ph.get("rolled_back_zone_numbers") == "1"


async def test_code_rejected_mid_list_rolls_back_and_raises(
    hass: HomeAssistant,
    three_zones: tuple[FakeClient, ClientHarness],
) -> None:
    client, mock_client = three_zones
    zones = _zone_entities(hass)
    base = client.async_set_zone_bypass

    panel_rejected = ServiceValidationError(
        translation_domain=DOMAIN, translation_key="code_rejected"
    )

    async def _bypass(zone_id: int, **params: Any) -> Any:
        if zone_id == 2 and params.get("bypassed") is True:
            raise panel_rejected
        return await base(zone_id, **params)

    client.calls.clear()
    _patch_bypass(client, mock_client, _bypass)
    with pytest.raises(ServiceValidationError) as err:
        await _call_zone_bypass(hass, [zones[1], zones[2]])
    assert err.value.translation_key == "code_rejected"
    assert err.value.translation_domain == DOMAIN
    assert err.value is panel_rejected
    assert code_rejected_error(err.value) is err.value
    assert err.value.__cause__ is None
    assert _panel_bypass_calls(mock_client) == [(1, True), (2, True), (1, False)]


async def test_duplicate_targets_preserve_order_under_sorted_entity_ids(
    hass: HomeAssistant,
    three_zones: tuple[FakeClient, ClientHarness],
) -> None:
    client, mock_client = three_zones
    zones = _zone_entities(hass)
    base = client.async_set_zone_bypass
    calls: list[tuple[int, bool]] = []

    async def _ok(zone_id: int, **params: Any) -> Any:
        calls.append((zone_id, params.get("bypassed")))
        return await base(zone_id, **params)

    client.calls.clear()
    _patch_bypass(client, mock_client, _ok)
    await _call_zone_bypass(hass, [zones[1], zones[1], zones[2], zones[1]])
    assert calls == [(1, True), (2, True)]


async def test_area_less_zone_skips_lock_and_still_bypasses(
    hass: HomeAssistant,
    setup: Setup,
    mock_client: ClientHarness,
) -> None:
    base = _three_zone_snapshot()
    zones_map = dict(base.zones)
    zones_map[5] = ZoneState(
        zone_id=5, name="No area", area_id=None, open=False, bypassed=False
    )
    client = ThreeZoneClient(
        snapshot=dataclasses.replace(base, zones=MappingProxyType(zones_map))
    )
    await setup(client)
    zones = _zone_entities(hass)
    assert 5 in zones
    client.calls.clear()
    await _call_zone_bypass(hass, [zones[1], zones[5]])
    assert _panel_bypass_calls(mock_client) == [(1, True), (5, True)]


async def test_zone_already_bypassed_under_lock_is_not_sent_again(
    hass: HomeAssistant,
    three_zones: tuple[FakeClient, ClientHarness],
) -> None:
    client, mock_client = three_zones
    zones = _zone_entities(hass)
    base = client.async_set_zone_bypass

    async def _bypass(zone_id: int, **params: Any) -> Any:
        result = await base(zone_id, **params)
        if zone_id == 1 and params.get("bypassed"):
            snap = _zone_bypassed_snapshot(client.snapshot, 1, bypassed=True)
            client.snapshot = _zone_bypassed_snapshot(snap, 2, bypassed=True)
        return result

    client.calls.clear()
    _patch_bypass(client, mock_client, _bypass)
    await _call_zone_bypass(hass, [zones[1], zones[2]])
    assert _panel_bypass_calls(mock_client) == [(1, True)]


async def test_snapshot_missing_under_lock_rolls_back_and_reports(
    hass: HomeAssistant,
    three_zones: tuple[FakeClient, ClientHarness],
) -> None:
    client, mock_client = three_zones
    zones = _zone_entities(hass)
    base = client.async_set_zone_bypass
    empty_snapshot = PanelSnapshot.empty()

    async def _bypass(zone_id: int, **params: Any) -> Any:
        result = await base(zone_id, **params)
        if zone_id == 1 and params.get("bypassed"):
            client.snapshot = empty_snapshot
        return result

    client.calls.clear()
    _patch_bypass(client, mock_client, _bypass)
    with pytest.raises(HomeAssistantError):
        await _call_zone_bypass(hass, [zones[1], zones[2]])
    assert _panel_bypass_calls(mock_client) == [(1, True), (1, False)]


async def test_still_changed_after_rollback_logs_warning(
    hass: HomeAssistant,
    three_zones: tuple[FakeClient, ClientHarness],
    caplog: pytest.LogCaptureFixture,
) -> None:
    client, _mock = three_zones
    zones = _zone_entities(hass)
    base = client.async_set_zone_bypass
    caplog.set_level(logging.WARNING)

    async def _bypass(zone_id: int, **params: Any) -> Any:
        if zone_id == 2 and params.get("bypassed"):
            raise HomeAssistantError(PANEL_REFUSED_ZONE_2)
        if zone_id == 1 and params.get("bypassed") is False:
            raise HomeAssistantError("rollback refused zone 1")
        return await base(zone_id, **params)

    _patch_bypass(client, _mock, _bypass)
    with pytest.raises(HomeAssistantError):
        await _call_zone_bypass(hass, [zones[1], zones[2]])
    assert any(
        "zones still changed after rollback" in record.message
        for record in caplog.records
    )


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
    mock_client: ClientHarness,
) -> None:
    client_a = ThreeZoneClient()
    await setup(client_a)
    harness_b = await _setup_second_panel(hass, mock_client, _panel_b_snapshot())
    zone_a1 = _zone_entity_for_serial(hass, PANEL_MAC, 1)
    zone_b1 = _zone_entity_for_serial(hass, PANEL_SERIAL_2, 1)
    await hass.services.async_call(
        DOMAIN,
        "zone_bypass",
        {"entity_id": [zone_a1, zone_b1], "code": CODE, "bypass": True},
        blocking=True,
    )
    assert _panel_bypass_calls(mock_client) == [(1, True)]
    assert _client_bypass_calls(harness_b) == [(1, True)]


async def test_two_panels_roll_back_all_prior_zones(
    hass: HomeAssistant,
    setup: Setup,
    mock_client: ClientHarness,
) -> None:
    client_a = ThreeZoneClient()
    await setup(client_a)
    harness_b = await _setup_second_panel(hass, mock_client, _panel_b_snapshot())
    zone_a1 = _zone_entity_for_serial(hass, PANEL_MAC, 1)
    zone_a2 = _zone_entity_for_serial(hass, PANEL_MAC, 2)
    zone_b1 = _zone_entity_for_serial(hass, PANEL_SERIAL_2, 1)
    zone_b2 = _zone_entity_for_serial(hass, PANEL_SERIAL_2, 2)
    zone_b3 = _zone_entity_for_serial(hass, PANEL_SERIAL_2, 3)
    client_b = ThreeZoneClient(snapshot=_panel_b_snapshot())
    client_b.install(harness_b.client)
    base_b = client_b.async_set_zone_bypass
    panel_b_attempts: list[tuple[int, bool]] = []

    async def _fail_b3(zone_id: int, **params: Any) -> Any:
        bypassed = params.get("bypassed")
        panel_b_attempts.append((zone_id, bypassed))
        if zone_id == 3 and bypassed is True:
            raise HomeAssistantError("panel B refused zone 3")
        return await base_b(zone_id, **params)

    harness_b.client.async_set_zone_bypass = AsyncMock(side_effect=_fail_b3)
    with pytest.raises(HomeAssistantError) as err:
        await _call_zone_bypass(
            hass,
            [zone_a1, zone_a2, zone_b1, zone_b2, zone_b3],
        )
    assert err.value.translation_key == "zone_bypass_failed_rolled_back"
    assert _panel_bypass_calls(mock_client) == [
        (1, True),
        (2, True),
        (2, False),
        (1, False),
    ]
    assert panel_b_attempts == [
        (1, True),
        (2, True),
        (3, True),
        (2, False),
        (1, False),
    ]
    ph = err.value.translation_placeholders or {}
    rolled = ph["rolled_back_zone_numbers"].split(", ")
    assert len(rolled) == 4
    assert rolled.count("1") == 2
    assert rolled.count("2") == 2


async def test_two_panels_both_locks_held_during_send(
    hass: HomeAssistant,
    setup: Setup,
    mock_client: ClientHarness,
) -> None:
    client_a = ThreeZoneClient()
    await setup(client_a)
    await _setup_second_panel(hass, mock_client, _panel_b_snapshot())
    zone_a1 = _zone_entity_for_serial(hass, PANEL_MAC, 1)
    zone_b1 = _zone_entity_for_serial(hass, PANEL_SERIAL_2, 1)
    entry_a = hass.config_entries.async_entries(DOMAIN)[0]
    entry_b = next(
        e
        for e in hass.config_entries.async_entries(DOMAIN)
        if e.unique_id == PANEL_SERIAL_2
    )
    coord_a = entry_a.runtime_data.coordinator
    coord_b = entry_b.runtime_data.coordinator
    holds: list[str] = []
    started = asyncio.Event()
    hold = asyncio.Event()
    base_a = client_a.async_set_zone_bypass

    def _wrap_lock(coordinator: Elke27DataUpdateCoordinator, label: str) -> None:
        base = coordinator.area_arm_lock

        def _lock(area_id: int) -> Any:
            lock = base(area_id)

            class Tracked:
                async def __aenter__(self) -> None:
                    holds.append(label)
                    await lock.acquire()

                async def __aexit__(self, *exc: object) -> None:
                    holds.remove(label)
                    lock.release()

            return Tracked()

        coordinator.area_arm_lock = _lock

    _wrap_lock(coord_a, "panel_a")
    _wrap_lock(coord_b, "panel_b")

    async def _slow_a(zone_id: int, **params: Any) -> Any:
        if zone_id == 1 and params.get("bypassed") is True:
            started.set()
            assert "panel_a" in holds
            assert "panel_b" in holds
            await hold.wait()
        return await base_a(zone_id, **params)

    _patch_bypass(client_a, mock_client, _slow_a)
    task = asyncio.create_task(_call_zone_bypass(hass, [zone_a1, zone_b1]))
    await started.wait()
    hold.set()
    await task


async def test_two_panels_first_panel_send_holds_second(
    hass: HomeAssistant,
    setup: Setup,
    mock_client: ClientHarness,
) -> None:
    client_a = ThreeZoneClient()
    await setup(client_a)
    harness_b = await _setup_second_panel(hass, mock_client, _panel_b_snapshot())
    zone_a1 = _zone_entity_for_serial(hass, PANEL_MAC, 1)
    zone_a2 = _zone_entity_for_serial(hass, PANEL_MAC, 2)
    zone_b1 = _zone_entity_for_serial(hass, PANEL_SERIAL_2, 1)
    client_b = ThreeZoneClient(snapshot=_panel_b_snapshot())
    client_b.install(harness_b.client)
    started_a = asyncio.Event()
    hold_a = asyncio.Event()
    base_a = client_a.async_set_zone_bypass
    base_b = client_b.async_set_zone_bypass
    b_calls: list[tuple[int, bool]] = []

    async def _slow_a(zone_id: int, **params: Any) -> Any:
        if zone_id == 1 and params.get("bypassed") is True:
            started_a.set()
            await hold_a.wait()
        return await base_a(zone_id, **params)

    async def _track_b(zone_id: int, **params: Any) -> Any:
        b_calls.append((zone_id, params.get("bypassed")))
        return await base_b(zone_id, **params)

    _patch_bypass(client_a, mock_client, _slow_a)
    harness_b.client.async_set_zone_bypass = AsyncMock(side_effect=_track_b)
    task = asyncio.create_task(_call_zone_bypass(hass, [zone_a1, zone_a2, zone_b1]))
    await started_a.wait()
    for _ in range(20):
        await asyncio.sleep(0)
    assert b_calls == []
    hold_a.set()
    await task
    assert _panel_bypass_calls(mock_client) == [(1, True), (2, True)]
    assert b_calls == [(1, True)]


async def test_two_panels_rollback_stays_on_originating_coordinator(
    hass: HomeAssistant,
    setup: Setup,
    mock_client: ClientHarness,
) -> None:
    client_a = ThreeZoneClient()
    await setup(client_a)
    harness_b = await _setup_second_panel(hass, mock_client, _panel_b_snapshot())
    zone_a1 = _zone_entity_for_serial(hass, PANEL_MAC, 1)
    zone_b1 = _zone_entity_for_serial(hass, PANEL_SERIAL_2, 1)
    client_b = ThreeZoneClient(snapshot=_panel_b_snapshot())
    client_b.install(harness_b.client)
    base_b = client_b.async_set_zone_bypass
    panel_b_attempts: list[tuple[int, bool]] = []

    async def _fail_b1(zone_id: int, **params: Any) -> Any:
        bypassed = params.get("bypassed")
        panel_b_attempts.append((zone_id, bypassed))
        if zone_id == 1 and bypassed is True:
            raise HomeAssistantError("panel B refused zone 1")
        return await base_b(zone_id, **params)

    harness_b.client.async_set_zone_bypass = AsyncMock(side_effect=_fail_b1)
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            DOMAIN,
            "zone_bypass",
            {"entity_id": [zone_a1, zone_b1], "code": CODE, "bypass": True},
            blocking=True,
        )
    assert _panel_bypass_calls(mock_client) == [(1, True), (1, False)]
    assert panel_b_attempts == [(1, True)]


async def test_unbypass_waits_for_lock_and_does_not_return_on_stale_pre_lock_state(
    hass: HomeAssistant,
    three_zones: tuple[FakeClient, ClientHarness],
) -> None:
    client, mock_client = three_zones
    zones = _zone_entities(hass)
    base = client.async_set_zone_bypass
    hold_bypass = asyncio.Event()
    bypass_started = asyncio.Event()

    async def _slow_bypass(zone_id: int, **params: Any) -> Any:
        if zone_id == 1 and params.get("bypassed") is True:
            bypass_started.set()
            await hold_bypass.wait()
        result = await base(zone_id, **params)
        if zone_id == 1 and params.get("bypassed") is True:
            client.snapshot = _zone_bypassed_snapshot(
                client.snapshot, zone_id=1, bypassed=True
            )
        return result

    client.calls.clear()
    _patch_bypass(client, mock_client, _slow_bypass)
    bypass_task = asyncio.create_task(
        hass.services.async_call(
            DOMAIN,
            "zone_bypass",
            {"entity_id": [zones[1]], "code": CODE, "bypass": True},
            blocking=True,
        )
    )
    await bypass_started.wait()
    unbypass_task = asyncio.create_task(
        hass.services.async_call(
            DOMAIN,
            "zone_bypass",
            {"entity_id": [zones[1]], "code": CODE, "bypass": False},
            blocking=True,
        )
    )
    for _ in range(20):
        await asyncio.sleep(0)
    assert not unbypass_task.done()
    hold_bypass.set()
    await bypass_task
    await unbypass_task
    assert _panel_bypass_calls(mock_client) == [(1, True), (1, False)]


async def test_single_zone_pin_required_not_map_to_not_acknowledged(
    hass: HomeAssistant,
    three_zones: tuple[FakeClient, ClientHarness],
) -> None:
    client, mock_client = three_zones
    zones = _zone_entities(hass)
    library_error = Elke27PinRequiredError("pin required")

    async def _pin(_zone_id: int, **_params: Any) -> Any:
        raise library_error

    client.calls.clear()
    _patch_bypass(client, mock_client, _pin)
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            DOMAIN,
            "zone_bypass",
            {"entity_id": [zones[1]], "code": CODE, "bypass": True},
            blocking=True,
        )
    assert err.value.translation_key == "pin_required_bypass"
    assert err.value.__cause__ is library_error


async def test_single_zone_panel_error_keeps_original_cause_chain(
    hass: HomeAssistant,
    three_zones: tuple[FakeClient, ClientHarness],
) -> None:
    client, mock_client = three_zones
    zones = _zone_entities(hass)
    inner = HomeAssistantError("inner")

    async def _fail(_zone_id: int, **_params: Any) -> Any:
        raise HomeAssistantError("panel refused") from inner

    client.calls.clear()
    _patch_bypass(client, mock_client, _fail)
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            DOMAIN,
            "zone_bypass",
            {"entity_id": [zones[1]], "code": CODE, "bypass": True},
            blocking=True,
        )
    assert err.value.__cause__ is inner


async def test_zone_vanished_under_lock_rolls_back_prior_changes(
    hass: HomeAssistant,
    three_zones: tuple[FakeClient, ClientHarness],
) -> None:
    client, mock_client = three_zones
    zones = _zone_entities(hass)
    base = client.async_set_zone_bypass

    async def _bypass(zone_id: int, **params: Any) -> Any:
        result = await base(zone_id, **params)
        if zone_id == 1 and params.get("bypassed") is True:
            snap = _zone_bypassed_snapshot(client.snapshot, zone_id=1, bypassed=True)
            client.snapshot = dataclasses.replace(
                snap, zones=MappingProxyType({1: snap.zones[1]})
            )
        return result

    client.calls.clear()
    _patch_bypass(client, mock_client, _bypass)
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            DOMAIN,
            "zone_bypass",
            {"entity_id": [zones[1], zones[2]], "code": CODE, "bypass": True},
            blocking=True,
        )
    assert err.value.translation_key == "zone_bypass_failed_rolled_back"
    ph = err.value.translation_placeholders or {}
    assert zones[2] in ph["reason"]
    assert isinstance(err.value.__cause__, HomeAssistantError)
    assert err.value.__cause__.translation_key == "zone_not_found"
    assert _panel_bypass_calls(mock_client) == [(1, True), (1, False)]


_cross_role: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "_cross_role", default=None
)


class _CrossBarrier:
    def __init__(self) -> None:
        self._first: dict[str, str | None] = {"x": None, "y": None}
        self.peer_blocked = asyncio.Event()
        self._mu = asyncio.Lock()

    async def after_first(self, role: str, panel: str) -> None:
        wait_peer = False
        async with self._mu:
            self._first[role] = panel
            other = "y" if role == "x" else "x"
            other_panel = self._first[other]
            if other_panel is not None and other_panel != panel:
                return
            wait_peer = other_panel is None
        if wait_peer:
            await self.peer_blocked.wait()
            self.peer_blocked.clear()


def _install_crosslock_barrier(
    coord_a: Elke27DataUpdateCoordinator,
    coord_b: Elke27DataUpdateCoordinator,
    *,
    entry_a_id: str,
    entry_b_id: str,
    barrier: _CrossBarrier,
) -> None:
    lock_counts: dict[int, int] = {}

    def _panel(entry_id: str) -> str:
        return "a" if entry_id == entry_a_id else "b"

    def _wrap(coordinator: Elke27DataUpdateCoordinator, entry_id: str) -> None:
        base = coordinator.area_arm_lock

        def _factory(area_id: int) -> Any:
            lock = base(area_id)

            class _Tracked:
                async def __aenter__(self) -> None:
                    role = _cross_role.get()
                    if role in ("x", "y") and lock.locked():
                        barrier.peer_blocked.set()
                    await lock.acquire()
                    if role not in ("x", "y"):
                        return
                    tid = id(asyncio.current_task())
                    lock_counts[tid] = lock_counts.get(tid, 0) + 1
                    if lock_counts[tid] == 1:
                        await barrier.after_first(role, _panel(entry_id))

                async def __aexit__(self, *exc: object) -> None:
                    lock.release()

            return _Tracked()

        coordinator.area_arm_lock = _factory

    _wrap(coord_a, entry_a_id)
    _wrap(coord_b, entry_b_id)


async def _zone_bypass_with_role(
    hass: HomeAssistant, entity_ids: list[str], role: str
) -> None:
    token = _cross_role.set(role)
    try:
        await hass.services.async_call(
            DOMAIN,
            "zone_bypass",
            {"entity_id": entity_ids, "code": CODE, "bypass": True},
            blocking=True,
        )
    finally:
        _cross_role.reset(token)


async def test_two_panels_crossing_lock_order_completes_with_sorted_entry_locks(
    hass: HomeAssistant,
    setup: Setup,
    mock_client: ClientHarness,
) -> None:
    snap_a = _snapshot_with_crosslock_zones(_three_zone_snapshot())
    client_a = ThreeZoneClient(snapshot=snap_a)
    await setup(client_a)
    harness_b = await _setup_second_panel(
        hass,
        mock_client,
        _snapshot_with_crosslock_zones(_panel_b_snapshot()),
    )
    entry_a = hass.config_entries.async_entries(DOMAIN)[0]
    entry_b = next(
        e
        for e in hass.config_entries.async_entries(DOMAIN)
        if e.unique_id == PANEL_SERIAL_2
    )
    xa91, xa92 = _crosslock_zone_entities(
        hass,
        entry_a,
        PANEL_MAC,
        slug_91="aaa_panel_a_zone",
        slug_92="zzz_panel_a_zone",
    )
    xb91, xb92 = _crosslock_zone_entities(
        hass,
        entry_b,
        PANEL_SERIAL_2,
        slug_91="zzz_panel_b_zone",
        slug_92="aaa_panel_b_zone",
    )
    x_a, x_b = xa91, xb91
    y_b, y_a = xb92, xa92
    assert x_a < x_b
    assert y_b < y_a
    coord_a = entry_a.runtime_data.coordinator
    coord_b = entry_b.runtime_data.coordinator
    barrier = _CrossBarrier()
    _install_crosslock_barrier(
        coord_a,
        coord_b,
        entry_a_id=entry_a.entry_id,
        entry_b_id=entry_b.entry_id,
        barrier=barrier,
    )
    send_log: list[tuple[str, int, bool]] = []
    base_a = client_a.async_set_zone_bypass
    base_b = harness_b.client.async_set_zone_bypass

    async def _panel_a_send(zone_id: int, **params: Any) -> Any:
        bypassed = params.get("bypassed") is True
        send_log.append(("a", zone_id, bypassed))
        return await base_a(zone_id, **params)

    async def _panel_b_send(zone_id: int, **params: Any) -> Any:
        bypassed = params.get("bypassed") is True
        send_log.append(("b", zone_id, bypassed))
        return await base_b(zone_id, **params)

    client_a.calls.clear()
    _patch_bypass(client_a, mock_client, _panel_a_send)
    harness_b.client.async_set_zone_bypass = AsyncMock(side_effect=_panel_b_send)
    call_y = asyncio.create_task(_zone_bypass_with_role(hass, [y_b, y_a], "y"))
    call_x = asyncio.create_task(_zone_bypass_with_role(hass, [x_a, x_b], "x"))
    await asyncio.wait_for(asyncio.gather(call_x, call_y), timeout=10.0)
    panel_a = [
        (zone_id, bypassed) for panel, zone_id, bypassed in send_log if panel == "a"
    ]
    panel_b = [
        (zone_id, bypassed) for panel, zone_id, bypassed in send_log if panel == "b"
    ]
    assert sorted(panel_a) == [(91, True), (92, True)]
    assert sorted(panel_b) == [(91, True), (92, True)]
    assert sorted(_panel_bypass_calls(mock_client)) == [(91, True), (92, True)]
    assert sorted(_client_bypass_calls(harness_b)) == [(91, True), (92, True)]


async def test_code_rejected_after_partial_rollback_shows_notification(
    hass: HomeAssistant,
    three_zones: tuple[FakeClient, ClientHarness],
) -> None:
    client, mock_client = three_zones
    zones = _zone_entities(hass)
    entry_id = hass.config_entries.async_entries(DOMAIN)[0].entry_id
    base = client.async_set_zone_bypass
    notices: list[tuple[str, str, str]] = []

    async def _bypass(zone_id: int, **params: Any) -> Any:
        if zone_id == 2 and params.get("bypassed") is True:
            raise ServiceValidationError(translation_key="code_rejected")
        msg = "rollback refused zone 1"
        if zone_id == 1 and params.get("bypassed") is False:
            raise HomeAssistantError(msg)
        return await base(zone_id, **params)

    client.calls.clear()
    _patch_bypass(client, mock_client, _bypass)

    def _record_create(
        _hass: HomeAssistant, *, title: str, message: str, notification_id: str
    ) -> None:
        notices.append((title, message, notification_id))

    with (
        patch(
            "custom_components.elke27.zone_notifications.persistent_notification.async_create",
            side_effect=_record_create,
        ),
        pytest.raises(ServiceValidationError) as err,
    ):
        await hass.services.async_call(
            DOMAIN,
            "zone_bypass",
            {"entity_id": [zones[1], zones[2]], "code": CODE, "bypass": True},
            blocking=True,
        )
    assert err.value.translation_key == "code_rejected"
    assert err.value.__cause__ is None
    assert notices
    assert "still bypassed" in notices[0][1].lower()
    assert "zones 1" in notices[0][1]
    assert notices[0][2] == f"elke27_zone_bypass_still_bypassed_{entry_id}"


async def test_pin_required_after_partial_rollback_shows_notification(
    hass: HomeAssistant,
    three_zones: tuple[FakeClient, ClientHarness],
) -> None:
    client, mock_client = three_zones
    zones = _zone_entities(hass)
    entry_id = hass.config_entries.async_entries(DOMAIN)[0].entry_id
    base = client.async_set_zone_bypass
    notices: list[tuple[str, str, str]] = []
    rollback_fail = HomeAssistantError("rollback refused")
    library_error = Elke27PinRequiredError("pin required")

    async def _bypass(zone_id: int, **params: Any) -> Any:
        bypassed = params.get("bypassed")
        if zone_id == 2 and bypassed:
            raise library_error
        if zone_id == 1 and not bypassed:
            raise rollback_fail
        return await base(zone_id, **params)

    client.calls.clear()
    _patch_bypass(client, mock_client, _bypass)

    def _record_create(
        _hass: HomeAssistant, *, title: str, message: str, notification_id: str
    ) -> None:
        notices.append((title, message, notification_id))

    with (
        patch(
            "custom_components.elke27.zone_notifications.persistent_notification.async_create",
            side_effect=_record_create,
        ),
        pytest.raises(HomeAssistantError) as err,
    ):
        await _call_zone_bypass(hass, [zones[1], zones[2]])
    assert err.value.translation_key == "pin_required_bypass"
    assert err.value.translation_domain == DOMAIN
    assert err.value.translation_placeholders in (None, {})
    assert err.value.__cause__ is library_error
    assert notices
    assert "zones 1" in notices[0][1]
    assert notices[0][2] == f"elke27_zone_bypass_still_bypassed_{entry_id}"


async def test_two_panels_still_changed_notifications_per_entry(
    hass: HomeAssistant,
    setup: Setup,
    mock_client: ClientHarness,
) -> None:
    client_a = ThreeZoneClient()
    await setup(client_a)
    harness_b = await _setup_second_panel(hass, mock_client, _panel_b_snapshot())
    entry_a = hass.config_entries.async_entries(DOMAIN)[0]
    entry_b = next(
        e
        for e in hass.config_entries.async_entries(DOMAIN)
        if e.unique_id == PANEL_SERIAL_2
    )
    zone_a1 = _zone_entity_for_serial(hass, PANEL_MAC, 1)
    zone_a2 = _zone_entity_for_serial(hass, PANEL_MAC, 2)
    zone_b1 = _zone_entity_for_serial(hass, PANEL_SERIAL_2, 1)
    base_a = client_a.async_set_zone_bypass
    base_b = harness_b.client.async_set_zone_bypass
    notices: dict[str, str] = {}
    rollback_refused = "rollback refused"

    async def _a(zone_id: int, **params: Any) -> Any:
        if zone_id == 1 and params.get("bypassed") is False:
            msg = rollback_refused
            raise HomeAssistantError(msg)
        return await base_a(zone_id, **params)

    async def _b(zone_id: int, **params: Any) -> Any:
        if zone_id == 1 and params.get("bypassed") is False:
            msg = rollback_refused
            raise HomeAssistantError(msg)
        return await base_b(zone_id, **params)

    client_a.calls.clear()
    harness_b.client.async_set_zone_bypass = AsyncMock(side_effect=_b)
    panel_rejected = ServiceValidationError(
        translation_domain=DOMAIN, translation_key="code_rejected"
    )

    async def _fail_a2(zone_id: int, **params: Any) -> Any:
        if zone_id == 2 and params.get("bypassed") is True:
            raise panel_rejected
        return await _a(zone_id, **params)

    _patch_bypass(client_a, mock_client, _fail_a2)

    def _record_create(
        _hass: HomeAssistant, *, title: str, message: str, notification_id: str
    ) -> None:
        notices[notification_id] = message

    with (
        patch(
            "custom_components.elke27.zone_notifications.persistent_notification.async_create",
            side_effect=_record_create,
        ),
        pytest.raises(ServiceValidationError) as err,
    ):
        await _call_zone_bypass(hass, [zone_a1, zone_b1, zone_a2])
    assert err.value is panel_rejected
    assert f"elke27_zone_bypass_still_bypassed_{entry_a.entry_id}" in notices
    assert f"elke27_zone_bypass_still_bypassed_{entry_b.entry_id}" in notices
    assert "zones 1" in notices[f"elke27_zone_bypass_still_bypassed_{entry_a.entry_id}"]
    assert "zones 1" in notices[f"elke27_zone_bypass_still_bypassed_{entry_b.entry_id}"]


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
    assert err.value.translation_key == "zone_bypass_failed_still_unbypassed_no_restore"
    ph = err.value.translation_placeholders or {}
    assert ph.get("still_unbypassed_zone_numbers") == "1"
    assert "rolled_back_zone_numbers" not in ph


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


async def test_same_panel_crossing_area_lock_order_completes(
    hass: HomeAssistant,
    setup: Setup,
    mock_client: ClientHarness,
) -> None:
    client = FourZoneClient()
    await setup(client)
    raw = _zone_entities(hass)
    zones = {
        1: _rename_zone_entity(hass, raw[1], "aaa_z"),
        2: _rename_zone_entity(hass, raw[2], "ccc_z"),
        3: _rename_zone_entity(hass, raw[3], "bbb_z"),
        4: _rename_zone_entity(hass, raw[4], "ddd_z"),
    }
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    coordinator = entry.runtime_data.coordinator
    barrier = _CrossBarrier()
    counts: dict[int, int] = {}
    base = coordinator.area_arm_lock

    def _factory(area_id: int) -> Any:
        lock = base(area_id)

        class _Tracked:
            async def __aenter__(self) -> None:
                role = _cross_role.get()
                if role in ("x", "y") and lock.locked():
                    barrier.peer_blocked.set()
                await lock.acquire()
                if role not in ("x", "y"):
                    return
                tid = id(asyncio.current_task())
                counts[tid] = counts.get(tid, 0) + 1
                if counts[tid] == 1:
                    await barrier.after_first(role, f"area{area_id}")

            async def __aexit__(self, *exc: object) -> None:
                lock.release()

        return _Tracked()

    coordinator.area_arm_lock = _factory
    call_y = asyncio.create_task(
        _zone_bypass_with_role(hass, [zones[3], zones[4]], "y")
    )
    call_x = asyncio.create_task(
        _zone_bypass_with_role(hass, [zones[1], zones[2]], "x")
    )
    await asyncio.wait_for(asyncio.gather(call_x, call_y), timeout=15.0)
    assert sorted(_panel_bypass_calls(mock_client)) == [
        (1, True),
        (2, True),
        (3, True),
        (4, True),
    ]


async def test_same_area_two_zones_do_not_self_deadlock(
    hass: HomeAssistant,
    three_zones: tuple[FakeClient, ClientHarness],
) -> None:
    _client, mock_client = three_zones
    zones = _zone_entities(hass)
    await asyncio.wait_for(_call_zone_bypass(hass, [zones[1], zones[2]]), timeout=10.0)
    assert _panel_bypass_calls(mock_client) == [(1, True), (2, True)]


async def _mixed_bypass_failure(
    hass: HomeAssistant,
    three_zones: tuple[FakeClient, ClientHarness],
    *,
    bypass: bool,
    ack_false: bool,
) -> HomeAssistantError:
    client, mock_client = three_zones
    zones = _zone_entities(hass)
    coordinator = hass.config_entries.async_entries(DOMAIN)[0].runtime_data.coordinator
    if not bypass:
        client.snapshot = _all_bypassed_snapshot()
        coordinator.async_set_updated_data(client.snapshot)
    if ack_false:
        original_bypass = coordinator.async_set_zone_bypass

        async def _never_forward(
            zone_id: int, *, bypassed: bool, pin: str | None = None
        ) -> bool:
            forward = bypassed is bypass
            if zone_id == 3 and forward:
                return False
            if zone_id == 1 and not forward:
                raise HomeAssistantError("rollback refused one")
            return await original_bypass(zone_id, bypassed=bypassed, pin=pin)

        coordinator.async_set_zone_bypass = _never_forward
    else:
        base = client.async_set_zone_bypass

        async def _send(zone_id: int, **params: Any) -> Any:
            bypassed = params.get("bypassed")
            forward = bypassed is bypass
            if zone_id == 3 and forward:
                raise HomeAssistantError("panel refused three")
            if zone_id == 1 and not forward:
                raise HomeAssistantError("rollback refused one")
            return await base(zone_id, **params)

        client.calls.clear()
        _patch_bypass(client, mock_client, _send)
    with pytest.raises(HomeAssistantError) as err:
        await _call_zone_bypass(hass, [zones[1], zones[2], zones[3]], bypass=bypass)
    return err.value


@pytest.mark.parametrize(
    ("bypass", "ack_false", "key"),
    [
        (True, False, "zone_bypass_failed_still_bypassed"),
        (False, False, "zone_bypass_failed_still_unbypassed"),
        (True, True, "zone_bypass_failed_not_acknowledged_still_bypassed"),
        (False, True, "zone_bypass_failed_not_acknowledged_still_unbypassed"),
    ],
)
async def test_mixed_rollback_still_and_unacknowledged_keys(
    hass: HomeAssistant,
    three_zones: tuple[FakeClient, ClientHarness],
    bypass: bool,
    ack_false: bool,
    key: str,
) -> None:
    err = await _mixed_bypass_failure(
        hass, three_zones, bypass=bypass, ack_false=ack_false
    )
    assert err.translation_key == key
    ph = err.translation_placeholders or {}
    assert ph["rolled_back_zone_numbers"] == "2"
    if bypass:
        assert ph["still_bypassed_zone_numbers"] == "1"
    else:
        assert ph["still_unbypassed_zone_numbers"] == "1"
    if ack_false:
        assert err.__cause__ is None
    else:
        assert ph["reason"] == "panel refused three"
        assert isinstance(err.__cause__, HomeAssistantError)


async def test_rollback_failed_keeps_service_failure_reason(
    hass: HomeAssistant,
    three_zones: tuple[FakeClient, ClientHarness],
) -> None:
    client, mock_client = three_zones
    zones = _zone_entities(hass)
    base = client.async_set_zone_bypass

    async def _send(zone_id: int, **params: Any) -> Any:
        if zone_id == 2 and params.get("bypassed"):
            raise HomeAssistantError("panel refused two")
        if zone_id == 1 and params.get("bypassed") is False:
            raise HomeAssistantError("rollback refused one")
        return await base(zone_id, **params)

    _patch_bypass(client, mock_client, _send)
    with pytest.raises(HomeAssistantError) as err:
        await _call_zone_bypass(hass, [zones[1], zones[2]])
    assert (err.value.translation_placeholders or {})["reason"] == "panel refused two"


async def test_partial_rollback_lists_every_still_bypassed_zone_on_one_panel(
    hass: HomeAssistant,
    three_zones: tuple[FakeClient, ClientHarness],
) -> None:
    client, mock_client = three_zones
    zones = _zone_entities(hass)
    base = client.async_set_zone_bypass
    notices: dict[str, str] = {}
    rejected = ServiceValidationError(
        translation_domain=DOMAIN, translation_key="code_rejected"
    )

    async def _send(zone_id: int, **params: Any) -> Any:
        if zone_id == 3 and params.get("bypassed"):
            raise rejected
        if zone_id in (1, 2) and params.get("bypassed") is False:
            raise HomeAssistantError("rollback refused")
        return await base(zone_id, **params)

    _patch_bypass(client, mock_client, _send)

    def _create(_h: Any, *, title: str, message: str, notification_id: str) -> None:
        notices[notification_id] = message

    with (
        patch(
            "custom_components.elke27.zone_notifications.persistent_notification.async_create",
            side_effect=_create,
        ),
        pytest.raises(ServiceValidationError),
    ):
        await _call_zone_bypass(hass, [zones[1], zones[2], zones[3]])
    (msg,) = notices.values()
    assert "zones 2, 1." in msg


async def test_two_panels_still_changed_lists_both_panels_in_error_and_log(
    hass: HomeAssistant,
    setup: Setup,
    mock_client: ClientHarness,
    caplog: pytest.LogCaptureFixture,
) -> None:
    client_a = ThreeZoneClient()
    await setup(client_a)
    harness_b = await _setup_second_panel(hass, mock_client, _panel_b_snapshot())
    za1 = _zone_entity_for_serial(hass, PANEL_MAC, 1)
    za2 = _zone_entity_for_serial(hass, PANEL_MAC, 2)
    zb1 = _zone_entity_for_serial(hass, PANEL_SERIAL_2, 1)
    base_a = client_a.async_set_zone_bypass
    base_b = harness_b.client.async_set_zone_bypass

    async def _a(zone_id: int, **params: Any) -> Any:
        if zone_id == 2 and params.get("bypassed"):
            raise HomeAssistantError("panel refused a2")
        if zone_id == 1 and params.get("bypassed") is False:
            raise HomeAssistantError("rb refused a1")
        return await base_a(zone_id, **params)

    async def _b(zone_id: int, **params: Any) -> Any:
        if zone_id == 1 and params.get("bypassed") is False:
            raise HomeAssistantError("rb refused b1")
        return await base_b(zone_id, **params)

    _patch_bypass(client_a, mock_client, _a)
    harness_b.client.async_set_zone_bypass = AsyncMock(side_effect=_b)
    caplog.set_level(logging.WARNING)
    with pytest.raises(HomeAssistantError) as err:
        await _call_zone_bypass(hass, [za1, zb1, za2])
    ph = err.value.translation_placeholders or {}
    assert ph["still_bypassed_zone_numbers"] == "1, 1"
    assert "zones still changed after rollback: 1, 1" in caplog.text


@pytest.mark.parametrize(
    "rejected_code",
    _REJECTED_PANEL_CODES,
    ids=lambda code: f"panel_{code}",
)
async def test_coordinator_code_rejected_bypass_notice_text_and_id(
    rejected_code: int,
    hass: HomeAssistant,
    setup: Setup,
) -> None:
    client = _CustomBypassScriptedClient(
        [None, None, _CUSTOM_BYPASS_REJECT, _CUSTOM_BYPASS_REJECT, None],
        rejected_code,
    )
    await setup(client)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            "alarm_control_panel",
            "alarm_arm_custom_bypass",
            {
                "entity_id": _ALARM_FOR_CUSTOM_BYPASS,
                ATTR_CODE: CODE,
            },
            blocking=True,
        )
    await hass.async_block_till_done()
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    notice = hass.data["persistent_notification"][
        f"elke27_code_rejected_bypass_{entry.entry_id}"
    ]
    assert notice["title"] == "Elk E27 zones still bypassed"
    assert (
        "could not be un-bypassed, so they are still bypassed: zones 2."
        in notice["message"]
    )


@pytest.mark.parametrize("ack_false", [True, False], ids=["unacknowledged", "raised"])
async def test_mid_list_failure_error_names_the_failing_zone(
    hass: HomeAssistant,
    three_zones: tuple[FakeClient, ClientHarness],
    ack_false: bool,
) -> None:
    client, mock_client = three_zones
    zones = _zone_entities(hass)
    if ack_false:
        entry = hass.config_entries.async_entries(DOMAIN)[0]
        coordinator = entry.runtime_data.coordinator
        original_bypass = coordinator.async_set_zone_bypass

        async def _unack(
            zone_id: int, *, bypassed: bool, pin: str | None = None
        ) -> bool:
            if zone_id == 2 and bypassed:
                return False
            return await original_bypass(zone_id, bypassed=bypassed, pin=pin)

        coordinator.async_set_zone_bypass = _unack
    else:
        base = client.async_set_zone_bypass

        async def _refuse(zone_id: int, **params: Any) -> Any:
            if zone_id == 2 and params.get("bypassed"):
                raise HomeAssistantError("panel refused zone 2")
            return await base(zone_id, **params)

        client.calls.clear()
        _patch_bypass(client, mock_client, _refuse)
    with pytest.raises(HomeAssistantError) as err:
        await _call_zone_bypass(hass, [zones[1], zones[2]])
    ph = err.value.translation_placeholders or {}
    assert "Zone B" in ph["zone"]
    assert "Zone A" not in ph["zone"]
