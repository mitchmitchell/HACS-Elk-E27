"""Permission levels for the E27 Dealer API.

These values are not bitmask flags. The *_DISARMED variants are a panel-state
gate layered on top of the base permission level.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from elke27_lib.errors import Elke27ProtocolError


class PermissionLevel(Enum):
    """Permission levels from the firmware, not a bitmask.

    *_DISARMED is a panel-state gate that requires all areas disarmed.

    Exact equivalent of PERMISSION_LEVEL_TYPE in the E27 firmware.
    Order and numeric values matter.
    """

    PLT_ENCRYPTION_KEY = 1
    PLT_ANY_USER = 2
    PLT_MASTER_USER = 3
    PLT_INSTALLER_USER = 4
    PLT_ENCRYPTION_KEY_DISARMED = 5
    PLT_ANY_USER_DISARMED = 6
    PLT_MASTER_USER_DISARMED = 7
    PLT_INSTALLER_USER_DISARMED = 8


_DISARMED_TO_BASE = {
    PermissionLevel.PLT_ENCRYPTION_KEY_DISARMED: PermissionLevel.PLT_ENCRYPTION_KEY,
    PermissionLevel.PLT_ANY_USER_DISARMED: PermissionLevel.PLT_ANY_USER,
    PermissionLevel.PLT_MASTER_USER_DISARMED: PermissionLevel.PLT_MASTER_USER,
    PermissionLevel.PLT_INSTALLER_USER_DISARMED: PermissionLevel.PLT_INSTALLER_USER,
}

AREA_GENERATORS = (
    ("area_get_table_info", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("area_get_attribs", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("area_set_attribs", PermissionLevel.PLT_INSTALLER_USER_DISARMED),
    ("area_get_def_flags", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("area_del_attribs", PermissionLevel.PLT_INSTALLER_USER_DISARMED),
    ("area_get_configured", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("area_get_bypassed_zones", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("area_set_alarm_state", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("area_get_status", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("area_set_status", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("area_set_arm_state", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("area_alarm_test", PermissionLevel.PLT_MASTER_USER_DISARMED),
    ("area_realloc", PermissionLevel.PLT_INSTALLER_USER_DISARMED),
    ("area_num_not_rdy_zones", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("area_get_unsecure_zones", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("area_get_troubles", PermissionLevel.PLT_ENCRYPTION_KEY),
)

BARRIER_GENERATORS = (
    ("barrier_get_table_info", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("barrier_get_attribs", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("barrier_set_attribs", PermissionLevel.PLT_INSTALLER_USER_DISARMED),
    ("barrier_get_configured", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("barrier_get_status", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("barrier_set_status", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("barrier_del_attribs", PermissionLevel.PLT_INSTALLER_USER_DISARMED),
)

BUS_IOS_GENERATORS = (
    ("bus_ios_get_table_info", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("bus_ios_get_configured", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("bus_ios_get_attribs", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("bus_ios_set_attribs", PermissionLevel.PLT_INSTALLER_USER_DISARMED),
    ("bus_ios_del_attribs", PermissionLevel.PLT_INSTALLER_USER_DISARMED),
    ("bus_ios_change_id", PermissionLevel.PLT_INSTALLER_USER_DISARMED),
    ("bus_ios_get_version_info", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("bus_ios_get_trouble", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("bus_ios_realloc", PermissionLevel.PLT_INSTALLER_USER_DISARMED),
    ("bus_ios_start_discovery", PermissionLevel.PLT_INSTALLER_USER_DISARMED),
    ("bus_ios_stop_discovery", PermissionLevel.PLT_INSTALLER_USER_DISARMED),
    ("bus_ios_get_error_counts", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("bus_ios_get_dev_error_counts", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("bus_ios_set_sn", PermissionLevel.PLT_INSTALLER_USER_DISARMED),
    ("bus_ios_fill_default", PermissionLevel.PLT_INSTALLER_USER_DISARMED),
)

CELL_GENERATORS = (
    ("cell_get_attribs", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("cell_set_attribs", PermissionLevel.PLT_INSTALLER_USER_DISARMED),
    ("cell_default", PermissionLevel.PLT_INSTALLER_USER_DISARMED),
    ("cell_get_table_info", PermissionLevel.PLT_ENCRYPTION_KEY),
)

LOG_GENERATORS = (
    ("log_get_table_info", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("log_get_log", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("log_get_list", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("log_clear", PermissionLevel.PLT_INSTALLER_USER_DISARMED),
    ("log_get_trouble", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("log_get_attribs", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("log_set_attribs", PermissionLevel.PLT_INSTALLER_USER_DISARMED),
    ("log_realloc", PermissionLevel.PLT_INSTALLER_USER_DISARMED),
    ("log_get_index", PermissionLevel.PLT_ENCRYPTION_KEY),
)

CONTROL_GENERATORS = (
    ("control_authenticate", PermissionLevel.PLT_ANY_USER),
    ("control_get_version_info", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("control_get_ssp_version_info", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("control_get_table_info", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("control_get_table_alloc_info", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("control_default", PermissionLevel.PLT_INSTALLER_USER_DISARMED),
    ("control_get_trouble", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("control_silence_trouble", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("control_get_system_time", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("control_set_system_time", PermissionLevel.PLT_MASTER_USER),
    ("control_get_factory_attribs", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("control_set_factory_attribs", PermissionLevel.PLT_INSTALLER_USER),
    ("control_test_realloc", PermissionLevel.PLT_INSTALLER_USER),
    ("control_backup", PermissionLevel.PLT_INSTALLER_USER),
    ("control_restore", PermissionLevel.PLT_INSTALLER_USER_DISARMED),
    ("control_load_default", PermissionLevel.PLT_INSTALLER_USER_DISARMED),
)

SYSTEM_GENERATORS = (
    ("system_get_table_info", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("system_get_system_time", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("system_set_system_time", PermissionLevel.PLT_MASTER_USER_DISARMED),
    ("system_set_system_key", PermissionLevel.PLT_INSTALLER_USER_DISARMED),
    ("system_get_attribs", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("system_set_attribs", PermissionLevel.PLT_INSTALLER_USER_DISARMED),
    ("system_get_cutoffs", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("system_set_cutoffs", PermissionLevel.PLT_INSTALLER_USER_DISARMED),
    ("system_get_sounders", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("system_reset_smokes", PermissionLevel.PLT_ENCRYPTION_KEY_DISARMED),
    ("system_get_trouble", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("system_get_troubles", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("system_r_u_alive", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("system_file_info", PermissionLevel.PLT_INSTALLER_USER),
    ("system_start_updt", PermissionLevel.PLT_INSTALLER_USER_DISARMED),
    ("system_set_run", PermissionLevel.PLT_INSTALLER_USER_DISARMED),
    ("system_reconfig", PermissionLevel.PLT_INSTALLER_USER_DISARMED),
    ("system_set_debug_flags", PermissionLevel.PLT_INSTALLER_USER),
    ("system_get_debug_flags", PermissionLevel.PLT_INSTALLER_USER),
    ("system_get_debug_string", PermissionLevel.PLT_INSTALLER_USER),
    ("template_get_configured", PermissionLevel.PLT_INSTALLER_USER),
    ("system_get_update", PermissionLevel.PLT_ENCRYPTION_KEY),
)

CS_PARAM_GENERATORS = (
    ("cs_param_get_attribs", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("cs_param_set_attribs", PermissionLevel.PLT_INSTALLER_USER_DISARMED),
    ("cs_param_del_attribs", PermissionLevel.PLT_INSTALLER_USER_DISARMED),
    ("cs_param_get_def_flags", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("cs_param_get_configured", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("cs_param_get_cs_paths", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("cs_param_set_cs_paths", PermissionLevel.PLT_INSTALLER_USER_DISARMED),
    ("cs_param_get_trouble", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("cs_param_get_table_info", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("cs_param_commtest", PermissionLevel.PLT_INSTALLER_USER_DISARMED),
)

FLAG_GENERATORS = (
    ("flag_get_table_info", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("flag_get_attribs", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("flag_set_attribs", PermissionLevel.PLT_MASTER_USER_DISARMED),
    ("flag_del_attribs", PermissionLevel.PLT_MASTER_USER_DISARMED),
    ("flag_get_configured", PermissionLevel.PLT_ENCRYPTION_KEY),
)

KEYFOB_GENERATORS = (
    ("keyfob_get_table_info", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("keyfob_get_attribs", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("keyfob_set_attribs", PermissionLevel.PLT_INSTALLER_USER_DISARMED),
    ("keyfob_del_attribs", PermissionLevel.PLT_INSTALLER_USER_DISARMED),
    ("keyfob_change_id", PermissionLevel.PLT_INSTALLER_USER_DISARMED),
    ("keyfob_get_configured", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("keyfob_default", PermissionLevel.PLT_INSTALLER_USER_DISARMED),
    ("keyfob_get_trouble", PermissionLevel.PLT_ENCRYPTION_KEY),
)

KEYPAD_GENERATORS = (
    ("keypad_get_table_info", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("keypad_get_attribs", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("keypad_set_attribs", PermissionLevel.PLT_INSTALLER_USER_DISARMED),
    ("keypad_del_attribs", PermissionLevel.PLT_INSTALLER_USER_DISARMED),
    ("keypad_change_id", PermissionLevel.PLT_INSTALLER_USER_DISARMED),
    ("keypad_get_configured", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("keypad_get_version_info", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("keypad_get_def_flags", PermissionLevel.PLT_ENCRYPTION_KEY),
)

LIGHT_GENERATORS = (
    ("light_get_table_info", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("light_get_attribs", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("light_set_attribs", PermissionLevel.PLT_INSTALLER_USER_DISARMED),
    ("light_get_configured", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("light_get_status", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("light_set_status", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("light_del_attribs", PermissionLevel.PLT_INSTALLER_USER_DISARMED),
)

LOCK_GENERATORS = (
    ("lock_get_table_info", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("lock_get_attribs", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("lock_set_attribs", PermissionLevel.PLT_INSTALLER_USER_DISARMED),
    ("lock_get_configured", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("lock_get_status", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("lock_set_status", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("lock_del_attribs", PermissionLevel.PLT_INSTALLER_USER_DISARMED),
    ("lock_get_user_code", PermissionLevel.PLT_MASTER_USER_DISARMED),
    ("lock_set_user_code", PermissionLevel.PLT_MASTER_USER_DISARMED),
)

NETWORK_PARAM_GENERATORS = (
    ("network_get_table_info", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("network_param_get_ssid", PermissionLevel.PLT_INSTALLER_USER),
    ("network_param_get_rssi", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("network_param_get_attribs", PermissionLevel.PLT_INSTALLER_USER),
    ("network_param_set_attribs", PermissionLevel.PLT_INSTALLER_USER),
    ("network_restart", PermissionLevel.PLT_INSTALLER_USER),
)

NET_DEV_GENERATORS = (
    ("link_function", PermissionLevel.PLT_INSTALLER_USER_DISARMED),
    ("logout_function", PermissionLevel.PLT_ANY_USER),
    ("net_dev_get_attribs", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("net_dev_set_attribs", PermissionLevel.PLT_INSTALLER_USER_DISARMED),
    ("net_dev_get_creds", PermissionLevel.PLT_INSTALLER_USER),
    ("net_dev_set_creds", PermissionLevel.PLT_INSTALLER_USER_DISARMED),
    ("net_dev_del_attribs", PermissionLevel.PLT_INSTALLER_USER_DISARMED),
    ("net_dev_default", PermissionLevel.PLT_INSTALLER_USER_DISARMED),
    ("net_dev_get_list", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("net_dev_get_table_info", PermissionLevel.PLT_ENCRYPTION_KEY),
)

OUTPUT_GENERATORS = (
    ("output_get_table_info", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("output_get_attribs", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("output_set_attribs", PermissionLevel.PLT_INSTALLER_USER_DISARMED),
    ("output_del_attribs", PermissionLevel.PLT_INSTALLER_USER_DISARMED),
    ("output_get_configured", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("output_get_status", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("output_set_status", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("output_refresh_status", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("output_realloc", PermissionLevel.PLT_INSTALLER_USER_DISARMED),
    ("output_get_available", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("output_get_all_outputs_status", PermissionLevel.PLT_ENCRYPTION_KEY),
)

REGISTER_GENERATORS = (
    ("register_get_table_info", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("register_get_attribs", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("register_set_attribs", PermissionLevel.PLT_MASTER_USER_DISARMED),
    ("register_get_configured", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("register_del_attribs", PermissionLevel.PLT_MASTER_USER_DISARMED),
)

RULE_GENERATORS = (
    ("rule_get_table_info", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("rule_get_storage_size", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("rule_get_rules", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("rule_set_rules", PermissionLevel.PLT_INSTALLER_USER_DISARMED),
    ("rule_realloc", PermissionLevel.PLT_INSTALLER_USER_DISARMED),
    ("rule_process", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("rule_get_trouble", PermissionLevel.PLT_ENCRYPTION_KEY),
)

TASK_GENERATORS = (
    ("task_get_table_info", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("task_get_attribs", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("task_set_attribs", PermissionLevel.PLT_INSTALLER_USER_DISARMED),
    ("task_del_attribs", PermissionLevel.PLT_INSTALLER_USER_DISARMED),
    ("task_get_configured", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("task_activate", PermissionLevel.PLT_ENCRYPTION_KEY),
)

TIMER_GENERATORS = (
    ("timer_get_table_info", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("timer_get_attribs", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("timer_set_attribs", PermissionLevel.PLT_MASTER_USER_DISARMED),
    ("timer_get_configured", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("timer_del_attribs", PermissionLevel.PLT_MASTER_USER_DISARMED),
)

TSTAT_GENERATORS = (
    ("tstat_get_table_info", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("tstat_get_attribs", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("tstat_set_attribs", PermissionLevel.PLT_INSTALLER_USER_DISARMED),
    ("tstat_get_configured", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("tstat_get_status", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("tstat_set_status", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("tstat_del_attribs", PermissionLevel.PLT_INSTALLER_USER_DISARMED),
    ("tstat_realloc", PermissionLevel.PLT_INSTALLER_USER_DISARMED),
)

USER_VALUE_GENERATORS = (
    ("user_value_get_table_info", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("user_value_get_attribs", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("user_value_set_attribs", PermissionLevel.PLT_MASTER_USER_DISARMED),
    ("user_value_get_configured", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("user_value_del_attribs", PermissionLevel.PLT_INSTALLER_USER_DISARMED),
    ("user_value_realloc", PermissionLevel.PLT_INSTALLER_USER_DISARMED),
)

USERGROUP_GENERATORS = (
    ("usergroup_get_table_info", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("usergroup_get_attribs", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("usergroup_set_attribs", PermissionLevel.PLT_MASTER_USER_DISARMED),
    ("usergroup_get_configured", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("usergroup_del_attribs", PermissionLevel.PLT_MASTER_USER_DISARMED),
)

USER_GENERATORS = (
    ("user_get_table_info", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("user_get_def_flags", PermissionLevel.PLT_MASTER_USER_DISARMED),
    ("user_get_attribs", PermissionLevel.PLT_ANY_USER),
    ("user_set_attribs", PermissionLevel.PLT_MASTER_USER_DISARMED),
    ("user_del_attribs", PermissionLevel.PLT_MASTER_USER_DISARMED),
    ("user_get_configured", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("user_realloc", PermissionLevel.PLT_INSTALLER_USER_DISARMED),
)

WIEGAND_GENERATORS = (
    ("wiegand_get_table_info", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("wiegand_get_attribs", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("wiegand_set_attribs", PermissionLevel.PLT_MASTER_USER_DISARMED),
    ("wiegand_del_attribs", PermissionLevel.PLT_MASTER_USER_DISARMED),
    ("wiegand_get_configured", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("wiegand_realloc", PermissionLevel.PLT_INSTALLER_USER_DISARMED),
)

WIN_COVER_GENERATORS = (
    ("win_cover_get_table_info", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("win_cover_get_attribs", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("win_cover_set_attribs", PermissionLevel.PLT_INSTALLER_USER_DISARMED),
    ("win_cover_get_configured", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("win_cover_get_status", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("win_cover_set_status", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("win_cover_del_attribs", PermissionLevel.PLT_INSTALLER_USER_DISARMED),
    ("win_cover_realloc", PermissionLevel.PLT_INSTALLER_USER_DISARMED),
)

WLTX_GENERATORS = (
    ("wltx_get_table_info", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("wltx_get_version_info", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("wltx_get_attribs", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("wltx_set_attribs", PermissionLevel.PLT_INSTALLER_USER_DISARMED),
    ("wltx_del_attribs", PermissionLevel.PLT_INSTALLER_USER_DISARMED),
    ("wltx_change_id", PermissionLevel.PLT_INSTALLER_USER_DISARMED),
    ("wltx_get_configured", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("wltx_get_trouble", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("wltx_realloc", PermissionLevel.PLT_INSTALLER_USER_DISARMED),
    ("wltx_start_discovery", PermissionLevel.PLT_INSTALLER_USER_DISARMED),
    ("wltx_stop_discovery", PermissionLevel.PLT_INSTALLER_USER_DISARMED),
    ("wltx_default", PermissionLevel.PLT_INSTALLER_USER_DISARMED),
)

ZONE_GENERATORS = (
    ("zone_get_table_info", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("zone_get_defs", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("zone_get_def_flags", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("zone_get_attribs", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("zone_set_attribs", PermissionLevel.PLT_INSTALLER_USER_DISARMED),
    ("zone_get_configured", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("zone_get_status", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("zone_set_status", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("zone_del_attribs", PermissionLevel.PLT_INSTALLER_USER_DISARMED),
    ("zone_pwr_get_trouble", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("zone_realloc", PermissionLevel.PLT_INSTALLER_USER_DISARMED),
    ("zone_default", PermissionLevel.PLT_INSTALLER_USER_DISARMED),
    ("zone_fill_default", PermissionLevel.PLT_INSTALLER_USER_DISARMED),
    ("zone_get_available", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("zone_get_all_zones_status", PermissionLevel.PLT_ENCRYPTION_KEY),
)

REPEATER_GENERATORS = (
    ("repeater_get_table_info", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("repeater_get_attribs", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("repeater_set_attribs", PermissionLevel.PLT_INSTALLER_USER_DISARMED),
    ("repeater_get_configured", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("repeater_del_attribs", PermissionLevel.PLT_INSTALLER_USER_DISARMED),
    ("repeater_realloc", PermissionLevel.PLT_INSTALLER_USER_DISARMED),
)

ZWAVE_GENERATORS = (
    ("zwave_get_table_info", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("zwave_inclusion", PermissionLevel.PLT_INSTALLER_USER_DISARMED),
    ("zwave_exclusion", PermissionLevel.PLT_INSTALLER_USER_DISARMED),
    ("zwave_rem_failed", PermissionLevel.PLT_INSTALLER_USER_DISARMED),
    ("zwave_abort", PermissionLevel.PLT_INSTALLER_USER_DISARMED),
    ("zwave_restart", PermissionLevel.PLT_MASTER_USER_DISARMED),
    ("zwave_default", PermissionLevel.PLT_INSTALLER_USER_DISARMED),
    ("zwave_clear", PermissionLevel.PLT_INSTALLER_USER_DISARMED),
    ("zwave_get_attribs", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("zwave_set_attribs", PermissionLevel.PLT_INSTALLER_USER_DISARMED),
    ("zwave_get_configured", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("zwave_get_trouble", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("zwave_set_assoc", PermissionLevel.PLT_INSTALLER_USER_DISARMED),
    ("zwave_assign_return_route", PermissionLevel.PLT_INSTALLER_USER_DISARMED),
    ("zwave_heal_node", PermissionLevel.PLT_MASTER_USER_DISARMED),
    ("zwave_get_battery", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("zwave_set_scene", PermissionLevel.PLT_ANY_USER),
    ("zwave_set_config", PermissionLevel.PLT_INSTALLER_USER_DISARMED),
    ("zwave_get_rssi", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("zwave_get_mfr", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("zwave_get_route_info", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("zwave_get_node_info", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("zwave_get_secure_node_info", PermissionLevel.PLT_ENCRYPTION_KEY),
    ("zwave_get_supported_sensor", PermissionLevel.PLT_ENCRYPTION_KEY),
)


_GENERATOR_GROUPS = (
    AREA_GENERATORS,
    BARRIER_GENERATORS,
    BUS_IOS_GENERATORS,
    CELL_GENERATORS,
    LOG_GENERATORS,
    CONTROL_GENERATORS,
    SYSTEM_GENERATORS,
    CS_PARAM_GENERATORS,
    FLAG_GENERATORS,
    KEYFOB_GENERATORS,
    KEYPAD_GENERATORS,
    LIGHT_GENERATORS,
    LOCK_GENERATORS,
    NETWORK_PARAM_GENERATORS,
    NET_DEV_GENERATORS,
    OUTPUT_GENERATORS,
    REGISTER_GENERATORS,
    RULE_GENERATORS,
    TASK_GENERATORS,
    TIMER_GENERATORS,
    TSTAT_GENERATORS,
    USER_VALUE_GENERATORS,
    USERGROUP_GENERATORS,
    USER_GENERATORS,
    WIEGAND_GENERATORS,
    WIN_COVER_GENERATORS,
    WLTX_GENERATORS,
    ZONE_GENERATORS,
    REPEATER_GENERATORS,
    ZWAVE_GENERATORS,
)


def _build_generator_permission() -> dict[str, PermissionLevel]:
    permissions: dict[str, PermissionLevel] = {}
    for group in _GENERATOR_GROUPS:
        for generator_key, level in group:
            permissions[generator_key] = level
    return permissions


GENERATOR_PERMISSION = _build_generator_permission()
ALL_PERMISSION_KEYS = set(GENERATOR_PERMISSION.keys())


def canonical_generator_key(name: str) -> str:
    if name.startswith("generator_"):
        return name[len("generator_") :]
    return name


def permission_for_generator(generator_key: str) -> PermissionLevel:
    generator_key = canonical_generator_key(generator_key)
    try:
        return GENERATOR_PERMISSION[generator_key]
    except KeyError as exc:
        raise Elke27ProtocolError(
            f"No permission metadata for generator {generator_key!r}."
        ) from exc


def requires_pin(level: PermissionLevel) -> bool:
    """Return True when a PIN is required for this permission level."""

    return level not in {
        PermissionLevel.PLT_ENCRYPTION_KEY,
        PermissionLevel.PLT_ENCRYPTION_KEY_DISARMED,
    }


def requires_disarmed(level: PermissionLevel) -> bool:
    """Return True when *_DISARMED requires the panel to be disarmed."""

    return level.name.endswith("_DISARMED")


def strip_disarmed(level: PermissionLevel) -> PermissionLevel:
    """Return the base permission level for a *_DISARMED variant."""

    return _DISARMED_TO_BASE.get(level, level)


def required_role(
    level: PermissionLevel,
) -> Literal["encryption_key", "any_user", "master", "installer"]:
    """Return the minimum role required for the permission level."""

    base = strip_disarmed(level)
    if base is PermissionLevel.PLT_ENCRYPTION_KEY:
        return "encryption_key"
    if base is PermissionLevel.PLT_ANY_USER:
        return "any_user"
    if base is PermissionLevel.PLT_MASTER_USER:
        return "master"
    if base is PermissionLevel.PLT_INSTALLER_USER:
        return "installer"
    raise ValueError(f"Unknown permission level: {level}")
