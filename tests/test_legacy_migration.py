# ruff: noqa: S101, PLR2004, ARG001, D103, TC001, TC003
"""
0.1.x -> 0.2.x upgrade in place (config entry VERSION 1 -> 2).

Design: grokbots/architecture/Elk/design-prod-upgrade-zone-migration.md §2 and
§8. Every test drives Home Assistant's public entry points (setup, reload,
reauth flow, registries) and the integration's public helpers; no private
members. Registry fixtures mirror production's shape with made-up identifiers.
"""

from __future__ import annotations

from collections.abc import Generator
import dataclasses
import json
import logging
from pathlib import Path
import re
from types import MappingProxyType
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, create_autospec, patch

from elke27_lib import AreaState, LinkKeys, OutputState, PanelInfo, ZoneState
from elke27_lib.client import Elke27Client
from elke27_lib.discovery import DiscoveredPanel
from elke27_lib.errors import (
    Elke27ConnectionError,
    Elke27InvalidArgument,
    Elke27ProtocolError,
)
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)

from custom_components.elke27 import async_migrate_entry
from custom_components.elke27.config_flow import (
    CONF_ACCESS_CODE,
    CONF_PASSPHRASE,
    Elke27ConfigFlow,
)
from custom_components.elke27.const import (
    CONF_INTEGRATION_ID,
    CONF_INTEGRATION_SERIAL,
    CONF_LEGACY_MIGRATION,
    CONF_LINK_KEYS_JSON,
    CONF_PANEL_MAC,
    CONF_PANEL_MAC_FILLED,
    DOMAIN,
    ISSUE_LEGACY_DUPLICATES,
    ISSUE_LEGACY_MIGRATION_BLOCKED,
    ISSUE_LEGACY_MIGRATION_PARTIAL,
    ISSUE_LEGACY_MIGRATION_RECORD_MALFORMED,
    ISSUE_LEGACY_MIGRATION_WRITE_FAILED,
    MIGRATION_STATE_PENDING,
)
from custom_components.elke27.diagnostics import (
    async_get_config_entry_diagnostics,
    migration_diagnostics,
)
from custom_components.elke27.migration import (
    LegacyMigrationRecordError,
    async_apply_legacy_migration,
    async_rebuild_legacy_record,
    blocked_issue_id,
    duplicates_issue_id,
    issue_variant,
    legacy_migration_pending,
    new_unique_id,
    parse_legacy_unique_id,
    record_issue_id,
    validate_legacy_migration_record,
)
from homeassistant.components.repairs.issue_handler import (
    async_process_repairs_platforms,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
    issue_registry as ir,
    label_registry as lr,
)
from homeassistant.setup import async_setup_component
from tests.conftest import HOST, INTEGRATION_ID, PORT, ClientHarness

if TYPE_CHECKING:
    from pytest_homeassistant_custom_component.typing import ClientSessionGenerator
    from syrupy.assertion import SnapshotAssertion

# Made-up identifiers only (design §10: no production values in fixtures).
LEGACY_SERIAL = "a1b2c3d4e5f6"  # 0.1.7 integration_serial (derived on the HA host)
RAW_MAC = "0A1B2C3D4E5F"  # discovery MAC_ADDR form
KEY = "0a:1b:2c:3d:4e:5f"  # 0.2.x identity key: the formatted discovery MAC
ACCESS = "908172"  # test-only link access code (not a PIN)
PASSPHRASE = "test-passphrase"
LINK_KEYS = LinkKeys(tempkey_hex="aa", linkkey_hex="bb", linkhmac_hex="cc")
OLD_LINK_KEYS_JSON = LinkKeys(
    tempkey_hex="01", linkkey_hex="02", linkhmac_hex="03"
).to_json()

# (domain, kind, n, object_id, settings) of the 0.1.x registry. Mirrors the
# production shape: alarm panel and one zone inherit the panel device's area,
# most zones have their own area, some icons, plus switch and panel sensors.
LEGACY_ROWS: list[tuple[str, str, int, str, dict[str, Any]]] = [
    ("alarm_control_panel", "area", 1, "elk_main_house", {}),
    ("binary_sensor", "zone", 1, "elk_front_door", {"area": "Foyer"}),
    (
        "binary_sensor",
        "zone",
        2,
        "elk_garage_door",
        {"area": "Garage", "icon": "mdi:garage"},
    ),
    ("binary_sensor", "zone", 3, "elk_kitchen_motion", {"disabled": True}),
    ("binary_sensor", "zone", 4, "elk_den_window", {"hidden": True, "area": "Den"}),
    ("binary_sensor", "zone", 5, "elk_patio_door", {"name": "Patio (pinned)"}),
    ("binary_sensor", "zone", 40, "elk_closet_motion", {}),
    ("switch", "output", 1, "elk_siren", {"icon": "mdi:bullhorn"}),
    ("sensor", "panel", 1, "elk_panel_name", {}),
    ("sensor", "panel", 2, "elk_panel_ready", {}),
]


def _new_uid(kind: str, n: int) -> str:
    value = new_unique_id(KEY, kind, n)
    assert value is not None
    return value


def _snapshot(harness: ClientHarness) -> Any:
    """Panel with one area and the zones/outputs of LEGACY_ROWS; no MAC in it."""
    zone_ids = [n for _, kind, n, _, _ in LEGACY_ROWS if kind == "zone"]
    return dataclasses.replace(
        harness.snapshot,
        panel=PanelInfo(mac=None, panel_name="Test Panel"),
        areas=MappingProxyType({1: AreaState(area_id=1, name="Main House")}),
        zones=MappingProxyType(
            {
                n: ZoneState(zone_id=n, name=f"Zone {n}", area_id=1, open=False)
                for n in zone_ids
            }
        ),
        outputs=MappingProxyType({1: OutputState(output_id=1, name="Siren")}),
    )


@pytest.fixture
def panel(mock_client: ClientHarness, mock_discover_host: AsyncMock) -> ClientHarness:
    """Live panel: the snapshot above, and discovery answers with RAW_MAC."""
    mock_client.snapshot = _snapshot(mock_client)
    mock_discover_host.return_value = DiscoveredPanel(
        host=HOST,
        port=PORT,
        tls_port=None,
        panel_name="Test Panel",
        panel_serial=None,
        panel_mac=RAW_MAC,
    )
    return mock_client


@pytest.fixture
def flow_client() -> Generator[Any]:
    """Client the re-link (reauth) flow creates; never touches the network."""
    client: Any = create_autospec(Elke27Client, instance=True)
    client.async_link.return_value = LINK_KEYS
    client.wait_ready.return_value = True
    client.get_snapshot.return_value = dataclasses.replace(
        ClientHarness().snapshot, panel=PanelInfo(mac=None, panel_name="Test Panel")
    )
    with (
        patch("custom_components.elke27.config_flow.Elke27Client", return_value=client),
        patch(
            "custom_components.elke27.config_flow.generate_integration_id",
            return_value=INTEGRATION_ID,
        ),
    ):
        yield client


def entry_017() -> MockConfigEntry:
    """0.1.7 entry: integration_serial as unique_id and identity base, no MAC."""
    return MockConfigEntry(
        domain=DOMAIN,
        title=HOST,
        version=1,
        minor_version=1,
        unique_id=LEGACY_SERIAL,
        data={
            CONF_HOST: HOST,
            CONF_PORT: PORT,
            CONF_LINK_KEYS_JSON: OLD_LINK_KEYS_JSON,
            CONF_INTEGRATION_SERIAL: LEGACY_SERIAL,
            "panel": {"host": HOST, "port": PORT},
        },
        options={
            "panel_info": {"panel_name": "Test Panel", "mac": None, "serial": None},
            "table_info": {},
        },
    )


def entry_019() -> MockConfigEntry:
    """0.1.9 entry: discovery MAC stored at add time; formatted MAC as base."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Test Panel",
        version=1,
        minor_version=1,
        unique_id=KEY,
        data={
            CONF_HOST: HOST,
            CONF_PORT: PORT,
            CONF_LINK_KEYS_JSON: OLD_LINK_KEYS_JSON,
            CONF_INTEGRATION_SERIAL: LEGACY_SERIAL,
            CONF_PANEL_MAC: RAW_MAC,
        },
        options={"panel_info": {"panel_name": "Test Panel", "mac": KEY}},
    )


def entry_relinked() -> MockConfigEntry:
    """Return a 0.1.7 entry after the re-link (Phase A plan added by the test)."""
    return MockConfigEntry(
        domain=DOMAIN,
        title=HOST,
        version=2,
        minor_version=1,
        unique_id=KEY,
        data={
            CONF_HOST: HOST,
            CONF_PORT: PORT,
            CONF_LINK_KEYS_JSON: LINK_KEYS.to_json(),
            CONF_INTEGRATION_ID: INTEGRATION_ID,
            CONF_INTEGRATION_SERIAL: LEGACY_SERIAL,
            CONF_PANEL_MAC: RAW_MAC,
            CONF_PANEL_MAC_FILLED: False,
        },
        options={"panel_info": {"panel_name": "Test Panel", "mac": KEY}},
    )


def seed_legacy_registry(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    *,
    base: str,
    device_identifier: str,
) -> dict[str, str]:
    """Create the 0.1.x panel device and entities; return kind:n -> entity_id."""
    areas = ar.async_get(hass)
    closet = areas.async_get_or_create("Master Closet")
    lr.async_get(hass).async_create("security")
    dev_reg = dr.async_get(hass)
    device = dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, device_identifier)},
        name=HOST,
    )
    dev_reg.async_update_device(device.id, area_id=closet.id, labels={"security"})
    ent_reg = er.async_get(hass)
    created: dict[str, str] = {}
    for domain, kind, n, object_id, settings in LEGACY_ROWS:
        reg = ent_reg.async_get_or_create(
            domain,
            DOMAIN,
            f"{base}:{kind}:{n}",
            config_entry=entry,
            device_id=device.id,
            suggested_object_id=object_id,
        )
        updates: dict[str, Any] = {}
        if "area" in settings:
            updates["area_id"] = areas.async_get_or_create(settings["area"]).id
        if "icon" in settings:
            updates["icon"] = settings["icon"]
        if "name" in settings:
            updates["name"] = settings["name"]
        if settings.get("disabled"):
            updates["disabled_by"] = er.RegistryEntryDisabler.USER
        if settings.get("hidden"):
            updates["hidden_by"] = er.RegistryEntryHider.USER
        if updates:
            ent_reg.async_update_entity(reg.entity_id, **updates)
        created[f"{kind}:{n}"] = reg.entity_id
    return created


def registry_view(hass: HomeAssistant, entry_id: str) -> dict[str, tuple[Any, ...]]:
    """Every user-visible field of the entry's entities, keyed by entity_id."""
    ent_reg = er.async_get(hass)
    return {
        reg.entity_id: (
            reg.id,
            reg.unique_id,
            reg.name,
            reg.icon,
            reg.area_id,
            reg.disabled_by,
            reg.hidden_by,
            frozenset(reg.labels),
        )
        for reg in er.async_entries_for_config_entry(ent_reg, entry_id)
    }


