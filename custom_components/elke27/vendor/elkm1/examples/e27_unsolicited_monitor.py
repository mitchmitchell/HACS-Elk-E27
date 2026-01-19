#!/usr/bin/env python3
"""
e27_unsolicited_monitor.py

Live example: link -> connect -> print unsolicited messages/events.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import time
from dataclasses import asdict, dataclass, is_dataclass
from typing import Optional

from elke27_lib import Elk
from elke27_lib import linking
from elke27_lib.session import SessionConfig, SessionState

LOG = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class E27Credentials:
    access_code: str
    passphrase: str


def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    import os
    value = os.environ.get(name, default)
    if value == "":
        return default
    return value


def _parse_client_identity(value: str) -> linking.E27Identity:
    text = value.strip()
    if "," in text:
        parts = [p.strip() for p in text.split(",")]
    else:
        parts = [p.strip() for p in text.split(":")]

    if len(parts) == 2:
        mn, sn = parts
        return linking.E27Identity(mn=mn, sn=sn, fwver="0", hwver="0", osver="0")
    if len(parts) == 5:
        mn, sn, fwver, hwver, osver = parts
        return linking.E27Identity(mn=mn, sn=sn, fwver=fwver, hwver=hwver, osver=osver)

    raise ValueError(
        "ELKE27_CLIENT_IDENTITY must be 'mn:sn' or 'mn:sn:fwver:hwver:osver' (':' or ',' separators)."
    )


def _event_to_dict(evt) -> dict:
    if is_dataclass(evt):
        return asdict(evt)
    if hasattr(evt, "__dict__"):
        return dict(evt.__dict__)
    return {"event": str(evt)}


def _print_unsolicited(evt) -> None:
    payload = _event_to_dict(evt)
    out = {
        "label": "UNSOLICITED MESSAGE",
        "time": time.time(),
        "kind": getattr(evt, "kind", None),
        "route": getattr(evt, "route", None),
        "seq": getattr(evt, "seq", None),
        "session_id": getattr(evt, "session_id", None),
        "classification": getattr(evt, "classification", None),
        "payload": payload,
    }
    print(json.dumps(out, indent=2, sort_keys=True))


async def main_async() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeout", type=float, default=10.0, help="Overall timeout for linking")
    ap.add_argument("--log-level", default=_env("LOG_LEVEL", "INFO"), help="DEBUG/INFO/WARNING/ERROR")
    args = ap.parse_args()

    logging.basicConfig(level=getattr(logging, str(args.log_level).upper(), logging.INFO))

    host = _env("ELKE27_HOST")
    client_identity_str = _env("ELKE27_CLIENT_IDENTITY")
    access_code = _env("ELKE27_ACCESS_CODE")
    passphrase = _env("ELKE27_PASSPHRASE")
    port = int(_env("ELKE27_PORT", "2101") or 2101)

    if not host:
        raise SystemExit("ERROR: ELKE27_HOST is required.")
    if not client_identity_str:
        raise SystemExit("ERROR: ELKE27_CLIENT_IDENTITY is required.")
    if not access_code or not passphrase:
        raise SystemExit("ERROR: ELKE27_ACCESS_CODE and ELKE27_PASSPHRASE are required.")

    client_identity = _parse_client_identity(client_identity_str)
    creds = E27Credentials(access_code=access_code, passphrase=passphrase)

    elk = Elk()

    panel = {"host": host, "port": port}

    link_keys = await elk.link(panel, client_identity, creds, timeout_s=float(args.timeout))
    session_cfg = SessionConfig(host=host, port=port, hello_timeout_s=float(args.timeout))
    state = await elk.connect(link_keys, client_identity=client_identity, session_config=session_cfg)

    if state is not SessionState.ACTIVE:
        raise SystemExit(f"ERROR: session not ACTIVE (state={state})")

    print("Connected; waiting for unsolicited messages... (Ctrl+C to exit)")

    try:
        while True:
            elk.session.pump_once(timeout_s=0.5)
            for evt in elk.drain_events():
                if getattr(evt, "classification", None) != "RESPONSE":
                    _print_unsolicited(evt)
            await asyncio.sleep(0)
    except KeyboardInterrupt:
        print("Exiting...")
    finally:
        await elk.close()

    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
