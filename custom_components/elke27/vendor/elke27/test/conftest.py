# test/conftest.py
from __future__ import annotations

import contextlib
import os
import pathlib
import uuid
from collections.abc import AsyncIterator, Generator
from dataclasses import dataclass
from typing import Any, cast

import pytest

from elke27_lib.client import Elke27Client
from elke27_lib.linking import E27Identity
from test.helpers.internal import get_kernel, set_private
from test.helpers.reporter import Reporter


@dataclass(frozen=True)
class LiveCredentials:
    access_code: str
    passphrase: str


def get_env(name: str) -> str | None:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return None
    return value


@pytest.fixture(scope="function")
async def live_e27_client(request: pytest.FixtureRequest) -> AsyncIterator[Elke27Client]:
    if get_env("ELKE27_LIVE") != "1":
        pytest.skip("ELKE27_LIVE not set; skipping live E27 tests.")

    host = get_env("ELKE27_HOST")
    access_code = get_env("ELKE27_ACCESS_CODE")
    passphrase = get_env("ELKE27_PASSPHRASE")
    if not host or not access_code or not passphrase:
        pytest.skip("Missing E27 live env vars; source ~/elk-e27-env-vars.sh")

    port = int(get_env("ELKE27_PORT") or "2101")

    client = Elke27Client()
    identity = E27Identity(
        mn=get_env("ELKE27_MN") or "CODEx",
        sn=get_env("ELKE27_SN") or "LIVE",
        fwver=get_env("ELKE27_FWVER") or "0",
        hwver=get_env("ELKE27_HWVER") or "0",
        osver=get_env("ELKE27_OSVER") or "0",
    )
    creds = LiveCredentials(access_code=access_code, passphrase=passphrase)

    skip_bootstrap = any("test_live_e27_keepalive.py" in arg for arg in request.config.args)
    if skip_bootstrap:
        kernel = get_kernel(client)

        def _noop_bootstrap_requests() -> None:
            return None

        set_private(kernel, "_bootstrap_requests", _noop_bootstrap_requests)

    try:
        link_keys = await client.async_link(
            host=host,
            port=port,
            access_code=creds.access_code,
            passphrase=creds.passphrase,
            client_identity=identity,
        )
    except Exception as exc:  # noqa: BLE001
        pytest.fail(f"E27 link failed; check credentials and panel availability: {exc}")

    try:
        await client.async_connect(host, port, link_keys)
    except Exception as exc:  # noqa: BLE001
        pytest.fail(f"E27 connect failed; check panel availability: {exc}")

    if not skip_bootstrap:
        await client.wait_ready(timeout_s=10.0)

    try:
        yield client
    finally:
        await client.async_disconnect()


def _get_or_create_run_id(cfg: pytest.Config) -> str:
    """Return a stable run_id for the entire pytest invocation.

    A single pytest invocation should write to a single jsonl file. If run_id is
    generated in more than one place (e.g., hooks + fixtures), you'll end up with
    multiple output files for what you think is "one run".
    """
    cfg_any = cast(Any, cfg)
    run_id = getattr(cfg_any, "_e27_run_id", None)
    if not run_id:
        run_id = uuid.uuid4().hex
        cfg_any._e27_run_id = run_id
    return run_id


def _get_or_create_report_path(cfg: pytest.Config) -> pathlib.Path:
    """Return the jsonl path for this pytest invocation (stable)."""
    cfg_any = cast(Any, cfg)
    p = getattr(cfg_any, "_e27_report_path", None)
    if p is None:
        base_dir = pathlib.Path(cfg.getoption("--e27-artifacts-dir"))
        #        artifacts_dir = base_dir / "test_runs"
        artifacts_dir = base_dir
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        p = artifacts_dir / f"{_get_or_create_run_id(cfg)}.jsonl"
        cfg_any._e27_report_path = p
    return p


def pytest_configure(config: pytest.Config) -> None:
    """Initialize run_id/report path early so hooks/fixtures share them."""
    _get_or_create_report_path(config)


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("e27")
    group.addoption(
        "--e27-live",
        action="store_true",
        default=False,
        help="Enable tests marked as live (requires real panel + credentials).",
    )
    group.addoption(
        "--e27-report",
        action="store",
        default=os.getenv("ELK_E27_REPORT_FORMAT", "jsonl"),
        choices=("jsonl", "yaml", "both", "none"),
        help="Test artifact format: jsonl (default), yaml, both, or none. "
        "YAML is derived from JSON records.",
    )
    group.addoption(
        "--e27-artifacts-dir",
        action="store",
        default=os.getenv("ELK_E27_ARTIFACTS_DIR", "artifacts/test_runs"),
        help="Directory to write E27 test artifacts (default: artifacts/test_runs).",
    )


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_makereport(
    item: pytest.Item, call: pytest.CallInfo[object]
) -> Generator[None, Any, Any]:
    # Attach the report to the item so hooks/fixtures can access pass/fail and exceptions.
    del call
    outcome = yield
    rep = outcome.get_result()
    setattr(item, f"rep_{rep.when}", rep)
    if rep.when == "teardown" and rep.failed:
        mark = item.get_closest_marker("xfail")
        if mark is not None:
            reason = mark.kwargs.get("reason")
            if reason is None and mark.args:
                reason = str(mark.args[0])
            if reason and "teardown" in str(reason).lower():
                rep.outcome = "skipped"
                rep.wasxfail = reason


