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
import importlib.util
from pathlib import Path
import re
import sys
import tokenize
from typing import TYPE_CHECKING

import pytest
import yaml

if TYPE_CHECKING:
    from types import ModuleType

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
WORKFLOW_SUFFIXES = {".yml", ".yaml"}
GATE_WORKFLOWS = {
    "lint.yml": "scripts/gate.sh lint",
    "tests.yml": "scripts/gate.sh tests",
    "validate.yml": "scripts/gate.sh validate",
}
_DIRECT_CHECK = re.compile(r"\b(ruff|mypy|pytest|loopcheck|hassfest)\b")
ALLOWED_ACTIONS = ("actions/checkout@", "actions/setup-python@", "hacs/action@")


def _load_workflow(path: Path) -> object:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _job_problems(where: str, job: object) -> tuple[list[str], list[str]]:
    """Return ``(problems, run lines)`` for one workflow job."""
    if not isinstance(job, dict):
        return [f"{where}: job is not a mapping"], []
    problems: list[str] = []
    if "uses" in job:
        problems.append(f"{where}: job-level uses {job['uses']} (reusable workflow)")
    steps = job.get("steps")
    if not isinstance(steps, list) or not steps:
        return [*problems, f"{where}: job has no steps"], []
    runs: list[str] = []
    for index, step in enumerate(steps):
        at = f"{where}: step {index}"
        if not isinstance(step, dict):
            problems.append(f"{at} is not a mapping")
            continue
        uses = step.get("uses")
        if uses is not None and not str(uses).startswith(ALLOWED_ACTIONS):
            problems.append(f"{at} uses {uses}, which is not allowlisted")
        if "run" in step:
            run = str(step["run"])
            runs.append(run)
            if _DIRECT_CHECK.search(run) and "gate.sh" not in run:
                problems.append(f"{at} runs a check outside scripts/gate.sh")
    return problems, runs


def _workflow_problems(name: str, workflow: object, command: str | None) -> list[str]:
    """Every per-job problem in one workflow, plus its gate-entry checks."""
    if not isinstance(workflow, dict):
        return [f"{name}: not a mapping"]
    jobs = workflow.get("jobs")
    if not isinstance(jobs, dict) or not jobs:
        return [f"{name}: no jobs"]
    problems: list[str] = []
    runs: list[str] = []
    for job_id, job in jobs.items():
        job_problems, job_runs = _job_problems(f"{name}:{job_id}", job)
        problems += job_problems
        runs += job_runs
    if command is not None and command not in runs:
        problems.append(f"{name}: must run `{command}`")
    triggers = workflow.get("on", workflow.get(True))
    pull_request = triggers.get("pull_request") if isinstance(triggers, dict) else None
    if command is not None and not (
        isinstance(triggers, dict) and "pull_request" in triggers
    ):
        problems.append(f"{name}: must run on pull_request")
    if isinstance(pull_request, dict) and "branches" in pull_request:
        problems.append(f"{name}: pull_request must not filter on branches")
    return problems


def _workflow_dir_problems(directory: Path) -> list[str]:
    """Check every *.yml / *.yaml workflow in ``directory``, job by job."""
    names = sorted(p.name for p in directory.iterdir() if p.suffix in WORKFLOW_SUFFIXES)
    problems = [
        f"{n}: workflow has no gate entry" for n in names if n not in GATE_WORKFLOWS
    ]
    problems += [
        f"{n}: gate workflow is missing" for n in GATE_WORKFLOWS if n not in names
    ]
    for name in names:
        problems += _workflow_problems(
            name, _load_workflow(directory / name), GATE_WORKFLOWS.get(name)
        )
    return problems


