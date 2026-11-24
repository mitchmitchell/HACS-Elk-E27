"""
Guards against tests that synchronise with sleeps or the wall clock.

Review kept finding tests that yield with ``asyncio.sleep(0)``, sleep a real
interval, loop on yields until something happens, or read
``time.monotonic()`` / ``loop.time()`` (directly in an assert or through a
variable). Those pass on a quiet machine and flake under load. Synchronise
with ``await hass.async_block_till_done()``, an
``asyncio.Event`` or ``Future`` the fake sets, or ``freezer`` plus
``async_fire_time_changed`` instead.

Existing uses are frozen per file and may only shrink. Line-level exceptions
will use the shared exception marker vendored from elke27 (``allow.py``); until
then there is no exception form, so a new use fails.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "custom_components" / "elke27"
TESTS = ROOT / "tests"
EXCLUDED = {TESTS / "loopcheck"}

SLEEP_CALLS = {"asyncio.sleep", "time.sleep"}
CLOCK_CALLS = {"time.monotonic", "time.time", "time.perf_counter", "time.monotonic_ns"}
LOOP_GETTERS = {"asyncio.get_running_loop", "asyncio.get_event_loop"}
ALIASED_CALLS = SLEEP_CALLS | CLOCK_CALLS

SLEEP_CEILING: dict[str, int] = {
    "tests/conftest.py": 3,
    "tests/test_coordinator.py": 19,
    "tests/test_coordinator_client.py": 2,
    "tests/test_coordinator_d3.py": 1,
    "tests/test_coordinator_d4.py": 1,
    "tests/test_reconnect.py": 2,
    "tests/test_services.py": 9,
    "tests/test_zone_bypass_service.py": 4,
}


def _rel(path: Path) -> str:
    return path.resolve().relative_to(ROOT).as_posix()


def _files(base: Path) -> list[Path]:
    return sorted(
        p
        for p in base.rglob("*.py")
        if not any(p.is_relative_to(excluded) for excluded in EXCLUDED)
    )


def _dotted(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base else None
    return None


class _Scanner:
    """Resolve import aliases and sleep/clock aliases (any scope) in one module."""

    def __init__(self, tree: ast.Module) -> None:
        self.tree = tree
        self.aliases: dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.aliases[alias.asname or alias.name] = alias.name
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                for alias in node.names:
                    self.aliases[alias.asname or alias.name] = (
                        f"{node.module}.{alias.name}"
                    )
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and (qualified := self.qualify(node.value)) in ALIASED_CALLS
            ):
                self.aliases[node.targets[0].id] = qualified

    def qualify(self, node: ast.AST) -> str | None:
        dotted = _dotted(node)
        if dotted is None:
            return None
        head, _, tail = dotted.partition(".")
        base = self.aliases.get(head, head)
        return f"{base}.{tail}" if tail else base

    def is_loop_time(self, call: ast.Call) -> bool:
        func = call.func
        if not (isinstance(func, ast.Attribute) and func.attr == "time"):
            return False
        receiver = func.value
        if isinstance(receiver, ast.Call):
            return self.qualify(receiver.func) in LOOP_GETTERS
        name = _dotted(receiver) or ""
        return name == "loop" or name.endswith(".loop")

    def awaited_sleeps(self) -> list[int]:
        return [
            node.lineno
            for node in ast.walk(self.tree)
            if isinstance(node, ast.Await)
            and isinstance(node.value, ast.Call)
            and self.qualify(node.value.func) == "asyncio.sleep"
        ]

    def blocking_sleeps(self) -> list[int]:
        return [
            node.lineno
            for node in ast.walk(self.tree)
            if isinstance(node, ast.Call) and self.qualify(node.func) == "time.sleep"
        ]

    def clock_reads(self) -> list[int]:
        return [
            node.lineno
            for node in ast.walk(self.tree)
            if isinstance(node, ast.Call)
            and (self.qualify(node.func) in CLOCK_CALLS or self.is_loop_time(node))
        ]

    def zero_sleeps(self) -> list[int]:
        return [
            node.lineno
            for node in ast.walk(self.tree)
            if isinstance(node, ast.Call)
            and self.qualify(node.func) == "asyncio.sleep"
            and _is_zero(_sleep_delay(node))
        ]


def _sleep_delay(call: ast.Call) -> ast.expr | None:
    """Return the ``delay`` argument of ``asyncio.sleep(delay, result=None)``."""
    if call.args:
        return call.args[0]
    return next((k.value for k in call.keywords if k.arg == "delay"), None)


def _is_zero(node: ast.expr | None) -> bool:
    return isinstance(node, ast.Constant) and node.value == 0


def _scanner(path: Path) -> _Scanner:
    return _Scanner(ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))


def _test_sync_lines(path: Path) -> list[int]:
    scanner = _scanner(path)
    lines = [
        *scanner.awaited_sleeps(),
        *scanner.blocking_sleeps(),
        *scanner.clock_reads(),
    ]
    return sorted(lines)


def _sync_counts() -> dict[str, list[int]]:
    return {
        _rel(path): lines
        for path in _files(TESTS)
        if path.resolve() != Path(__file__).resolve()
        and (lines := _test_sync_lines(path))
    }


def test_tests_do_not_synchronise_with_sleeps_or_the_clock() -> None:
    """Awaited sleeps, time.sleep and clock reads in tests only shrink."""
    found = _sync_counts()
    problems = []
    for rel in sorted(set(found) | set(SLEEP_CEILING)):
        count = len(found.get(rel, []))
        limit = SLEEP_CEILING.get(rel, 0)
        if count > limit:
            lines = ", ".join(map(str, found[rel]))
            problems.append(f"{rel}: {count} > ceiling {limit} (lines {lines})")
        elif count < limit:
            problems.append(f"{rel}: {count} < ceiling {limit}; lower SLEEP_CEILING")
    if problems:
        pytest.fail(
            "Synchronise with hass.async_block_till_done(), an Event/Future the "
            "fake sets, or freezer + async_fire_time_changed instead of sleeping "
            "or reading the clock:\n" + "\n".join(problems)
        )


def test_integration_never_yields_with_sleep_zero() -> None:
    """``asyncio.sleep(0)`` in the integration is a test seam, not a fix."""
    found = {
        f"{_rel(path)}:{line}"
        for path in _files(SOURCE)
        for line in _scanner(path).zero_sleeps()
    }
    if found:
        pytest.fail(f"asyncio.sleep(0) in the integration: {sorted(found)}")


def test_scanner_counts_each_form(tmp_path: Path) -> None:
    """Awaited sleeps, aliases, time.sleep and every clock read count once."""
    module = tmp_path / "test_sample.py"
    module.write_text(
        "import asyncio\n"
        "import time\n"
        "from asyncio import sleep as nap\n"
        "_REAL = asyncio.sleep\n"
        "async def f(loop, hass):\n"
        "    await asyncio.sleep(0)\n"
        "    await nap(1)\n"
        "    for _ in range(5):\n"
        "        await _REAL(0)\n"
        "    time.sleep(0.1)\n"
        "    assert time.monotonic() > 0\n"
        "    assert loop.time() > 0\n"
        "    assert asyncio.get_running_loop().time() > 0\n"
        "    task = asyncio.create_task(asyncio.sleep(10))\n"
        "    t0 = time.perf_counter()\n"
        "    now = hass.loop.time()\n"
        "    s = asyncio.sleep\n"
        "    await s(delay=0)\n"
        "    clock = time.monotonic_ns\n"
        "    elapsed = clock() - t0\n",
        encoding="utf-8",
    )
    expected = [6, 7, 9, 10, 11, 12, 13, 15, 16, 18, 20]
    if _test_sync_lines(module) != expected:
        pytest.fail(f"{_test_sync_lines(module)} != {expected}")


def test_zero_sleep_matches_positional_and_keyword_delay(tmp_path: Path) -> None:
    """``asyncio.sleep(0)`` and ``asyncio.sleep(delay=0)`` are both caught."""
    module = tmp_path / "sample.py"
    module.write_text(
        "import asyncio\n"
        "async def f():\n"
        "    await asyncio.sleep(0)\n"
        "    await asyncio.sleep(delay=0)\n"
        "    await asyncio.sleep(0.5)\n"
        "    await asyncio.sleep(delay=1, result=None)\n",
        encoding="utf-8",
    )
    if _scanner(module).zero_sleeps() != [3, 4]:
        pytest.fail(f"{_scanner(module).zero_sleeps()} != [3, 4]")