@pytest.fixture()
def fail_teardown(request: pytest.FixtureRequest) -> Generator[None, None, None]:
    def fin():
        pytest.fail("intentional teardown failure", pytrace=True)

    request.addfinalizer(fin)
    yield


@pytest.fixture(scope="session")
def e27_run_id(request: pytest.FixtureRequest) -> str:
    # Stable per pytest invocation (shared with hooks).
    return _get_or_create_run_id(request.config)


@pytest.fixture()
def reporter(request: pytest.FixtureRequest, e27_run_id: str) -> Generator[Reporter, None, None]:
    """
    Create a per-test Reporter and attach it to the item.
    We do NOT finalize here; finalization happens in pytest_runtest_teardown,
    after rep_teardown has been generated by pytest.
    """
    cfg = request.config
    report_mode = str(cfg.getoption("--e27-report")).lower()
    artifacts_dir = pathlib.Path(str(cfg.getoption("--e27-artifacts-dir")))
    emit_jsonl = report_mode in ("jsonl", "both")
    emit_yaml = report_mode in ("yaml", "both")
    enable = report_mode != "none"

    node = cast(pytest.Item, request.node)
    node_any = cast(Any, node)
    r = Reporter(
        run_id=e27_run_id,
        test_id=node.nodeid,
        artifacts_dir=artifacts_dir,
        emit_jsonl=emit_jsonl,
        emit_yaml=emit_yaml,
        enable=enable,
    )
    r.test_start()

    # Make it accessible to hooks
    node_any._e27_reporter = r

    yield r


def _finalize_reporter_for_item(item: pytest.Item) -> None:
    item_any = cast(Any, item)
    r: Reporter | None = getattr(item_any, "_e27_reporter", None)
    if r is None:
        return

    rep_setup = getattr(item, "rep_setup", None)
    rep_call = getattr(item, "rep_call", None)
    rep_teardown = getattr(item, "rep_teardown", None)

    outcome = "unknown"
    longrepr = None
    when = None

    # Setup failures always win
    if rep_setup is not None and rep_setup.failed:
        outcome = "fail"
        longrepr = rep_setup.longrepr
        when = "setup"

    # Call phase
    elif rep_call is not None:
        when = "call"
        if rep_call.passed:
            outcome = "pass"
        elif rep_call.skipped:
            outcome = "skip"
            longrepr = rep_call.longrepr
        else:
            outcome = "fail"
            longrepr = rep_call.longrepr

    # Teardown failures override a pass
    if outcome == "pass" and rep_teardown is not None and rep_teardown.failed:
        outcome = "fail"
        longrepr = rep_teardown.longrepr
        when = "teardown"

    r.test_end(outcome=outcome, when=when, longrepr=longrepr)
    r.finalize()

    # Prevent double-finalization if pytest calls teardown hooks unusually
    with contextlib.suppress(Exception):
        delattr(item_any, "_e27_reporter")


def pytest_runtest_teardown(item: pytest.Item, nextitem: pytest.Item | None) -> None:
    """
    Finalize after teardown completes and rep_teardown is available.
    This is the key change for reliable teardown failure reporting.
    """
    del nextitem
    _finalize_reporter_for_item(item)


def pytest_runtest_setup(item: pytest.Item) -> None:
    """
    If setup itself fails before the test body can request the reporter fixture,
    we still want the reporter to exist and capture the failure.
    So: create a reporter eagerly if reporting is enabled.
    """
    cfg = item.config
    report_mode = str(cfg.getoption("--e27-report")).lower()
    if report_mode == "none":
        return

    item_any = cast(Any, item)
    if hasattr(item_any, "_e27_reporter"):
        return

    cfg_any = cast(Any, cfg)
    run_id = getattr(cfg_any, "_e27_run_id", None)
    if run_id is None:
        run_id = uuid.uuid4().hex
        cfg_any._e27_run_id = run_id

    artifacts_dir = pathlib.Path(str(cfg.getoption("--e27-artifacts-dir")))
    emit_jsonl = report_mode in ("jsonl", "both")
    emit_yaml = report_mode in ("yaml", "both")

    r = Reporter(
        run_id=run_id,
        test_id=item.nodeid,
        artifacts_dir=artifacts_dir,
        emit_jsonl=emit_jsonl,
        emit_yaml=emit_yaml,
        enable=True,
    )
    r.test_start()
    item_any._e27_reporter = r


@pytest.fixture()
def fail_setup():
    raise AssertionError("intentional setup failure")
