"""Repairs issues when zone bypass state remains changed after a refusal."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.core import callback
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN, ISSUE_ZONES_STILL_BYPASSED, ISSUE_ZONES_STILL_UNBYPASSED

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from elke27_lib import ZoneState

    from homeassistant.core import HomeAssistant


def zones_still_bypassed_issue_id(config_entry_id: str) -> str:
    """Return the Repairs issue id when bypassed zones could not be restored."""
    return f"{ISSUE_ZONES_STILL_BYPASSED}_{config_entry_id}"


def zones_still_unbypassed_issue_id(config_entry_id: str) -> str:
    """Return the Repairs issue id when un-bypassed zones could not be restored."""
    return f"{ISSUE_ZONES_STILL_UNBYPASSED}_{config_entry_id}"


@callback
def async_clear_zones_still_changed_issues(
    hass: HomeAssistant, config_entry_id: str
) -> None:
    """Remove still-changed zone Repairs issues for one config entry."""
    ir.async_delete_issue(hass, DOMAIN, zones_still_bypassed_issue_id(config_entry_id))
    ir.async_delete_issue(
        hass, DOMAIN, zones_still_unbypassed_issue_id(config_entry_id)
    )


def notify_still_changed_zones_by_panel(
    hass: HomeAssistant,
    *,
    zones_by_config_entry: Mapping[str, Sequence[ZoneState]],
    bypass: bool,
) -> None:
    """Raise a repair issue once per config entry; zone numbers only, never the code."""
    for config_entry_id, zones in zones_by_config_entry.items():
        if not zones:
            continue
        zone_ids = ", ".join(str(zone.zone_id) for zone in zones)
        if bypass:
            issue_id = zones_still_bypassed_issue_id(config_entry_id)
            translation_key = ISSUE_ZONES_STILL_BYPASSED
        else:
            issue_id = zones_still_unbypassed_issue_id(config_entry_id)
            translation_key = ISSUE_ZONES_STILL_UNBYPASSED
        ir.async_create_issue(
            hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=translation_key,
            translation_placeholders={"zone_ids": zone_ids},
        )