def test_ci_runs_checks_only_through_the_gate_script() -> None:
    """
    CI and the local gate are one script, so they cannot drift (repeat CI miss).

    Checked per job in every workflow file: no job-level ``uses`` (a reusable
    workflow would run checks the gate never sees), at least one step, every
    step action allowlisted (Tester F1), no check run outside ``gate.sh``, each
    gate workflow runs its gate group, and ``pull_request`` has no branch filter.
    """
    assert _workflow_dir_problems(WORKFLOWS) == []


_REUSABLE_JOB = {
    "uses": "org/repo/.github/workflows/lint.yml@v1",
    "permissions": {},
}


def test_reusable_workflow_job_fails_the_guard(tmp_path: Path) -> None:
    """A job-level reusable workflow fails in a gate workflow and in any other."""
    for path in WORKFLOWS.iterdir():
        if path.suffix in WORKFLOW_SUFFIXES:
            (tmp_path / path.name).write_text(
                path.read_text(encoding="utf-8"), encoding="utf-8"
            )
    assert _workflow_dir_problems(tmp_path) == []

    lint = _load_workflow(tmp_path / "lint.yml")
    assert isinstance(lint, dict)
    lint["jobs"]["shadow"] = _REUSABLE_JOB
    assert _workflow_problems("lint.yml", lint, GATE_WORKFLOWS["lint.yml"]) == [
        (
            "lint.yml:shadow: job-level uses "
            "org/repo/.github/workflows/lint.yml@v1 (reusable workflow)"
        ),
        "lint.yml:shadow: job has no steps",
    ]

    for name in ("extra.yml", "extra.yaml"):
        (tmp_path / name).write_text(
            yaml.safe_dump(
                {"on": {"pull_request": None}, "jobs": {"x": _REUSABLE_JOB}}
            ),
            encoding="utf-8",
        )
    problems = _workflow_dir_problems(tmp_path)
    for name in ("extra.yml", "extra.yaml"):
        assert f"{name}: workflow has no gate entry" in problems
        assert (
            f"{name}:x: job-level uses org/repo/.github/workflows/lint.yml@v1 "
            "(reusable workflow)"
        ) in problems
        assert f"{name}:x: job has no steps" in problems


def test_unlisted_step_action_fails_the_guard() -> None:
    """A step action outside the allowlist fails (Tester F1)."""
    workflow = {
        "on": {"pull_request": None},
        "jobs": {
            "lint": {
                "steps": [
                    {"uses": "actions/checkout@abc"},
                    {"uses": "home-assistant/actions/hassfest@abc"},
                    {"run": "scripts/gate.sh lint"},
                ]
            }
        },
    }
    assert _workflow_problems("lint.yml", workflow, "scripts/gate.sh lint") == [
        (
            "lint.yml:lint: step 1 uses home-assistant/actions/hassfest@abc, "
            "which is not allowlisted"
        )
    ]


def _load_commentcheck() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "commentcheck", ROOT / "scripts" / "commentcheck.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["commentcheck"] = module
    spec.loader.exec_module(module)
    return module


commentcheck = _load_commentcheck()

_PY_SAMPLE = """\
#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# https://github.com/home-assistant/core
import os  # noqa: F401 -- kept for the public re-export
import re  # noqa: F401
import sys  # noqa
x: int = "a"  # type: ignore[assignment] -- fixture for the type checker
y: int = "b"  # type: ignore
if os:  # pragma: no cover -- only on a real panel
    pass
# guard-exception: sleep - waits on real timer - expires 2999-01-01 - approved by Mitch
# Explain the next line.
z = 1  # trailing explanation
"""

_TOML_SAMPLE = """\
ignore = [
    "ANN401", # typing.Any is allowed at the HA boundary
]
# reason for the per-file entry below
"tests/test_x.py" = ["S101"]
# a plain explanation
key = "value # not a comment"
extend-exclude = [
    "scripts/vendored.py", # vendored upstream file, kept verbatim
    "tests/fixtures", # why
]
other = [
    "x", # not an exclusion list entry
]
"""


