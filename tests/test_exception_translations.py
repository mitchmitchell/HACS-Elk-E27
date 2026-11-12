# ruff: noqa: S101
"""Exception translation keys match strings.json and are used consistently."""

from __future__ import annotations

import ast
from collections import defaultdict
import dataclasses
import json
from pathlib import Path
import re
from types import MappingProxyType
from typing import TYPE_CHECKING

from elke27_lib import AreaState, PanelSnapshot, ZoneState
import pytest

from custom_components.elke27 import errors
from custom_components.elke27.arm_automatic_errors import (
    ARM_NOT_SENT_REASON,
    arm_automatic_cancelled_error,
    arm_automatic_multi_failed_error,
    arm_automatic_not_armed_error,
    arm_automatic_uncertain_error,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

if TYPE_CHECKING:
    from collections.abc import Callable

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "elke27"
STRINGS_PATH = COMPONENT / "strings.json"

PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")
ARM_KEY_BASE_RE = re.compile(r'_arm_key\(\s*"([^"]+)"')
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
LEGACY_MIGRATION_EXCEPTION_KEYS = frozenset(
    {
        "legacy_migration_blocked",
        "legacy_migration_partial",
        "legacy_migration_record_malformed",
        "legacy_migration_write_failed",
    }
)
STATIC_EXCEPTION_KEYS = frozenset(
    {
        "pin_required_fallback",
        "arm_mode_not_supported",
        "code_not_numeric",
        "code_all_zero",
        "not_connected",
        "not_accepted",
    }
)


def _exception_keys_from_strings(path: Path) -> set[str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return set(data["exceptions"])


def _placeholders_in_message(message: str) -> set[str]:
    return set(PLACEHOLDER_RE.findall(message))


def _call_name(call: ast.Call) -> str | None:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _literal_translation_key(node: ast.expr | None) -> str | None:
    if node is None:
        return None
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "errors"
        and hasattr(errors, node.attr)
    ):
        value = getattr(errors, node.attr)
        if isinstance(value, str):
            return value
    return None


ALLOWED_KEY_NAMES = frozenset(
    {"NOT_CONNECTED_TRANSLATION_KEY", "NOT_ACCEPTED_TRANSLATION_KEY"}
)


def _allowed_translation_key_arg(node: ast.expr | None) -> bool:
    if _literal_translation_key(node) is not None:
        return True
    if isinstance(node, ast.Name) and node.id in ALLOWED_KEY_NAMES:
        return True
    if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name):
        return node.value.id == "_ENTITY_INVALID_KEY"
    return False


def _translation_key_keyword(call: ast.Call) -> ast.expr | None:
    for kw in call.keywords:
        if kw.arg == "translation_key":
            return kw.value
    return None


def _placeholder_keys_from_dict(node: ast.expr | None) -> set[str] | None:
    if not isinstance(node, ast.Dict):
        return None
    keys: set[str] = set()
    for key_node in node.keys:
        if not isinstance(key_node, ast.Constant) or not isinstance(
            key_node.value, str
        ):
            return None
        keys.add(key_node.value)
    return keys


