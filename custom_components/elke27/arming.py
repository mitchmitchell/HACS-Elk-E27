"""Elke27 arming helpers: refusal classifiers, status parsing and bypass errors."""

from __future__ import annotations

import dataclasses
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from elke27_lib import ArmMode, PanelSnapshot, ZoneState
from elke27_lib.errors import (
    Elke27AuthError,
    Elke27InvalidArgument,
    Elke27PanelError,
    Elke27PermissionError,
    Elke27PinRequiredError,
)

from homeassistant.exceptions import HomeAssistantError

from .errors import (
    arm_mode_not_supported_error,
    code_all_zero_error,
    code_not_numeric_error,
    panel_error_code,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

# How long automatic arming waits for area_get_status to show armed after a
# prior arm (for example a queued alarm_arm_automatic on the same area).
ARM_STATUS_POLL_INTERVAL = 0.25


ARM_STATUS_POLL_TIMEOUT = 2.5


PIN_REQUIRED_REASON = "a user code is required"


class ZoneBypassFailedError(HomeAssistantError):
    """Bypass could not be applied to a zone."""

    def __init__(
        self,
        zone: ZoneState,
        reason: str,
        bypassed_zones: list[ZoneState] | tuple[ZoneState, ...] = (),
        *,
        pin_required: bool = False,
    ) -> None:
        """Initialize with the failed zone, the reason and zones already bypassed."""
        self.zone = zone
        self.reason = reason
        self.bypassed_zones: tuple[ZoneState, ...] = tuple(bypassed_zones)
        self.pin_required = pin_required
        message = f"{zone_bypass_label(zone)}: {reason}"
        if self.bypassed_zones:
            labels = ", ".join(zone_bypass_label(item) for item in self.bypassed_zones)
            message = f"{message} (already bypassed: {labels})"
        super().__init__(message)


# The panel answers "not allowed when armed" (ELKERR_NOT_ALLOWED_WHEN_ARMED).
PANEL_ERROR_ALREADY_ARMED = 11028


_ARMED_MODES = frozenset({ArmMode.ARMED_AWAY, ArmMode.ARMED_STAY, ArmMode.ARMED_NIGHT})


_ZONE_STATUS_BYPASSED = frozenset("DEF")


_ZONE_STATUS_VIOLATED = frozenset("9AB")


_ZONE_STATUS_KNOWN = frozenset("0123456789ABCDEF")


def _strict_int(value: object) -> int | None:
    """Return the value only when it is a true int (bool excluded)."""
    return value if type(value) is int else None


def _arm_mode_from_text(value: Any) -> ArmMode | None:
    """Map a panel arm_state string to an ArmMode (as elke27 does)."""
    if not isinstance(value, str):
        return None
    lowered = value.lower()
    if "disarm" in lowered:
        return ArmMode.DISARMED
    if "stay" in lowered:
        return ArmMode.ARMED_STAY
    if "away" in lowered:
        return ArmMode.ARMED_AWAY
    if "night" in lowered:
        return ArmMode.ARMED_NIGHT
    return None


def _zone_with_status_char(zone: ZoneState, ch: str) -> ZoneState:
    """Apply one zone_get_all_zones_status character (elke27's encoding)."""
    if ch not in _ZONE_STATUS_KNOWN:
        return zone
    if ch in _ZONE_STATUS_BYPASSED:
        return dataclasses.replace(zone, bypassed=True)
    return dataclasses.replace(zone, bypassed=False, open=ch in _ZONE_STATUS_VIOLATED)


def snapshot_with_status(
    snapshot: PanelSnapshot | None,
    area_id: int,
    area_payload: Mapping[str, Any] | None,
    zones_payload: Mapping[str, Any] | None,
) -> PanelSnapshot | None:
    """Overlay area and zone status reply payloads on a snapshot."""
    if snapshot is None:
        return None
    areas = dict(snapshot.areas)
    zones = dict(snapshot.zones)
    if area_payload is not None and area_id in areas:
        area = areas[area_id]
        updates: dict[str, Any] = {}
        mode = _arm_mode_from_text(
            area_payload.get("arm_state") or area_payload.get("armed_state")
        )
        if mode is not None:
            updates["arm_mode"] = mode
        has_exit_delay_fields = any(
            key in area_payload for key in ("arm_cmd_state", "ee_timer", "alarm_zone")
        )
        if not has_exit_delay_fields:
            updates["arm_cmd_mode"] = None
            updates["ee_timer"] = None
            updates["alarm_zone"] = None
        else:
            if "arm_cmd_state" in area_payload:
                cmd_raw = area_payload.get("arm_cmd_state")
                updates["arm_cmd_mode"] = (
                    _arm_mode_from_text(cmd_raw) if isinstance(cmd_raw, str) else None
                )
            else:
                updates["arm_cmd_mode"] = None
            if "ee_timer" in area_payload:
                updates["ee_timer"] = _strict_int(area_payload["ee_timer"])
            else:
                updates["ee_timer"] = None
            if "alarm_zone" in area_payload:
                alarm_zone = area_payload.get("alarm_zone")
                updates["alarm_zone"] = (
                    alarm_zone if isinstance(alarm_zone, str) else None
                )
            else:
                updates["alarm_zone"] = None
        areas[area_id] = dataclasses.replace(area, **updates)
    status = zones_payload.get("status") if zones_payload is not None else None
    if isinstance(status, str):
        for index, ch in enumerate("".join(status.split()).upper()):
            zone = zones.get(index + 1)
            if zone is not None:
                zones[index + 1] = _zone_with_status_char(zone, ch)
    return dataclasses.replace(
        snapshot,
        areas=MappingProxyType(areas),
        zones=MappingProxyType(zones),
    )


def area_armed_from_status_reply(
    area_payload: Mapping[str, Any] | None,
) -> bool | None:
    """
    Return whether area_get_status reported an armed mode.

    None when the request failed: callers must not treat the cached snapshot as
    armed for skip or no-op decisions.
    """
    if area_payload is None:
        return None
    mode = _arm_mode_from_text(
        area_payload.get("arm_state") or area_payload.get("armed_state")
    )
    if mode is None:
        return False
    return mode in _ARMED_MODES


def area_arming_from_status_reply(area_payload: Mapping[str, Any]) -> bool:
    """
    Return True when the reply explicitly shows exit-delay arming.

    Unknown or missing fields never count as arming (returns False).
    """
    arm_state_raw = area_payload.get("arm_state") or area_payload.get("armed_state")
    if not isinstance(arm_state_raw, str):
        return False
    if _arm_mode_from_text(arm_state_raw) is not ArmMode.DISARMED:
        return False
    cmd_raw = area_payload.get("arm_cmd_state")
    if not isinstance(cmd_raw, str):
        return False
    cmd_mode = _arm_mode_from_text(cmd_raw)
    if cmd_mode not in (ArmMode.ARMED_AWAY, ArmMode.ARMED_STAY):
        return False
    ee_timer = area_payload.get("ee_timer")
    if type(ee_timer) is not int or ee_timer <= 0:
        return False
    if "alarm_zone" not in area_payload:
        return False
    alarm_zone = area_payload.get("alarm_zone")
    return isinstance(alarm_zone, str) and alarm_zone == ""


def area_skip_automatic_arm_from_status_reply(
    area_payload: Mapping[str, Any] | None,
) -> bool | None:
    """
    Return whether automatic arming should skip from a fresh area_get_status reply.

    True when the area is armed or in exit-delay arming; False when it is not.
    None when the request failed.
    """
    if area_payload is None:
        return None
    if area_armed_from_status_reply(area_payload):
        return True
    return area_arming_from_status_reply(area_payload)


def area_is_armed(snapshot: PanelSnapshot | None, area_id: int) -> bool:
    """Return True when the snapshot shows the area in any armed state."""
    if snapshot is None:
        return False
    area = snapshot.areas.get(area_id)
    return area is not None and area.arm_mode in _ARMED_MODES


def is_already_armed_refusal(err: BaseException) -> bool:
    """Return True when the panel refused because the area is already armed."""
    return panel_error_code(err) == PANEL_ERROR_ALREADY_ARMED


def is_definitive_refusal(err: BaseException) -> bool:
    """
    Return True when a failed command is known not to have taken effect.

    Definitive: a panel refusal with an error code, a missing user code, an
    invalid argument, or a check made before anything was sent: an integration
    check (a HomeAssistantError with no library cause), Elke27PermissionError
    (elke27 raises it only from its pre-send session and disarmed-state checks)
    and Elke27AuthError (raised for arm only by elke27's pre-send PIN check).
    Not definitive, because the panel may have acted: timeouts, dropped or
    not-ready sessions (Elke27ConnectionError can also come from the receive
    path), Elke27LinkRequiredError (not reachable from an arm in elke27, so kept
    conservative), the generic "Failed to arm area." protocol error, and any
    unknown error. "Already armed" (11028) is a panel code but must not cause a
    rollback; callers check is_already_armed_refusal first.
    """
    if isinstance(
        err,
        (
            Elke27PinRequiredError,
            Elke27InvalidArgument,
            Elke27PanelError,
            Elke27PermissionError,
            Elke27AuthError,
        ),
    ):
        return True
    if getattr(err, "panel_error_code", None) is not None:
        return True
    if isinstance(err, HomeAssistantError):
        cause = err.__cause__
        if cause is None:
            return True
        return is_definitive_refusal(cause)
    return False


def area_faulted_zones(snapshot: PanelSnapshot | None, area_id: int) -> list[ZoneState]:
    """Return open, non-bypassed zones assigned to the given area."""
    if snapshot is None:
        return []
    return [zone for zone in snapshot.faulted_zones if zone.area_id == area_id]


def zone_bypass_label(zone: ZoneState) -> str:
    """Return a user-facing label for a zone, such as 'Perimeter (zone 16)'."""
    name = (zone.name or "").strip()
    if name:
        return f"{name} (zone {zone.zone_id})"
    return f"Zone {zone.zone_id}"


def _validated_pin(pin: str) -> str:
    """Return the user code as a digit string; raise if not numeric or all zeros."""
    value = str(pin).strip()
    # ASCII digits only; all-zero detected without int() (no huge-int conversion).
    if not (value.isascii() and value.isdigit()):
        raise code_not_numeric_error()
    if not value.strip("0"):
        raise code_all_zero_error()
    return value


def _library_arm_mode(mode: Any) -> ArmMode:
    """Map an integration arm request to the elke27 arm mode."""
    # The E27 has Away and Stay arming only; it has no Night mode.
    if mode in (ArmMode.ARMED_STAY, ArmMode.ARMED_AWAY):
        return ArmMode(mode)
    # Custom bypass: the entity bypasses open zones first, then arms away.
    if isinstance(mode, str) and mode.upper() == "ARMED_CUSTOM_BYPASS":
        return ArmMode.ARMED_AWAY
    raise arm_mode_not_supported_error()
