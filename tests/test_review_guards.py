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
import datetime as dt
from pathlib import Path
import re
import tokenize

import yaml

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

_NOQA = re.compile(r"#\s*(ruff:\s*)?noqa\b(?::([^#]*))?", re.IGNORECASE)
WATCHED = ("SLF001", "BLE001")

# "<file>:<code>" -> comments that silence it. The key ends in ":file" for a
# file-level ruff noqa header. Only real comments count (tokenized, so noqa text in
# strings and docstrings is ignored). Matching is case-insensitive, codes may be
# comma- or space-separated, and a bare noqa (no codes, line- or file-level)
# counts for every watched code. New tests use the public API and never add to
# this.
NOQA_CEILING: dict[str, int] = {
    "custom_components/elke27/coordinator.py:BLE001": 4,
    "custom_components/elke27/panel_mac.py:BLE001": 1,
    "tests/test_arming.py:SLF001": 1,
    "tests/test_availability.py:SLF001:file": 1,
    "tests/test_command_errors.py:SLF001:file": 1,
    "tests/test_commands.py:SLF001": 1,
    "tests/test_config_flow.py:SLF001:file": 1,
    "tests/test_coordinator.py:SLF001:file": 1,
    "tests/test_coordinator_client.py:SLF001:file": 1,
    "tests/test_coordinator_d3.py:SLF001:file": 1,
    "tests/test_init.py:SLF001:file": 1,
    "tests/test_services.py:SLF001": 18,
}


def _comments(path: Path) -> list[str]:
    with path.open("rb") as handle:
        return [
            tok.string
            for tok in tokenize.tokenize(handle.readline)
            if tok.type == tokenize.COMMENT
        ]


def _noqa_counts() -> dict[str, int]:
    counts: dict[str, int] = {}
    for path in [*_files(SOURCE), *_test_modules()]:
        for comment in _comments(path):
            for match in _NOQA.finditer(comment):
                listed = (match.group(2) or "").strip()
                codes = set(re.split(r"[\s,]+", listed.upper()))
                for code in WATCHED:
                    if not listed or code in codes:
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


# Private imports/reflection per test file, counted per private name. May only
# shrink: lower or drop an entry when you remove one.
LEGACY_PRIVATE_IMPORTS: dict[str, int] = {
    "tests/test_area_devices.py": 1,
    "tests/test_arming.py": 1,
    "tests/test_coordinator_client.py": 1,
    "tests/test_entities.py": 1,
    "tests/test_init.py": 1,
}


def test_tests_reach_no_private_library_members() -> None:
    """Private access ruff SLF001 cannot see: imports and getattr("_x")."""
    counts: dict[str, int] = {}
    for path in _test_modules():
        for node in ast.walk(_tree(path)):
            hits = 0
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
                ("elke27_lib", "custom_components")
            ):
                hits = sum(1 for alias in node.names if _is_private(alias.name))
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in {"getattr", "setattr", "hasattr", "delattr"}
                and _is_private(_literal_name(node) or "")
            ):
                hits = 1
            if hits:
                counts[_rel(path)] = counts.get(_rel(path), 0) + hits
    assert counts == LEGACY_PRIVATE_IMPORTS, (
        "Use the public API instead of private imports/reflection. If you removed "
        "one, lower LEGACY_PRIVATE_IMPORTS."
    )


# --- helpers imported from other test modules -------------------------------------


# (importer, imported test module). Move these to conftest.py and drop the entry.
LEGACY_TEST_MODULE_IMPORTS = {
    ("tests/test_services.py", "test_commands"),
    ("tests/test_services.py", "test_entities"),
}


_TEST_PACKAGES = {"", "tests"}


def _imported_test_modules(path: Path) -> set[str]:
    """
    Return every test module imported, in any import form.

    Covers import tests.test_x, import test_x, from tests.test_x import y,
    from tests import test_x and from . import test_x.
    """
    found: set[str] = set()
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.Import):
            found |= {
                a.name for a in node.names if a.name.split(".")[-1].startswith("test_")
            }
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module.split(".")[-1].startswith("test_"):
                found.add(module)
            elif node.level or module in _TEST_PACKAGES:
                prefix = f"{module}." if module else "." * node.level
                found |= {
                    f"{prefix}{a.name}"
                    for a in node.names
                    if a.name.startswith("test_")
                }
    return found


