# https://github.com/mitchmitchell/elke27/blob/d3b51d0b9726da86f16b5293ec71cf71b9a9c107/devtools/loopcheck.py
"""Report blocking calls in event-loop modules (ELK001-005, ELK101-110).

A file is a *loop* file when its name ends in ``_loop.py``, when it is listed in
``loop_modules``, or when it sits under a directory in ``loop_paths``. A file is a
*thread* file when its name ends in ``_thread.py``. Loop files carry the full ban in
every scope (``async def``, sync ``def``, lambdas, nested functions, module level).
Thread files may block but may not mix in asyncio.

Configuration is read from ``[tool.elke27.loopcheck]`` in ``pyproject.toml``:
``loop_modules``, ``loop_paths`` and ``core_modules``. Paths are relative to the
directory holding the config file.

A finding on a line is suppressed only by ``# noqa: ELKnnn -- <reason>`` with a
reason of at least three words. ELK003 and ELK005 cannot be suppressed.

Usage: ``python devtools/loopcheck.py [--config pyproject.toml] PATH...``. Prints
``path:line:col ELKnnn message`` per finding and exits 1 when there is any. A file
that does not parse is reported as one ELK000 finding. A path that does not exist is
a usage error (exit 2).
"""

from __future__ import annotations

import argparse
import ast
import io
import re
import sys
import tokenize
import tomllib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

LOOP_SUFFIX = "_loop.py"
THREAD_SUFFIX = "_thread.py"

DEFAULT_CORE_MODULES = (
    "session_thread",
    "hello_thread",
    "linking_thread",
    "kernel_loop",
    "framing_loop",
    "presentation_loop",
    "encryption_loop",
    "dispatcher_loop",
    "outbound_loop",
    "pending_loop",
    "message_loop",
    "client",
    "discovery",
)
ELK001_STDLIB = {"asyncio", "socket", "select", "ssl"}
ELK002_BANNED_IMPORTS = {
    "socket",
    "select",
    "subprocess",
    "requests",
    "urllib.request",
    "http.client",
    "queue",
}
THREAD_ASYNCIO_ALLOWED = {"run_coroutine_threadsafe", "AbstractEventLoop"}

SLEEP = {"time.sleep"}
SOCKET_CALLS = {
    "socket.socket",
    "socket.create_connection",
    "socket.getaddrinfo",
    "socket.gethostbyname",
    "socket.gethostbyname_ex",
    "socket.gethostbyaddr",
    "select.select",
    "select.poll",
    "select.epoll",
}
SOCKET_FACTORIES = {"socket.socket", "socket.create_connection"}
SOCKET_METHODS = {
    "recv",
    "recv_into",
    "recvfrom",
    "recvfrom_into",
    "recvmsg",
    "send",
    "sendall",
    "sendto",
    "accept",
    "connect",
}
HTTP_PREFIXES = ("requests.", "urllib.request.", "http.client.", "urllib3.")
PROCESS_CALLS = {
    "subprocess.run",
    "subprocess.call",
    "subprocess.check_call",
    "subprocess.check_output",
    "subprocess.Popen",
    "os.system",
    "os.popen",
    "os.wait",
    "os.waitpid",
    "os.wait3",
    "os.wait4",
    "os.waitid",
}
FILE_CALLS = {
    "io.open",
    "os.read",
    "os.write",
    "shutil.copy",
    "shutil.copy2",
    "shutil.copyfile",
    "shutil.copyfileobj",
    "shutil.copytree",
    "shutil.move",
    "json.load",
    "json.dump",
}
PATH_FACTORIES = {"pathlib.Path", "pathlib.PurePath", "pathlib.PosixPath", "pathlib.WindowsPath"}
PATH_IO_METHODS = {"read_text", "read_bytes", "write_text", "write_bytes"}
LOCK_FACTORIES = {
    "threading.Lock",
    "threading.RLock",
    "threading.Condition",
    "threading.Semaphore",
    "threading.BoundedSemaphore",
    "threading.Event",
}
QUEUE_FACTORIES = {"queue.Queue", "queue.SimpleQueue", "queue.LifoQueue", "queue.PriorityQueue"}
FUTURE_FACTORIES = {"concurrent.futures.Future", "asyncio.run_coroutine_threadsafe"}
THREAD_FACTORIES = {"threading.Thread"}
WAIT_CALLS = {"concurrent.futures.wait", "asyncio.run"}
TLS_KDF_CALLS = {"ssl.wrap_socket", "hashlib.pbkdf2_hmac", "hashlib.scrypt"}

