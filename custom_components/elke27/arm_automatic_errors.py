"""Translated errors for automatic arming (no English built in placeholders)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from elke27_lib import PanelSnapshot, ZoneState  # noqa: TC002

from .arming import (
    PIN_REQUIRED_REASON,
    AreaPhrase,
    ZoneBypassFailedError,
    ZonePhrase,
    area_automatic_phrase,
    zone_automatic_phrase,
    zone_number_list,
)
from .errors import PinAction, homeassistant_error, pin_required_error

if TYPE_CHECKING:
    from collections.abc import Sequence

    from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

# Internal reason strings stored on failure events (not shown in translated raises).
ARM_NOT_SENT_REASON = "the arm command was not sent (the panel is not connected)"

_EXCEPTIONS: dict[str, dict[str, str]] = json.loads(
    (Path(__file__).parent / "strings.json").read_text(encoding="utf-8")
)["exceptions"]


def _arm_key(base: str, area: AreaPhrase, zone: ZonePhrase | None) -> str:
    if zone is None:
        return f"{base}_{'named' if area.named else 'unnamed'}"
    area_part = "named" if area.named else "unnamed"
    zone_part = "named" if zone.named else "unnamed"
    return f"{base}_{area_part}_zone_{zone_part}"


def _area_placeholders(area: AreaPhrase) -> dict[str, str]:
    if area.named:
        return {"area_name": area.name, "area_number": area.number}
    return {"area_number": area.number}


def _zone_placeholders(zone: ZonePhrase) -> dict[str, str]:
    if zone.named:
        return {"zone_name": zone.name, "zone_number": zone.number}
    return {"zone_number": zone.number}


def _translated_error(key: str, placeholders: dict[str, str]) -> HomeAssistantError:
    message = _EXCEPTIONS[key]["message"].format(**placeholders)
    return homeassistant_error(key, message, translation_placeholders=placeholders)


def arm_automatic_not_armed_error(
    area_id: int,
    snapshot: PanelSnapshot | None,
    *,
    entity_name: str | None,
    reason: str,
    zone: ZoneState | None,
    rolled_back_zones: Sequence[ZoneState],
    still_bypassed_zones: Sequence[ZoneState],
    pin_required: bool,
) -> HomeAssistantError | ServiceValidationError:
    """Return the translated error when automatic arming did not arm an area."""
    if pin_required or reason == PIN_REQUIRED_REASON:
        action: PinAction = "arm"
        return pin_required_error(action)

    area = area_automatic_phrase(area_id, snapshot, entity_name=entity_name)
    zone_phrase = zone_automatic_phrase(zone) if zone is not None else None
    cleared = zone_number_list(list(rolled_back_zones))
    still = zone_number_list(list(still_bypassed_zones))
    placeholders = _area_placeholders(area)

    if reason == ARM_NOT_SENT_REASON:
        key = _arm_key("arm_automatic_not_armed_disconnected", area, None)
        return _translated_error(key, placeholders)

    if zone is not None and reason == "bypass was not acknowledged.":
        if zone_phrase is None:
            msg = "internal error: zone state missing for bypass unconfirmed"
            raise RuntimeError(msg)
        key = _arm_key("arm_automatic_not_armed_bypass_unconfirmed", area, zone_phrase)
        ph = {**placeholders, **_zone_placeholders(zone_phrase)}
        return _translated_error(key, ph)

    if zone is not None and zone_phrase is not None:
        if still and cleared:
            key = _arm_key(
                "arm_automatic_not_armed_zone_partial_rollback", area, zone_phrase
            )
            ph = {
                **placeholders,
                **_zone_placeholders(zone_phrase),
                "cleared_zone_numbers": cleared,
                "still_bypassed_zone_numbers": still,
            }
        elif still:
            key = _arm_key(
                "arm_automatic_not_armed_zone_still_bypassed", area, zone_phrase
            )
            ph = {
                **placeholders,
                **_zone_placeholders(zone_phrase),
                "still_bypassed_zone_numbers": still,
            }
        elif cleared:
            key = _arm_key(
                "arm_automatic_not_armed_zone_rolled_back", area, zone_phrase
            )
            ph = {
                **placeholders,
                **_zone_placeholders(zone_phrase),
                "cleared_zone_numbers": cleared,
            }
        else:
            key = _arm_key("arm_automatic_not_armed_zone", area, zone_phrase)
            ph = {**placeholders, **_zone_placeholders(zone_phrase)}
        return _translated_error(key, ph)

    if still and cleared:
        key = _arm_key("arm_automatic_not_armed_refused_partial_rollback", area, None)
        ph = {
            **placeholders,
            "cleared_zone_numbers": cleared,
            "still_bypassed_zone_numbers": still,
        }
        return _translated_error(key, ph)
    if still:
        key = _arm_key("arm_automatic_not_armed_refused_still_bypassed", area, None)
        ph = {**placeholders, "still_bypassed_zone_numbers": still}
        return _translated_error(key, ph)
    if cleared:
        key = _arm_key("arm_automatic_not_armed_refused_rolled_back", area, None)
        ph = {**placeholders, "cleared_zone_numbers": cleared}
        return _translated_error(key, ph)
    key = _arm_key("arm_automatic_not_armed_refused", area, None)
    return _translated_error(key, placeholders)


def arm_automatic_uncertain_error(
    area_id: int,
    snapshot: PanelSnapshot | None,
    *,
    entity_name: str | None,
    bypassed_zones: Sequence[ZoneState],
) -> HomeAssistantError:
    """Return the translated error when the arm result is unknown."""
    area = area_automatic_phrase(area_id, snapshot, entity_name=entity_name)
    placeholders = _area_placeholders(area)
    if bypassed_zones:
        key = _arm_key("arm_automatic_uncertain_with_bypasses", area, None)
        placeholders = {
            **placeholders,
            "bypassed_zone_numbers": zone_number_list(list(bypassed_zones)),
        }
        return _translated_error(key, placeholders)
    key = _arm_key("arm_automatic_uncertain", area, None)
    return _translated_error(key, placeholders)


def arm_automatic_cancelled_error(
    area_id: int,
    snapshot: PanelSnapshot | None,
    *,
    entity_name: str | None,
    bypassed_zones: Sequence[ZoneState],
    arm_sent: bool,
) -> HomeAssistantError:
    """Return the translated error when a disarm cancels automatic arming."""
    area = area_automatic_phrase(area_id, snapshot, entity_name=entity_name)
    placeholders = _area_placeholders(area)
    if not bypassed_zones:
        key = _arm_key("arm_automatic_cancelled", area, None)
        return _translated_error(key, placeholders)
    placeholders = {
        **placeholders,
        "bypassed_zone_numbers": zone_number_list(list(bypassed_zones)),
    }
    if arm_sent:
        key = _arm_key("arm_automatic_cancelled_arm_sent", area, None)
        return _translated_error(key, placeholders)
    key = _arm_key("arm_automatic_cancelled_bypasses", area, None)
    return _translated_error(key, placeholders)


def arm_automatic_multi_failed_error(
    failed_count: int, total_count: int
) -> HomeAssistantError:
    """Return the translated error when several areas fail automatic arming."""
    key = "arm_automatic_multi_failed"
    placeholders = {
        "failed_count": str(failed_count),
        "total_count": str(total_count),
    }
    return _translated_error(key, placeholders)


def zone_bypass_failed_pin_required(err: ZoneBypassFailedError) -> bool:
    """Return True when a bypass failure is missing a user code."""
    return err.pin_required or err.reason == PIN_REQUIRED_REASON


ARM_AUTOMATIC_KEYS = frozenset(
    key for key in _EXCEPTIONS if key.startswith("arm_automatic")
)
