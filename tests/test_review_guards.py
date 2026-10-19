# ruff: noqa: S101
"""
Guards for review findings that kept coming back.

Each test here turns a finding that Architect or Claude raised on several PRs
into a check, so CI catches it before review does:

- tests reaching into private members (ruff SLF001 is on; these tests stop new
  ``noqa`` escapes and private access ruff can't see);
- helpers imported from another test module instead of ``conftest.py``;
- strict xfails without ``raises=`` or a linked issue;
- docstrings that promise retry behaviour without a test pinning it;
- ``getattr``/``hasattr`` with a literal name instead of a typed member;
- new ``noqa`` escapes for broad ``except`` (BLE001).

Legacy code is listed explicitly. The lists may only shrink: when you fix one,
lower or remove its entry (the equality checks fail until you do).
"""

from __future__ import annotations

import ast
import configparser
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "custom_components" / "elke27"
TESTS = ROOT / "tests"
THIS = Path(__file__).resolve()


def _files(base: Path) -> list[Path]:
    return sorted(base.rglob("*.py"))


def _rel(path: Path) -> str:
    return path.resolve().relative_to(ROOT).as_posix()


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _is_private(name: str) -> bool:
    return name.startswith("_") and not (name.startswith("__") and name.endswith("__"))


def _literal_name(call: ast.Call) -> str | None:
    if len(call.args) >= 2:  # noqa: PLR2004
        arg = call.args[1]
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            return arg.value
    return None


def _test_modules() -> list[Path]:
    return [p for p in _files(TESTS) if p.resolve() != THIS]


# --- noqa escapes for SLF001 / BLE001 ------------------------------------------

_NOQA = re.compile(r"#\s*(ruff:\s*)?noqa:([^#]*)")
WATCHED = ("SLF001", "BLE001")

# "<file>:<code>" -> lines that silence it. The key ends in ":file" for a
# file-level ruff noqa header. Codes may be comma- or space-separated, as ruff
# accepts both. New tests use the public API and never add to this.
NOQA_CEILING: dict[str, int] = {
    "custom_components/elke27/coordinator.py:BLE001": 4,
    "custom_components/elke27/panel_mac.py:BLE001": 1,
    "tests/test_arming.py:SLF001": 1,
    "tests/test_availability.py:SLF001:file": 1,
    "tests/test_command_errors.py:SLF001:file": 1,
    "tests/test_commands.py:SLF001": 6,
    "tests/test_config_flow.py:SLF001:file": 1,
    "tests/test_coordinator.py:SLF001:file": 1,
    "tests/test_coordinator_client.py:SLF001:file": 1,
    "tests/test_coordinator_d3.py:SLF001:file": 1,
    "tests/test_init.py:SLF001:file": 1,
    "tests/test_services.py:SLF001": 18,
}


def _noqa_counts() -> dict[str, int]:
    counts: dict[str, int] = {}
    for path in [*_files(SOURCE), *_test_modules()]:
        for line in path.read_text(encoding="utf-8").splitlines():
            for match in _NOQA.finditer(line):
                codes = set(re.split(r"[\s,]+", match.group(2).strip()))
                for code in WATCHED:
                    if code in codes:
                        key = f"{_rel(path)}:{code}" + (
                            ":file" if match.group(1) else ""
                        )
                        counts[key] = counts.get(key, 0) + 1
    return counts


def test_no_new_noqa_for_private_access_or_broad_except() -> None:
    """SLF001/BLE001 escapes only shrink (repeat review finding)."""
    assert _noqa_counts() == NOQA_CEILING, (
        "Drive tests through public calls instead of silencing SLF001, and catch "
        "specific exceptions instead of silencing BLE001. If you removed an "
        "escape, lower NOQA_CEILING."
    )


def test_tests_reach_no_private_library_members() -> None:
    """Private access ruff SLF001 cannot see: imports and getattr("_x")."""
    counts: dict[str, int] = {}
    for path in _test_modules():
        for node in ast.walk(_tree(path)):
            if (
                isinstance(node, ast.ImportFrom)
                and (node.module or "").startswith(("elke27_lib", "custom_components"))
                and any(_is_private(alias.name) for alias in node.names)
            ) or (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in {"getattr", "setattr", "hasattr", "delattr"}
                and _is_private(_literal_name(node) or "")
            ):
                counts[_rel(path)] = counts.get(_rel(path), 0) + 1
    assert counts == LEGACY_PRIVATE_IMPORTS


# Existing private imports/reflection per test file. May only shrink.
LEGACY_PRIVATE_IMPORTS: dict[str, int] = {
    "tests/test_area_devices.py": 1,
    "tests/test_arming.py": 1,
    "tests/test_coordinator_client.py": 1,
    "tests/test_entities.py": 1,
    "tests/test_init.py": 1,
}


# --- helpers imported from other test modules -------------------------------------


# (importer, imported test module). Move these to conftest.py and drop the entry.
LEGACY_TEST_MODULE_IMPORTS = {
    ("tests/test_services.py", "test_commands"),
    ("tests/test_services.py", "test_entities"),
}


def test_no_helper_imports_between_test_modules() -> None:
    """Shared fakes and fixtures live in conftest.py, not in another test module."""
    found = {
        (_rel(path), node.module)
        for path in _test_modules()
        for node in ast.walk(_tree(path))
        if isinstance(node, ast.ImportFrom)
        and node.module
        and node.module.split(".")[-1].startswith("test_")
    }
    assert found - LEGACY_TEST_MODULE_IMPORTS == set(), (
        "Move shared helpers to conftest.py"
    )
    assert LEGACY_TEST_MODULE_IMPORTS - found == set(), (
        "Drop fixed LEGACY_TEST_MODULE_IMPORTS"
    )