def device_view(hass: HomeAssistant, entry_id: str) -> dict[str, tuple[Any, ...]]:
    dev_reg = dr.async_get(hass)
    return {
        device.id: (frozenset(device.identifiers), device.area_id, device.labels)
        for device in dr.async_entries_for_config_entry(dev_reg, entry_id)
    }


def our_issues(hass: HomeAssistant) -> set[str]:
    return {
        issue_id for (domain, issue_id) in ir.async_get(hass).issues if domain == DOMAIN
    }


def reauth_flows(hass: HomeAssistant, entry: MockConfigEntry) -> list[Any]:
    return [
        flow
        for flow in hass.config_entries.flow.async_progress()
        if flow["context"].get("source") == "reauth"
        and flow["context"].get("entry_id") == entry.entry_id
    ]


async def relink(hass: HomeAssistant, entry: MockConfigEntry) -> Any:
    """Complete the re-link card the user sees (access code + passphrase)."""
    flows = reauth_flows(hass, entry)
    assert len(flows) == 1
    result = await hass.config_entries.flow.async_configure(
        flows[0]["flow_id"], {CONF_ACCESS_CODE: ACCESS, CONF_PASSPHRASE: PASSPHRASE}
    )
    await hass.async_block_till_done()
    return result


def assert_migrated(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    before: dict[str, tuple[Any, ...]],
    legacy: dict[str, str],
    closet_id: str,
) -> None:
    """P1-P6 of design §8 on the registries."""
    state = entry.data[CONF_LEGACY_MIGRATION]
    assert entry.version == 2
    assert entry.state is ConfigEntryState.LOADED
    assert state["state"] == "done"
    assert state["done_at"]
    assert CONF_INTEGRATION_SERIAL not in entry.data
    after = registry_view(hass, entry.entry_id)
    # P2: same entity_ids, nothing renamed, no _2 duplicates.
    assert set(after) == set(before)
    assert not [eid for eid in after if eid.endswith("_2")]
    for key, entity_id in legacy.items():
        kind, n = key.split(":")
        old = before[entity_id]
        new = after[entity_id]
        assert new[0] == old[0]  # P3: registry id kept
        assert new[1] == _new_uid(kind, int(n))  # P4: new unique_id
        assert new[2:4] == old[2:4]  # P5: name override, icon
        assert new[5:] == old[5:]  # P5: disabled, hidden, labels
        if old[4] is None and entity_id.startswith(
            ("alarm_control_panel.", "binary_sensor.")
        ):
            assert new[4] == closet_id  # Q2: inherited area pinned
        else:
            assert new[4] == old[4]


async def test_upgrade_from_017_in_place_with_one_relink(
    hass: HomeAssistant, panel: ClientHarness, flow_client: Any
) -> None:
    """0.1.7 -> migration build: one re-link, then every entity is kept (§8 P1-P6)."""
    entry = entry_017()
    entry.add_to_hass(hass)
    legacy = seed_legacy_registry(
        hass, entry, base=LEGACY_SERIAL, device_identifier=f"222-{LEGACY_SERIAL}"
    )
    before = registry_view(hass, entry.entry_id)
    (panel_device_id,) = device_view(hass, entry.entry_id)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    # Phase A: version 2, plan stored, entry unique_id (host-derived) cleared,
    # registry untouched, and the re-link card is up instead of a delete.
    assert entry.version == 2
    assert entry.unique_id is None
    assert legacy_migration_pending(entry)
    assert len(entry.data[CONF_LEGACY_MIGRATION]["plan"]) == len(LEGACY_ROWS)
    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert registry_view(hass, entry.entry_id) == before
    panel.client.async_connect.assert_not_awaited()

    result = await relink(hass, entry)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful_upgrade"

    closet = ar.async_get(hass).async_get_area_by_name("Master Closet")
    assert closet is not None
    assert_migrated(hass, entry, before, legacy, closet.id)
    assert entry.unique_id == KEY
    assert entry.data[CONF_PANEL_MAC] == RAW_MAC
    assert entry.data[CONF_PANEL_MAC_FILLED] is False
    assert entry.data[CONF_INTEGRATION_ID] == INTEGRATION_ID
    assert entry.data[CONF_LINK_KEYS_JSON] == LINK_KEYS.to_json()

    # P6: same panel device id, new identifier, area and label kept.
    dev_reg = dr.async_get(hass)
    device = dev_reg.async_get(panel_device_id)
    assert device is not None
    assert device.identifiers == {(DOMAIN, KEY)}
    assert device.area_id == closet.id
    assert device.labels == {"security"}
    # Zones and the alarm panel moved to the per-area device; switch and
    # sensors stayed on the panel.
    ent_reg = er.async_get(hass)
    area_device = dev_reg.async_get_device(identifiers={(DOMAIN, f"{KEY}:area:1")})
    assert area_device is not None
    zone = ent_reg.async_get(legacy["zone:1"])
    siren = ent_reg.async_get(legacy["output:1"])
    alarm = ent_reg.async_get(legacy["area:1"])
    assert zone is not None
    assert siren is not None
    assert alarm is not None
    assert zone.device_id == area_device.id
    assert alarm.device_id == area_device.id
    assert siren.device_id == panel_device_id
    # Entities load under their old entity_ids (automations keep working).
    assert hass.states.get(legacy["zone:1"]) is not None
    assert hass.states.get(legacy["area:1"]) is not None
    assert our_issues(hass) == set()


async def test_upgrade_from_019_in_place(
    hass: HomeAssistant, panel: ClientHarness, flow_client: Any
) -> None:
    """0.1.9 (MAC base, `<key>` device): unique_id kept, rows rewritten."""
    entry = entry_019()
    entry.add_to_hass(hass)
    legacy = seed_legacy_registry(hass, entry, base=KEY, device_identifier=KEY)
    before = registry_view(hass, entry.entry_id)
    devices_before = device_view(hass, entry.entry_id)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.unique_id == KEY  # a formatted panel MAC is a panel identity
    assert legacy_migration_pending(entry)

    result = await relink(hass, entry)
    assert result["reason"] == "reauth_successful_upgrade"
    closet = ar.async_get(hass).async_get_area_by_name("Master Closet")
    assert closet is not None
    assert_migrated(hass, entry, before, legacy, closet.id)
    (panel_device_id,) = devices_before
    device = dr.async_get(hass).async_get(panel_device_id)
    assert device is not None
    assert device.identifiers == {(DOMAIN, KEY)}  # no-op move


async def test_fresh_020_entry_only_gets_the_version_bump(
    hass: HomeAssistant, panel: ClientHarness
) -> None:
    """A 0.2.0 entry (deleted and re-added) sees no change and no re-link."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Test Panel",
        version=1,
        unique_id=KEY,
        data={
            CONF_HOST: HOST,
            CONF_PORT: PORT,
            CONF_LINK_KEYS_JSON: LINK_KEYS.to_json(),
            CONF_INTEGRATION_ID: INTEGRATION_ID,
            CONF_PANEL_MAC: RAW_MAC,
        },
    )
    entry.add_to_hass(hass)
    ent_reg = er.async_get(hass)
    zone = ent_reg.async_get_or_create(
        "binary_sensor", DOMAIN, f"{KEY}:1", config_entry=entry
    )
    sensor = ent_reg.async_get_or_create(
        "sensor", DOMAIN, f"{KEY}:panel_name", config_entry=entry
    )
    data_before = dict(entry.data)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.version == 2
    assert entry.minor_version == 1
    assert entry.state is ConfigEntryState.LOADED
    assert dict(entry.data) == data_before  # no migration record at all
    assert entry.unique_id == KEY
    assert reauth_flows(hass, entry) == []
    for reg in (zone, sensor):
        current = ent_reg.async_get(reg.entity_id)
        assert current is not None
        assert current.unique_id == reg.unique_id
    assert migration_diagnostics(entry) is None


async def test_conflict_writes_nothing_and_raises_repairs(
    hass: HomeAssistant, panel: ClientHarness, caplog: pytest.LogCaptureFixture
) -> None:
    """§8 N1: a new-format entity already exists -> blocked, registry unchanged."""
    entry = entry_017()
    entry.add_to_hass(hass)
    legacy = seed_legacy_registry(
        hass, entry, base=LEGACY_SERIAL, device_identifier=f"222-{LEGACY_SERIAL}"
    )
    assert await async_migrate_entry(hass, entry)
    relinked = entry_relinked()
    hass.config_entries.async_update_entry(
        entry,
        unique_id=KEY,
        data={
            **relinked.data,
            CONF_LEGACY_MIGRATION: entry.data[CONF_LEGACY_MIGRATION],
        },
    )
    # Left over from a 0.2.0 trial on the same HA.
    ent_reg = er.async_get(hass)
    trial = ent_reg.async_get_or_create(
        "binary_sensor",
        DOMAIN,
        _new_uid("zone", 2),
        config_entry=entry,
        suggested_object_id="trial_garage",
    )
    before = registry_view(hass, entry.entry_id)
    devices_before = device_view(hass, entry.entry_id)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert legacy_migration_pending(entry)
    assert registry_view(hass, entry.entry_id) == before
    assert device_view(hass, entry.entry_id) == devices_before
    assert blocked_issue_id(entry.entry_id) in our_issues(hass)
    blocked_logs = [
        r.getMessage() for r in caplog.records if "blocked" in r.getMessage()
    ]
    assert blocked_logs
    assert not any(KEY in m for m in blocked_logs)
    issue = ir.async_get(hass).async_get_issue(DOMAIN, blocked_issue_id(entry.entry_id))
    assert issue is not None
    assert issue.translation_key == ISSUE_LEGACY_MIGRATION_BLOCKED
    assert issue.translation_placeholders is not None
    assert trial.entity_id in issue.translation_placeholders["conflicts"]
    # Platforms were not forwarded: no surprise new entities or states.
    assert hass.states.get(legacy["zone:1"]) is None

    # Fix: remove the conflicting entity and reload -> completes.
    ent_reg.async_remove(trial.entity_id)
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert not legacy_migration_pending(entry)
    assert blocked_issue_id(entry.entry_id) not in our_issues(hass)
    current = ent_reg.async_get(legacy["zone:2"])
    assert current is not None
    assert current.unique_id == _new_uid("zone", 2)


async def test_device_identifier_conflict_blocks(
    hass: HomeAssistant, panel: ClientHarness
) -> None:
    """Another device already owns `(elke27, key)` -> blocked, nothing written."""
    entry = entry_relinked()
    entry.add_to_hass(hass)
    seed_legacy_registry(
        hass, entry, base=LEGACY_SERIAL, device_identifier=f"222-{LEGACY_SERIAL}"
    )
    other = MockConfigEntry(domain=DOMAIN, version=2, title="trial")
    other.add_to_hass(hass)
    dr.async_get(hass).async_get_or_create(
        config_entry_id=other.entry_id, identifiers={(DOMAIN, KEY)}
    )
    await _phase_a_on_relinked(hass, entry)
    before = registry_view(hass, entry.entry_id)
    devices_before = device_view(hass, entry.entry_id)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert registry_view(hass, entry.entry_id) == before
    assert device_view(hass, entry.entry_id) == devices_before
    assert blocked_issue_id(entry.entry_id) in our_issues(hass)


async def _phase_a_on_relinked(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Store a Phase A plan on a relinked-shape entry (keeps its re-link data)."""
    data = dict(entry.data)
    hass.config_entries.async_update_entry(
        entry, version=1, data={k: v for k, v in data.items() if k != "integration_id"}
    )
    assert await async_migrate_entry(hass, entry)
    hass.config_entries.async_update_entry(
        entry,
        unique_id=KEY,
        data={**data, CONF_LEGACY_MIGRATION: entry.data[CONF_LEGACY_MIGRATION]},
    )


