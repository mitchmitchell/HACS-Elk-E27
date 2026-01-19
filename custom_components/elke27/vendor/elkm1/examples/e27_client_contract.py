#!/usr/bin/env python3
"""
Minimal client contract example for Elke27Client.

Flow:
- discover or use --host/--port
- link -> connect
- wait ready
- subscribe and receive an event
- read one state snapshot
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import time
from dataclasses import dataclass

from elke27_lib import Elke27Client

LOG = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class E27Identity:
    mn: str
    sn: str
    fwver: str = "0"
    hwver: str = "0"
    osver: str = "0"


@dataclass(frozen=True, slots=True)
class E27Credentials:
    access_code: str
    passphrase: str


def _env(name: str, default: str | None = None) -> str | None:
    import os

    return os.environ.get(name, default)


def _env_any(names: list[str], default: str | None = None) -> str | None:
    for name in names:
        value = _env(name)
        if value:
            return value
    return default


def _truthy(value: str | None) -> bool:
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


async def main_async() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeout", type=float, default=10.0, help="Overall timeout")
    ap.add_argument("--pump-s", type=float, default=0.25, help="Per-pump timeout")
    ap.add_argument("--address", type=str, default=None, help="Discovery broadcast override")
    ap.add_argument("--host", type=str, default=_env_any(["ELKE27_HOST", "ELK_HOST"], None))
    ap.add_argument("--port", type=int, default=int(_env_any(["ELKE27_PORT", "ELK_PORT"], "2101") or 2101))
    ap.add_argument("--log-level", default=_env_any(["LOG_LEVEL"], "INFO"))
    args = ap.parse_args()

    logging.basicConfig(level=getattr(logging, str(args.log_level).upper(), logging.INFO))

    mn = _env_any(["ELKE27_MN", "ELK_MN"], "")
    sn = _env_any(["ELKE27_SN", "ELK_SN"], "")
    access_code = _env_any(["ELKE27_ACCESS_CODE", "ELK_ACCESS_CODE"], "")
    passphrase = _env_any(["ELKE27_PASSPHRASE", "ELK_PASSPHRASE"], "")

    if not mn or not sn:
        raise SystemExit("ERROR: set ELK_MN and ELK_SN (client_identity).")
    if not access_code or not passphrase:
        raise SystemExit("ERROR: set ELK_ACCESS_CODE and ELK_PASSPHRASE (provisioning).")

    client = Elke27Client()

    if args.host:
        panel = {"host": args.host, "port": args.port}
    else:
        discover = await client.discover(timeout=int(args.timeout), address=args.address)
        if not discover.ok or not discover.data or not discover.data.panels:
            raise SystemExit("ERROR: no panels discovered.")
        panel = discover.data.panels[0]

    client_identity = E27Identity(mn=mn, sn=sn, fwver="0", hwver="0", osver="0")
    creds = E27Credentials(access_code=access_code, passphrase=passphrase)

    link = await client.link(panel, client_identity, creds, timeout_s=float(args.timeout))
    if not link.ok:
        raise SystemExit(f"ERROR: link failed: {link.error}")

    connect = await client.connect(link.data, panel=panel, client_identity=client_identity)
    if not connect.ok:
        raise SystemExit(f"ERROR: connect failed: {connect.error}")

    ready = await asyncio.to_thread(client.wait_ready, timeout_s=float(args.timeout))
    if not ready:
        raise SystemExit("ERROR: timed out waiting for readiness.")

    got_event = False

    def _on_event(event) -> None:
        nonlocal got_event
        got_event = True
        LOG.info("Event received: %s", event.kind)

    client.subscribe(_on_event)
    request = await asyncio.to_thread(client.request, ("control", "get_version_info"))
    if not request.ok:
        raise SystemExit(f"ERROR: request failed: {request.error}")

    deadline = time.monotonic() + float(args.timeout)
    while time.monotonic() < deadline and not got_event:
        pump = await asyncio.to_thread(client.pump_once, timeout_s=float(args.pump_s))
        if not pump.ok:
            raise SystemExit(f"ERROR: pump failed: {pump.error}")
        for event in client.drain_events():
            LOG.debug("Drained event: %s", event.kind)
        await asyncio.sleep(0)

    client.unsubscribe(_on_event)

    print("Snapshot:")
    panel_info = client.panel_info
    print(f"  session_id = {panel_info.session_id}")
    print(f"  model      = {panel_info.model}")
    print(f"  firmware   = {panel_info.firmware}")
    print(f"  serial     = {panel_info.serial}")

    close = await client.disconnect()
    if not close.ok:
        raise SystemExit(f"ERROR: close failed: {close.error}")

    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
