# https://github.com/mitchmitchell/elke27/blob/5f247f995883ee5a12d5db8ce15472325bee9db1/devtools/privacc.py
# vendored from elke27 5f247f9 (#176); do not local-edit
"""Count private reach that ruff SLF001 does not see.

SLF001 flags ``obj._name`` attribute access. Tests also reach private members in
ways it cannot see; this module counts each one as a :class:`Hit`:

- string patch targets with a private segment: ``patch("pkg.mod._x")``,
  ``patch(target=...)``, ``patch.dict("pkg.mod._cache", ...)``,
  ``monkeypatch.setattr("pkg.mod._x", ...)`` and ``delattr`` alike;
- private attribute names given as strings, positional or keyword:
  ``patch.object(o, "_x")`` / ``patch.object(o, attribute="_x")`` under any root
  (``mock.``, ``mocker.``, ``unittest.mock.``), ``patch.multiple(o, _x=...)``,
  ``setattr``/``getattr``/``hasattr``/``delattr`` and ``monkeypatch.setattr(o, name="_x")``,
  ``object.__setattr__(o, "_x", v)`` and the other dunder reflection calls;
- private keys through the instance dict: ``o.__dict__["_x"]``, ``vars(o)["_x"]``;
- imports of private names or private modules from a watched package:
  ``from pkg.mod import _x``, ``from pkg._mod import X``, ``import pkg._mod``;
- subclasses of a watched package's classes that define a private method or assign
  a private class attribute (``def _x``, ``_x = ...``, ``_x: int = ...``).

Which packages are watched is the caller's choice (``packages=``); string targets
count whatever package they name. The module uses only the standard library and
imports nothing from the project, so another repository can vendor it unchanged
together with ``allow.py``; ``privacc_probes.py`` holds the shared probe set every
copy must pass.

Run ``python devtools/privacc.py [--package NAME ...] PATH ...`` to list the hits.
"""

from __future__ import annotations

import argparse
import ast
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PACKAGES: tuple[str, ...] = ("elke27_lib",)
DESCRIPTION = "List private reach that ruff SLF001 does not see."

_TARGET_KEYWORDS = ("target", "in_dict")
_NAME_KEYWORDS = ("attribute", "name")
_REFLECTION = {"setattr", "getattr", "hasattr", "delattr"}
_DUNDER_REFLECTION = {"__setattr__", "__getattribute__", "__getattr__", "__delattr__"}
_GETTERS = {"attrgetter", "methodcaller"}
_DICT_METHODS = {"get", "pop", "setdefault", "update"}


@dataclass(frozen=True, order=True)
class Hit:
    """One private reach: its 1-based line and a short description of the form."""

    line: int
    form: str


def is_private(name: str) -> bool:
    """``_x`` is private; ``__x__`` dunders are not."""
    return name.startswith("_") and not (name.startswith("__") and name.endswith("__"))


def _chain(node: ast.expr) -> list[str]:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return parts[::-1]