NOQA_RE = re.compile(
    r"#\s*noqa(?P<colon>:\s*(?P<codes>[A-Za-z]+[0-9]+(?:[\s,]+[A-Za-z]+[0-9]+)*))?"
)
IGNORE_RE = re.compile(r"#\s*(?:type|pyright):\s*ignore(?:\[[^\]]*\])?")
UNSUPPRESSIBLE = {"ELK000", "ELK003", "ELK005"}


@dataclass(frozen=True, order=True)
class Finding:
    """One checker result, printed as ``path:line:col code message``."""

    path: str
    line: int
    col: int
    code: str
    message: str

    def render(self) -> str:
        """Return the one-line report form."""
        return f"{self.path}:{self.line}:{self.col} {self.code} {self.message}"


@dataclass
class Config:
    """Loop scope settings; paths are absolute after loading."""

    root: Path
    loop_modules: list[Path] = field(default_factory=list)
    loop_paths: list[Path] = field(default_factory=list)
    core_modules: set[str] = field(default_factory=lambda: set(DEFAULT_CORE_MODULES))
    config_file: Path | None = None

    @classmethod
    def load(cls, config_file: Path) -> Config:
        """Read ``[tool.elke27.loopcheck]`` from a TOML file."""
        data = tomllib.loads(config_file.read_text(encoding="utf-8"))
        section = data.get("tool", {}).get("elke27", {}).get("loopcheck", {})
        root = config_file.resolve().parent
        core = section.get("core_modules")
        return cls(
            root=root,
            loop_modules=[root / p for p in section.get("loop_modules", [])],
            loop_paths=[root / p for p in section.get("loop_paths", [])],
            core_modules=set(core) if core is not None else set(DEFAULT_CORE_MODULES),
            config_file=config_file,
        )

    def is_thread(self, path: Path) -> bool:
        """True for ``*_thread.py`` files."""
        return path.name.endswith(THREAD_SUFFIX)

    def is_loop(self, path: Path) -> bool:
        """True for ``*_loop.py``, listed loop modules and files under loop paths."""
        resolved = path.resolve()
        if path.name.endswith(LOOP_SUFFIX):
            return True
        if resolved in {p.resolve() for p in self.loop_modules}:
            return True
        return any(resolved.is_relative_to(p.resolve()) for p in self.loop_paths)

    def stale_entries(self) -> list[Finding]:
        """ELK005 for configured paths that do not exist."""
        where = self.display(self.config_file) if self.config_file else "<config>"
        return [
            Finding(
                where,
                1,
                1,
                "ELK005",
                f"configured loop path does not exist: {p.relative_to(self.root).as_posix()}",
            )
            for p in [*self.loop_modules, *self.loop_paths]
            if not p.exists()
        ]

    def display(self, path: Path) -> str:
        """Path relative to the config root when possible."""
        resolved = path.resolve()
        if resolved.is_relative_to(self.root):
            return resolved.relative_to(self.root).as_posix()
        return path.as_posix()


def _reason_words(text: str) -> int:
    _, sep, reason = text.partition("--")
    return len(reason.split()) if sep else 0


@dataclass
class _Suppressions:
    by_line: dict[int, set[str]] = field(default_factory=dict)
    findings: list[tuple[int, int, str]] = field(default_factory=list)


