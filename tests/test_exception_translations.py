# ruff: noqa: S101
"""Exception translation keys match strings.json and are used consistently."""

from __future__ import annotations

import ast
import json
from pathlib import Path
import re

import pytest

from custom_components.elke27 import errors

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "elke27"

HELPER_KEY_PATTERN = re.compile(
    r"(?:homeassistant_error|service_validation_error)\(\s*[\"']([a-z_0-9]+)[\"']"
)
SETUP_EXCEPTION_PATTERN = re.compile(
    r"raise\s+(?:ConfigEntryNotReady|ConfigEntryAuthFailed|UpdateFailed|HomeAssistantError|ConfigEntryError)\("
    r"[^)]*?translation_key\s*=\s*[\"']([a-z_0-9]+)[\"']",
    re.DOTALL,
)


def _exception_keys_from_strings(path: Path) -> set[str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return set(data["exceptions"])


def _keys_referenced_in_code() -> set[str]:
    keys: set[str] = set()
    for path in COMPONENT.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        keys.update(HELPER_KEY_PATTERN.findall(text))
        keys.update(SETUP_EXCEPTION_PATTERN.findall(text))
    arm_path = COMPONENT / "arm_automatic_errors.py"
    if arm_path.exists():
        keys.update(re.findall(r'key = "([a-z_0-9]+)"', arm_path.read_text()))
    keys.update({"not_connected", "not_accepted"})
    keys.update(
        {
            "code_rejected",
            "code_not_numeric",
            "code_all_zero",
            "command_timeout",
            "command_connection_lost",
            "panel_error",
            "pin_required_arm",
            "pin_required_bypass",
            "pin_required_control",
            "pin_required_disarm",
            "pin_required_fallback",
            "pin_required_lock",
            "pin_required_unlock",
            "arm_mode_not_supported",
        }
    )
    return keys


def test_exception_keys_exist_in_strings_and_en() -> None:
    """Code translation keys exist in strings.json and en.json."""
    strings_keys = _exception_keys_from_strings(COMPONENT / "strings.json")
    en_keys = _exception_keys_from_strings(COMPONENT / "translations/en.json")
    used = _keys_referenced_in_code()
    missing_strings = used - strings_keys
    missing_en = used - en_keys
    assert not missing_strings, f"missing from strings.json: {sorted(missing_strings)}"
    assert not missing_en, f"missing from en.json: {sorted(missing_en)}"
    assert strings_keys == en_keys


def test_no_untranslated_ha_raises_in_custom_components() -> None:
    """Direct HA or service validation raises include translation_key."""
    for path in (ROOT / "custom_components").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Raise) or node.exc is None:
                continue
            call = node.exc
            if not isinstance(call, ast.Call):
                continue
            func = call.func
            name = None
            if isinstance(func, ast.Name):
                name = func.id
            elif isinstance(func, ast.Attribute):
                name = func.attr
            if name not in {"HomeAssistantError", "ServiceValidationError"}:
                continue
            keyword_keys = {
                kw.arg for kw in call.keywords if kw.arg == "translation_key"
            }
            assert keyword_keys, (
                f"{path}:{node.lineno} raises {name} without translation_key"
            )


@pytest.mark.parametrize(
    ("helper", "key", "message"),
    [
        (
            "homeassistant_error",
            "not_connected",
            "The panel isn't connected. The command wasn't sent.",
        ),
        (
            "homeassistant_error",
            "not_accepted",
            "The panel didn't accept the command.",
        ),
        (
            "service_validation_error",
            "code_rejected",
            "The panel rejected your user code.",
        ),
    ],
)
def test_error_helpers_set_translation_metadata(
    helper: str, key: str, message: str
) -> None:
    """Factory helpers attach domain, key, and optional placeholders."""
    factory = getattr(errors, helper)
    err = factory(key, message)
    assert err.translation_domain == "elke27"
    assert err.translation_key == key
