#!/usr/bin/env python3
"""
Report comments that break the comment convention, against a per-file ceiling.

Code and config carry no explanatory comments; docstrings, names and tests carry
the explanation. A comment is allowed only when it is one of:

- a shebang on the first line;
- license text (``SPDX-License-Identifier:`` or a ``Copyright`` line);
- nothing but an upstream URL;
- a suppression with codes and a reason of at least three words after ``--``:
  ``noqa: CODE``, ``ruff: noqa: CODE``, ``type: ignore[code]``,
  ``pyright: ignore[code]``, ``pragma: no cover``, ``pragma: no branch`` or a
  ``fmt:`` directive;
- in a config file, the reason for a rule code: a comment on the line that names
  the code, or a full-line comment directly above that line or above a
  per-file-ignores entry;
- in a config file, the reason on an entry of an ``exclude`` or
  ``extend-exclude`` list (those reasons are required).

Every other comment is a finding. Existing findings are frozen per file in the
``ceiling`` table of ``[tool.elke27.commentcheck]``; the counts must match
exactly, so a fixed comment lowers its entry in the same change.

Usage: ``python scripts/commentcheck.py [--config commentcheck.toml]``. Prints
``path:line text`` for each finding in a file over its ceiling, names entries to
lower, and exits 1 on any mismatch. ``--counts`` prints the current per-file
counts as a TOML table instead.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import io
from pathlib import Path
import re
import sys
import tokenize
import tomllib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

SHELL_SHEBANG = "#!"
CONFIG_SUFFIXES = {".toml", ".ini", ".cfg", ".txt", ".yml", ".yaml", ".sh"}
MIN_REASON_WORDS = 3

URL_ONLY = re.compile(r"https?://\S+")
LICENSE = re.compile(r"SPDX-License-Identifier:|^Copyright\b")
CODE_LIST = r"[A-Za-z]+[0-9]+(?:[\s,]+[A-Za-z]+[0-9]+)*"
SUPPRESSION = re.compile(
    rf"^(?:(?:ruff:\s*)?noqa(?P<noqa>:\s*{CODE_LIST})?"
    r"|(?:type|pyright):\s*ignore(?P<codes>\[[^\]]+\])?"
    r"|pragma:\s*no\s+(?:cover|branch)"
    r"|fmt:\s*(?:off|on|skip))",
    re.IGNORECASE,
)
RULE_CODE = re.compile(r"[\"'][A-Z]{1,4}[0-9]{2,4}[\"']")
PER_FILE_ENTRY = re.compile(r"^\s*[\"'][^\"']+[\"']\s*=\s*\[")
EXCLUDE_LIST = re.compile(r"^\s*(?:extend-)?exclude\s*=\s*\[")
LIST_ENTRY = re.compile(r"^\s*[\"'][^\"']+[\"']\s*,?\s*$")


@dataclass(frozen=True, order=True)
class Finding:
    """One disallowed comment."""

    path: str
    line: int
    text: str

    def render(self) -> str:
        """Return the one-line report form."""
        return f"{self.path}:{self.line} {self.text}"


@dataclass
class Config:
    """Scope and ceiling, with paths relative to the config file's directory."""

    root: Path
    paths: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)
    ceiling: dict[str, int] = field(default_factory=dict)

    @classmethod
    def load(cls, config_file: Path) -> Config:
        """Read ``[tool.elke27.commentcheck]`` from a TOML file."""
        data = tomllib.loads(config_file.read_text(encoding="utf-8"))
        section = data.get("tool", {}).get("elke27", {}).get("commentcheck", {})
        return cls(
            root=config_file.resolve().parent,
            paths=list(section.get("paths", [])),
            exclude=list(section.get("exclude", [])),
            ceiling={str(k): int(v) for k, v in section.get("ceiling", {}).items()},
        )

    def excluded(self, rel: str) -> bool:
        """Return True when ``rel`` is an excluded file or under an excluded dir."""
        return any(
            rel == e or rel.startswith(f"{e.rstrip('/')}/") for e in self.exclude
        )


def is_shell_script(path: Path) -> bool:
    """Return True for an extensionless file that starts with a shebang."""
    if path.suffix:
        return False
    try:
        with path.open("rb") as handle:
            return handle.read(2) == SHELL_SHEBANG.encode()
    except OSError:
        return False


def in_scope(path: Path) -> bool:
    """Return True for files this checker reads."""
    return (
        path.suffix == ".py" or path.suffix in CONFIG_SUFFIXES or is_shell_script(path)
    )


def iter_files(config: Config) -> list[Path]:
    """Every in-scope file under the configured paths, minus exclusions."""
    out: set[Path] = set()
    for entry in config.paths:
        base = config.root / entry
        candidates = base.rglob("*") if base.is_dir() else [base]
        for path in candidates:
            if not path.is_file() or not in_scope(path):
                continue
            rel = path.relative_to(config.root).as_posix()
            if "__pycache__" in rel or config.excluded(rel):
                continue
            out.add(path)
    return sorted(out)


def _reason_words(text: str) -> int:
    _, sep, reason = text.partition("--")
    return len(reason.split()) if sep else 0


