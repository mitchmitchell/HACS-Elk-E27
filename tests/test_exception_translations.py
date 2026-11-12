# ruff: noqa: S101
"""Exception translation keys match strings.json and are used consistently."""

from __future__ import annotations

import ast
import dataclasses
import json
from pathlib import Path
import re
from types import MappingProxyType

from elke27_lib import AreaState, PanelSnapshot, ZoneState
import pytest

from custom_components.elke27 import errors
from custom_components.elke27.arm_automatic_errors import (
    ARM_NOT_SENT_REASON,
    arm_automatic_not_armed_error,
)

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "elke27"
STRINGS_PATH = COMPONENT / "strings.json"

PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")
ARM_KEY_BASE_RE = re.compile(r'_arm_key\(\s*"([^"]+)"')
MODULE_CONSTANT_KEYS = {
    "errors.NOT_CONNECTED_TRANSLATION_KEY": errors.NOT_CONNECTED_TRANSLATION_KEY,
    "errors.NOT_ACCEPTED_TRANSLATION_KEY": errors.NOT_ACCEPTED_TRANSLATION_KEY,
}
COMMAND_ERROR_KEYS = frozenset(
    {
        "code_rejected",
        "not_ready",
        "not_bypassable",
        "in_alarm",
        "not_allowed_armed",
        "command_timeout",
        "command_connection_lost",
        "panel_error",
    }
)
PIN_ACTION_KEYS = frozenset(
    {
        "pin_required_arm",
        "pin_required_disarm",
        "pin_required_bypass",
        "pin_required_unlock",
        "pin_required_lock",
        "pin_required_control",
    }
)
ENTITY_INVALID_KEYS = frozenset({"entity_invalid_area", "entity_invalid_zone"})
TRANSLATED_EXCEPTION_CLASSES = frozenset(
    {
        "HomeAssistantError",
        "ServiceValidationError",
        "ConfigEntryNotReady",
        "ConfigEntryAuthFailed",
        "ConfigEntryError",
        "UpdateFailed",
    }
)
HELPER_NAMES = frozenset({"homeassistant_error", "service_validation_error"})