def test_no_helper_imports_between_test_modules() -> None:
    """Shared fakes and fixtures live in conftest.py, not in another test module."""
    found = {
        (_rel(path), module)
        for path in _test_modules()
        for module in _imported_test_modules(path)
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
    "custom_components/elke27/migration.py::blocking_entities": (
        "tests/test_legacy_migration.py::test_blocked_flow_rechecks_targets_at_submit",
    ),
    "custom_components/elke27/repairs.py::RetryFlow": (
        "tests/test_legacy_migration.py::test_retry_flows_reload",
    ),
    "custom_components/elke27/repairs.py::BlockedUpgradeFlow": (
        "tests/test_legacy_migration.py::test_blocked_flow_deletes_nothing_until_confirm_submitted",
    ),
    "custom_components/elke27/repairs.py::BlockedUpgradeFlow.async_step_init": (
        "tests/test_legacy_migration.py::test_blocked_flow_deletes_nothing_until_confirm_submitted",
    ),
    "custom_components/elke27/repairs.py::BlockedUpgradeFlow.async_step_retry": (
        "tests/test_legacy_migration.py::test_retry_flows_reload",
    ),
    "custom_components/elke27/repairs.py::BlockedUpgradeFlow.async_step_retry_manual": (
        "tests/test_legacy_migration.py::test_blocked_flow_with_only_unowned_lists_them_and_retries",
    ),
    "custom_components/elke27/coordinator.py::Elke27DataUpdateCoordinator"
    ".async_set_barrier": (
        "tests/test_cover.py::test_command_sent_once_then_status_read",
        "tests/test_cover.py::test_outcome_unknown_not_retried",
    ),
    "custom_components/elke27/cover.py::Elke27BarrierCover.async_open_cover": (
        "tests/test_cover.py::test_command_sent_once_then_status_read",
        "tests/test_cover.py::test_outcome_unknown_not_retried",
    ),
    "custom_components/elke27/cover.py::Elke27BarrierCover.async_close_cover": (
        "tests/test_cover.py::test_command_sent_once_then_status_read",
        "tests/test_cover.py::test_outcome_unknown_not_retried",
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


WORKFLOWS = ROOT / ".github" / "workflows"
GATE_WORKFLOWS = {
    "lint.yml": "scripts/gate.sh lint",
    "tests.yml": "scripts/gate.sh tests",
    "validate.yml": "scripts/gate.sh validate",
}
_DIRECT_CHECK = re.compile(r"\b(ruff|mypy|pytest|loopcheck|hassfest)\b")


def _workflow(name: str) -> dict[object, object]:
    loaded = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _run_lines(workflow: dict[object, object]) -> list[str]:
    jobs = workflow.get("jobs")
    assert isinstance(jobs, dict)
    return [
        str(step["run"])
        for job in jobs.values()
        for step in job.get("steps", [])
        if "run" in step
    ]


def test_ci_runs_checks_only_through_the_gate_script() -> None:
    """CI and the local gate are one script, so they cannot drift (repeat CI miss)."""
    assert sorted(p.name for p in WORKFLOWS.glob("*.yml")) == sorted(GATE_WORKFLOWS)
    for name, command in GATE_WORKFLOWS.items():
        workflow = _workflow(name)
        runs = _run_lines(workflow)
        assert command in runs, f"{name} must run `{command}`"
        direct = [r for r in runs if _DIRECT_CHECK.search(r) and "gate.sh" not in r]
        assert direct == [], (
            f"{name} runs a check outside scripts/gate.sh; add it as a gate step"
        )
        triggers = workflow.get("on", workflow.get(True))
        assert isinstance(triggers, dict)
        assert "pull_request" in triggers
        pull_request = triggers["pull_request"]
        assert not (isinstance(pull_request, dict) and "branches" in pull_request), (
            f"{name} must run on every pull request so stacked PRs are checked"
        )


GUARD_EXCEPTION = re.compile(
    r"#\s*guard-exception:\s*(?P<guard>[a-z][a-z-]*)\s+-\s+(?P<reason>\S.*?\S)"
    r"\s+-\s+expires\s+(?P<expires>\d{4}-\d{2}-\d{2})"
    r"\s+-\s+approved by\s+(?P<approver>\S.*?)\s*$"
)
_GUARD_EXCEPTION_START = re.compile(r"#\s*guard-exception\b")
_MIN_REASON_WORDS = 3


def _comment_tokens(path: Path) -> list[tokenize.TokenInfo]:
    with path.open("rb") as handle:
        return [
            tok
            for tok in tokenize.tokenize(handle.readline)
            if tok.type == tokenize.COMMENT
        ]


def _guard_exception_lines(path: Path, guard: str) -> set[int]:
    """Return the lines that carry a valid, unexpired exception for ``guard``."""
    today = dt.datetime.now(tz=dt.UTC).date()
    lines: set[int] = set()
    for tok in _comment_tokens(path):
        match = GUARD_EXCEPTION.search(tok.string)
        if (
            match
            and match["guard"] == guard
            and len(match["reason"].split()) >= _MIN_REASON_WORDS
            and dt.date.fromisoformat(match["expires"]) >= today
        ):
            lines.add(tok.start[0])
    return lines


def test_guard_exceptions_are_well_formed_and_unexpired() -> None:
    """
    A guard exception sits on the offending line in one exact form.

    ``# guard-exception: <guard> - <reason, 3+ words> - expires YYYY-MM-DD -
    approved by <named human>``. An expired or malformed one fails here, so an
    exception cannot outlive its approval.
    """
    today = dt.datetime.now(tz=dt.UTC).date()
    problems: list[str] = []
    for path in [*_files(SOURCE), *_files(TESTS)]:
        for tok in _comment_tokens(path):
            if not _GUARD_EXCEPTION_START.search(tok.string):
                continue
            where = f"{_rel(path)}:{tok.start[0]}"
            match = GUARD_EXCEPTION.search(tok.string)
            if match is None or len(match["reason"].split()) < _MIN_REASON_WORDS:
                problems.append(f"{where}: malformed guard exception")
            elif dt.date.fromisoformat(match["expires"]) < today:
                problems.append(f"{where}: guard exception expired {match['expires']}")
    assert problems == []


_PACKAGES = ("custom_components", "elke27_lib", "homeassistant")
_DOTTED = re.compile(r"[A-Za-z_]\w*(\.[A-Za-z_]\w*)+")
_REFLECTION_ATTRS = {
    "setattr",
    "delattr",
    "getattr",
    "hasattr",
    "object",
    "__setattr__",
    "__delattr__",
    "__getattribute__",
}
_PATCH_CALLS = {"patch", "setattr", "delattr"}


def _call_name(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


def _str_arg(call: ast.Call, index: int) -> str | None:
    if len(call.args) > index:
        arg = call.args[index]
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            return arg.value
    return None


def _private_path(value: str | None) -> bool:
    return (
        value is not None
        and _DOTTED.fullmatch(value) is not None
        and any(_is_private(part) for part in value.split("."))
    )


def _private_reach_call(call: ast.Call) -> bool:
    """Reflection on a private name that the older counters do not see."""
    name = _call_name(call)
    if (
        isinstance(call.func, ast.Attribute)
        and name in _REFLECTION_ATTRS
        and _is_private(_str_arg(call, 1) or "")
    ):
        return True
    return name in _PATCH_CALLS and _private_path(_str_arg(call, 0))


def _imported_package_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
            _PACKAGES
        ):
            names |= {alias.asname or alias.name for alias in node.names}
        elif isinstance(node, ast.Import):
            names |= {
                (alias.asname or alias.name).split(".")[0]
                for alias in node.names
                if alias.name.startswith(_PACKAGES)
            }
    return names


def _base_root(base: ast.expr) -> str | None:
    while isinstance(base, ast.Attribute):
        base = base.value
    if isinstance(base, ast.Subscript):
        return _base_root(base.value)
    return base.id if isinstance(base, ast.Name) else None


def _private_overrides(tree: ast.Module) -> list[int]:
    """Private methods defined on a test subclass of a library or HA class."""
    imported = _imported_package_names(tree)
    return [
        item.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef)
        and any(_base_root(base) in imported for base in node.bases)
        for item in node.body
        if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef)
        and _is_private(item.name)
    ]