def _scan_comments(source: str) -> _Suppressions:
    out = _Suppressions()
    tokens = tokenize.generate_tokens(io.StringIO(source).readline)
    for tok in tokens:
        if tok.type != tokenize.COMMENT:
            continue
        line, col = tok.start
        text = tok.string
        noqa = NOQA_RE.search(text)
        if noqa:
            codes = {c.upper() for c in re.split(r"[\s,]+", noqa.group("codes") or "") if c}
            rest = text[noqa.end() :]
            if not codes:
                out.findings.append(
                    (line, col + 1, "blanket noqa; name the code and give a reason")
                )
            elif _reason_words(rest) < 3:
                out.findings.append(
                    (line, col + 1, "noqa without a reason; use '# noqa: CODE -- <reason>'")
                )
            else:
                out.by_line.setdefault(line, set()).update(codes)
        ignore = IGNORE_RE.search(text)
        if ignore and _reason_words(text[ignore.end() :]) < 3:
            out.findings.append((line, col + 1, "type/pyright ignore without a reason after '--'"))
    return out


def _dotted(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base else None
    return None


class ImportTable(ast.NodeVisitor):
    """Alias map from local names to qualified names, plus the import list."""

    def __init__(self) -> None:
        self.aliases: dict[str, str] = {}
        self.imports: list[tuple[ast.stmt, str, bool]] = []

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.imports.append((node, alias.name, False))
            if alias.asname:
                self.aliases[alias.asname] = alias.name
            else:
                head = alias.name.split(".")[0]
                self.aliases.setdefault(head, head)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        relative = node.level > 0
        for alias in node.names:
            qualified = f"{module}.{alias.name}" if module else alias.name
            self.imports.append(
                (node, qualified if not relative else module or alias.name, relative)
            )
            self.aliases[alias.asname or alias.name] = qualified


class _Checker:
    """Walks one module and collects ELK findings."""

    def __init__(self, tree: ast.Module, imports: ImportTable) -> None:
        self.tree = tree
        self.aliases = imports.aliases
        self.found: list[tuple[int, int, str, str]] = []
        self.locks: set[str] = set()
        self.queues: set[str] = set()
        self.futures: set[str] = set()
        self.threads: set[str] = set()
        self.paths: set[str] = set()
        self.sockets: set[str] = set()

    def qualify(self, node: ast.AST) -> str | None:
        dotted = _dotted(node)
        if dotted is None:
            return None
        head, _, tail = dotted.partition(".")
        base = self.aliases.get(head)
        if base is None:
            return dotted
        return f"{base}.{tail}" if tail else base

    def is_builtin(self, node: ast.AST, name: str) -> bool:
        return isinstance(node, ast.Name) and node.id == name and name not in self.aliases

    def add(self, node: ast.AST, code: str, message: str) -> None:
        self.found.append(
            (getattr(node, "lineno", 1), getattr(node, "col_offset", 0) + 1, code, message)
        )

    def collect_targets(self) -> None:
        for node in ast.walk(self.tree):
            pairs: list[tuple[ast.AST, ast.AST]] = []
            if isinstance(node, ast.Assign):
                pairs = [(t, node.value) for t in node.targets]
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                pairs = [(node.target, node.value)]
            elif isinstance(node, ast.withitem) and node.optional_vars is not None:
                pairs = [(node.optional_vars, node.context_expr)]
            for target, value in pairs:
                name = _dotted(target)
                if name is None or not isinstance(value, ast.Call):
                    continue
                factory = self.qualify(value.func) or ""
                func = value.func
                if factory in LOCK_FACTORIES:
                    self.locks.add(name)
                elif factory in QUEUE_FACTORIES:
                    self.queues.add(name)
                elif factory in FUTURE_FACTORIES or (
                    isinstance(func, ast.Attribute) and func.attr == "submit"
                ):
                    self.futures.add(name)
                elif factory in THREAD_FACTORIES:
                    self.threads.add(name)
                elif factory in PATH_FACTORIES:
                    self.paths.add(name)
                elif factory in SOCKET_FACTORIES:
                    self.sockets.add(name)

    def receiver(self, call: ast.Call) -> tuple[str | None, str | None, ast.AST | None]:
        func = call.func
        if not isinstance(func, ast.Attribute):
            return None, None, None
        return _dotted(func.value), func.attr, func.value

    def check_calls(self) -> None:
        for node in ast.walk(self.tree):
            if isinstance(node, ast.With | ast.AsyncWith):
                for item in node.items:
                    expr = item.context_expr
                    name = _dotted(expr)
                    if name in self.locks or (
                        isinstance(expr, ast.Call)
                        and (self.qualify(expr.func) or "") in LOCK_FACTORIES
                    ):
                        self.add(
                            expr, "ELK106", "blocking 'with' on a threading lock in a loop module"
                        )
            if not isinstance(node, ast.Call):
                continue
            self.check_call(node)

    def check_call(self, call: ast.Call) -> None:
        qualified = self.qualify(call.func) or ""
        kwargs = {k.arg for k in call.keywords if k.arg}
        base, method, base_node = self.receiver(call)
        if qualified in SLEEP:
            self.add(call, "ELK101", "time.sleep blocks the event loop")
        elif qualified in SOCKET_CALLS:
            self.add(call, "ELK102", f"sync socket call {qualified}")
        elif base in self.sockets and method in SOCKET_METHODS:
            self.add(call, "ELK102", f"sync socket .{method}() in a loop module")
        elif qualified.startswith(HTTP_PREFIXES):
            self.add(call, "ELK103", f"blocking HTTP call {qualified}")
        elif qualified in PROCESS_CALLS:
            self.add(call, "ELK104", f"blocking process call {qualified}")
        elif qualified in FILE_CALLS or self.is_builtin(call.func, "open"):
            self.add(call, "ELK105", f"blocking file I/O {qualified or 'open'}")
        elif method in PATH_IO_METHODS:
            self.add(call, "ELK105", f"blocking file I/O .{method}()")
        elif method == "open" and (
            base in self.paths
            or (
                isinstance(base_node, ast.Call)
                and (self.qualify(base_node.func) or "") in PATH_FACTORIES
            )
        ):
            self.add(call, "ELK105", "blocking file I/O Path.open()")
        elif (
            base in self.locks
            and method == "acquire"
            and not _nonblocking(call, kwargs, "blocking")
        ):
            self.add(call, "ELK106", "lock .acquire() without blocking=False or timeout=")
        elif base in self.locks and method == "wait" and not (call.args or "timeout" in kwargs):
            self.add(call, "ELK106", "threading .wait() without a timeout")
        elif self.is_builtin(call.func, "input"):
            self.add(call, "ELK107", "input() blocks the event loop")
        elif (
            base in self.queues
            and method in {"get", "put"}
            and not _queue_nonblocking(call, kwargs, method)
        ):
            self.add(call, "ELK108", f"queue .{method}() without block=False or timeout=")
        elif base in self.queues and method == "join":
            self.add(call, "ELK108", "queue .join() blocks the event loop")
        elif qualified in WAIT_CALLS or method == "run_until_complete":
            self.add(call, "ELK109", f"blocking wait {qualified or method}")
        elif method in {"result", "exception"} and (
            base in self.futures or _is_threadsafe_call(self, base_node)
        ):
            self.add(call, "ELK109", f"concurrent future .{method}() blocks the event loop")
        elif base in self.threads and method == "join":
            self.add(call, "ELK109", "Thread.join() blocks the event loop")
        elif qualified in TLS_KDF_CALLS or method == "do_handshake":
            self.add(call, "ELK110", f"blocking TLS or KDF call {qualified or method}")


def _nonblocking(call: ast.Call, kwargs: set[str], flag: str) -> bool:
    if "timeout" in kwargs:
        return True
    for kw in call.keywords:
        if kw.arg == flag and isinstance(kw.value, ast.Constant) and kw.value.value is False:
            return True
    if call.args and isinstance(call.args[0], ast.Constant) and call.args[0].value is False:
        return True
    return len(call.args) >= 2


def _queue_nonblocking(call: ast.Call, kwargs: set[str], method: str) -> bool:
    if "timeout" in kwargs:
        return True
    for kw in call.keywords:
        if kw.arg == "block" and isinstance(kw.value, ast.Constant) and kw.value.value is False:
            return True
    index = 0 if method == "get" else 1
    if len(call.args) > index:
        arg = call.args[index]
        if isinstance(arg, ast.Constant) and arg.value is False:
            return True
    return len(call.args) > index + 1


def _is_threadsafe_call(checker: _Checker, node: ast.AST | None) -> bool:
    return (
        isinstance(node, ast.Call)
        and (checker.qualify(node.func) or "") == "asyncio.run_coroutine_threadsafe"
    )


def _imports_core(imports: ImportTable, core: set[str]) -> str | None:
    for _, name, relative in imports.imports:
        head = name.split(".")[0]
        if not relative and head in ELK001_STDLIB:
            return head
        parts = name.split(".")
        if relative and parts[-1] in core:
            return parts[-1]
        if not relative and len(parts) >= 2 and parts[0] == "elke27_lib" and parts[1] in core:
            return parts[1]
    return None


def check_source(source: str, path: Path, config: Config) -> list[Finding]:
    """Check one module's source text and return its findings."""
    display = config.display(path)
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as e:
        return [Finding(display, e.lineno or 1, e.offset or 1, "ELK000", f"syntax error: {e.msg}")]
    imports = ImportTable()
    imports.visit(tree)
    comments = _scan_comments(source)
    raw: list[tuple[int, int, str, str]] = []
    loop = config.is_loop(path)
    thread = config.is_thread(path)

    if not loop and not thread:
        hit = _imports_core(imports, config.core_modules)
        if hit is not None:
            raw.append(
                (
                    1,
                    1,
                    "ELK001",
                    f"imports {hit} but is not *_loop.py, *_thread.py or a configured loop module",
                )
            )

    if loop or thread:
        raw += [(line, col, "ELK003", msg) for line, col, msg in comments.findings]

    if loop:
        for node, name, relative in imports.imports:
            if not relative and any(
                name == b or name.startswith(f"{b}.") for b in ELK002_BANNED_IMPORTS
            ):
                raw.append(
                    (node.lineno, node.col_offset + 1, "ELK002", f"loop module imports {name}")
                )
        checker = _Checker(tree, imports)
        checker.collect_targets()
        checker.check_calls()
        raw += checker.found

    if thread:
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef):
                raw.append(
                    (node.lineno, node.col_offset + 1, "ELK004", "thread module defines async def")
                )
            elif isinstance(node, ast.ImportFrom) and node.module == "asyncio" and node.level == 0:
                bad = [a.name for a in node.names if a.name not in THREAD_ASYNCIO_ALLOWED]
                if bad:
                    raw.append(
                        (
                            node.lineno,
                            node.col_offset + 1,
                            "ELK004",
                            f"thread module imports asyncio.{bad[0]}",
                        )
                    )
            elif isinstance(node, ast.Attribute):
                if (_dotted(node.value) or "") in {
                    k for k, v in imports.aliases.items() if v == "asyncio"
                } and node.attr not in THREAD_ASYNCIO_ALLOWED:
                    raw.append(
                        (
                            node.lineno,
                            node.col_offset + 1,
                            "ELK004",
                            f"thread module uses asyncio.{node.attr}",
                        )
                    )

    findings = {
        Finding(display, line, col, code, msg)
        for line, col, code, msg in raw
        if code in UNSUPPRESSIBLE or code not in comments.by_line.get(line, set())
    }
    return sorted(findings)


def iter_files(paths: Iterable[Path]) -> list[Path]:
    """Expand files and directories into a sorted list of ``.py`` files."""
    out: set[Path] = set()
    for p in paths:
        if p.is_dir():
            out.update(p.rglob("*.py"))
        elif p.suffix == ".py":
            out.add(p)
    return sorted(out)


def check_paths(paths: Iterable[Path], config: Config) -> list[Finding]:
    """Check every ``.py`` file under ``paths`` plus the config itself."""
    findings = config.stale_entries()
    for path in iter_files(paths):
        findings += check_source(path.read_text(encoding="utf-8"), path, config)
    return sorted(findings)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point; returns 1 when there are findings."""
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--config", type=Path, default=Path("pyproject.toml"))
    parser.add_argument("paths", nargs="+", type=Path)
    args = parser.parse_args(argv)
    missing = [str(p) for p in args.paths if not p.exists()]
    if missing:
        parser.error(f"no such path: {', '.join(missing)}")
    config = Config.load(args.config)
    findings = check_paths(args.paths, config)
    for f in findings:
        print(f.render())
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