def _exception_keys_from_strings(path: Path) -> set[str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return set(data["exceptions"])


def _placeholders_in_message(message: str) -> set[str]:
    return set(PLACEHOLDER_RE.findall(message))


def _string_from_ast(node: ast.expr | None, scope: dict[str, str]) -> str | None:
    if node is None:
        return None
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return scope.get(node.id)
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        qual = f"{node.value.id}.{node.attr}"
        if qual in MODULE_CONSTANT_KEYS:
            return MODULE_CONSTANT_KEYS[qual]
        if node.value.id == "errors" and hasattr(errors, node.attr):
            value = getattr(errors, node.attr)
            if isinstance(value, str):
                return value
    if isinstance(node, ast.IfExp):
        left = _string_from_ast(node.body, scope)
        right = _string_from_ast(node.orelse, scope)
        if left == right:
            return left
    return None


def _assignment_scope(func: ast.AST) -> dict[str, str]:
    scope: dict[str, str] = {}
    for node in ast.walk(func):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        literal = _string_from_ast(node.value, scope)
        if literal is not None:
            scope[target.id] = literal
    return scope


def _call_name(call: ast.Call) -> str | None:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _dict_keys_from_ast(node: ast.Dict, scope: dict[str, str]) -> dict[str, str] | None:
    out: dict[str, str] = {}
    for key_node, val_node in zip(node.keys, node.values, strict=True):
        key = _string_from_ast(key_node, scope)
        if key is None:
            return None
        val = _string_from_ast(val_node, scope)
        if val is None and not isinstance(val_node, (ast.Call, ast.Name)):
            return None
        out[key] = val or ""
    return out


def _collect_helper_calls(path: Path) -> list[tuple[int, str, dict[str, str] | None]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    sites: list[tuple[int, str, dict[str, str] | None]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        scope = _assignment_scope(node)
        for inner in ast.walk(node):
            if not isinstance(inner, ast.Call):
                continue
            name = _call_name(inner)
            if name not in HELPER_NAMES:
                continue
            if not inner.args:
                continue
            key = _string_from_ast(inner.args[0], scope)
            if key is None:
                continue
            placeholders = None
            for kw in inner.keywords:
                if kw.arg == "translation_placeholders" and isinstance(
                    kw.value, ast.Dict
                ):
                    placeholders = _dict_keys_from_ast(kw.value, scope)
            sites.append((inner.lineno, key, placeholders))
    return sites


def _arm_automatic_bases() -> set[str]:
    text = (COMPONENT / "arm_automatic_errors.py").read_text(encoding="utf-8")
    bases = set(ARM_KEY_BASE_RE.findall(text))
    bases.add("arm_automatic_multi_failed")
    bases.add("arm_automatic_not_armed_bypass_unconfirmed")
    bases.add("arm_automatic_not_armed_bypass_unconfirmed_still_bypassed")
    return bases


def _arm_automatic_catalog_keys() -> set[str]:
    return {
        k
        for k in _exception_keys_from_strings(STRINGS_PATH)
        if k.startswith("arm_automatic")
    }


def _arm_base_uses_zone_variants(base: str) -> bool:
    if "bypass_unconfirmed" in base:
        return True
    return base.startswith("arm_automatic_not_armed_zone")


def _expected_arm_automatic_keys() -> set[str]:
    keys: set[str] = set()
    for base in _arm_automatic_bases():
        if base == "arm_automatic_multi_failed":
            keys.add(base)
            continue
        if _arm_base_uses_zone_variants(base):
            for area in ("named", "unnamed"):
                for zone in ("named", "unnamed"):
                    keys.add(f"{base}_{area}_zone_{zone}")
        else:
            keys.add(f"{base}_named")
            keys.add(f"{base}_unnamed")
    return keys


def _setup_translation_keys_from_raises(path: Path) -> set[str]:
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(path))
    scope = _assignment_scope(tree)
    keys: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Raise) or node.exc is None:
            continue
        call = node.exc
        if not isinstance(call, ast.Call):
            continue
        if _call_name(call) not in TRANSLATED_EXCEPTION_CLASSES:
            continue
        for kw in call.keywords:
            if kw.arg != "translation_key":
                continue
            key = _string_from_ast(kw.value, scope)
            if key is not None:
                keys.add(key)
    return keys


def _keys_referenced_in_code() -> set[str]:
    keys: set[str] = set()
    for path in COMPONENT.rglob("*.py"):
        for _line, key, _ph in _collect_helper_calls(path):
            keys.add(key)
    keys.update(COMMAND_ERROR_KEYS)
    keys.update(PIN_ACTION_KEYS)
    keys.add("pin_required_fallback")
    keys.update(ENTITY_INVALID_KEYS)
    keys.add("arm_mode_not_supported")
    keys.update(_expected_arm_automatic_keys())
    for path in COMPONENT.rglob("*.py"):
        keys.update(_setup_translation_keys_from_raises(path))
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


def test_arm_automatic_catalog_matches_code_bases() -> None:
    """Every arm_automatic strings key is generated from an _arm_key base in code."""
    catalog = _arm_automatic_catalog_keys()
    expected = _expected_arm_automatic_keys()
    orphan = catalog - expected
    assert not orphan, f"orphan arm_automatic keys in catalog: {sorted(orphan)}"
    missing = expected - catalog
    assert not missing, f"arm_automatic keys missing from catalog: {sorted(missing)}"


def test_arm_automatic_dynamic_placeholders_match_catalog() -> None:
    """arm_automatic_not_armed_error placeholders match strings for exercised paths."""
    catalog = json.loads(STRINGS_PATH.read_text(encoding="utf-8"))["exceptions"]
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
    zone = snap.zones[2]
    scenarios = [
        arm_automatic_not_armed_error(
            1,
            snap,
            entity_name="House",
            reason=ARM_NOT_SENT_REASON,
            zone=None,
            rolled_back_zones=[snap.zones[1]],
            still_bypassed_zones=[snap.zones[2]],
            pin_required=False,
        ),
        arm_automatic_not_armed_error(
            1,
            snap,
            entity_name="House",
            reason="bypass was not acknowledged.",
            zone=zone,
            rolled_back_zones=[],
            still_bypassed_zones=[zone],
            pin_required=False,
        ),
        arm_automatic_not_armed_error(
            1,
            snap,
            entity_name="House",
            reason="ignored",
            zone=None,
            rolled_back_zones=[],
            still_bypassed_zones=[snap.zones[1]],
            pin_required=True,
        ),
    ]
    mismatches: list[str] = []
    for err in scenarios:
        key = err.translation_key
        ph = err.translation_placeholders or {}
        expected = _placeholders_in_message(catalog[key]["message"])
        if expected != set(ph):
            mismatches.append(f"{key} catalog={sorted(expected)} code={sorted(ph)}")
    assert not mismatches, "\n".join(mismatches)


def test_translation_placeholders_match_catalog() -> None:
    """Placeholder names in JSON match translation_placeholders at static call sites."""
    catalog = json.loads(STRINGS_PATH.read_text(encoding="utf-8"))["exceptions"]
    mismatches: list[str] = []
    for path in COMPONENT.rglob("*.py"):
        for line, key, placeholders in _collect_helper_calls(path):
            if key not in catalog or placeholders is None:
                continue
            expected = _placeholders_in_message(catalog[key]["message"])
            got = set(placeholders)
            if expected != got:
                mismatches.append(
                    f"{path}:{line} key={key} catalog={sorted(expected)} "
                    f"code={sorted(got)}"
                )
    assert not mismatches, "\n".join(mismatches)


def test_no_untranslated_raises_in_custom_components() -> None:
    """Direct translated exception raises include translation_key."""
    for path in (ROOT / "custom_components").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Raise) or node.exc is None:
                continue
            call = node.exc
            if not isinstance(call, ast.Call):
                continue
            name = _call_name(call)
            if name not in TRANSLATED_EXCEPTION_CLASSES:
                continue
            keyword_keys = {
                kw.arg for kw in call.keywords if kw.arg == "translation_key"
            }
            assert keyword_keys, (
                f"{path}:{node.lineno} raises {name} without translation_key"
            )


@pytest.mark.parametrize(
    ("factory", "key"),
    [
        (errors.homeassistant_error, "not_connected"),
        (errors.homeassistant_error, "not_accepted"),
        (errors.service_validation_error, "code_rejected"),
    ],
)
def test_error_helpers_set_translation_metadata(factory: object, key: str) -> None:
    """Factory helpers attach domain and key."""
    err = factory(key)  # type: ignore[operator]
    assert err.translation_domain == "elke27"
    assert err.translation_key == key