async def test_failure_mid_write_rolls_back_everything(
    hass: HomeAssistant, panel: ClientHarness
) -> None:
    """A registry error part-way through undoes every write (all-or-nothing)."""
    entry = entry_relinked()
    entry.add_to_hass(hass)
    legacy = seed_legacy_registry(
        hass, entry, base=LEGACY_SERIAL, device_identifier=f"222-{LEGACY_SERIAL}"
    )
    await _phase_a_on_relinked(hass, entry)
    before = registry_view(hass, entry.entry_id)
    devices_before = device_view(hass, entry.entry_id)

    real_update = er.EntityRegistry.async_update_entity
    calls = {"n": 0}

    def flaky(self: er.EntityRegistry, entity_id: str, **kwargs: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 4:
            msg = "simulated registry failure"
            raise ValueError(msg)
        return real_update(self, entity_id, **kwargs)

    with patch.object(er.EntityRegistry, "async_update_entity", flaky):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    # Three writes (the device move came first), the failure, then three undos.
    assert calls["n"] == 7
    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert legacy_migration_pending(entry)
    assert registry_view(hass, entry.entry_id) == before
    assert device_view(hass, entry.entry_id) == devices_before
    issue = ir.async_get(hass).async_get_issue(DOMAIN, blocked_issue_id(entry.entry_id))
    assert issue is not None
    # A write failure, not a conflict: own text, no empty placeholders.
    assert issue.translation_key == ISSUE_LEGACY_MIGRATION_WRITE_FAILED
    assert not issue.translation_placeholders

    # Next attempt (no fault) completes.
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    current = er.async_get(hass).async_get(legacy["area:1"])
    assert current is not None
    assert current.unique_id == _new_uid("area", 1)


async def test_failed_undo_stays_pending_and_logs_counts_only(
    hass: HomeAssistant, panel: ClientHarness, caplog: pytest.LogCaptureFixture
) -> None:
    """If the undo itself fails: pending, blocked issue, ERROR with no identifiers."""
    entry = entry_relinked()
    entry.add_to_hass(hass)
    seed_legacy_registry(
        hass, entry, base=LEGACY_SERIAL, device_identifier=f"222-{LEGACY_SERIAL}"
    )
    await _phase_a_on_relinked(hass, entry)
    real_update = er.EntityRegistry.async_update_entity
    calls = {"n": 0}

    def flaky(self: er.EntityRegistry, entity_id: str, **kwargs: Any) -> Any:
        calls["n"] += 1
        if calls["n"] >= 4:  # the 4th write and every undo fail
            msg = "simulated registry failure"
            raise ValueError(msg)
        return real_update(self, entity_id, **kwargs)

    with (
        patch.object(er.EntityRegistry, "async_update_entity", flaky),
        caplog.at_level(logging.ERROR, logger="custom_components.elke27"),
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert legacy_migration_pending(entry)
    assert blocked_issue_id(entry.entry_id) in our_issues(hass)
    errors = [
        r.getMessage()
        for r in caplog.records
        if r.levelno == logging.ERROR and r.name.startswith("custom_components")
    ]
    assert any("3 of 4 writes not reverted" in m for m in errors)
    issue = ir.async_get(hass).async_get_issue(DOMAIN, blocked_issue_id(entry.entry_id))
    assert issue is not None
    assert issue.translation_key == ISSUE_LEGACY_MIGRATION_PARTIAL
    for message in errors:
        assert KEY not in message
        assert LEGACY_SERIAL not in message
        assert "binary_sensor." not in message
        assert "registry unchanged" not in message


async def test_reload_after_partial_undo_finishes_the_upgrade(
    hass: HomeAssistant, panel: ClientHarness
) -> None:
    """A reload with no fault after a failed undo reaches LOADED, clears issue."""
    entry = entry_relinked()
    entry.add_to_hass(hass)
    legacy = seed_legacy_registry(
        hass, entry, base=LEGACY_SERIAL, device_identifier=f"222-{LEGACY_SERIAL}"
    )
    await _phase_a_on_relinked(hass, entry)
    real_update = er.EntityRegistry.async_update_entity
    calls = {"n": 0}

    def flaky(self: er.EntityRegistry, entity_id: str, **kwargs: Any) -> Any:
        calls["n"] += 1
        if calls["n"] >= 4:  # the 4th write and every undo fail
            msg = "simulated registry failure"
            raise ValueError(msg)
        return real_update(self, entity_id, **kwargs)

    with patch.object(er.EntityRegistry, "async_update_entity", flaky):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    issue_reg = ir.async_get(hass)
    issue = issue_reg.async_get_issue(DOMAIN, blocked_issue_id(entry.entry_id))
    assert issue is not None
    assert issue.translation_key == ISSUE_LEGACY_MIGRATION_PARTIAL
    assert legacy_migration_pending(entry)

    # Fault gone: the retry finishes from the partly rewritten registry.
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert not legacy_migration_pending(entry)
    assert issue_reg.async_get_issue(DOMAIN, blocked_issue_id(entry.entry_id)) is None
    current = er.async_get(hass).async_get(legacy["area:1"])
    assert current is not None
    assert current.unique_id == _new_uid("area", 1)


def _assert_key_absent(
    hass: HomeAssistant, entry: MockConfigEntry, caplog: pytest.LogCaptureFixture
) -> ir.IssueEntry:
    issue = ir.async_get(hass).async_get_issue(DOMAIN, blocked_issue_id(entry.entry_id))
    assert issue is not None
    assert issue.translation_placeholders is not None
    for value in issue.translation_placeholders.values():
        assert KEY not in value
        assert LEGACY_SERIAL not in value
    for record in caplog.records:
        assert KEY not in record.getMessage()
        assert LEGACY_SERIAL not in record.getMessage()
    return issue


async def test_repairs_and_logs_never_name_the_panel_key(
    hass: HomeAssistant, panel: ClientHarness, caplog: pytest.LogCaptureFixture
) -> None:
    """Blocked and partial issues list entity IDs or counts, never the panel key."""
    caplog.set_level(logging.DEBUG, logger="custom_components.elke27")
    entry = entry_relinked()
    entry.add_to_hass(hass)
    legacy = seed_legacy_registry(
        hass, entry, base=LEGACY_SERIAL, device_identifier=f"222-{LEGACY_SERIAL}"
    )
    await _phase_a_on_relinked(hass, entry)
    ent_reg = er.async_get(hass)

    # Blocked: another device owns (elke27, KEY) and a trial entity owns a
    # new-format unique_id built from KEY.
    other = MockConfigEntry(domain=DOMAIN, version=2, title="trial")
    other.add_to_hass(hass)
    trial_device = dr.async_get(hass).async_get_or_create(
        config_entry_id=other.entry_id, identifiers={(DOMAIN, KEY)}
    )
    trial = ent_reg.async_get_or_create(
        "binary_sensor",
        DOMAIN,
        _new_uid("zone", 2),
        config_entry=other,
        suggested_object_id="trial_garage",
    )
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_RETRY
    issue = _assert_key_absent(hass, entry, caplog)
    assert issue.translation_key == f"{ISSUE_LEGACY_MIGRATION_BLOCKED}_mixed"
    assert issue.translation_placeholders is not None
    conflicts = issue.translation_placeholders["conflicts"]
    assert trial.entity_id in conflicts
    assert issue.translation_placeholders["other"] == "1"

    # Partial: the conflicts are gone, but a write and every undo fail with
    # the panel key in the error text.
    ent_reg.async_remove(trial.entity_id)
    dr.async_get(hass).async_remove_device(trial_device.id)
    real_update = er.EntityRegistry.async_update_entity
    calls = {"n": 0}

    def flaky(self: er.EntityRegistry, entity_id: str, **kwargs: Any) -> Any:
        calls["n"] += 1
        if calls["n"] >= 4:
            msg = f"simulated registry failure for {KEY}"
            raise ValueError(msg)
        return real_update(self, entity_id, **kwargs)

    with patch.object(er.EntityRegistry, "async_update_entity", flaky):
        await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_RETRY
    issue = _assert_key_absent(hass, entry, caplog)
    assert issue.translation_key == ISSUE_LEGACY_MIGRATION_PARTIAL
    assert issue.translation_placeholders is not None
    conflicts = issue.translation_placeholders["conflicts"]
    # Three rows were rewritten before the fault and none could be undone.
    listed = [line[2:] for line in conflicts.strip().splitlines()]
    assert len(listed) == 3
    assert set(listed) <= set(legacy.values())


async def test_failure_on_device_move_writes_nothing(
    hass: HomeAssistant, panel: ClientHarness, caplog: pytest.LogCaptureFixture
) -> None:
    """A device registry error on the first write leaves every row as it was."""
    entry = entry_relinked()
    entry.add_to_hass(hass)
    seed_legacy_registry(
        hass, entry, base=LEGACY_SERIAL, device_identifier=f"222-{LEGACY_SERIAL}"
    )
    await _phase_a_on_relinked(hass, entry)
    before = registry_view(hass, entry.entry_id)
    devices_before = device_view(hass, entry.entry_id)

    def broken(self: dr.DeviceRegistry, device_id: str, **kwargs: Any) -> Any:
        msg = "simulated device failure"
        raise HomeAssistantError(msg)

    with (
        patch.object(dr.DeviceRegistry, "async_update_device", broken),
        caplog.at_level(logging.ERROR, logger="custom_components.elke27"),
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert legacy_migration_pending(entry)
    assert registry_view(hass, entry.entry_id) == before
    assert device_view(hass, entry.entry_id) == devices_before
    # A write failure is not a conflict: its own text, never "delete entities".
    issue = ir.async_get(hass).async_get_issue(DOMAIN, blocked_issue_id(entry.entry_id))
    assert issue is not None
    assert issue.translation_key == ISSUE_LEGACY_MIGRATION_WRITE_FAILED
    assert not issue.translation_placeholders
    messages = [r.getMessage() for r in caplog.records]
    assert any("registry write failed" in m for m in messages)
    assert not any("conflict" in m for m in messages)
    assert not any("simulated device failure" in m for m in messages)


async def test_migration_is_idempotent(
    hass: HomeAssistant, panel: ClientHarness
) -> None:
    """Reloads after success, and a repeated Phase B, change nothing."""
    entry = entry_relinked()
    entry.add_to_hass(hass)
    seed_legacy_registry(
        hass, entry, base=LEGACY_SERIAL, device_identifier=f"222-{LEGACY_SERIAL}"
    )
    await _phase_a_on_relinked(hass, entry)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    done = dict(entry.data[CONF_LEGACY_MIGRATION])
    after = registry_view(hass, entry.entry_id)
    devices_after = device_view(hass, entry.entry_id)

    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.data[CONF_LEGACY_MIGRATION] == done
    assert registry_view(hass, entry.entry_id) == after
    assert device_view(hass, entry.entry_id) == devices_after

    # VERSION 2 is never migrated again.
    assert await async_migrate_entry(hass, entry)
    assert entry.data[CONF_LEGACY_MIGRATION] == done

    # Registry already rewritten but the record still says pending (e.g. HA
    # stopped before the entry was saved): Phase B finishes without errors.
    hass.config_entries.async_update_entry(
        entry,
        data={**entry.data, CONF_LEGACY_MIGRATION: {**done, "state": "pending"}},
    )
    async_apply_legacy_migration(hass, entry, KEY)
    assert entry.data[CONF_LEGACY_MIGRATION]["state"] == "done"
    assert registry_view(hass, entry.entry_id) == after


async def test_retry_on_partly_migrated_registry_succeeds(
    hass: HomeAssistant, panel: ClientHarness
) -> None:
    """Rows already at their new unique_id but still needing the area pin."""
    entry = entry_relinked()
    entry.add_to_hass(hass)
    legacy = seed_legacy_registry(
        hass, entry, base=LEGACY_SERIAL, device_identifier=f"222-{LEGACY_SERIAL}"
    )
    await _phase_a_on_relinked(hass, entry)
    ent_reg = er.async_get(hass)
    # An earlier attempt got this far: alarm panel and zone 40 (both inherit
    # the device area, so both need the pin) and zone 1 already rewritten.
    for key in ("area:1", "zone:40", "zone:1"):
        kind, n = key.split(":")
        ent_reg.async_update_entity(legacy[key], new_unique_id=_new_uid(kind, int(n)))

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert not legacy_migration_pending(entry)
    assert blocked_issue_id(entry.entry_id) not in our_issues(hass)
    closet = ar.async_get(hass).async_get_area_by_name("Master Closet")
    assert closet is not None
    for key in ("area:1", "zone:40"):
        current = ent_reg.async_get(legacy[key])
        assert current is not None
        assert current.area_id == closet.id
    for key, entity_id in legacy.items():
        kind, n = key.split(":")
        current = ent_reg.async_get(entity_id)
        assert current is not None
        assert current.unique_id == _new_uid(kind, int(n))


async def test_panel_offline_after_relink_keeps_pending(
    hass: HomeAssistant, panel: ClientHarness
) -> None:
    """§8 N2: no connection -> setup retries, registry untouched until connected."""
    entry = entry_relinked()
    entry.add_to_hass(hass)
    seed_legacy_registry(
        hass, entry, base=LEGACY_SERIAL, device_identifier=f"222-{LEGACY_SERIAL}"
    )
    await _phase_a_on_relinked(hass, entry)
    before = registry_view(hass, entry.entry_id)
    panel.client.async_connect.side_effect = Elke27ConnectionError("offline")

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert legacy_migration_pending(entry)
    assert registry_view(hass, entry.entry_id) == before

    panel.client.async_connect.side_effect = None
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert not legacy_migration_pending(entry)


async def test_relink_to_wrong_panel_keeps_pending(
    hass: HomeAssistant, panel: ClientHarness, flow_client: Any
) -> None:
    """§8 N3-like: re-link aborts wrong_panel -> nothing rewritten, user can redo."""
    entry = entry_017()
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        entry, options={"panel_info": {"panel_name": "X", "mac": "ff:ee:dd:cc:bb:aa"}}
    )
    seed_legacy_registry(
        hass, entry, base=LEGACY_SERIAL, device_identifier=f"222-{LEGACY_SERIAL}"
    )
    before = registry_view(hass, entry.entry_id)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    result = await relink(hass, entry)
    assert result["reason"] == "wrong_panel"
    assert legacy_migration_pending(entry)
    assert CONF_INTEGRATION_ID not in entry.data
    assert registry_view(hass, entry.entry_id) == before


async def test_duplicate_legacy_rows_keep_majority_base_and_raise_repairs(
    hass: HomeAssistant, panel: ClientHarness
) -> None:
    """Mixed bases (0.1.5-0.1.7): keep the majority base, list the rest."""
    entry = entry_relinked()
    entry.add_to_hass(hass)
    legacy = seed_legacy_registry(
        hass, entry, base=LEGACY_SERIAL, device_identifier=f"222-{LEGACY_SERIAL}"
    )
    ent_reg = er.async_get(hass)
    stale = ent_reg.async_get_or_create(
        "binary_sensor",
        DOMAIN,
        "old-host-base:zone:1",
        config_entry=entry,
        suggested_object_id="elk_front_door_old",
    )
    await _phase_a_on_relinked(hass, entry)
    state = entry.data[CONF_LEGACY_MIGRATION]
    assert [row["entity_registry_id"] for row in state["duplicates"]] == [stale.id]
    issue = ir.async_get(hass).async_get_issue(
        DOMAIN, duplicates_issue_id(entry.entry_id)
    )
    assert issue is not None
    assert issue.translation_key == ISSUE_LEGACY_DUPLICATES

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    kept = ent_reg.async_get(legacy["zone:1"])
    left = ent_reg.async_get(stale.entity_id)
    assert kept is not None
    assert left is not None
    assert kept.unique_id == _new_uid("zone", 1)
    assert left.unique_id == "old-host-base:zone:1"  # untouched, user deletes
    assert duplicates_issue_id(entry.entry_id) in our_issues(hass)

    ent_reg.async_remove(stale.entity_id)
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert duplicates_issue_id(entry.entry_id) not in our_issues(hass)


async def test_rebuild_with_duplicate_raises_issue_that_survives_reload(
    hass: HomeAssistant, panel: ClientHarness
) -> None:
    """A rebuilt record's duplicates get the issue on setup, again on reload."""
    entry = entry_relinked()
    entry.add_to_hass(hass)
    seed_legacy_registry(
        hass, entry, base=LEGACY_SERIAL, device_identifier=f"222-{LEGACY_SERIAL}"
    )
    ent_reg = er.async_get(hass)
    stale = ent_reg.async_get_or_create(
        "binary_sensor",
        DOMAIN,
        "old-host-base:zone:1",
        config_entry=entry,
        suggested_object_id="elk_front_door_old",
    )
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_LEGACY_MIGRATION: {"state": "bogus"}}
    )
    assert async_rebuild_legacy_record(hass, entry) == MIGRATION_STATE_PENDING
    record = entry.data[CONF_LEGACY_MIGRATION]
    assert [row["entity_registry_id"] for row in record["duplicates"]] == [stale.id]
    issue_id = duplicates_issue_id(entry.entry_id)
    assert issue_id not in our_issues(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.translation_key == ISSUE_LEGACY_DUPLICATES
    assert stale.entity_id in issue.translation_placeholders["entities"]

    # Non-persistent issue: a restart drops it, the next setup restores it.
    ir.async_delete_issue(hass, DOMAIN, issue_id)
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert issue_id in our_issues(hass)

    ent_reg.async_remove(stale.entity_id)
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert issue_id not in our_issues(hass)


async def test_duplicate_tie_keeps_newest(hass: HomeAssistant) -> None:
    """Equal base counts: the most recently modified registry row is kept."""
    entry = entry_017()
    entry.add_to_hass(hass)
    ent_reg = er.async_get(hass)
    older = ent_reg.async_get_or_create(
        "binary_sensor", DOMAIN, "base-a:zone:7", config_entry=entry
    )
    newer = ent_reg.async_get_or_create(
        "binary_sensor", DOMAIN, "base-b:zone:7", config_entry=entry
    )
    ent_reg.async_update_entity(newer.entity_id, icon="mdi:door")
    assert await async_migrate_entry(hass, entry)
    state = entry.data[CONF_LEGACY_MIGRATION]
    assert [row["entity_registry_id"] for row in state["plan"]] == [newer.id]
    assert [row["entity_registry_id"] for row in state["duplicates"]] == [older.id]


async def test_unparseable_and_unmapped_rows_are_left_alone(
    hass: HomeAssistant, panel: ClientHarness
) -> None:
    """Kind/platform mismatch, junk and panel:3 stay as they are."""
    entry = entry_relinked()
    entry.add_to_hass(hass)
    seed_legacy_registry(
        hass, entry, base=LEGACY_SERIAL, device_identifier=f"222-{LEGACY_SERIAL}"
    )
    ent_reg = er.async_get(hass)
    odd = [
        ent_reg.async_get_or_create("sensor", DOMAIN, uid, config_entry=entry)
        for uid in (f"{LEGACY_SERIAL}:zone:9", "junk", f"{LEGACY_SERIAL}:panel:3")
    ]
    await _phase_a_on_relinked(hass, entry)
    planned = {
        row["old_unique_id"] for row in entry.data[CONF_LEGACY_MIGRATION]["plan"]
    }
    assert not planned & {reg.unique_id for reg in odd}

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    for reg in odd:
        current = ent_reg.async_get(reg.entity_id)
        assert current is not None
        assert current.unique_id == reg.unique_id


async def test_skipped_panel_sensors_are_logged_and_in_diagnostics(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Panel sensors other than 1 and 2: WARNING, listed (redacted) in diagnostics."""
    entry = entry_017()
    entry.add_to_hass(hass)
    reg = er.async_get(hass).async_get_or_create(
        "sensor",
        DOMAIN,
        f"{LEGACY_SERIAL}:panel:3",
        config_entry=entry,
        suggested_object_id="elk_panel_extra",
    )
    with caplog.at_level(logging.WARNING, logger="custom_components.elke27"):
        assert await async_migrate_entry(hass, entry)
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any(reg.entity_id in r.getMessage() for r in warnings)
    assert not any(LEGACY_SERIAL in r.getMessage() for r in caplog.records)
    state = entry.data[CONF_LEGACY_MIGRATION]
    assert [row["entity_registry_id"] for row in state["skipped"]] == [reg.id]
    assert state["plan"] == []
    diag = migration_diagnostics(entry)
    assert diag is not None
    assert diag["skipped"] == [
        {
            "entity_registry_id": reg.id,
            "entity_id": reg.entity_id,
            "old_unique_id": "**REDACTED**:panel:3",
        }
    ]
    assert LEGACY_SERIAL not in repr(diag)


async def test_newer_entry_version_is_refused(hass: HomeAssistant) -> None:
    """VERSION 3 (a future release) is not loaded; data stays as it is."""
    entry = MockConfigEntry(domain=DOMAIN, version=3, data={CONF_HOST: HOST})
    entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.MIGRATION_ERROR
    assert entry.version == 3
    assert dict(entry.data) == {CONF_HOST: HOST}


def test_flow_version_blocks_downgrade() -> None:
    """VERSION 2 is a major bump, so 0.1.x (VERSION 1) refuses a migrated entry."""
    assert Elke27ConfigFlow.VERSION == 2
    assert Elke27ConfigFlow.MINOR_VERSION == 1


@pytest.mark.parametrize(
    ("unique_id", "options", "kept"),
    [
        (LEGACY_SERIAL, {}, False),  # 0.1.7: integration_serial
        ("00000001", {"panel_info": {"serial": "00000001"}}, False),  # placeholder
        ("some-host", {}, False),  # host fallback
        (KEY, {}, True),  # formatted MAC
        ("PANEL-SN-9", {"panel_info": {"serial": "PANEL-SN-9"}}, True),  # real serial
    ],
)
async def test_phase_a_clears_entry_unique_id_unless_panel_identity(
    hass: HomeAssistant, unique_id: str, options: dict[str, Any], *, kept: bool
) -> None:
    """Only a panel MAC or real panel serial survives as the entry unique_id."""
    entry = entry_017()
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(entry, unique_id=unique_id, options=options)
    assert await async_migrate_entry(hass, entry)
    assert entry.unique_id == (unique_id if kept else None)


async def test_relink_while_pending_keys_on_discovery_mac(
    hass: HomeAssistant, panel: ClientHarness, flow_client: Any
) -> None:
    """§2.3 step 2: the re-link stores the MAC as tier 1 (filled = False)."""
    entry = entry_017()
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await relink(hass, entry)
    assert entry.data[CONF_PANEL_MAC] == RAW_MAC
    assert entry.data[CONF_PANEL_MAC_FILLED] is False
    assert entry.state is ConfigEntryState.LOADED
    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, KEY)})
    assert device is not None


async def test_entry_without_id_and_not_pending_is_still_an_error(
    hass: HomeAssistant,
) -> None:
    """A 0.2.x entry with no ID keeps today's error (no re-link card)."""
    entry = MockConfigEntry(
        domain=DOMAIN, version=2, data={CONF_HOST: HOST, CONF_PORT: PORT}
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert reauth_flows(hass, entry) == []


async def test_blocked_raises_not_ready(hass: HomeAssistant) -> None:
    """Phase B on its own: a conflict raises ConfigEntryNotReady (pinned key)."""
    entry = entry_relinked()
    entry.add_to_hass(hass)
    seed_legacy_registry(
        hass, entry, base=LEGACY_SERIAL, device_identifier=f"222-{LEGACY_SERIAL}"
    )
    await _phase_a_on_relinked(hass, entry)
    er.async_get(hass).async_get_or_create(
        "sensor", DOMAIN, f"{KEY}:panel_ready", config_entry=entry
    )
    with pytest.raises(ConfigEntryNotReady, match=ISSUE_LEGACY_MIGRATION_BLOCKED):
        async_apply_legacy_migration(hass, entry, KEY)
    assert legacy_migration_pending(entry)


_ROW = {
    "entity_registry_id": "0" * 32,
    "entity_id": "binary_sensor.elk_extra",
    "old_unique_id": f"{KEY}:zone:99",
}
# (path, field): every field the migration reads, top level and per row.
_REQUIRED_FIELDS = [
    ("", "state"),
    ("", "from"),
    ("", "plan"),
    ("", "duplicates"),
    ("", "skipped"),
    *[
        ("plan", f)
        for f in ("entity_registry_id", "domain", "kind", "n", "old_unique_id")
    ],
    *[
        (name, f)
        for name in ("duplicates", "skipped")
        for f in ("entity_registry_id", "entity_id", "old_unique_id")
    ],
]


def _drop(record: dict[str, Any], path: str, field: str) -> tuple[dict, str]:
    """Return a copy of ``record`` without ``field``, and its reported name."""
    record = dict(record)
    if not path:
        record.pop(field)
        return record, field
    rows = [dict(row) for row in record[path]] or [dict(_ROW)]
    rows[0].pop(field)
    record[path] = rows
    return record, f"{path}[].{field}"


async def _pending_with(
    hass: HomeAssistant, path: str, field: str
) -> tuple[MockConfigEntry, dict[str, Any], str]:
    entry = entry_relinked()
    entry.add_to_hass(hass)
    seed_legacy_registry(hass, entry, base=KEY, device_identifier=KEY)
    await _phase_a_on_relinked(hass, entry)
    record, name = _drop(dict(entry.data[CONF_LEGACY_MIGRATION]), path, field)
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_LEGACY_MIGRATION: record}
    )
    return entry, record, name


def _malformed(name: str) -> str:
    return rf"^legacy migration record is malformed \(missing {re.escape(name)}\)$"


@pytest.mark.parametrize(("path", "field"), _REQUIRED_FIELDS)
async def test_malformed_record_fails_setup_cleanly(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    panel: ClientHarness,
    caplog: pytest.LogCaptureFixture,
    path: str,
    field: str,
) -> None:
    """Setup: SETUP_ERROR, record unchanged (not done), log has type + field only."""
    entry, record, name = await _pending_with(hass, path, field)
    before = registry_view(hass, entry.entry_id)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert entry.data[CONF_LEGACY_MIGRATION] == record
    assert "done_at" not in entry.data[CONF_LEGACY_MIGRATION]
    assert registry_view(hass, entry.entry_id) == before
    # HA logs the ConfigEntryError (field only); we add no second line.
    setup_logs = [r.getMessage() for r in caplog.records if name in r.getMessage()]
    assert setup_logs
    assert not [
        r
        for r in caplog.records
        if r.name.startswith("custom_components.elke27")
        and "malformed" in r.getMessage()
    ]
    ours = "\n".join(
        r.getMessage()
        for r in caplog.records
        if r.name.startswith("custom_components.elke27")
    )
    assert KEY not in ours
    assert LEGACY_SERIAL not in ours
    # Diagnostics degrade to type + field only, for every required field.
    assert await async_setup_component(hass, "diagnostics", {})
    result = await get_diagnostics_for_config_entry(hass, hass_client, entry)
    assert result["legacy_registry_migration"] == {
        "error": "LegacyMigrationRecordError",
        "field": name,
    }


@pytest.mark.parametrize(
    ("path", "field", "value", "name"),
    [
        ("", "state", "bogus", "state"),
        ("plan", "n", "x", "plan[].n"),
        ("plan", "kind", "panel", "plan[].kind"),
        ("", "plan", "nope", "plan"),
        ("", "duplicates", "nope", "duplicates"),
        ("", "skipped", "nope", "skipped"),
    ],
)
async def test_invalid_record_value_diagnostics_type_and_field(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    panel: ClientHarness,
    path: str,
    field: str,
    value: Any,
    name: str,
) -> None:
    """Invalid values: setup fails and diagnostics give type + field only."""
    entry = entry_relinked()
    entry.add_to_hass(hass)
    seed_legacy_registry(hass, entry, base=KEY, device_identifier=KEY)
    await _phase_a_on_relinked(hass, entry)
    record = dict(entry.data[CONF_LEGACY_MIGRATION])
    if path:
        rows = [dict(row) for row in record[path]]
        rows[0][field] = value
        if field == "kind":
            rows[0]["n"] = 99
        record[path] = rows
    else:
        record[field] = value
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_LEGACY_MIGRATION: record}
    )
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert entry.data[CONF_LEGACY_MIGRATION] == record
    assert await async_setup_component(hass, "diagnostics", {})
    result = await get_diagnostics_for_config_entry(hass, hass_client, entry)
    assert result["legacy_registry_migration"] == {
        "error": "LegacyMigrationRecordError",
        "field": name,
    }


