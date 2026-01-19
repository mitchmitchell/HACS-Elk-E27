#!/usr/bin/env python3
"""
Minimal Elke27Client contract example.

Flow:
- instantiate client
- connect
- wait_ready
- subscribe for events
- receive at least one event
- read a snapshot

Environment variables:
  ELKE27_MN, ELKE27_SN, ELKE27_FWVER, ELKE27_HWVER, ELKE27_OSVER
  ELKE27_ACCESS_CODE, ELKE27_PASSPHRASE
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from dataclasses import dataclass
from typing import Optional

from elke27_lib.client import E27Identity, Elke27Client
from elke27_lib.events import (
    AreaConfiguredInventoryReady,
    AuthorizationRequiredEvent,
    BootstrapCountsReady,
    ConnectionStateChanged,
    ZoneConfiguredInventoryReady,
)
from elke27_lib.session import SessionConfig

LOG = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Credentials:
    access_code: str
    passphrase: str


def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    return os.environ.get(name, default)

def _print_summary() -> None:
    summary_path = os.path.join(os.path.dirname(__file__), "..", "docs", "client_contract_summary.md")
    print("Client Contract Summary", flush=True)
    print("-----------------------", flush=True)
    try:
        with open(summary_path, "r", encoding="utf-8") as fh:
            print(fh.read().strip(), flush=True)
    except OSError as exc:
        print(f"[summary unavailable: {exc}]", flush=True)
    print("", flush=True)


async def main_async() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
    _print_summary()

    mn = _env("ELKE27_MN", "")
    sn = _env("ELKE27_SN", "")
    access_code = _env("ELKE27_ACCESS_CODE", "")
    passphrase = _env("ELKE27_PASSPHRASE", "")

    if not mn or not sn:
        raise SystemExit("ERROR: set ELKE27_MN and ELKE27_SN (client_identity).")
    if not access_code or not passphrase:
        raise SystemExit("ERROR: set ELKE27_ACCESS_CODE and ELKE27_PASSPHRASE (provisioning).")

    client = Elke27Client(filter_attribs_to_configured=True)
    print("filter_attribs_to_configured=True", flush=True)

    event_seen = asyncio.Event()
    auth_seen = asyncio.Event()
    area_auth_required = False
    zone_auth_required = False
    event_count = 0

    def on_event(evt) -> None:
        nonlocal event_count, area_auth_required, zone_auth_required
        event_count += 1
        if not hasattr(evt, "kind"):
            raise RuntimeError("Non-semantic event received.")
        if isinstance(
            evt,
            (
                ConnectionStateChanged,
                AuthorizationRequiredEvent,
                BootstrapCountsReady,
                AreaConfiguredInventoryReady,
                ZoneConfiguredInventoryReady,
            ),
        ):
            LOG.info("event=%s domain=%s", evt.kind, evt.domain)
        if isinstance(evt, BootstrapCountsReady):
            LOG.info("bootstrap_counts_ready: bootstrap_complete_counts=%s", client.bootstrap_complete_counts)
        if isinstance(evt, AreaConfiguredInventoryReady):
            LOG.info("area_configured_inventory_ready: configured_areas=%s", len(client.areas))
        if isinstance(evt, ZoneConfiguredInventoryReady):
            LOG.info("zone_configured_inventory_ready: configured_zones=%s", len(client.zones))
        event_seen.set()
        if isinstance(evt, AuthorizationRequiredEvent):
            auth_seen.set()
            if evt.scope == "area":
                area_auth_required = True
            if evt.scope == "zone":
                zone_auth_required = True

    client.subscribe(
        on_event,
        kinds=[
            ConnectionStateChanged.KIND,
            AuthorizationRequiredEvent.KIND,
            BootstrapCountsReady.KIND,
            AreaConfiguredInventoryReady.KIND,
            ZoneConfiguredInventoryReady.KIND,
        ],
    )

    discover = await client.discover()
    panels = discover.unwrap().panels
    if not panels:
        raise SystemExit("ERROR: no panels discovered.")

    client_identity = E27Identity(
        mn=mn,
        sn=sn,
        fwver=_env("ELKE27_FWVER", "0") or "0",
        hwver=_env("ELKE27_HWVER", "0") or "0",
        osver=_env("ELKE27_OSVER", "0") or "0",
    )
    creds = Credentials(access_code=access_code, passphrase=passphrase)

    link_keys = (await client.link(panels[0], client_identity, creds)).unwrap()
    session_cfg = SessionConfig(host=panels[0].panel_host, port=int(panels[0].port))
    LOG.info("import_path=elke27_lib.client.Elke27Client")
    LOG.info("is_ready(before)=%s", client.is_ready)
    LOG.info("bootstrap_complete_counts(before)=%s", client.bootstrap_complete_counts)
    (await client.connect(link_keys, client_identity=client_identity, session_config=session_cfg)).unwrap()

    await asyncio.wait_for(event_seen.wait(), timeout=5.0)

    client.request(("control", "get_version_info")).unwrap()
    client.request(("area", "get_table_info")).unwrap()
    client.request(("zone", "get_table_info")).unwrap()
    client.request(("output", "get_table_info")).unwrap()
    client.request(("tstat", "get_table_info")).unwrap()
    client.request(("network", "get_ssid")).unwrap()

    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        await asyncio.to_thread(client.pump_once, timeout_s=0.25)
        if client.is_ready and auth_seen.is_set():
            break

    if not client.is_ready:
        LOG.info("table_info keys=%s", sorted(client.table_info.keys()))
        raise SystemExit("ERROR: client did not become ready.")

    panel_info = client.panel_info
    LOG.info("is_ready(after)=%s", client.is_ready)
    LOG.info("bootstrap_complete_counts(after)=%s", client.bootstrap_complete_counts)
    LOG.info("panel session_id=%s", panel_info.session_id)
    LOG.info("panel mac=%s", getattr(panel_info, "mac", None) or "MISSING")
    LOG.info("panel model=%s", panel_info.model or "MISSING")
    LOG.info("panel firmware=%s", panel_info.firmware or "MISSING")
    LOG.info("panel serial=%s", panel_info.serial or "MISSING")

    table_info = client.table_info
    zone_info = table_info.get("zone") if isinstance(table_info, dict) else None
    area_info = table_info.get("area") if isinstance(table_info, dict) else None
    output_info = table_info.get("output") if isinstance(table_info, dict) else None
    tstat_info = table_info.get("tstat") if isinstance(table_info, dict) else None
    zone_count = zone_info.get("table_elements") if isinstance(zone_info, dict) else None
    area_count = area_info.get("table_elements") if isinstance(area_info, dict) else None
    output_count = output_info.get("table_elements") if isinstance(output_info, dict) else None
    tstat_count = tstat_info.get("table_elements") if isinstance(tstat_info, dict) else None
    LOG.info("table_info.zone.table_elements=%s", zone_count if zone_count is not None else "MISSING")
    LOG.info("table_info.area.table_elements=%s", area_count if area_count is not None else "MISSING")
    LOG.info("table_info.output.table_elements=%s", output_count if output_count is not None else "MISSING")
    LOG.info("table_info.tstat.table_elements=%s", tstat_count if tstat_count is not None else "MISSING")

    configured_area_ids = list(client.areas)
    configured_zone_ids = list(client.zones)
    if area_auth_required and not configured_area_ids:
        LOG.info("configured_areas=MISSING (auth required)")
    else:
        LOG.info("configured_areas=%s", len(configured_area_ids))
    if zone_auth_required and not configured_zone_ids:
        LOG.info("configured_zones=MISSING (auth required)")
    else:
        LOG.info("configured_zones=%s", len(configured_zone_ids))

    for area_id in sorted(configured_area_ids)[:5]:
        area = client.areas.get(area_id)
        name = getattr(area, "name", None) if area is not None else None
        LOG.info("area[%s].name=%s", area_id, name or "MISSING")

    for zone_id in sorted(configured_zone_ids)[:5]:
        zone = client.zones.get(zone_id)
        name = getattr(zone, "name", None) if zone is not None else None
        LOG.info("zone[%s].name=%s", zone_id, name or "MISSING")

    if auth_seen.is_set():
        LOG.info("auth_required_event=seen")
    else:
        LOG.info("auth_required_event=not_seen")

    client.unsubscribe(on_event)
    prior_count = event_count
    await client.disconnect()
    await asyncio.sleep(1.0)
    if event_count != prior_count:
        LOG.info("unsubscribe_check=failed (events_after_unsubscribe=%s)", event_count - prior_count)
    else:
        LOG.info("unsubscribe_check=ok")

    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
