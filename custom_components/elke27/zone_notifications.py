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


def _parse_zone_ids_placeholder(value: str | None) -> set[int]:
    if not value:
        return set()
    return {int(part.strip()) for part in value.split(",") if part.strip()}


def _zone_ids_placeholder(zone_ids: set[int]) -> str:
    return ", ".join(str(zone_id) for zone_id in sorted(zone_ids))


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
        if bypass:
            issue_id = zones_still_bypassed_issue_id(config_entry_id)
            translation_key = ISSUE_ZONES_STILL_BYPASSED
        else:
            issue_id = zones_still_unbypassed_issue_id(config_entry_id)
            translation_key = ISSUE_ZONES_STILL_UNBYPASSED
        new_ids = {zone.zone_id for zone in zones}
        existing = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
        if existing is not None:
            placeholders = existing.translation_placeholders or {}
            new_ids |= _parse_zone_ids_placeholder(placeholders.get("zone_ids"))
        ir.async_create_issue(
            hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=translation_key,
            translation_placeholders={"zone_ids": _zone_ids_placeholder(new_ids)},
        )


@callback
def async_clear_restored_zones_from_still_changed_issue(
    hass: HomeAssistant,
    config_entry_id: str,
    restored_zone_ids: Sequence[int],
    *,
    service_bypass: bool,
) -> None:
    """Trim or delete the still-changed issue for zones restored on success."""
    if service_bypass:
        issue_id = zones_still_unbypassed_issue_id(config_entry_id)
        translation_key = ISSUE_ZONES_STILL_UNBYPASSED
    else:
        issue_id = zones_still_bypassed_issue_id(config_entry_id)
        translation_key = ISSUE_ZONES_STILL_BYPASSED
    issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    if issue is None:
        return
    placeholders = issue.translation_placeholders or {}
    remaining = _parse_zone_ids_placeholder(placeholders.get("zone_ids")) - set(
        restored_zone_ids
    )
    if not remaining:
        ir.async_delete_issue(hass, DOMAIN, issue_id)
        return
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=translation_key,
        translation_placeholders={"zone_ids": _zone_ids_placeholder(remaining)},
    )
