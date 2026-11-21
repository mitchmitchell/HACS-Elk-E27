"""Persistent notifications when zone bypass state remains changed after a refusal."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components import persistent_notification

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from elke27_lib import ZoneState

    from homeassistant.core import HomeAssistant


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
        if bypass:
            title = "Elk E27 zones still bypassed"
            message = (
                "The panel refused the command and these zones could not be"
                f" un-bypassed, so they are still bypassed: zones {zone_ids}."
                " Clear them with the zone bypass service."
            )
        else:
            title = "Elk E27 zones still un-bypassed"
            message = (
                "The panel refused the command and these zones could not be"
                f" re-bypassed, so they are no longer bypassed: zones {zone_ids}."
                " Bypass them again with the zone bypass service."
            )
        notification_id = f"elke27_{notification_id_prefix}_{config_entry_id}"
        persistent_notification.async_create(
            hass,
            title=title,
            message=message,
            notification_id=notification_id,
        )
