# ruff: noqa: S101
"""Arming status-reply paths with ARMED_VACATION and unknown arm states (#145)."""

from __future__ import annotations

from typing import Any

from elke27_lib import ArmMode

from custom_components.elke27.arming import (
    area_armed_from_status_reply,
    area_arming_from_status_reply,
    area_skip_automatic_arm_from_status_reply,
)
from tests.conftest import panel_snapshot

UNKNOWN_ARMED_LOOKING = "ARMED_AWAY_PENDING"
UNKNOWN_DISARMED_LOOKING = "DISARMED_PENDING"


def _reply(arm_state: str, **fields: Any) -> dict[str, Any]:
    """Build an ``area_get_status`` reply with arm_state and the given fields."""
    payload: dict[str, Any] = {
        "area_id": 1,
        "arm_state": arm_state,
        "arm_cmd_state": "DISARMED",
        "ee_timer": 0,
        "alarm_zone": "",
    }
    payload.update(fields)
    return payload


def test_vacation_reply_counts_as_armed() -> None:
    """ARMED_VACATION counts as armed, so automatic arming skips."""
    assert area_armed_from_status_reply(_reply("ARMED_VACATION")) is True
    assert area_skip_automatic_arm_from_status_reply(_reply("ARMED_VACATION")) is True


def test_vacation_exit_delay_counts_as_arming() -> None:
    """An exit delay toward ARMED_VACATION counts as arming."""
    reply = _reply("DISARMED", arm_cmd_state="ARMED_VACATION", ee_timer=30)
    assert area_arming_from_status_reply(reply) is True


def test_night_exit_delay_counts_as_arming() -> None:
    """An exit delay toward ARMED_NIGHT counts as arming."""
    reply = _reply("DISARMED", arm_cmd_state="ARMED_STAY_INST", ee_timer=30)
    assert area_arming_from_status_reply(reply) is True


def test_unknown_reply_is_not_armed() -> None:
    """An unknown arm_state is never treated as armed."""
    assert area_armed_from_status_reply(_reply(UNKNOWN_ARMED_LOOKING)) is False
    assert (
        area_skip_automatic_arm_from_status_reply(_reply(UNKNOWN_ARMED_LOOKING))
        is False
    )


def test_unknown_reply_is_not_exit_delay_arming() -> None:
    """An unknown arm_state is never treated as disarmed during exit delay."""
    reply = _reply(UNKNOWN_DISARMED_LOOKING, arm_cmd_state="ARMED_AWAY", ee_timer=30)
    assert area_arming_from_status_reply(reply) is False


def test_unknown_command_state_is_not_exit_delay_arming() -> None:
    """An unknown arm_cmd_state does not count as exit-delay arming."""
    reply = _reply("DISARMED", arm_cmd_state=UNKNOWN_ARMED_LOOKING, ee_timer=30)
    assert area_arming_from_status_reply(reply) is False


def test_cached_snapshot_unchanged_by_unknown_reply() -> None:
    """Unknown arm states in a status reply do not change skip/armed parsing alone."""
    assert panel_snapshot().areas[1].arm_mode is ArmMode.DISARMED