# --- xfail pairing ---------------------------------------------------------------

_ISSUE = re.compile(r"#\d+|/issues/\d+")


def _xfail_problems(path: Path) -> list[str]:
    tree = _tree(path)
    called = {id(n.func): n for n in ast.walk(tree) if isinstance(n, ast.Call)}
    problems: list[str] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Attribute) and node.attr == "xfail"):
            continue
        where = f"{_rel(path)}:{node.lineno}"
        if isinstance(node.value, ast.Name) and node.value.id == "pytest":
            problems.append(f"{where}: imperative pytest.xfail(); use a marker")
            continue
        call = called.get(id(node))
        if call is None:
            problems.append(f"{where}: bare xfail; add raises= and reason='#<issue>'")
            continue
        keywords = {k.arg: k.value for k in call.keywords if k.arg}
        reason = keywords.get("reason")
        strict = keywords.get("strict")
        if "raises" not in keywords:
            problems.append(f"{where}: xfail without raises=")
        if strict is not None and not (
            isinstance(strict, ast.Constant) and strict.value is True
        ):
            problems.append(f"{where}: non-strict xfail")
        if not (
            isinstance(reason, ast.Constant)
            and isinstance(reason.value, str)
            and _ISSUE.search(reason.value)
        ):
            problems.append(f"{where}: xfail reason must link the open issue (#<n>)")
    return problems


def test_xfails_are_strict_and_paired_with_an_issue() -> None:
    """xfail_strict is on; every xfail names the exception and the issue it tracks."""
    config = configparser.ConfigParser()
    config.read(ROOT / "pytest.ini")
    assert config.getboolean("pytest", "xfail_strict", fallback=False)
    problems = [p for path in _test_modules() for p in _xfail_problems(path)]
    assert problems == []


# --- docstring retry claims --------------------------------------------------------

_CLAIM = re.compile(
    r"\b(resen[dt]|resend(?:s|ing)?|retr(?:y|ies|ied|ying))\b", re.IGNORECASE
)

# Public docstring that talks about retry/resend -> the behaviour tests that pin it,
# or "n/a: <why>" when the sentence is advice, not a behaviour claim.
DOCSTRING_CLAIMS: dict[str, tuple[str, ...] | str] = {
    "custom_components/elke27/coordinator.py::Elke27DataUpdateCoordinator"
    ".async_bypass_faulted_zones": (
        "tests/test_commands.py::test_partial_failure_names_zones_already_bypassed",
    ),
    "custom_components/elke27/coordinator.py::Elke27DataUpdateCoordinator"
    ".async_rollback_bypasses": (
        "tests/test_commands.py::test_rollback_failures_are_not_retried",
    ),
    "custom_components/elke27/errors.py::is_reconnect_retryable": (
        "tests/test_reconnect.py::test_transport_failures_never_raise_issue",
        "tests/test_reconnect.py::test_non_transport_issue_on_third_failure",
    ),
}


def _claims(path: Path) -> set[str]:
    found: set[str] = set()

    def visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                qualname = f"{prefix}{child.name}"
                doc = ast.get_docstring(child)
                if doc and not child.name.startswith("_") and _CLAIM.search(doc):
                    found.add(f"{_rel(path)}::{qualname}")
                visit(child, f"{qualname}.")

    visit(_tree(path), "")
    return found


def _test_exists(node_id: str) -> bool:
    file, _, name = node_id.partition("::")
    path = ROOT / file
    return path.exists() and any(
        isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef) and n.name == name
        for n in ast.walk(_tree(path))
    )


def test_retry_docstrings_are_pinned_by_tests() -> None:
    """A public docstring promising (no) retry has a behaviour test behind it."""
    found = {claim for path in _files(SOURCE) for claim in _claims(path)}
    assert found - DOCSTRING_CLAIMS.keys() == set(), (
        "New retry/resend wording in a public docstring: add a behaviour test and "
        "register it in DOCSTRING_CLAIMS (or 'n/a: <why>')."
    )
    assert DOCSTRING_CLAIMS.keys() - found == set(), (
        "Drop stale DOCSTRING_CLAIMS entries"
    )
    missing = [
        node_id
        for tests in DOCSTRING_CLAIMS.values()
        if not isinstance(tests, str)
        for node_id in tests
        if not _test_exists(node_id)
    ]
    assert missing == []


# --- getattr/hasattr with a literal name -------------------------------------------

# Literal-name getattr/hasattr calls per file (ruff B009 only covers the no-default
# form). Prefer typed library members; if one is truly needed, raise the count in the
# same PR so review sees it. Lower the count when you remove one.
GETATTR_LITERAL_CEILING: dict[str, int] = {
    "custom_components/elke27/arming.py": 1,
    "custom_components/elke27/config_flow.py": 26,
    "custom_components/elke27/entity.py": 1,
    "custom_components/elke27/errors.py": 1,
    "custom_components/elke27/panel_mac.py": 2,
}


def test_getattr_literal_names_do_not_grow() -> None:
    """Typed members instead of getattr/hasattr probes (repeat review finding)."""
    counts = {
        _rel(path): n
        for path in _files(SOURCE)
        if (
            n := sum(
                1
                for node in ast.walk(_tree(path))
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in {"getattr", "hasattr"}
                and _literal_name(node) is not None
            )
        )
    }
    assert counts == GETATTR_LITERAL_CEILING