def test_commentcheck_allows_only_the_listed_forms(tmp_path: Path) -> None:
    """Explanations and reasonless suppressions are findings; the rest pass."""
    sample = tmp_path / "sample.py"
    sample.write_text(_PY_SAMPLE, encoding="utf-8")
    lines = [f.line for f in commentcheck.check_file(sample, "sample.py")]
    assert lines == [5, 6, 8, 11, 12, 13]

    config = tmp_path / "sample.toml"
    config.write_text(_TOML_SAMPLE, encoding="utf-8")
    lines = [f.line for f in commentcheck.check_file(config, "sample.toml")]
    assert lines == [6, 10, 13]


@pytest.mark.parametrize(
    "line",
    [
        "x = 1  # noqa -- blanket without any codes",
        "x = 1  # type: ignore -- no error code given",
        "x = 1  # fmt: skip",
        "x = 1  # fmt: skip -- keeps the aligned table",
        "# fmt: off -- keeps the aligned table",
    ],
)
def test_commentcheck_rejects_suppression_edges(tmp_path: Path, line: str) -> None:
    """A reason never rescues a codeless noqa or ignore, or any fmt: directive."""
    sample = tmp_path / "sample.py"
    sample.write_text(f"{line}\n", encoding="utf-8")
    assert [f.line for f in commentcheck.check_file(sample, "sample.py")] == [1]


def test_commentcheck_shebang_only_on_first_line(tmp_path: Path) -> None:
    """A shebang is allowed on line 1 only (D55)."""
    sample = tmp_path / "sample.py"
    sample.write_text(
        "#!/usr/bin/env python3\nx = 1\n#!/usr/bin/env python3\n", encoding="utf-8"
    )
    assert [f.line for f in commentcheck.check_file(sample, "sample.py")] == [3]


def test_commentcheck_shell_gets_no_rule_reason_allowance(tmp_path: Path) -> None:
    """The config rule-reason form does not apply to shell scripts (D55)."""
    sample = tmp_path / "sample.sh"
    sample.write_text(
        '#!/bin/sh\n# explains the next line\nruff --select "S101" .\n',
        encoding="utf-8",
    )
    assert [f.line for f in commentcheck.check_file(sample, "sample.sh")] == [2]


def test_commentcheck_ceiling_must_match_exactly(tmp_path: Path) -> None:
    """A file above its ceiling fails with its lines; below it asks to lower it."""
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.py").write_text("x = 1  # why\n", encoding="utf-8")
    (tmp_path / "pkg" / "b.py").write_text("y = 2\n", encoding="utf-8")
    cfg = tmp_path / "commentcheck.toml"
    cfg.write_text(
        '[tool.elke27.commentcheck]\npaths = ["pkg"]\n'
        '[tool.elke27.commentcheck.ceiling]\n"pkg/b.py" = 1\n',
        encoding="utf-8",
    )
    config = commentcheck.Config.load(cfg)
    report = commentcheck.compare(config, commentcheck.collect(config))
    assert report == [
        "pkg/a.py: 1 disallowed comments, ceiling 0",
        "  pkg/a.py:1 # why",
        "pkg/b.py: 0 disallowed comments; lower the ceiling to 0",
    ]
    assert commentcheck.main(["--config", str(cfg)]) == 1


def test_commentcheck_scope_covers_code_tests_scripts_and_config() -> None:
    """The repo config reads the integration, tests, scripts and config files."""
    config = commentcheck.Config.load(ROOT / "commentcheck.toml")
    scanned = {p.relative_to(ROOT).as_posix() for p in commentcheck.iter_files(config)}
    for expected in (
        "custom_components/elke27/diagnostics.py",
        "tests/conftest.py",
        "scripts/gate.sh",
        "scripts/commentcheck.py",
        ".github/workflows/lint.yml",
        ".ruff.toml",
        "pytest.ini",
    ):
        assert expected in scanned
    assert "scripts/loopcheck.py" not in scanned
    assert not any(p.startswith("tests/loopcheck/") for p in scanned)


