# ruff: noqa: S101, D103, FBT001, FBT003
"""Repairs issues for zone bypass state left changed after a refusal."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from elke27_lib import ZoneState
import pytest

from custom_components.elke27 import async_remove_entry
from custom_components.elke27.const import (
    DOMAIN,
    ISSUE_ZONES_STILL_BYPASSED,
    ISSUE_ZONES_STILL_UNBYPASSED,
)
from custom_components.elke27.zone_notifications import (
    notify_still_changed_zones_by_panel,
    zones_still_bypassed_issue_id,
    zones_still_unbypassed_issue_id,
)
from homeassistant.helpers import issue_registry as ir
from tests.conftest import FakeClient, Setup, command_result

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.issue_registry import IssueRegistry

_COMPONENT = Path(__file__).parent.parent / "custom_components" / "elke27"
_STRINGS = json.loads((_COMPONENT / "strings.json").read_text(encoding="utf-8"))
_ISSUES = _STRINGS["issues"]


def _expect_issue(
    issue_registry: IssueRegistry,
    *,
    entry_id: str,
    bypass: bool,
    zone_ids: str,
) -> None:
    key = ISSUE_ZONES_STILL_BYPASSED if bypass else ISSUE_ZONES_STILL_UNBYPASSED
    issue_id = (
        zones_still_bypassed_issue_id(entry_id)
        if bypass
        else zones_still_unbypassed_issue_id(entry_id)
    )
    issue = issue_registry.async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.is_fixable is False
    assert issue.translation_key == key
    assert issue.translation_placeholders == {"zone_ids": zone_ids}
    assert issue.severity == ir.IssueSeverity.WARNING
    strings = _ISSUES[key]
    assert strings["title"]
    assert zone_ids in strings["description"].format(zone_ids=zone_ids)


@pytest.mark.parametrize("bypass", [True, False], ids=["bypassed", "unbypassed"])
def test_notify_still_changed_creates_repair_issue(
    hass: HomeAssistant,
    issue_registry: IssueRegistry,
    bypass: bool,
) -> None:
    entry_id = "entry-1"
    zones = (ZoneState(zone_id=2, name="", area_id=1, open=False, bypassed=bypass),)
    notify_still_changed_zones_by_panel(
        hass,
        zones_by_config_entry={entry_id: zones},
        bypass=bypass,
    )
    _expect_issue(issue_registry, entry_id=entry_id, bypass=bypass, zone_ids="2")


async def test_remove_entry_clears_zones_still_changed_issues(
    hass: HomeAssistant,
    setup: Setup,
    issue_registry: IssueRegistry,
) -> None:
    client = FakeClient(command_result(True))
    await setup(client)
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    bypassed_zones = (
        ZoneState(zone_id=1, name="", area_id=1, open=False, bypassed=True),
    )
    unbypassed_zones = (
        ZoneState(zone_id=2, name="", area_id=1, open=False, bypassed=False),
    )
    notify_still_changed_zones_by_panel(
        hass,
        zones_by_config_entry={entry.entry_id: bypassed_zones},
        bypass=True,
    )
    notify_still_changed_zones_by_panel(
        hass,
        zones_by_config_entry={entry.entry_id: unbypassed_zones},
        bypass=False,
    )
    bypassed_issue_id = zones_still_bypassed_issue_id(entry.entry_id)
    unbypassed_issue_id = zones_still_unbypassed_issue_id(entry.entry_id)
    assert issue_registry.async_get_issue(DOMAIN, bypassed_issue_id) is not None
    assert issue_registry.async_get_issue(DOMAIN, unbypassed_issue_id) is not None
    await async_remove_entry(hass, entry)
    assert issue_registry.async_get_issue(DOMAIN, bypassed_issue_id) is None
    assert issue_registry.async_get_issue(DOMAIN, unbypassed_issue_id) is None


def test_notify_still_changed_merges_zone_ids_on_repeat_failure(
    hass: HomeAssistant,
    issue_registry: IssueRegistry,
) -> None:
    entry_id = "entry-1"
    notify_still_changed_zones_by_panel(
        hass,
        zones_by_config_entry={
            entry_id: (
                ZoneState(zone_id=1, name="", area_id=1, open=False, bypassed=True),
            )
        },
        bypass=True,
    )
    notify_still_changed_zones_by_panel(
        hass,
        zones_by_config_entry={
            entry_id: (
                ZoneState(zone_id=2, name="", area_id=1, open=False, bypassed=True),
            )
        },
        bypass=True,
    )
    _expect_issue(
        issue_registry,
        entry_id=entry_id,
        bypass=True,
        zone_ids="1, 2",
    )


def test_still_bypassed_and_still_unbypassed_issues_stay_separate(
    hass: HomeAssistant,
    issue_registry: IssueRegistry,
) -> None:
    entry_id = "entry-1"
    notify_still_changed_zones_by_panel(
        hass,
        zones_by_config_entry={
            entry_id: (
                ZoneState(zone_id=1, name="", area_id=1, open=False, bypassed=True),
            )
        },
        bypass=True,
    )
    notify_still_changed_zones_by_panel(
        hass,
        zones_by_config_entry={
            entry_id: (
                ZoneState(zone_id=2, name="", area_id=1, open=False, bypassed=False),
            )
        },
        bypass=False,
    )
    bypassed = issue_registry.async_get_issue(
        DOMAIN, f"{ISSUE_ZONES_STILL_BYPASSED}_{entry_id}"
    )
    unbypassed = issue_registry.async_get_issue(
        DOMAIN, f"{ISSUE_ZONES_STILL_UNBYPASSED}_{entry_id}"
    )
    assert bypassed is not None
    assert unbypassed is not None
    assert bypassed.translation_key == ISSUE_ZONES_STILL_BYPASSED
    assert bypassed.translation_placeholders == {"zone_ids": "1"}
    assert unbypassed.translation_key == ISSUE_ZONES_STILL_UNBYPASSED
    assert unbypassed.translation_placeholders == {"zone_ids": "2"}