async def test_unmappable_plan_row_raises(hass: HomeAssistant) -> None:
    """A plan row with no 0.2.x unique_id is malformed: raise, write nothing."""
    entry = entry_relinked()
    entry.add_to_hass(hass)
    seed_legacy_registry(hass, entry, base=KEY, device_identifier=KEY)
    await _phase_a_on_relinked(hass, entry)
    record = dict(entry.data[CONF_LEGACY_MIGRATION])
    record["plan"] = [{**record["plan"][0], "kind": "panel", "n": 99}]
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_LEGACY_MIGRATION: record}
    )
    before = registry_view(hass, entry.entry_id)
    with pytest.raises(
        LegacyMigrationRecordError,
        match=r"^legacy migration record is malformed \(unmappable plan\[\]\.kind\)$",
    ):
        validate_legacy_migration_record(entry)
    assert legacy_migration_pending(entry)
    assert registry_view(hass, entry.entry_id) == before


async def test_diagnostics_redact_old_unique_id_base(
    hass: HomeAssistant,
    panel: ClientHarness,
    hass_client: ClientSessionGenerator,
    snapshot: SnapshotAssertion,
) -> None:
    """Diagnostics (real endpoint, after setup ran Phase A): no MAC or serial."""
    assert await async_setup_component(hass, "diagnostics", {})
    entry = entry_019()
    entry.add_to_hass(hass)
    seed_legacy_registry(hass, entry, base=KEY, device_identifier=KEY)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert legacy_migration_pending(entry)
    result = await get_diagnostics_for_config_entry(hass, hass_client, entry)
    diag = result["legacy_registry_migration"]
    assert diag["state"] == "pending"
    assert diag["done_at"] is None
    assert len(diag["plan"]) == len(LEGACY_ROWS)
    text = repr(result)
    assert KEY not in text
    assert LEGACY_SERIAL not in text
    assert RAW_MAC not in text
    assert {row["old_unique_id"] for row in diag["plan"]} >= {
        "**REDACTED**:zone:1",
        "**REDACTED**:panel:2",
    }
    # Registry ids are random per run: snapshot the stable shape only.
    stable = {
        **diag,
        "plan": [
            {k: v for k, v in row.items() if k != "entity_registry_id"}
            for row in diag["plan"]
        ],
    }
    assert stable == snapshot