def _one_job(job: object, triggers: object = None) -> list[str]:
    """Run the workflow guard on a lint.yml holding just ``job``."""
    on = {"pull_request": None} if triggers is None else triggers
    workflow = {"on": on, "jobs": {"lint": job}}
    return _workflow_problems("lint.yml", workflow, "scripts/gate.sh lint")


_GATE_STEP = {"run": "scripts/gate.sh lint"}


def test_empty_steps_fail_the_guard() -> None:
    """A job with an empty or missing ``steps`` list fails."""
    assert _one_job({"steps": []}) == [
        "lint.yml:lint: job has no steps",
        "lint.yml: must run `scripts/gate.sh lint`",
    ]
    missing = _one_job({"runs-on": "ubuntu-latest"})
    assert missing[0] == "lint.yml:lint: job has no steps"


_LOOKALIKES = (
    "actions/checkout-evil@v4",
    "actions/checkoutx@v4",
    "evil/actions/checkout@v4",
    "evil/hacs/action@v1",
    "actions/evil@v4",
    "actions/setup-python-evil@v5",
    "hacs/actionx@v1",
    "./.github/actions/lint",
    "docker://alpine:3",
)


def test_lookalike_actions_fail_the_guard() -> None:
    """Prefix, suffix, fork-owner and local or docker actions are not allowlisted."""
    for uses in _LOOKALIKES:
        assert _one_job({"steps": [{"uses": uses}, _GATE_STEP]}) == [
            f"lint.yml:lint: step 0 uses {uses}, which is not allowlisted"
        ], uses


def test_each_allowlisted_action_passes_the_guard() -> None:
    """Every allowlisted action passes on its own."""
    for uses in ("actions/checkout@v4", "actions/setup-python@v5", "hacs/action@main"):
        assert _one_job({"steps": [{"uses": uses}, _GATE_STEP]}) == [], uses


def test_direct_checks_fail_and_gate_steps_pass() -> None:
    """A bare check fails; naming gate steps on ``gate.sh`` is fine."""
    assert _one_job({"steps": [{"run": "ruff check ."}, _GATE_STEP]}) == [
        "lint.yml:lint: step 0 runs a check outside scripts/gate.sh"
    ]
    assert _one_job({"steps": [{"run": "scripts/gate.sh ruff mypy"}, _GATE_STEP]}) == []


def test_non_mapping_step_fails_the_guard() -> None:
    """A step that is not a mapping fails."""
    assert _one_job({"steps": ["oops", _GATE_STEP]}) == [
        "lint.yml:lint: step 0 is not a mapping"
    ]


def test_gate_entry_and_trigger_rules_fail_the_guard() -> None:
    """Missing gate command, no ``pull_request`` and a branch filter each fail."""
    assert _one_job({"steps": [{"run": "scripts/gate.sh tests"}]}) == [
        "lint.yml: must run `scripts/gate.sh lint`"
    ]
    assert _one_job({"steps": [_GATE_STEP]}, {"push": None}) == [
        "lint.yml: must run on pull_request"
    ]
    assert _one_job(
        {"steps": [_GATE_STEP]}, {"pull_request": {"branches": ["main"]}}
    ) == ["lint.yml: pull_request must not filter on branches"]
    assert _workflow_problems("x.yml", {"on": {}, "jobs": {}}, None) == [
        "x.yml: no jobs"
    ]


def test_missing_gate_workflow_fails_the_guard(tmp_path: Path) -> None:
    """Dropping a gate workflow file fails."""
    (tmp_path / "lint.yml").write_text(
        (WORKFLOWS / "lint.yml").read_text(encoding="utf-8"), encoding="utf-8"
    )
    problems = _workflow_dir_problems(tmp_path)
    assert "tests.yml: gate workflow is missing" in problems
    assert "validate.yml: gate workflow is missing" in problems