def _collect_literal_helper_calls(
    path: Path,
) -> list[tuple[int, str, set[str] | None]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    sites: list[tuple[int, str, set[str] | None]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node)
        if name not in HELPER_NAMES:
            continue
        key_arg = _translation_key_keyword(node)
        if key_arg is None:
            continue
        if isinstance(key_arg, ast.Subscript):
            sites.extend(
                (node.lineno, entity_key, set()) for entity_key in ENTITY_INVALID_KEYS
            )
            continue
        if isinstance(key_arg, ast.Name) and key_arg.id in ALLOWED_KEY_NAMES:
            key = getattr(errors, key_arg.id)
        else:
            key = _literal_translation_key(key_arg)
        if key is None:
            continue
        placeholder_keys: set[str] | None = None
        for kw in node.keywords:
            if kw.arg == "translation_placeholders":
                placeholder_keys = _placeholder_keys_from_dict(kw.value)
        sites.append((node.lineno, key, placeholder_keys))
    return sites


def _collect_translated_exception_sites(
    path: Path,
) -> list[tuple[int, str, set[str] | None]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    sites: list[tuple[int, str, set[str] | None]] = []
    for node in ast.walk(tree):
        call: ast.Call | None = None
        if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call):
            call = node.exc
        elif isinstance(node, ast.Return) and isinstance(node.value, ast.Call):
            call = node.value
        if call is None or _call_name(call) not in TRANSLATED_EXCEPTION_CLASSES:
            continue
        key: str | None = None
        placeholder_keys: set[str] | None = set()
        for kw in call.keywords:
            if kw.arg == "translation_key":
                key = _literal_translation_key(kw.value)
            if kw.arg == "translation_placeholders":
                placeholder_keys = _placeholder_keys_from_dict(kw.value)
        if key is None:
            continue
        sites.append((node.lineno, key, placeholder_keys))
    return sites


def _collect_non_literal_helper_violations(path: Path) -> list[str]:
    if path.name in {"arm_automatic_errors.py", "errors.py"}:
        return []
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if _call_name(node) not in HELPER_NAMES:
            continue
        if node.args:
            violations.append(f"{path}:{node.lineno} positional helper arguments")
            continue
        key_arg = _translation_key_keyword(node)
        if key_arg is None:
            violations.append(f"{path}:{node.lineno} missing translation_key")
            continue
        if _allowed_translation_key_arg(key_arg):
            continue
        violations.append(f"{path}:{node.lineno} non-literal translation key")
    return violations


def _arm_automatic_bases() -> set[str]:
    text = (COMPONENT / "arm_automatic_errors.py").read_text(encoding="utf-8")
    bases = set(ARM_KEY_BASE_RE.findall(text))
    bases.add("arm_automatic_multi_failed")
    bases.add("arm_automatic_not_armed_bypass_unconfirmed")
    bases.add("arm_automatic_not_armed_bypass_unconfirmed_still_bypassed")
    bases.add("arm_automatic_not_armed_bypass_unconfirmed_partial_rollback")
    bases.add("arm_automatic_not_armed_pin_required_still_bypassed_partial_rollback")
    return bases


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


def _keys_referenced_in_code() -> set[str]:
    keys: set[str] = set()
    for path in COMPONENT.rglob("*.py"):
        for _line, key, _ph in _collect_literal_helper_calls(path):
            keys.add(key)
    keys.update(COMMAND_ERROR_KEYS)
    keys.update(PIN_ACTION_KEYS)
    keys.update(STATIC_EXCEPTION_KEYS)
    keys.update(ENTITY_INVALID_KEYS)
    keys.update(LEGACY_MIGRATION_EXCEPTION_KEYS)
    keys.update(_expected_arm_automatic_keys())
    for path in COMPONENT.rglob("*.py"):
        for _line, key, _ph in _collect_translated_exception_sites(path):
            keys.add(key)
    return keys


def _arm_automatic_snap(*, named_area: bool) -> PanelSnapshot:
    area = AreaState(
        area_id=1,
        name="House" if named_area else "",
        ready=True,
    )
    return dataclasses.replace(
        PanelSnapshot.empty(),
        areas=MappingProxyType({1: area}),
        zones=MappingProxyType(
            {
                1: ZoneState(zone_id=1, name="Front", area_id=1, open=False),
                2: ZoneState(zone_id=2, area_id=1, open=True),
                3: ZoneState(zone_id=3, name="Back", area_id=1, open=True),
            }
        ),
    )


def arm_automatic_translation_samples() -> list[
    HomeAssistantError | ServiceValidationError
]:
    """Return one translated error per distinct arm_automatic catalog branch."""
    samples: list[HomeAssistantError | ServiceValidationError] = []

    for named_area in (True, False):
        snap = _arm_automatic_snap(named_area=named_area)
        z1, z2, z3 = snap.zones[1], snap.zones[2], snap.zones[3]
        entity_name = "House" if named_area else None

        for case in (
            (ARM_NOT_SENT_REASON, None, [z1], [z2], False),
            (ARM_NOT_SENT_REASON, None, [], [z2], False),
            (ARM_NOT_SENT_REASON, None, [z1], [], False),
            (ARM_NOT_SENT_REASON, None, [], [], False),
            ("nope", None, [z1], [z2], False),
            ("nope", None, [], [z2], False),
            ("nope", None, [z1], [], False),
            ("nope", None, [], [], False),
            ("bypass was not acknowledged.", z3, [z1], [z2], False),
            ("bypass was not acknowledged.", z3, [], [z2], False),
            ("bypass was not acknowledged.", z3, [], [], False),
            ("bypass was not acknowledged.", z2, [z1], [z2], False),
            ("bypass was not acknowledged.", z2, [], [z2], False),
            ("bypass was not acknowledged.", z2, [], [], False),
            ("zone", z3, [z1], [z2], False),
            ("zone", z2, [z1], [z2], False),
            ("zone", z2, [], [z2], False),
            ("zone", z3, [z1], [], False),
            ("zone", z2, [], [], False),
            ("zone", z3, [], [], False),
            ("zone", z2, [z1], [], False),
            ("zone", z2, [], [z2], False),
            ("zone", z3, [], [z2], False),
            ("pin", None, [], [z2], True),
            ("pin", None, [z1], [z2], True),
            ("pin", None, [], [], True),
        ):
            reason, zone, rolled_back, still, pin_required = case
            samples.append(
                arm_automatic_not_armed_error(
                    1,
                    snap,
                    entity_name=entity_name,
                    reason=reason,
                    zone=zone,
                    rolled_back_zones=rolled_back,
                    still_bypassed_zones=still,
                    pin_required=pin_required,
                )
            )

        samples.append(
            arm_automatic_uncertain_error(
                1, snap, entity_name=entity_name, bypassed_zones=[]
            )
        )
        samples.append(
            arm_automatic_uncertain_error(
                1, snap, entity_name=entity_name, bypassed_zones=[z2]
            )
        )
        samples.append(
            arm_automatic_cancelled_error(
                1, snap, entity_name=entity_name, bypassed_zones=[], arm_sent=False
            )
        )
        samples.append(
            arm_automatic_cancelled_error(
                1, snap, entity_name=entity_name, bypassed_zones=[z2], arm_sent=False
            )
        )
        samples.append(
            arm_automatic_cancelled_error(
                1, snap, entity_name=entity_name, bypassed_zones=[z2], arm_sent=True
            )
        )

    samples.append(
        arm_automatic_multi_failed_error(
            ["alarm_control_panel.a", "alarm_control_panel.b"],
            [
                HomeAssistantError(
                    translation_domain="elke27",
                    translation_key="not_ready",
                ),
                HomeAssistantError("other"),
            ],
            2,
        )
    )
    return samples


def _placeholder_observations() -> dict[str, set[frozenset[str]]]:
    catalog = json.loads(STRINGS_PATH.read_text(encoding="utf-8"))["exceptions"]
    by_key: dict[str, set[frozenset[str]]] = defaultdict(set)

    def record(key: str, fields: set[str] | None) -> None:
        if key not in catalog:
            return
        by_key[key].add(frozenset(fields or set()))

    for path in COMPONENT.rglob("*.py"):
        for _line, key, fields in _collect_literal_helper_calls(path):
            if fields is not None:
                record(key, fields)
            else:
                record(key, set())

    for path in (ROOT / "custom_components").rglob("*.py"):
        for _line, key, fields in _collect_translated_exception_sites(path):
            record(key, fields if fields is not None else set())

    for err in arm_automatic_translation_samples():
        record(err.translation_key, set(err.translation_placeholders or {}))

    for key in (
        COMMAND_ERROR_KEYS
        | PIN_ACTION_KEYS
        | STATIC_EXCEPTION_KEYS
        | ENTITY_INVALID_KEYS
        | LEGACY_MIGRATION_EXCEPTION_KEYS
    ):
        record(key, set())

    return by_key


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
    catalog = {
        k
        for k in _exception_keys_from_strings(STRINGS_PATH)
        if k.startswith("arm_automatic")
    }
    expected = _expected_arm_automatic_keys()
    orphan = catalog - expected
    assert not orphan, f"orphan arm_automatic keys in catalog: {sorted(orphan)}"
    missing = expected - catalog
    assert not missing, f"arm_automatic keys missing from catalog: {sorted(missing)}"


def test_helper_calls_use_literal_translation_keys() -> None:
    """Translation helper calls pass literal keys (no msg indirection)."""
    violations: list[str] = []
    for path in COMPONENT.rglob("*.py"):
        violations.extend(_collect_non_literal_helper_violations(path))
    assert not violations, "\n".join(violations)


def test_translated_exceptions_include_translation_key() -> None:
    """Raises and returns of translated HA exceptions declare translation_key."""
    for path in (ROOT / "custom_components").rglob("*.py"):
        if path.name == "errors.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            call: ast.Call | None = None
            if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call):
                call = node.exc
            elif isinstance(node, ast.Return) and isinstance(node.value, ast.Call):
                call = node.value
            if call is None or _call_name(call) not in TRANSLATED_EXCEPTION_CLASSES:
                continue
            has_key = any(kw.arg == "translation_key" for kw in call.keywords)
            assert has_key, (
                f"{path}:{node.lineno} {_call_name(call)} without translation_key"
            )


