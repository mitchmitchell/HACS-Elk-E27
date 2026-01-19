from __future__ import annotations

from collections.abc import Mapping
from typing import Iterable


_REQUIRED_KEYS: dict[str, tuple[str, ...]] = {
    "control_authenticate": (
        "seq",
        "session_id",
        "installer",
        "user_id",
        "group_id",
        "network_csm",
        "area_csm",
        "user_group_csm",
        "user_csm",
        "zone_csm",
        "keyfob_csm",
        "system_csm",
        "wltx_csm",
        "output_csm",
        "cs_param_csm",
        "flag_csm",
        "pir_flag_csm",
        "user_value_csm",
        "register_csm",
        "rule_csm",
        "task_csm",
        "light_csm",
        "lock_csm",
        "tstat_csm",
        "barrier_csm",
        "win_cover_csm",
        "timer_csm",
        "zwave_csm",
        "bus_io_dev_csm",
        "keypad_csm",
        "wgnd_csm",
        "sounder_csm",
        "repeater_csm",
        "error_code",
    ),
    "area_get_configured": ("areas", "block_count"),
    "area_get_attribs": (
        "area_id",
        "name",
        "flags",
        "kp_single_key_arm",
        "entry1_time",
        "exit_time",
        "ext_entry_time",
        "aa_warn_mins",
        "auto_arm_time",
        "auto_arm_level",
        "sounder",
        "error_code",
    ),
    "area_get_status": (
        "area_id",
        "arm_state",
        "ready_status",
        "alarm_zone",
        "alarms",
        "alarm_mem",
        "zones_bypassed",
        "arm_cmd_state",
        "troubles",
        "chime_count",
        "Chime",
        "xzn_id",
        "xzn_time",
        "ee_timer",
        "alrm_snd",
        "auto_arm_timer",
        "error_code",
    ),
    "area_set_status": ("area_id", "error_code"),
    "area_set_arm_state": ("area_id", "error_code"),
    "zone_get_configured": ("zones", "block_count"),
    "zone_get_attribs": ("zone_id", "name", "area_id", "definition", "flags", "error_code"),
    "zone_get_status": ("zone_id", "secure_state", "contact_state", "BYPASSED", "error_code"),
    "zone_set_status": ("zone_id", "error_code"),
    "zone_get_all_zones_status": ("status", "error_code"),
    "zone_get_defs": ("definitions", "block_count"),
    "zone_get_def_flags": ("definition", "flags", "error_code"),
    "output_get_configured": ("outputs", "block_count"),
    "output_get_attribs": ("output_id", "name", "source_id", "device_id", "hide", "error_code"),
    "output_get_status": ("output_id", "status", "error_code"),
    "output_get_all_outputs_status": ("status", "block_count"),
    "rule_get_rules": ("rules", "block_count"),
    "system_get_trouble": ("troubles", "at", "error_code"),
    "user_get_configured": ("users", "block_count"),
    "user_get_attribs": ("user_id", "name", "pin", "group_id", "flags", "error_code"),
    "keypad_get_configured": ("keypads", "block_count"),
    "keypad_get_attribs": (
        "keypad_id",
        "source_id",
        "device_id",
        "name",
        "area",
        "backlight",
        "zone_id",
        "quiet_start",
        "quiet_end",
        "flags",
        "error_code",
    ),
    "area_get_table_info": (
        "table_csm",
        "table_elements",
        "tablesize",
        "increment_size",
        "element_size",
        "error_code",
    ),
    "zone_get_table_info": (
        "table_csm",
        "table_elements",
        "tablesize",
        "increment_size",
        "element_size",
        "error_code",
    ),
    "output_get_table_info": (
        "table_csm",
        "table_elements",
        "tablesize",
        "increment_size",
        "element_size",
        "error_code",
    ),
    "tstat_get_table_info": (
        "table_csm",
        "table_elements",
        "tablesize",
        "increment_size",
        "element_size",
        "error_code",
    ),
    "tstat_get_status": (
        "tstat_id",
        "temperature",
        "mode",
        "fan_mode",
        "heat_setpoint",
        "cool_setpoint",
        "humidity",
        "rssi",
        "prec",
        "battery level",
        "error_code",
    ),
    "network_param_get_ssid": ("value", "error_code"),
    "control_get_version_info": (
        "sn",
        "hw",
        "boot",
        "app",
        "db1",
        "db1ver",
        "schema",
        "SSP",
        "BUILD_DATETIME",
        "error_code",
    ),
}

_ALTERNATE_REQUIRED: dict[str, tuple[tuple[str, ...], ...]] = {
    "area_get_configured": (("areas", "block_id", "block_count", "error_code"),),
    "zone_get_configured": (("zones", "block_id", "block_count", "error_code"),),
    "zone_get_defs": (("definitions", "block_id", "block_count", "error_code"),),
    "output_get_configured": (("outputs", "block_id", "block_count", "error_code"),),
    "output_get_all_outputs_status": (("status", "error_code"),),
    "rule_get_rules": (("data", "block_id", "block_count", "error_code"),),
    "user_get_configured": (("users", "block_id", "block_count", "error_code"),),
    "keypad_get_configured": (("keypads", "block_id", "block_count", "error_code"),),
}

_OPTIONAL_KEYS: dict[str, tuple[str, ...]] = {
    "zone_get_status": ("rssi", "low_batt"),
}


def assert_payload_shape(command_key: str, payload: Mapping[str, object] | None) -> None:
    if payload is None:
        raise AssertionError(f"{command_key}: missing payload")
    if not isinstance(payload, Mapping):
        raise AssertionError(f"{command_key}: payload must be a mapping (got {type(payload).__name__})")

    required = _REQUIRED_KEYS.get(command_key)
    if required is None:
        raise AssertionError(f"{command_key}: no payload shape registered")

    target = _select_payload(command_key, payload)
    optional = _OPTIONAL_KEYS.get(command_key, ())
    required_sets = [required]
    required_sets.extend(_ALTERNATE_REQUIRED.get(command_key, ()))
    for required_set in required_sets:
        missing = [key for key in required_set if key not in target and key not in optional]
        if not missing:
            return
    raise AssertionError(f"{command_key}: missing keys {missing} in payload {target!r}")


def _select_payload(command_key: str, payload: Mapping[str, object]) -> Mapping[str, object]:
    if command_key == "system_get_trouble":
        nested = payload.get("get_troubles")
        if isinstance(nested, Mapping):
            return nested
    return payload


def expected_keys(command_key: str) -> Iterable[str]:
    return _REQUIRED_KEYS.get(command_key, ())