def _private_reach_lines(path: Path) -> list[int]:
    tree = _tree(path)
    lines = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _private_reach_call(node)
    ]
    if path.is_relative_to(TESTS):
        lines += _private_overrides(tree)
    allowed = _guard_exception_lines(path, "private-access")
    return [line for line in lines if line not in allowed]


PRIVATE_REACH_CEILING: dict[str, int] = {
    "tests/test_config_flow.py": 1,
    "tests/test_coordinator_d3.py": 4,
    "tests/test_panel_mac.py": 1,
}


def test_no_new_private_reach_through_strings_or_subclasses() -> None:
    """
    Private reach the import/getattr counter misses (repeat review finding).

    Counts, per file: string patch targets with a private part
    (``patch("pkg._x")``, ``monkeypatch.setattr("pkg._x", v)``), attribute
    reflection on a private name (``monkeypatch.setattr(x, "_y", v)``,
    ``patch.object(x, "_y")``, ``object.__setattr__(x, "_y", v)``) and private
    methods defined on a test subclass of an elke27, integration or Home
    Assistant class. ``PRIVATE_REACH_CEILING`` was frozen on 2026-11-24 and may
    only shrink. A one-off goes on the line as a ``private-access`` guard
    exception.
    """
    counts = {
        _rel(path): len(lines)
        for path in [*_files(SOURCE), *_test_modules()]
        if (lines := _private_reach_lines(path))
    }
    assert counts == PRIVATE_REACH_CEILING, (
        "Use a public seam instead of patching or overriding a private name. If you "
        "removed one, lower PRIVATE_REACH_CEILING."
    )