def test_exception_catalog_placeholder_parity() -> None:
    """Every exception key's placeholders match every code path that emits it."""
    catalog = json.loads(STRINGS_PATH.read_text(encoding="utf-8"))["exceptions"]
    by_key = _placeholder_observations()
    mismatches: list[str] = []
    unobserved = _keys_referenced_in_code() - set(by_key)
    if unobserved:
        mismatches.append(
            f"no code path recorded placeholders for: {sorted(unobserved)}"
        )
    for key, observed_sets in sorted(by_key.items()):
        expected = _placeholders_in_message(catalog[key]["message"])
        mismatches.extend(
            f"key={key} catalog={sorted(expected)} code={sorted(observed)}"
            for observed in observed_sets
            if set(observed) != expected
        )
    assert not mismatches, "\n".join(mismatches)


@pytest.mark.parametrize(
    ("factory", "key"),
    [
        (errors.homeassistant_error, "not_connected"),
        (errors.homeassistant_error, "not_accepted"),
        (errors.service_validation_error, "code_rejected"),
    ],
)
def test_error_helpers_set_translation_metadata(
    factory: Callable[..., HomeAssistantError | ServiceValidationError], key: str
) -> None:
    """Factory helpers attach domain and key."""
    err = factory(translation_key=key)
    assert err.translation_domain == "elke27"
    assert err.translation_key == key