def suppression_ok(body: str) -> bool | None:
    """
    Classify a suppression comment body.

    Returns None when ``body`` is not a suppression, True when it names its
    codes and gives a reason after ``--``, False otherwise.
    """
    match = SUPPRESSION.match(body)
    if match is None:
        return None
    lowered = body.lower()
    if "noqa" in lowered.split("--")[0] and not match.group("noqa"):
        return False
    if lowered.startswith(("type:", "pyright:")) and not match.group("codes"):
        return False
    return _reason_words(body[match.end() :]) >= MIN_REASON_WORDS


def allowed(body: str, *, first_line: bool, raw: str) -> bool:
    """Return True when a comment body is one of the allowed forms."""
    if first_line and raw.startswith(SHELL_SHEBANG):
        return True
    if LICENSE.search(body) or URL_ONLY.fullmatch(body):
        return True
    return bool(suppression_ok(body))


def python_comments(source: str) -> Iterator[tuple[int, str]]:
    """Yield ``(line, raw comment)`` for each comment token."""
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type == tokenize.COMMENT:
            yield tok.start[0], tok.string


def _hash_index(line: str) -> int | None:
    quote: str | None = None
    for index, char in enumerate(line):
        if quote:
            if char == quote:
                quote = None
        elif char in {"'", '"'}:
            quote = char
        elif char == "#" and (index == 0 or line[index - 1].isspace()):
            return index
    return None


def config_comments(source: str) -> Iterator[tuple[int, str, str]]:
    """Yield ``(line, raw comment, code before it)`` for ``#`` comments."""
    for number, line in enumerate(source.splitlines(), 1):
        index = _hash_index(line)
        if index is not None:
            yield number, line[index:], line[:index]


def _explains_rule(lines: list[str], number: int, code: str) -> bool:
    if RULE_CODE.search(code):
        return True
    if code.strip():
        return False
    for following in lines[number:]:
        stripped = following.strip()
        if not stripped or stripped.startswith("#"):
            continue
        return bool(RULE_CODE.search(following) or PER_FILE_ENTRY.match(following))
    return False


def exclusion_entry_lines(lines: list[str]) -> set[int]:
    """Return the line numbers of string entries inside exclude lists."""
    entries: set[int] = set()
    inside = False
    for number, line in enumerate(lines, 1):
        index = _hash_index(line)
        code = line if index is None else line[:index]
        if EXCLUDE_LIST.match(code):
            inside = "]" not in code
            continue
        if inside:
            if LIST_ENTRY.match(code):
                entries.add(number)
            if "]" in code:
                inside = False
    return entries


def check_file(path: Path, rel: str) -> list[Finding]:
    """Return the disallowed comments in one file."""
    source = path.read_text(encoding="utf-8")
    findings: list[Finding] = []
    if path.suffix == ".py":
        for number, raw in python_comments(source):
            body = raw.lstrip("#").strip()
            if not allowed(body, first_line=number == 1, raw=raw):
                findings.append(Finding(rel, number, raw.strip()))
        return findings
    lines = source.splitlines()
    excluded_entries = exclusion_entry_lines(lines)
    for number, raw, code in config_comments(source):
        body = raw.lstrip("#").strip()
        if allowed(body, first_line=number == 1, raw=raw):
            continue
        shell = path.suffix == ".sh" or is_shell_script(path)
        if not shell and (
            _explains_rule(lines, number, code)
            or (number in excluded_entries and len(body.split()) >= MIN_REASON_WORDS)
        ):
            continue
        findings.append(Finding(rel, number, raw.strip()))
    return findings


def collect(config: Config) -> dict[str, list[Finding]]:
    """Return findings per relative path, for files that have any."""
    out: dict[str, list[Finding]] = {}
    for path in iter_files(config):
        rel = path.relative_to(config.root).as_posix()
        found = check_file(path, rel)
        if found:
            out[rel] = found
    return out


def compare(config: Config, found: dict[str, list[Finding]]) -> list[str]:
    """Report lines for every file whose count differs from its ceiling."""
    report: list[str] = []
    for rel in sorted(set(found) | set(config.ceiling)):
        count = len(found.get(rel, []))
        limit = config.ceiling.get(rel, 0)
        if count > limit:
            report.append(f"{rel}: {count} disallowed comments, ceiling {limit}")
            report += [f"  {f.render()}" for f in found[rel]]
        elif count < limit:
            report.append(
                f"{rel}: {count} disallowed comments; lower the ceiling to {count}"
            )
    return report


def render_counts(found: dict[str, list[Finding]]) -> str:
    """Return the current counts as a TOML ceiling table."""
    rows = [f'"{rel}" = {len(items)}' for rel, items in sorted(found.items())]
    return "\n".join(["[tool.elke27.commentcheck.ceiling]", *rows])


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point; returns 1 when any file differs from its ceiling."""
    parser = argparse.ArgumentParser(
        description=(__doc__ or "").strip().splitlines()[0]
    )
    parser.add_argument("--config", type=Path, default=Path("commentcheck.toml"))
    parser.add_argument("--counts", action="store_true")
    args = parser.parse_args(argv)
    config = Config.load(args.config)
    found = collect(config)
    if args.counts:
        sys.stdout.write(f"{render_counts(found)}\n")
        return 0
    report = compare(config, found)
    for line in report:
        sys.stdout.write(f"{line}\n")
    if not report:
        total = sum(map(len, found.values()))
        sys.stdout.write(f"commentcheck: {total} frozen findings, none new\n")
    return 1 if report else 0


if __name__ == "__main__":
    sys.exit(main())
