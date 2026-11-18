# ruff: noqa: S101
"""The elke27 pin in the manifest matches the test requirements and the install."""

from __future__ import annotations

from importlib.metadata import version as package_version
import json
from pathlib import Path
import re

from packaging.requirements import Requirement

_REPO_ROOT = Path(__file__).resolve().parents[1]
_MANIFEST = _REPO_ROOT / "custom_components" / "elke27" / "manifest.json"
_REQUIREMENTS_TEST = _REPO_ROOT / "requirements_test.txt"
_PIN = re.compile(r"^elke27==(?P<version>\d+(?:\.\d+)*)$")


def _manifest_pin() -> str:
    requirements = json.loads(_MANIFEST.read_text(encoding="utf-8"))["requirements"]
    pins = [r for r in requirements if Requirement(r).name == "elke27"]
    assert len(pins) == 1, pins
    return pins[0]


def _requirements_test_pin() -> str:
    lines = _REQUIREMENTS_TEST.read_text(encoding="utf-8").splitlines()
    pins = [
        line.strip()
        for line in lines
        if line.strip()
        and not line.lstrip().startswith(("#", "-"))
        and Requirement(line.strip()).name == "elke27"
    ]
    assert len(pins) == 1, pins
    return pins[0]


def test_manifest_pins_a_released_elke27_version() -> None:
    """The manifest pins elke27 with ==, not a git, URL or range requirement."""
    pin = _manifest_pin()
    requirement = Requirement(pin)
    assert requirement.url is None
    assert _PIN.fullmatch(pin), pin


def test_manifest_pin_matches_requirements_test() -> None:
    """requirements_test.txt pins the same elke27 requirement as the manifest."""
    assert _requirements_test_pin() == _manifest_pin()


def test_manifest_pin_matches_installed_elke27() -> None:
    """The tests run against the elke27 version the manifest pins."""
    match = _PIN.fullmatch(_manifest_pin())
    assert match is not None
    assert package_version("elke27") == match["version"]