async def test_user_device_class_override_survives_upgrade(
    hass: HomeAssistant, panel: ClientHarness, flow_client: Any
) -> None:
    """
    A 'Show as' choice (registry device_class) survives A, B, re-link, reload.

    Migration code never writes device_class: every registry update it makes
    is recorded and checked.
    """
    entry = entry_017()
    entry.add_to_hass(hass)
    legacy = seed_legacy_registry(
        hass, entry, base=LEGACY_SERIAL, device_identifier=f"222-{LEGACY_SERIAL}"
    )
    ent_reg = er.async_get(hass)
    zone = legacy["zone:4"]
    ent_reg.async_update_entity(zone, device_class="window")

    def device_class() -> str | None:
        reg = ent_reg.async_get(zone)
        assert reg is not None
        return reg.device_class

    calls: list[dict[str, Any]] = []
    real_update = er.EntityRegistry.async_update_entity

    def spy(self: er.EntityRegistry, entity_id: str, **kwargs: Any) -> Any:
        calls.append(kwargs)
        return real_update(self, entity_id, **kwargs)

    with patch.object(er.EntityRegistry, "async_update_entity", spy):
        await hass.config_entries.async_setup(entry.entry_id)  # Phase A
        await hass.async_block_till_done()
        assert legacy_migration_pending(entry)
        assert device_class() == "window"
        result = await relink(hass, entry)  # re-link, then Phase B
        assert result["reason"] == "reauth_successful_upgrade"
        assert not legacy_migration_pending(entry)
        assert device_class() == "window"
        state = hass.states.get(zone)
        assert state is not None
        assert state.attributes.get("device_class") == "window"
        await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert device_class() == "window"
    state = hass.states.get(zone)
    assert state is not None
    assert state.attributes.get("device_class") == "window"
    assert calls  # Phase B did rewrite unique_ids through the registry
    assert not [c for c in calls if "device_class" in c]


