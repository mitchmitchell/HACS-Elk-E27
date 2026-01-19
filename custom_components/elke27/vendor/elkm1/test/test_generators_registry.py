import pytest

from elke27_lib.generators.registry import COMMANDS
from elke27_lib.permissions import (
    ALL_PERMISSION_KEYS,
    GENERATOR_PERMISSION,
    PermissionLevel,
    canonical_generator_key,
)


EXPECTED_PERMISSIONS = {
    "area_get_table_info": PermissionLevel.PLT_ENCRYPTION_KEY,
    "area_get_configured": PermissionLevel.PLT_ENCRYPTION_KEY,
    "area_get_attribs": PermissionLevel.PLT_ENCRYPTION_KEY,
    "area_get_status": PermissionLevel.PLT_ENCRYPTION_KEY,
    "area_set_status": PermissionLevel.PLT_ENCRYPTION_KEY,
    "area_set_arm_state": PermissionLevel.PLT_ENCRYPTION_KEY,
    "zone_get_table_info": PermissionLevel.PLT_ENCRYPTION_KEY,
    "zone_get_configured": PermissionLevel.PLT_ENCRYPTION_KEY,
    "zone_get_attribs": PermissionLevel.PLT_ENCRYPTION_KEY,
    "zone_get_status": PermissionLevel.PLT_ENCRYPTION_KEY,
    "zone_get_all_zones_status": PermissionLevel.PLT_ENCRYPTION_KEY,
    "zone_set_status": PermissionLevel.PLT_ENCRYPTION_KEY,
    "output_get_table_info": PermissionLevel.PLT_ENCRYPTION_KEY,
    "output_get_configured": PermissionLevel.PLT_ENCRYPTION_KEY,
    "output_get_attribs": PermissionLevel.PLT_ENCRYPTION_KEY,
    "output_get_status": PermissionLevel.PLT_ENCRYPTION_KEY,
    "output_set_status": PermissionLevel.PLT_ENCRYPTION_KEY,
    "output_get_all_outputs_status": PermissionLevel.PLT_ENCRYPTION_KEY,
    "system_get_trouble": PermissionLevel.PLT_ENCRYPTION_KEY,
    "rule_get_rules": PermissionLevel.PLT_ENCRYPTION_KEY,
    "user_get_configured": PermissionLevel.PLT_ENCRYPTION_KEY,
    "user_get_attribs": PermissionLevel.PLT_ANY_USER,
    "keypad_get_configured": PermissionLevel.PLT_ENCRYPTION_KEY,
    "keypad_get_attribs": PermissionLevel.PLT_ENCRYPTION_KEY,
    "control_get_version_info": PermissionLevel.PLT_ENCRYPTION_KEY,
    "control_get_table_info": PermissionLevel.PLT_ENCRYPTION_KEY,
}


EXPECTED_CALLS = {
    "area_get_table_info": {},
    "area_get_configured": {"block_id": 1},
    "area_get_attribs": {"area_id": 1},
    "area_get_status": {"area_id": 1},
    "area_set_status": {"area_id": 1, "chime": True},
    "area_set_arm_state": {"area_id": 1, "arm_state": "ARMED_AWAY", "pin": 1234},
    "zone_get_table_info": {},
    "zone_get_configured": {"block_id": 1},
    "zone_get_attribs": {"zone_id": 1},
    "zone_get_status": {"zone_id": 1},
    "zone_get_all_zones_status": {},
    "zone_set_status": {"zone_id": 1, "pin": 1234, "bypassed": True},
    "output_get_table_info": {},
    "output_get_configured": {"block_id": 1},
    "output_get_attribs": {"output_id": 1},
    "output_get_status": {"output_id": 1},
    "output_set_status": {"output_id": 1, "status": "ON"},
    "output_get_all_outputs_status": {"block_id": 1},
    "system_get_trouble": {},
    "rule_get_rules": {"block_id": 0},
    "user_get_configured": {"block_id": 1},
    "user_get_attribs": {"user_id": 1},
    "keypad_get_configured": {"block_id": 1},
    "keypad_get_attribs": {"keypad_id": 1},
    "control_get_version_info": {},
    "control_get_table_info": {},
}


def test_registry_contains_all_permission_keys():
    assert set(COMMANDS.keys()) == set(ALL_PERMISSION_KEYS)


def test_registry_min_permissions_match_permission_table():
    for key, expected in EXPECTED_PERMISSIONS.items():
        assert COMMANDS[key].min_permission is expected
    for key, spec in COMMANDS.items():
        assert spec.min_permission is GENERATOR_PERMISSION[key]


def test_registry_generators_and_handlers_are_callable():
    for key, spec in COMMANDS.items():
        assert callable(spec.generator)
        assert callable(spec.handler)


def test_registry_callable_names_follow_convention():
    for key, spec in COMMANDS.items():
        assert spec.generator.__name__ == f"generator_{key}"
        assert spec.handler.__name__ == f"handler_{key}"


def test_canonical_generator_key_strips_prefix():
    assert canonical_generator_key("generator_area_get_status") == "area_get_status"
    assert canonical_generator_key("area_get_status") == "area_get_status"


def test_generators_return_payload_and_response_key():
    for key, call_kwargs in EXPECTED_CALLS.items():
        spec = COMMANDS[key]
        payload, response_key = spec.generator(**call_kwargs)
        assert isinstance(payload, dict)
        assert isinstance(response_key, tuple)
        assert response_key == (spec.domain, spec.command)


def test_generators_are_pure():
    for key in EXPECTED_CALLS:
        generator = COMMANDS[key].generator
        globals_keys = generator.__globals__.keys()
        assert "elk" not in globals_keys
        assert "session" not in globals_keys
        assert "Elke27Client" not in globals_keys


def test_stubbed_commands_raise_not_implemented():
    stub_key = "log_get_log"
    spec = COMMANDS[stub_key]

    with pytest.raises(NotImplementedError, match=stub_key):
        spec.generator()

    with pytest.raises(NotImplementedError, match=stub_key):
        spec.handler({}, None)
