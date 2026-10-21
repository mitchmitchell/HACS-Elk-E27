"""Translated errors for automatic arming (no English built in placeholders)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .arming import PIN_REQUIRED_REASON, ZoneBypassFailedError
from .errors import PinAction, homeassistant_error, pin_required_error

if TYPE_CHECKING:
    from collections.abc import Sequence

    from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

# Internal reason strings stored on failure events (not shown in translated raises).
ARM_NOT_SENT_REASON = "the arm command was not sent (the panel is not connected)"


def _zone_list(labels: Sequence[str]) -> str:
    return ", ".join(labels)


def arm_automatic_not_armed_error(
    area_id: int,
    *,
    reason: str,
    zone_label: str | None,
    rolled_back_labels: Sequence[str],
    still_bypassed_labels: Sequence[str],
    pin_required: bool,
) -> HomeAssistantError | ServiceValidationError:
    """Return the translated error when automatic arming did not arm an area."""
    if pin_required or reason == PIN_REQUIRED_REASON:
        action: PinAction = "arm"
        return pin_required_error(action)

    area = str(area_id)
    cleared = _zone_list(rolled_back_labels)
    still = _zone_list(still_bypassed_labels)

    if reason == ARM_NOT_SENT_REASON:
        key = "arm_automatic_not_armed_disconnected"
        message = (
            f"Area {area} wasn't armed. The panel isn't connected. "
            "Check its network connection, then try again."
        )
        return homeassistant_error(
            key, message, translation_placeholders={"area": area}
        )

    if zone_label is not None and reason == "bypass was not acknowledged.":
        key = "arm_automatic_not_armed_bypass_unconfirmed"
        message = (
            f"Area {area} wasn't armed. The panel didn't confirm the bypass for "
            f"{zone_label}. Check the zone's status, then try again."
        )
        return homeassistant_error(
            key,
            message,
            translation_placeholders={"area": area, "zone": zone_label},
        )

    if zone_label is not None:
        if still and cleared:
            key = "arm_automatic_not_armed_zone_partial_rollback"
            message = (
                f"Area {area} wasn't armed. {zone_label} couldn't be bypassed. "
                f"Bypasses were cleared: {cleared}. These zones are still bypassed: "
                f"{still}. Clear them with the zone bypass service."
            )
            placeholders = {
                "area": area,
                "zone": zone_label,
                "cleared_zones": cleared,
                "still_bypassed": still,
            }
        elif still:
            key = "arm_automatic_not_armed_zone_still_bypassed"
            message = (
                f"Area {area} wasn't armed. {zone_label} couldn't be bypassed. "
                f"These zones are still bypassed: {still}. Clear them with the "
                "zone bypass service."
            )
            placeholders = {
                "area": area,
                "zone": zone_label,
                "still_bypassed": still,
            }
        elif cleared:
            key = "arm_automatic_not_armed_zone_rolled_back"
            message = (
                f"Area {area} wasn't armed. {zone_label} couldn't be bypassed. "
                f"Bypasses were cleared: {cleared}."
            )
            placeholders = {
                "area": area,
                "zone": zone_label,
                "cleared_zones": cleared,
            }
        else:
            key = "arm_automatic_not_armed_zone"
            message = (
                f"Area {area} wasn't armed. {zone_label} couldn't be bypassed. "
                "Close or bypass open zones, then try again."
            )
            placeholders = {"area": area, "zone": zone_label}
        return homeassistant_error(key, message, translation_placeholders=placeholders)

    if still and cleared:
        key = "arm_automatic_not_armed_refused_partial_rollback"
        message = (
            f"Area {area} wasn't armed. The panel didn't accept the command. "
            f"Bypasses were cleared: {cleared}. These zones are still bypassed: "
            f"{still}. Clear them with the zone bypass service."
        )
        return homeassistant_error(
            key,
            message,
            translation_placeholders={
                "area": area,
                "cleared_zones": cleared,
                "still_bypassed": still,
            },
        )
    if still:
        key = "arm_automatic_not_armed_refused_still_bypassed"
        message = (
            f"Area {area} wasn't armed. The panel didn't accept the command. "
            f"These zones are still bypassed: {still}. Clear them with the zone "
            "bypass service."
        )
        return homeassistant_error(
            key,
            message,
            translation_placeholders={"area": area, "still_bypassed": still},
        )
    if cleared:
        key = "arm_automatic_not_armed_refused_rolled_back"
        message = (
            f"Area {area} wasn't armed. The panel didn't accept the command. "
            f"Bypasses were cleared: {cleared}."
        )
        return homeassistant_error(
            key,
            message,
            translation_placeholders={"area": area, "cleared_zones": cleared},
        )
    key = "arm_automatic_not_armed_refused"
    message = (
        f"Area {area} wasn't armed. The panel didn't accept the command. "
        "Check that the area is ready, then try again."
    )
    return homeassistant_error(key, message, translation_placeholders={"area": area})


def arm_automatic_uncertain_error(
    area_id: int, *, bypassed_labels: Sequence[str]
) -> HomeAssistantError:
    """Return the translated error when the arm result is unknown."""
    area = str(area_id)
    zones = _zone_list(bypassed_labels)
    if bypassed_labels:
        key = "arm_automatic_uncertain_with_bypasses"
        message = (
            f"Area {area} may or may not be armed. Check the area on the panel. "
            f"These zones were left bypassed: {zones}. If the area isn't armed, "
            "clear the bypasses with the zone bypass service."
        )
        return homeassistant_error(
            key,
            message,
            translation_placeholders={"area": area, "zones": zones},
        )
    key = "arm_automatic_uncertain"
    message = f"Area {area} may or may not be armed. Check the area on the panel."
    return homeassistant_error(key, message, translation_placeholders={"area": area})


def arm_automatic_cancelled_error(
    area_id: int,
    *,
    bypassed_labels: Sequence[str],
    arm_sent: bool,
) -> HomeAssistantError:
    """Return the translated error when a disarm cancels automatic arming."""
    area = str(area_id)
    zones = _zone_list(bypassed_labels)
    if not bypassed_labels:
        key = "arm_automatic_cancelled"
        message = (
            f"Automatic arming for area {area} stopped because someone disarmed it."
        )
        return homeassistant_error(
            key, message, translation_placeholders={"area": area}
        )
    if arm_sent:
        key = "arm_automatic_cancelled_arm_sent"
        message = (
            f"Automatic arming for area {area} stopped because someone disarmed it. "
            f"The arm had already been sent. If these zones are still bypassed, "
            f"clear them with the zone bypass service: {zones}."
        )
        return homeassistant_error(
            key,
            message,
            translation_placeholders={"area": area, "zones": zones},
        )
    key = "arm_automatic_cancelled_bypasses"
    message = (
        f"Automatic arming for area {area} stopped because someone disarmed it. "
        f"These zones may still be bypassed: {zones}. If they are, clear them "
        "with the zone bypass service."
    )
    return homeassistant_error(
        key,
        message,
        translation_placeholders={"area": area, "zones": zones},
    )


def arm_automatic_multi_failed_error(
    failed_count: int, total_count: int
) -> HomeAssistantError:
    """Return the translated error when several areas fail automatic arming."""
    key = "arm_automatic_multi_failed"
    message = (
        f"Automatic arming failed for {failed_count} of {total_count} areas. "
        "Open each area's notification for details."
    )
    return homeassistant_error(
        key,
        message,
        translation_placeholders={
            "failed_count": str(failed_count),
            "total_count": str(total_count),
        },
    )


def zone_bypass_failed_pin_required(err: ZoneBypassFailedError) -> bool:
    """Return True when a bypass failure is missing a user code."""
    return err.pin_required or err.reason == PIN_REQUIRED_REASON