async def test_removing_entry_clears_migration_issues(
    hass: HomeAssistant, panel: ClientHarness
) -> None:
    """Deleting a blocked entry leaves no Repairs issue behind."""
    entry = entry_relinked()
    entry.add_to_hass(hass)
    seed_legacy_registry(
        hass, entry, base=LEGACY_SERIAL, device_identifier=f"222-{LEGACY_SERIAL}"
    )
    await _phase_a_on_relinked(hass, entry)
    er.async_get(hass).async_get_or_create(
        "switch", DOMAIN, f"{KEY}:1", config_entry=entry
    )
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert blocked_issue_id(entry.entry_id) in our_issues(hass)
    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert our_issues(hass) == set()


@pytest.mark.parametrize(
    ("domain", "unique_id", "expected"),
    [
        ("binary_sensor", "a1b2c3d4e5f6:zone:12", ("a1b2c3d4e5f6", "zone", 12)),
        ("binary_sensor", f"{KEY}:zone:3", (KEY, "zone", 3)),  # colons in base
        ("alarm_control_panel", "222-x:area:1", ("222-x", "area", 1)),
        ("switch", "b:output:4", ("b", "output", 4)),
        ("light", "b:light:2", ("b", "light", 2)),
        ("lock", "b:lock:1", ("b", "lock", 1)),
        ("climate", "b:tstat:1", ("b", "tstat", 1)),
        ("sensor", "b:panel:2", ("b", "panel", 2)),
        ("sensor", "b:zone:2", None),  # kind/platform mismatch
        ("binary_sensor", f"{KEY}:3", None),  # already 0.2.x
        ("binary_sensor", "b:zone:x", None),
        ("binary_sensor", ":zone:1", None),  # empty base
    ],
)
def test_parse_legacy_unique_id(
    domain: str, unique_id: str, expected: tuple[str, str, int] | None
) -> None:
    """Right-anchored parse of `<base>:<kind>:<N>` (design §2.2 step 4)."""
    assert parse_legacy_unique_id(domain, unique_id) == expected


@pytest.mark.parametrize(
    ("kind", "n", "expected"),
    [
        ("area", 1, f"{KEY}:1"),
        ("zone", 40, f"{KEY}:40"),
        ("output", 3, f"{KEY}:3"),
        ("tstat", 1, f"{KEY}:1"),
        ("panel", 1, f"{KEY}:panel_name"),
        ("panel", 2, f"{KEY}:panel_ready"),
        ("panel", 3, None),
    ],
)
def test_new_unique_id_table(kind: str, n: int, expected: str | None) -> None:
    """The design §2.3 c mapping table."""
    assert new_unique_id(KEY, kind, n) == expected


# Tester probe for #107 (comment 6274122036): planted panel key, device and
# host must never reach Repairs placeholders, logs or diagnostics, and the
# conflicts placeholder lists entity IDs only.
_EID_RE = re.compile(r"^[a-z_]+\.[a-z0-9_]+$")
_PLANT_NAME = "PLANTED-DEVICE-7f3e9c"


def _forbidden(*extra: str) -> list[str]:
    return [
        KEY,
        KEY.replace(":", ""),
        RAW_MAC,
        LEGACY_SERIAL,
        ACCESS,
        PASSPHRASE,
        HOST,
        *extra,
    ]


async def _assert_no_identifiers(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
    forbidden: list[str],
) -> dict[str, str]:
    issue = ir.async_get(hass).async_get_issue(DOMAIN, blocked_issue_id(entry.entry_id))
    assert issue is not None
    placeholders = dict(issue.translation_placeholders or {})
    logs = "\n".join(
        r.getMessage() for r in caplog.records if r.name.startswith("custom_components")
    )
    diagnostics = json.dumps(
        await async_get_config_entry_diagnostics(hass, entry), default=str
    )
    for text in (json.dumps(placeholders), logs, diagnostics):
        assert not [f for f in forbidden if f.lower() in text.lower()]
    listing = placeholders.get("conflicts", "")
    assert "more)" not in listing
    items = [line.removeprefix("- ") for line in listing.strip().splitlines()]
    assert all(_EID_RE.match(x) for x in items)
    # Entity IDs are listed in Repairs only; the log carries counts.
    assert not [x for x in items if x in logs]
    return placeholders


async def test_entity_conflict_lists_entity_id_only(
    hass: HomeAssistant, panel: ClientHarness, caplog: pytest.LogCaptureFixture
) -> None:
    """A planted new-format unique_id is reported by its entity_id only."""
    entry = entry_017()
    entry.add_to_hass(hass)
    seed_legacy_registry(
        hass, entry, base=LEGACY_SERIAL, device_identifier=f"222-{LEGACY_SERIAL}"
    )
    assert await async_migrate_entry(hass, entry)
    relinked = entry_relinked()
    hass.config_entries.async_update_entry(
        entry,
        unique_id=KEY,
        data={
            **relinked.data,
            CONF_LEGACY_MIGRATION: entry.data[CONF_LEGACY_MIGRATION],
        },
    )
    trial = er.async_get(hass).async_get_or_create(
        "binary_sensor",
        DOMAIN,
        _new_uid("zone", 2),
        config_entry=entry,
        suggested_object_id="trial_planted",
    )
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    placeholders = await _assert_no_identifiers(
        hass, entry, caplog, _forbidden(_new_uid("zone", 2))
    )
    assert trial.entity_id in placeholders["conflicts"]


async def test_device_conflict_is_counted_not_named(
    hass: HomeAssistant, panel: ClientHarness, caplog: pytest.LogCaptureFixture
) -> None:
    """A device owning the panel key is counted; its id and name never shown."""
    entry = entry_relinked()
    entry.add_to_hass(hass)
    seed_legacy_registry(
        hass, entry, base=LEGACY_SERIAL, device_identifier=f"222-{LEGACY_SERIAL}"
    )
    other = MockConfigEntry(domain=DOMAIN, version=2, title="trial")
    other.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=other.entry_id, identifiers={(DOMAIN, KEY)}, name=_PLANT_NAME
    )
    await _phase_a_on_relinked(hass, entry)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    placeholders = await _assert_no_identifiers(
        hass, entry, caplog, _forbidden(device.id, _PLANT_NAME)
    )
    # Device-only conflict: the _devices variant, a count and nothing empty.
    assert placeholders == {"count": "1"}
    issue = ir.async_get(hass).async_get_issue(DOMAIN, blocked_issue_id(entry.entry_id))
    assert issue is not None
    assert issue.translation_key == f"{ISSUE_LEGACY_MIGRATION_BLOCKED}_devices"


def entry_017_discovered() -> MockConfigEntry:
    """
    0.1.7 entry as its discover step really stored it (elke27 0.3.11).

    `panel` is asdict() of the discovered panel, title is the host (the panel
    reported no name), and options carry the 0.1.7 panel_info snapshot.
    """
    entry = entry_017()
    data = dict(entry.data)
    data["panel"] = {
        "host": HOST,
        "port": PORT,
        "tls_port": None,
        "panel_name": None,
        "panel_serial": None,
        "panel_mac": RAW_MAC,
    }
    return MockConfigEntry(
        domain=DOMAIN,
        title=HOST,
        version=1,
        minor_version=1,
        unique_id=LEGACY_SERIAL,
        data=data,
        options={"panel_info": {"mac": None, "panel_name": None}, "table_info": {}},
    )


@pytest.mark.parametrize(
    "error",
    [
        pytest.param(Elke27ProtocolError("panel said no at 192.0.2.10"), id="protocol"),
        pytest.param(Elke27InvalidArgument("port must be ..."), id="invalid_arg"),
    ],
)
async def test_017_relink_link_failure_logs_type_only_and_retries(
    hass: HomeAssistant,
    panel: ClientHarness,
    flow_client: Any,
    caplog: pytest.LogCaptureFixture,
    error: Exception,
) -> None:
    """
    HW 912f3c3: a 0.1.7 re-link showed a bare 'unknown' with nothing logged.

    The flow now logs the exception type (never its message, which can carry
    the host), an invalid argument is a form error instead of a 500, the
    upgrade stays pending, and a second attempt finishes it.
    """
    entry = entry_017_discovered()
    entry.add_to_hass(hass)
    seed_legacy_registry(
        hass, entry, base=LEGACY_SERIAL, device_identifier=f"222-{LEGACY_SERIAL}"
    )
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert legacy_migration_pending(entry)

    flow_client.async_link.side_effect = error
    caplog.clear()
    with caplog.at_level(logging.DEBUG):
        result = await relink(hass, entry)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "unknown"}
    assert legacy_migration_pending(entry)
    lines = [
        r.getMessage()
        for r in caplog.records
        if r.name.startswith("custom_components.elke27")
    ]
    assert any(type(error).__name__ in line for line in lines), lines
    text = caplog.text
    for secret in (HOST, LEGACY_SERIAL, RAW_MAC, KEY, ACCESS, PASSPHRASE, str(error)):
        assert secret not in text

    flow_client.async_link.side_effect = None
    flow_client.async_link.return_value = LINK_KEYS
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ACCESS_CODE: ACCESS, CONF_PASSPHRASE: PASSPHRASE}
    )
    await hass.async_block_till_done()
    assert result["reason"] == "reauth_successful_upgrade"
    assert entry.state is ConfigEntryState.LOADED
    assert entry.data[CONF_LEGACY_MIGRATION]["state"] == "done"
    assert entry.unique_id == KEY