def _src_private_imports(path: Path) -> list[str]:
    allowed = _guard_exception_lines(path, "private-access")
    return [
        alias.name
        for node in ast.walk(_tree(path))
        if isinstance(node, ast.ImportFrom)
        and (node.level or (node.module or "").startswith(_PACKAGES))
        and node.lineno not in allowed
        for alias in node.names
        if _is_private(alias.name)
    ]


SRC_PRIVATE_IMPORT_CEILING: dict[str, list[str]] = {
    "custom_components/elke27/__init__.py": [
        "_area_id_from_unique_id",
        "_async_handle_alarm_arm_automatic",
        "_async_handle_zone_bypass",
        "_numeric_id_from_unique_id",
        "_service_mode_to_arm_mode",
    ],
    "custom_components/elke27/coordinator.py": ["_library_arm_mode"],
}


def test_src_modules_import_no_private_names() -> None:
    """
    One integration module never imports another module's ``_name``.

    ruff's PLC2701 is preview-only, so this is the check. The existing imports
    were frozen in ``SRC_PRIVATE_IMPORT_CEILING`` on 2026-11-24 and may only be
    removed.
    """
    found = {
        _rel(path): names
        for path in _files(SOURCE)
        if (names := sorted(_src_private_imports(path)))
    }
    assert found == SRC_PRIVATE_IMPORT_CEILING, (
        "Do not import a private name from another module; make it public. If you "
        "removed one, drop it from SRC_PRIVATE_IMPORT_CEILING."
    )


def test_guard_exception_exempts_only_its_own_line(tmp_path: Path) -> None:
    """A valid exception on the line exempts it; an expired one does not."""
    module = tmp_path / "test_sample.py"
    module.write_text(
        "patch.object(m, '_a')  # guard-exception: private-access - seam lands "
        "in #999 - expires 2999-01-01 - approved by Mitch Mitchell\n"
        "patch.object(m, '_b')  # guard-exception: private-access - seam lands "
        "in #999 - expires 2000-01-01 - approved by Mitch Mitchell\n"
        "patch.object(m, '_c')\n",
        encoding="utf-8",
    )
    assert _guard_exception_lines(module, "private-access") == {1}
    assert _guard_exception_lines(module, "sleep") == set()
    assert _private_reach_lines(module) == [2, 3]
