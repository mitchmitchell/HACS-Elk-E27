"""Persistent notifications when zone bypass state remains changed after a refusal."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components import persistent_notification
from homeassistant.helpers import translation

from .const import DOMAIN

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from elke27_lib import ZoneState

    from homeassistant.core import HomeAssistant

_COMPONENT_NOTIFICATIONS = f"component.{DOMAIN}.notifications."


def _localized_still_changed_notification(
    hass: HomeAssistant, *, bypass: bool, zone_ids: str
) -> tuple[str, str]:
    key = "still_bypassed" if bypass else "still_unbypassed"
    translations = translation.async_get_cached_translations(
        hass,
        hass.config.language,
        "notifications",
        DOMAIN,
    )
    title = translations[f"{_COMPONENT_NOTIFICATIONS}{key}.title"]
    message = translations[f"{_COMPONENT_NOTIFICATIONS}{key}.message"].format(
        zone_ids=zone_ids
    )
    return title, message


def notify_still_changed_zones_by_panel(
    hass: HomeAssistant,
    *,
    zones_by_config_entry: Mapping[str, Sequence[ZoneState]],
    bypass: bool,
    notification_id_prefix: str,
) -> None:
    """Notify the user once per config entry; zone numbers only, never the code."""
    for config_entry_id, zones in zones_by_config_entry.items():
        if not zones:
            continue
        zone_ids = ", ".join(str(zone.zone_id) for zone in zones)
        title, message = _localized_still_changed_notification(
            hass, bypass=bypass, zone_ids=zone_ids
        )
        notification_id = f"elke27_{notification_id_prefix}_{config_entry_id}"
        persistent_notification.async_create(
            hass,
            title=title,
            message=message,
            notification_id=notification_id,
        )