@pytest.mark.parametrize(
    ("plan", "field"),
    [
        ({"not": "a list"}, "plan"),
        ("text", "plan"),
        ([["not", "a", "mapping"]], "plan[]"),
        (["row"], "plan[]"),
    ],
)
async def test_diagnostics_degrade_on_non_list_or_non_mapping_rows(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    panel: ClientHarness,
    plan: Any,
    field: str,
) -> None:
    """A done record (not re-validated) with bad shapes degrades, never raises."""
    entry = entry_relinked()
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        entry,
        data={
            **entry.data,
            CONF_LEGACY_MIGRATION: {
                "from": 1,
                "state": "done",
                "done_at": "2026-01-01T00:00:00+00:00",
                "plan": plan,
                "duplicates": [],
                "skipped": [],
            },
        },
    )
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    # Setup does not re-check a done record, so the entry loads.
    assert entry.state is ConfigEntryState.LOADED
    assert await async_setup_component(hass, "diagnostics", {})
    result = await get_diagnostics_for_config_entry(hass, hass_client, entry)
    assert result["legacy_registry_migration"] == {
        "error": "LegacyMigrationRecordError",
        "field": field,
    }


@pytest.mark.parametrize(
    "base", ["legacy_migration_blocked", "legacy_migration_partial"]
)
@pytest.mark.parametrize(
    ("conflicts", "suffix", "expected"),
    [
        (
            ["binary_sensor.a", "sensor.b"],
            "",
            {"count": "2", "conflicts": "\n- binary_sensor.a\n- sensor.b"},
        ),
        ([None, None], "_devices", {"count": "2"}),
        (
            ["binary_sensor.a", None],
            "_mixed",
            {"count": "2", "conflicts": "\n- binary_sensor.a", "other": "1"},
        ),
    ],
    ids=["entities_only", "non_entities_only", "both"],
)
def test_issue_variant_never_leaves_a_placeholder_empty(
    base: str, conflicts: list[str | None], suffix: str, expected: dict[str, str]
) -> None:
    """Each variant's text uses exactly its placeholders; none empty or zero."""
    key, placeholders = issue_variant(base, conflicts)
    assert key == f"{base}{suffix}"
    assert placeholders == expected
    strings = json.loads(
        (
            Path(__file__).parent.parent / "custom_components/elke27/strings.json"
        ).read_text(encoding="utf-8")
    )
    text = strings["issues"][key]["description"]
    used = set(re.findall(r"(?<!one )(?<!other ){(\w+)[,}]", text))
    assert used == set(placeholders)
    for value in placeholders.values():
        assert value
        assert value != "0"


# Repairs fix flows (repairs.py).

_STRINGS_DIR = Path(__file__).parent.parent / "custom_components/elke27"


async def _flow_manager(hass: HomeAssistant) -> Any:
    assert await async_setup_component(hass, "repairs", {})
    await async_process_repairs_platforms(hass)
    return hass.data["repairs"]["flow_manager"]


def _registry_view(hass: HomeAssistant, entry_id: str) -> dict[str, tuple[Any, ...]]:
    return {
        reg.id: (reg.entity_id, reg.unique_id, reg.name, reg.device_class)
        for reg in er.async_entries_for_config_entry(er.async_get(hass), entry_id)
    }


async def _blocked_entry(
    hass: HomeAssistant,
) -> tuple[MockConfigEntry, er.RegistryEntry, er.RegistryEntry]:
    """Block an upgrade: one owned leftover, one held by another entry."""
    entry = entry_017()
    entry.add_to_hass(hass)
    seed_legacy_registry(
        hass, entry, base=LEGACY_SERIAL, device_identifier=f"222-{LEGACY_SERIAL}"
    )
    assert await async_migrate_entry(hass, entry)
    relinked = entry_relinked()
    hass.config_entries.async_update_entry(
        entry,
        unique_id=KEY,
        data={
            **relinked.data,
            CONF_LEGACY_MIGRATION: entry.data[CONF_LEGACY_MIGRATION],
        },
    )
    ent_reg = er.async_get(hass)
    owned = ent_reg.async_get_or_create(
        "binary_sensor",
        DOMAIN,
        _new_uid("zone", 2),
        config_entry=entry,
        suggested_object_id="trial_owned",
    )
    other = MockConfigEntry(domain=DOMAIN, unique_id="other-entry")
    other.add_to_hass(hass)
    foreign = ent_reg.async_get_or_create(
        "binary_sensor",
        DOMAIN,
        _new_uid("zone", 3),
        config_entry=other,
        suggested_object_id="trial_foreign",
    )
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_RETRY
    return entry, owned, foreign


async def test_blocked_flow_deletes_nothing_until_confirm_submitted(
    hass: HomeAssistant, panel: ClientHarness, caplog: pytest.LogCaptureFixture
) -> None:
    """The confirm step lists owned leftovers only; submitting it deletes them."""
    entry, owned, foreign = await _blocked_entry(hass)
    issue_id = blocked_issue_id(entry.entry_id)
    issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.is_fixable
    # Repairs lists every entity_id, logs carry the count only.
    assert owned.entity_id in (issue.translation_placeholders or {})["conflicts"]
    logs = "\n".join(
        r.getMessage() for r in caplog.records if r.name.startswith("custom_components")
    )
    assert owned.entity_id not in logs
    assert foreign.entity_id not in logs

    manager = await _flow_manager(hass)
    with patch.object(hass.config_entries, "async_schedule_reload") as reload:
        result = await manager.async_init(DOMAIN, data={"issue_id": issue_id})
        assert result["step_id"] == "confirm_partial"
        assert result["data_schema"].schema == {}
        placeholders = result["description_placeholders"]
        listed = placeholders["entities"]
        assert owned.entity_id in listed
        # Another entry's entity is never offered or deleted, but is named
        # as one to remove by hand, so the counts match the notice.
        assert foreign.entity_id not in listed
        assert foreign.entity_id in placeholders["kept"]
        notice = issue.translation_placeholders or {}
        assert int(placeholders["count"]) + int(placeholders["kept_count"]) == int(
            notice["count"]
        )
        ent_reg = er.async_get(hass)
        assert ent_reg.async_get(owned.entity_id) is not None
        reload.assert_not_called()

        caplog.clear()
        result = await manager.async_configure(result["flow_id"], {})
    assert result["type"] == "create_entry"
    assert ent_reg.async_get(owned.entity_id) is None
    assert ent_reg.async_get(foreign.entity_id) is not None
    reload.assert_called_once_with(entry.entry_id)
    logs = "\n".join(
        r.getMessage() for r in caplog.records if r.name.startswith("custom_components")
    )
    assert "leftover entities deleted: 1" in logs
    assert owned.entity_id not in logs


async def test_blocked_flow_rechecks_targets_at_submit(
    hass: HomeAssistant, panel: ClientHarness
) -> None:
    """A leftover changed between showing and submitting is not deleted."""
    entry, owned, _foreign = await _blocked_entry(hass)
    manager = await _flow_manager(hass)
    with patch.object(hass.config_entries, "async_schedule_reload"):
        result = await manager.async_init(
            DOMAIN, data={"issue_id": blocked_issue_id(entry.entry_id)}
        )
        ent_reg = er.async_get(hass)
        ent_reg.async_update_entity(owned.entity_id, new_unique_id="user-kept")
        await manager.async_configure(result["flow_id"], {})
    assert ent_reg.async_get(owned.entity_id) is not None


async def test_blocked_flow_deletes_only_shown_and_reconfirms_new(
    hass: HomeAssistant, panel: ClientHarness
) -> None:
    """A leftover that appears after the form is shown survives and is re-listed."""
    entry, owned, _foreign = await _blocked_entry(hass)
    manager = await _flow_manager(hass)
    with patch.object(hass.config_entries, "async_schedule_reload") as reload:
        result = await manager.async_init(
            DOMAIN, data={"issue_id": blocked_issue_id(entry.entry_id)}
        )
        assert result["step_id"] == "confirm_partial"
        ent_reg = er.async_get(hass)
        late = ent_reg.async_get_or_create(
            "binary_sensor",
            DOMAIN,
            _new_uid("zone", 4),
            config_entry=entry,
            suggested_object_id="trial_late",
        )
        assert late.entity_id not in result["description_placeholders"]["entities"]
        result = await manager.async_configure(result["flow_id"], {})
        # Only the shown entity is deleted; the new one is confirmed again.
        assert ent_reg.async_get(owned.entity_id) is None
        assert ent_reg.async_get(late.entity_id) is not None
        assert result["type"] == "form"
        assert result["step_id"] == "confirm_partial"
        listed = result["description_placeholders"]["entities"]
        assert late.entity_id in listed
        assert owned.entity_id not in listed
        reload.assert_not_called()
        result = await manager.async_configure(result["flow_id"], {})
    assert result["type"] == "create_entry"
    assert ent_reg.async_get(late.entity_id) is None
    reload.assert_called_once_with(entry.entry_id)


async def test_retry_flows_reload(hass: HomeAssistant) -> None:
    """Interrupted and write-failed issues: submitting retries (reloads)."""
    entry = MockConfigEntry(domain=DOMAIN, version=2)
    entry.add_to_hass(hass)
    hass.config.components.add(DOMAIN)
    manager = await _flow_manager(hass)
    for key in (ISSUE_LEGACY_MIGRATION_PARTIAL, ISSUE_LEGACY_MIGRATION_WRITE_FAILED):
        issue_id = blocked_issue_id(entry.entry_id)
        ir.async_create_issue(
            hass,
            DOMAIN,
            issue_id,
            is_fixable=True,
            data={"entry_id": entry.entry_id, "flow": "retry"},
            severity=ir.IssueSeverity.ERROR,
            translation_key=key,
        )
        with patch.object(hass.config_entries, "async_schedule_reload") as reload:
            result = await manager.async_init(DOMAIN, data={"issue_id": issue_id})
            assert result["step_id"] == "confirm"
            reload.assert_not_called()
            result = await manager.async_configure(result["flow_id"], {})
        assert result["type"] == "create_entry"
        reload.assert_called_once_with(entry.entry_id)
        assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None


