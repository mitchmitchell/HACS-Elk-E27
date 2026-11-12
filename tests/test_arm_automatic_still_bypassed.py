# ruff: noqa: S101
"""Automatic arming errors keep still-bypassed zones on special failure paths."""

from __future__ import annotations

import dataclasses
from types import MappingProxyType

from elke27_lib import AreaState, PanelSnapshot, ZoneState

from custom_components.elke27.arm_automatic_errors import (
    ARM_NOT_SENT_REASON,
    arm_automatic_not_armed_error,
)


def test_disconnected_reports_still_bypassed() -> None:
    """Panel disconnected after bypasses: still-bypassed zones stay in the raise."""
    snap = dataclasses.replace(
        PanelSnapshot.empty(),
        areas=MappingProxyType({1: AreaState(area_id=1, name="House", ready=True)}),
        zones=MappingProxyType(
            {
                1: ZoneState(zone_id=1, area_id=1, open=False),
                2: ZoneState(zone_id=2, area_id=1, open=False),
            }
        ),
    )
    err = arm_automatic_not_armed_error(
        1,
        snap,
        entity_name="alarm_control_panel.house",
        reason=ARM_NOT_SENT_REASON,
        zone=None,
        rolled_back_zones=[snap.zones[1]],
        still_bypassed_zones=[snap.zones[2]],
        pin_required=False,
    )
    assert (
        err.translation_key
        == "arm_automatic_not_armed_disconnected_partial_rollback_named"
    )
    ph = err.translation_placeholders or {}
    assert ph.get("still_bypassed_zone_numbers") == "2"
    assert ph.get("cleared_zone_numbers") == "1"


def test_bypass_unconfirmed_reports_still_bypassed() -> None:
    """Bypass not acknowledged with leftover bypasses names them in the raise."""
    snap = dataclasses.replace(
        PanelSnapshot.empty(),
        areas=MappingProxyType({1: AreaState(area_id=1, name="House", ready=True)}),
        zones=MappingProxyType(
            {2: ZoneState(zone_id=2, name="Back", area_id=1, open=True)}
        ),
    )
    zone = snap.zones[2]
    err = arm_automatic_not_armed_error(
        1,
        snap,
        entity_name="House",
        reason="bypass was not acknowledged.",
        zone=zone,
        rolled_back_zones=[],
        still_bypassed_zones=[zone],
        pin_required=False,
    )
    assert (
        err.translation_key
        == "arm_automatic_not_armed_bypass_unconfirmed_still_bypassed_named_zone_named"
    )
    ph = err.translation_placeholders or {}
    assert ph.get("still_bypassed_zone_numbers") == "2"


def test_pin_required_with_still_bypassed() -> None:
    """PIN required after partial bypass leaves still-bypassed zones in the raise."""
    snap = dataclasses.replace(
        PanelSnapshot.empty(),
        areas=MappingProxyType({1: AreaState(area_id=1, name="House", ready=True)}),
        zones=MappingProxyType({1: ZoneState(zone_id=1, area_id=1, open=False)}),
    )
    err = arm_automatic_not_armed_error(
        1,
        snap,
        entity_name="House",
        reason="ignored",
        zone=None,
        rolled_back_zones=[],
        still_bypassed_zones=[snap.zones[1]],
        pin_required=True,
    )
    assert (
        err.translation_key
        == "arm_automatic_not_armed_pin_required_still_bypassed_named"
    )
    ph = err.translation_placeholders or {}
    assert ph.get("still_bypassed_zone_numbers") == "1"