def _string(node: ast.expr | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _argument(call: ast.Call, index: int, keywords: Iterable[str]) -> str | None:
    """The string passed at position ``index`` or under one of ``keywords``."""
    wanted = set(keywords)
    for keyword in call.keywords:
        if keyword.arg in wanted:
            return _string(keyword.value)
    if len(call.args) > index and not any(isinstance(a, ast.Starred) for a in call.args):
        return _string(call.args[index])
    return None


def _private_target(target: str | None) -> bool:
    return target is not None and "." in target and any(map(is_private, target.split(".")))


def _via_dict(node: ast.expr) -> bool:
    """``obj.__dict__`` or ``vars(obj)``."""
    if isinstance(node, ast.Attribute):
        return node.attr == "__dict__"
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "vars"


def _literal_keys(node: ast.expr | None) -> list[str]:
    if not isinstance(node, ast.Dict):
        return []
    return [k for k in map(_string, node.keys) if k is not None and is_private(k)]


def _dict_method_hits(call: ast.Call) -> list[str]:
    """``__dict__``/``vars()`` ``.get``/``.pop``/``.setdefault``/``.update`` with private keys."""
    func = call.func
    if not (
        isinstance(func, ast.Attribute) and func.attr in _DICT_METHODS and _via_dict(func.value)
    ):
        return []
    keys = [k for k in (_string(call.args[0]) if call.args else None,) if k and is_private(k)]
    if func.attr == "update":
        keys = [
            *(_literal_keys(call.args[0]) if call.args else []),
            *(k.arg for k in call.keywords if k.arg and is_private(k.arg)),
        ]
    return [f"__dict__/vars().{func.attr}({k!r})" for k in keys]


def _call_hits(call: ast.Call) -> list[str]:
    chain = _chain(call.func)
    if (dict_hits := _dict_method_hits(call)) or not chain:
        return dict_hits
    last = chain[-1]
    under_patch = len(chain) >= 2 and chain[-2] == "patch"
    hits: list[str] = []
    if under_patch and last == "object":
        name = _argument(call, 1, _NAME_KEYWORDS)
        if name is not None and is_private(name):
            hits.append(f"patch.object(..., {name!r})")
    elif under_patch and last == "multiple":
        target = _argument(call, 0, _TARGET_KEYWORDS)
        if _private_target(target):
            hits.append(f"patch.multiple({target!r})")
        hits += [
            f"patch.multiple(..., {k.arg}=)" for k in call.keywords if k.arg and is_private(k.arg)
        ]
    elif under_patch and last == "dict":
        target = _argument(call, 0, _TARGET_KEYWORDS)
        if _private_target(target):
            hits.append(f"patch.dict({target!r})")
        in_dict = call.args[0] if call.args else None
        in_dict = next((k.value for k in call.keywords if k.arg == "in_dict"), in_dict)
        if in_dict is not None and _via_dict(in_dict):
            values = call.args[1] if len(call.args) > 1 else None
            values = next((k.value for k in call.keywords if k.arg == "values"), values)
            keys = [
                *_literal_keys(values),
                *(k.arg for k in call.keywords if k.arg and is_private(k.arg)),
            ]
            hits += [f"patch.dict(..., {k!r})" for k in keys]
    elif last in _GETTERS:
        names = [_string(a) for a in call.args]
        hits += [
            f"{last}({n!r})" for n in names if n is not None and any(map(is_private, n.split(".")))
        ]
    elif last == "patch":
        target = _argument(call, 0, _TARGET_KEYWORDS)
        if _private_target(target):
            hits.append(f"patch({target!r})")
    elif last in _REFLECTION or last in _DUNDER_REFLECTION:
        first = _string(call.args[0]) if call.args else None
        target = first if first is not None else _argument(call, 0, ("target",))
        name = _argument(call, 0 if first is not None else 1, ("name",))
        if last in {"setattr", "delattr"} and _private_target(target):
            hits.append(f"{last}({target!r})")
        elif name is not None and is_private(name):
            hits.append(f"{last}(..., {name!r})")
    return hits


def _dict_key_hit(node: ast.Subscript) -> str | None:
    key = _string(node.slice)
    if key is None or not is_private(key):
        return None
    return f"[{key!r}] via __dict__/vars()" if _via_dict(node.value) else None


def _watched(module: str, packages: frozenset[str]) -> bool:
    return module.split(".")[0] in packages


def _import_hits(node: ast.Import | ast.ImportFrom, packages: frozenset[str]) -> list[str]:
    if isinstance(node, ast.Import):
        return [
            f"import {a.name}"
            for a in node.names
            if _watched(a.name, packages) and any(map(is_private, a.name.split(".")))
        ]
    module = node.module or ""
    if node.level or not _watched(module, packages):
        return []
    hits = [f"from {module} import"] if any(map(is_private, module.split("."))) else []
    hits += [f"from {module} import {a.name}" for a in node.names if is_private(a.name)]
    return hits


def _imported_names(tree: ast.Module, packages: frozenset[str]) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ImportFrom)
            and not node.level
            and _watched(node.module or "", packages)
        ):
            names |= {a.asname or a.name for a in node.names}
        elif isinstance(node, ast.Import):
            names |= {
                a.asname or a.name.split(".")[0] for a in node.names if _watched(a.name, packages)
            }
    links: list[tuple[list[str], list[ast.expr]]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            links.append(([node.name], list(node.bases)))
        elif isinstance(node, ast.Assign):
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
            links.append((targets, [node.value]))
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            if isinstance(node.target, ast.Name):
                links.append(([node.target.id], [node.value]))
    grew = True
    while grew:
        grew = False
        for targets, sources in links:
            if any((_chain(v) or [""])[0] in names for v in sources):
                fresh = set(targets) - names
                names |= fresh
                grew = grew or bool(fresh)
    return names


def _override_hits(node: ast.ClassDef, library: set[str]) -> list[str]:
    if not any((_chain(base) or [""])[0] in library for base in node.bases):
        return []
    hits: list[str] = []
    for item in node.body:
        if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef) and is_private(item.name):
            hits.append(f"{node.name}.{item.name} override")
        targets = (
            item.targets
            if isinstance(item, ast.Assign)
            else [item.target]
            if isinstance(item, ast.AnnAssign)
            else []
        )
        hits += [
            f"{node.name}.{t.id} override"
            for t in targets
            if isinstance(t, ast.Name) and is_private(t.id)
        ]
    return hits


def reach(source: str, packages: Iterable[str] = DEFAULT_PACKAGES) -> list[Hit]:
    """Every private reach in ``source``, sorted by line."""
    watched = frozenset(packages)
    tree = ast.parse(source)
    library = _imported_names(tree, watched)
    hits: list[Hit] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            hits += [Hit(node.lineno, form) for form in _call_hits(node)]
        elif isinstance(node, ast.Subscript) and (form := _dict_key_hit(node)):
            hits.append(Hit(node.lineno, form))
        elif isinstance(node, ast.Import | ast.ImportFrom):
            hits += [Hit(node.lineno, form) for form in _import_hits(node, watched)]
        elif isinstance(node, ast.ClassDef):
            hits += [Hit(node.lineno, form) for form in _override_hits(node, library)]
    return sorted(hits)


def reach_in_file(path: Path, packages: Iterable[str] = DEFAULT_PACKAGES) -> list[Hit]:
    """:func:`reach` for one file."""
    return reach(path.read_text(encoding="utf-8"), packages)


def main(argv: Sequence[str] | None = None) -> int:
    """Print every hit as ``path:line form``; exit 1 when there is any."""
    parser = argparse.ArgumentParser(description=DESCRIPTION)
    parser.add_argument("--package", action="append", help="watched package (repeatable)")
    parser.add_argument("paths", nargs="+", type=Path)
    args = parser.parse_args(argv)
    packages = tuple(args.package or DEFAULT_PACKAGES)
    total = 0
    for base in args.paths:
        for path in sorted(base.rglob("*.py")) if base.is_dir() else [base]:
            for hit in reach_in_file(path, packages):
                print(f"{path.as_posix()}:{hit.line} {hit.form}")
                total += 1
    print(f"privacc: {total} hit(s)")
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