def _duplicate_entry(hass: HomeAssistant) -> tuple[MockConfigEntry, str, str]:
    """Two 0.1.x rows for zone 1 under bases of equal weight; corrupt record."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        data={CONF_LEGACY_MIGRATION: {"state": "bogus"}},
    )
    entry.add_to_hass(hass)
    ent_reg = er.async_get(hass)
    first = ent_reg.async_get_or_create(
        "binary_sensor",
        DOMAIN,
        "aaaa:zone:1",
        config_entry=entry,
        suggested_object_id="zone_first",
    )
    second = ent_reg.async_get_or_create(
        "binary_sensor",
        DOMAIN,
        "bbbb:zone:1",
        config_entry=entry,
        suggested_object_id="zone_second",
    )
    # The user edits the older duplicate after upgrading: it is now newest.
    ent_reg.async_update_entity(first.entity_id, icon="mdi:door")
    return entry, first.entity_id, second.entity_id


async def test_rebuild_with_user_edited_duplicate_needs_confirm(
    hass: HomeAssistant, panel: ClientHarness, caplog: pytest.LogCaptureFixture
) -> None:
    """An unclear keeper is shown keep vs leave; nothing changes until submit."""
    entry, edited, other = _duplicate_entry(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_ERROR
    issue_id = record_issue_id(entry.entry_id)
    issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.is_fixable
    assert issue.translation_key == ISSUE_LEGACY_MIGRATION_RECORD_MALFORMED
    assert not issue.translation_placeholders
    before = _registry_view(hass, entry.entry_id)
    stored = dict(entry.data[CONF_LEGACY_MIGRATION])

    manager = await _flow_manager(hass)
    with patch.object(hass.config_entries, "async_schedule_reload") as reload:
        result = await manager.async_init(DOMAIN, data={"issue_id": issue_id})
        result = await manager.async_configure(result["flow_id"], {})
        assert result["step_id"] == "keepers"
        assert edited in result["description_placeholders"]["keep"]
        assert other in result["description_placeholders"]["leave"]
        assert entry.data[CONF_LEGACY_MIGRATION] == stored
        reload.assert_not_called()
        result = await manager.async_configure(result["flow_id"], {})
    assert result["type"] == "create_entry"
    reload.assert_called_once_with(entry.entry_id)
    record = entry.data[CONF_LEGACY_MIGRATION]
    assert record["state"] == MIGRATION_STATE_PENDING
    ent_reg = er.async_get(hass)
    keeper = ent_reg.async_get(edited)
    assert keeper is not None
    assert [row["entity_registry_id"] for row in record["plan"]] == [keeper.id]
    assert [row["entity_id"] for row in record["duplicates"]] == [other]
    # Only the record is written: no unique_id, name or device_class.
    assert _registry_view(hass, entry.entry_id) == before


async def test_rebuild_is_deterministic_on_rerun(hass: HomeAssistant) -> None:
    """The same registry always rebuilds the same record; a valid one is kept."""
    entry, _edited, _other = _duplicate_entry(hass)
    assert async_rebuild_legacy_record(hass, entry) == MIGRATION_STATE_PENDING
    first = entry.data[CONF_LEGACY_MIGRATION]
    assert async_rebuild_legacy_record(hass, entry) == "valid"
    assert entry.data[CONF_LEGACY_MIGRATION] == first
    hass.config_entries.async_update_entry(
        entry, data={CONF_LEGACY_MIGRATION: {"state": "bogus"}}
    )
    assert async_rebuild_legacy_record(hass, entry) == MIGRATION_STATE_PENDING
    assert entry.data[CONF_LEGACY_MIGRATION] == first


def test_strings_and_translation_match_without_field_placeholder() -> None:
    """strings.json equals en.json; user text never names a record field."""
    strings = (_STRINGS_DIR / "strings.json").read_text(encoding="utf-8")
    english = (_STRINGS_DIR / "translations/en.json").read_text(encoding="utf-8")
    assert json.loads(strings) == json.loads(english)
    assert not re.search(r"\{field\}", strings)
    assert "more)" not in strings


async def test_rebuild_after_partial_run_skips_converted_entities(
    hass: HomeAssistant,
) -> None:
    """A rebuild after a partial run plans only the rows still in 0.1.x form."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        data={
            CONF_INTEGRATION_ID: INTEGRATION_ID,
            CONF_LEGACY_MIGRATION: {"state": "bogus"},
        },
    )
    entry.add_to_hass(hass)
    ent_reg = er.async_get(hass)
    pending = ent_reg.async_get_or_create(
        "binary_sensor",
        DOMAIN,
        "aaaa:zone:1",
        config_entry=entry,
        suggested_object_id="zone_pending",
    )
    converted = ent_reg.async_get_or_create(
        "binary_sensor",
        DOMAIN,
        _new_uid("zone", 2),
        config_entry=entry,
        suggested_object_id="zone_converted",
    )
    before = _registry_view(hass, entry.entry_id)
    assert async_rebuild_legacy_record(hass, entry) == MIGRATION_STATE_PENDING
    record = entry.data[CONF_LEGACY_MIGRATION]
    planned = [row["entity_registry_id"] for row in record["plan"]]
    assert planned == [pending.id]
    assert converted.id not in planned
    assert record["duplicates"] == []
    assert _registry_view(hass, entry.entry_id) == before


async def test_rebuild_when_nothing_left_records_done_and_is_a_no_op(
    hass: HomeAssistant,
) -> None:
    """Every entity already converted: the rebuild marks done and changes nothing."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        data={
            CONF_INTEGRATION_ID: INTEGRATION_ID,
            CONF_LEGACY_MIGRATION: {"state": "bogus"},
        },
    )
    entry.add_to_hass(hass)
    er.async_get(hass).async_get_or_create(
        "binary_sensor",
        DOMAIN,
        _new_uid("zone", 1),
        config_entry=entry,
        suggested_object_id="zone_done",
    )
    before = _registry_view(hass, entry.entry_id)
    assert async_rebuild_legacy_record(hass, entry) == "done"
    record = entry.data[CONF_LEGACY_MIGRATION]
    assert record["state"] == "done"
    assert record["plan"] == []
    assert record["duplicates"] == []
    assert not legacy_migration_pending(entry)
    # Re-running on the done record keeps it as is.
    assert async_rebuild_legacy_record(hass, entry) == "valid"
    assert entry.data[CONF_LEGACY_MIGRATION] == record
    assert _registry_view(hass, entry.entry_id) == before


def test_duplicates_issue_gives_the_exact_manual_step() -> None:
    """The duplicates text names where to delete and how to clear the notice."""
    for path in ("strings.json", "translations/en.json"):
        data = json.loads((_STRINGS_DIR / path).read_text(encoding="utf-8"))
        text = data["issues"][ISSUE_LEGACY_DUPLICATES]["description"]
        assert "{entities}" in text
        assert "Settings → Devices & services → Entities" in text
        assert "**Delete**" in text
        assert "**Reload**" in text


def _render_icu(text: str, values: dict[str, str]) -> str:
    """Render the ICU plural and plain placeholders used in strings.json."""
    plural = re.compile(r"\{(\w+), plural, one \{([^{}]*)\} other \{([^{}]*)\}\}")

    def _plural(match: re.Match[str]) -> str:
        count = int(values[match[1]])
        return (match[2] if count == 1 else match[3]).replace("#", str(count))

    text = plural.sub(_plural, text)
    return re.sub(r"\{(\w+)\}", lambda m: values[m[1]], text)


@pytest.mark.parametrize(
    ("key", "values", "singular", "plural"),
    [
        (
            "legacy_migration_blocked",
            {"conflicts": "\n- switch.a"},
            "1 leftover entity already uses",
            "2 leftover entities already use",
        ),
        (
            "legacy_migration_blocked_devices",
            {},
            "1 leftover Elk E27 device is",
            "2 leftover Elk E27 devices are",
        ),
        (
            "legacy_migration_blocked_mixed",
            {"conflicts": "\n- switch.a", "other": "1"},
            "1 leftover item is",
            "2 leftover items are",
        ),
        (
            "legacy_migration_partial",
            {"conflicts": "\n- switch.a"},
            "1 change couldn't",
            "2 changes couldn't",
        ),
        (
            "legacy_migration_partial_devices",
            {},
            "1 device change couldn't",
            "2 device changes couldn't",
        ),
    ],
)
def test_issue_counts_use_singular_and_plural(
    key: str, values: dict[str, str], singular: str, plural: str
) -> None:
    """Every counted notice reads correctly for one and for many."""
    for path in ("strings.json", "translations/en.json"):
        text = json.loads((_STRINGS_DIR / path).read_text(encoding="utf-8"))["issues"][
            key
        ]["description"]
        one = _render_icu(text, {**values, "count": "1"})
        many = _render_icu(text, {**values, "count": "2"})
        assert singular in one
        assert plural not in one
        assert plural in many
        assert "entities already uses" not in many
        assert "{" not in one
        assert "{" not in many


def test_blocked_notice_matches_what_the_flow_does() -> None:
    """The notice no longer promises a delete the next screen may not offer."""
    data = json.loads((_STRINGS_DIR / "strings.json").read_text(encoding="utf-8"))
    for key in ("legacy_migration_blocked", "legacy_migration_blocked_mixed"):
        text = data["issues"][key]["description"]
        assert "review and delete them" not in text
        assert "remove by hand" in text


def test_mixed_confirm_text_counts_owned_and_unowned() -> None:
    """The confirm text says how many listed entities stay and lists them."""
    data = json.loads((_STRINGS_DIR / "strings.json").read_text(encoding="utf-8"))
    step = data["issues"]["legacy_migration_blocked"]["fix_flow"]["step"]
    text = step["confirm_partial"]["description"]
    one = _render_icu(
        text,
        {
            "count": "25",
            "entities": "\n- switch.owned",
            "kept_count": "1",
            "kept": "\n- switch.orphan",
        },
    )
    assert "These 25 entities are left over" in one
    assert "1 more listed entity isn't owned by this Elk E27 integration" in one
    assert "Remove it by hand" in one
    assert "- switch.orphan" in one
    many = _render_icu(
        text,
        {
            "count": "1",
            "entities": "\n- switch.owned",
            "kept_count": "2",
            "kept": "\n- switch.o1\n- switch.o2",
        },
    )
    assert "This entity is left over" in many
    assert "2 more listed entities aren't owned" in many
    assert "Remove them by hand" in many


async def test_blocked_flow_with_only_unowned_lists_them_and_retries(
    hass: HomeAssistant, panel: ClientHarness
) -> None:
    """Nothing deletable: the retry screen names what to remove by hand."""
    entry, owned, foreign = await _blocked_entry(hass)
    ent_reg = er.async_get(hass)
    ent_reg.async_remove(owned.entity_id)
    manager = await _flow_manager(hass)
    with patch.object(hass.config_entries, "async_schedule_reload") as reload:
        result = await manager.async_init(
            DOMAIN, data={"issue_id": blocked_issue_id(entry.entry_id)}
        )
        assert result["step_id"] == "retry_manual"
        placeholders = result["description_placeholders"]
        assert placeholders["count"] == "1"
        assert foreign.entity_id in placeholders["entities"]
        reload.assert_not_called()
        result = await manager.async_configure(result["flow_id"], {})
    assert result["type"] == "create_entry"
    assert ent_reg.async_get(foreign.entity_id) is not None
    reload.assert_called_once_with(entry.entry_id)
