import pytest

from elke27_lib.permissions import (
    PermissionLevel,
    required_role,
    requires_disarmed,
    requires_pin,
    strip_disarmed,
)


@pytest.mark.parametrize(
    ("level", "expected"),
    [
        (PermissionLevel.PLT_ENCRYPTION_KEY, False),
        (PermissionLevel.PLT_ANY_USER, False),
        (PermissionLevel.PLT_MASTER_USER, False),
        (PermissionLevel.PLT_INSTALLER_USER, False),
        (PermissionLevel.PLT_ENCRYPTION_KEY_DISARMED, True),
        (PermissionLevel.PLT_ANY_USER_DISARMED, True),
        (PermissionLevel.PLT_MASTER_USER_DISARMED, True),
        (PermissionLevel.PLT_INSTALLER_USER_DISARMED, True),
    ],
)
def test_requires_disarmed(level, expected):
    assert requires_disarmed(level) is expected


@pytest.mark.parametrize(
    ("level", "expected"),
    [
        (PermissionLevel.PLT_ENCRYPTION_KEY, False),
        (PermissionLevel.PLT_ANY_USER, True),
        (PermissionLevel.PLT_MASTER_USER, True),
        (PermissionLevel.PLT_INSTALLER_USER, True),
        (PermissionLevel.PLT_ENCRYPTION_KEY_DISARMED, False),
        (PermissionLevel.PLT_ANY_USER_DISARMED, True),
        (PermissionLevel.PLT_MASTER_USER_DISARMED, True),
        (PermissionLevel.PLT_INSTALLER_USER_DISARMED, True),
    ],
)
def test_requires_pin(level, expected):
    assert requires_pin(level) is expected


@pytest.mark.parametrize(
    ("level", "expected"),
    [
        (PermissionLevel.PLT_ENCRYPTION_KEY, "encryption_key"),
        (PermissionLevel.PLT_ANY_USER, "any_user"),
        (PermissionLevel.PLT_MASTER_USER, "master"),
        (PermissionLevel.PLT_INSTALLER_USER, "installer"),
        (PermissionLevel.PLT_ENCRYPTION_KEY_DISARMED, "encryption_key"),
        (PermissionLevel.PLT_ANY_USER_DISARMED, "any_user"),
        (PermissionLevel.PLT_MASTER_USER_DISARMED, "master"),
        (PermissionLevel.PLT_INSTALLER_USER_DISARMED, "installer"),
    ],
)
def test_required_role(level, expected):
    assert required_role(level) == expected


@pytest.mark.parametrize(
    ("level", "expected"),
    [
        (PermissionLevel.PLT_ENCRYPTION_KEY, PermissionLevel.PLT_ENCRYPTION_KEY),
        (PermissionLevel.PLT_ANY_USER, PermissionLevel.PLT_ANY_USER),
        (PermissionLevel.PLT_MASTER_USER, PermissionLevel.PLT_MASTER_USER),
        (PermissionLevel.PLT_INSTALLER_USER, PermissionLevel.PLT_INSTALLER_USER),
        (
            PermissionLevel.PLT_ENCRYPTION_KEY_DISARMED,
            PermissionLevel.PLT_ENCRYPTION_KEY,
        ),
        (PermissionLevel.PLT_ANY_USER_DISARMED, PermissionLevel.PLT_ANY_USER),
        (PermissionLevel.PLT_MASTER_USER_DISARMED, PermissionLevel.PLT_MASTER_USER),
        (
            PermissionLevel.PLT_INSTALLER_USER_DISARMED,
            PermissionLevel.PLT_INSTALLER_USER,
        ),
    ],
)
def test_strip_disarmed(level, expected):
    assert strip_disarmed(level) is expected
