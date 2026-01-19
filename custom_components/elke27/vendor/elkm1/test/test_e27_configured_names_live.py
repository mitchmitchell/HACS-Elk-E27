"""Live test: print configured panel/area/zone names."""

from __future__ import annotations

import logging
import os
import asyncio
import time
from dataclasses import dataclass

import pytest

from elke27_lib import linking
from elke27_lib.client import Elke27Client
from elke27_lib.errors import AuthorizationRequired
from elke27_lib.errors import AuthorizationRequired
from test.helpers.payload_validation import assert_payload_shape


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name, default)
    if value == "":
        return default
    return value


def _require_env(name: str) -> str:
    value = _env(name)
    if not value:
        pytest.skip(f"Missing {name} for live configured-names test.")
    return value


@dataclass(frozen=True, slots=True)
class _Credentials:
    access_code: str
    passphrase: str


@pytest.mark.integration
@pytest.mark.asyncio
async def test_live_configured_names() -> None:
    log_level = str(_env("LOG_LEVEL", "INFO") or "INFO").upper()
    logging.basicConfig(level=getattr(logging, log_level, logging.INFO), force=True)
    logging.getLogger("elke27_lib.session").setLevel(logging.DEBUG)

    host = _require_env("ELKE27_HOST")
    port = int(_env("ELKE27_PORT", "2101") or 2101)
    access_code = _require_env("ELKE27_ACCESS_CODE")
    passphrase = _require_env("ELKE27_PASSPHRASE")
    mn = _require_env("ELKE27_MN")
    sn = _require_env("ELKE27_SN")
    fwver = _require_env("ELKE27_FWVER")
    hwver = _require_env("ELKE27_HWVER")
    osver = _require_env("ELKE27_OSVER")
    timeout_s = float(_env("ELKE27_TIMEOUT_S", "60.0") or 60.0)
    max_runtime_s = float(_env("ELKE27_TEST_TIMEOUT_S", str(timeout_s)) or timeout_s)
    if max_runtime_s > 60.0:
        max_runtime_s = 60.0
    if max_runtime_s <= 0:
        max_runtime_s = timeout_s

    client_identity = linking.E27Identity(mn=mn, sn=sn, fwver=fwver, hwver=hwver, osver=osver)
    client = Elke27Client(
        features=["elke27_lib.features.control", "elke27_lib.features.area", "elke27_lib.features.zone"]
    )

    creds = _Credentials(access_code=access_code, passphrase=passphrase)
    try:
        link_keys = await client.async_link(
            host,
            port,
            access_code=creds.access_code,
            passphrase=creds.passphrase,
            client_identity=client_identity,
            timeout_s=timeout_s,
        )
        await client.async_connect(host, port, link_keys)
    except Exception as exc:  # noqa: BLE001
        pytest.fail(f"Link/connect failed: {exc}")

    area_auth_required = False
    zone_auth_required = False
    area_names_seen = False
    zone_names_seen = False

    start = time.monotonic()
    next_debug = start + 5.0
    total_deadline = time.monotonic() + max_runtime_s
    next_version_retry = time.monotonic() + 5.0
    while time.monotonic() < total_deadline:
        await asyncio.sleep(0.5)
        if not client.state.panel.connected:
            pytest.fail("Panel disconnected during configured-names test.")

        if not client.state.panel.model or not client.state.panel.firmware or not client.state.panel.serial:
            result = await client.async_execute("control_get_version_info", timeout_s=timeout_s)
            if result.ok:
                assert_payload_shape("control_get_version_info", result.data)

        if not client.state.inventory.configured_areas_complete:
            configured = await client.async_execute("area_get_configured", timeout_s=timeout_s)
            if not configured.ok and isinstance(configured.error, AuthorizationRequired):
                area_auth_required = True
            if configured.ok:
                assert_payload_shape("area_get_configured", configured.data)
        if not client.state.inventory.configured_zones_complete:
            configured = await client.async_execute("zone_get_configured", timeout_s=timeout_s)
            if not configured.ok and isinstance(configured.error, AuthorizationRequired):
                zone_auth_required = True
            if configured.ok:
                assert_payload_shape("zone_get_configured", configured.data)

        if client.state.inventory.configured_areas:
            for area_id in sorted(client.state.inventory.configured_areas):
                result = await client.async_execute("area_get_attribs", area_id=area_id, timeout_s=timeout_s)
                if not result.ok and isinstance(result.error, AuthorizationRequired):
                    area_auth_required = True
                if result.ok:
                    assert_payload_shape("area_get_attribs", result.data)
        if client.state.inventory.configured_zones:
            for zone_id in sorted(client.state.inventory.configured_zones):
                result = await client.async_execute("zone_get_attribs", zone_id=zone_id, timeout_s=timeout_s)
                if not result.ok and isinstance(result.error, AuthorizationRequired):
                    zone_auth_required = True
                if result.ok:
                    assert_payload_shape("zone_get_attribs", result.data)
        area_names_seen = any(
            getattr(client.state.areas.get(area_id), "name", None)
            for area_id in client.state.inventory.configured_areas
        )
        zone_names_seen = any(
            getattr(client.state.zones.get(zone_id), "name", None)
            for zone_id in client.state.inventory.configured_zones
        )

        inv = client.state.inventory
        panel_info = client.state.panel
        now = time.monotonic()
        if (not panel_info.model or not panel_info.firmware or not panel_info.serial) and now >= next_version_retry:
            result = await client.async_execute("control_get_version_info", timeout_s=timeout_s)
            if result.ok:
                assert_payload_shape("control_get_version_info", result.data)
            next_version_retry = time.monotonic() + 5.0
        panel_ready = bool(panel_info.model) and bool(panel_info.firmware) and bool(panel_info.serial)
        if inv.configured_areas_complete and inv.configured_zones_complete:
            if (inv.configured_areas and area_names_seen) or area_auth_required:
                if (inv.configured_zones and zone_names_seen) or zone_auth_required:
                    if panel_ready:
                        break
        if now >= next_debug:
            elapsed = now - start
            print(
                "debug loop: "
                f"elapsed={elapsed:.1f}s "
                f"panel_ready={panel_ready} "
                f"areas_complete={inv.configured_areas_complete} "
                f"zones_complete={inv.configured_zones_complete} "
                f"area_names_seen={area_names_seen} "
                f"zone_names_seen={zone_names_seen}"
            )
            next_debug = now + 5.0

    panel_info = client.state.panel
    print(f"panel model={panel_info.model or 'MISSING'}")
    print(f"panel firmware={panel_info.firmware or 'MISSING'}")
    print(f"panel serial={panel_info.serial or 'MISSING'}")

    inv = client.state.inventory
    if area_auth_required:
        print("area_attribs=MISSING (auth required)")
    if not inv.configured_areas and area_auth_required:
        print("configured_areas=MISSING (auth required)")
    else:
        print(f"configured_areas={len(inv.configured_areas)}")
        for area_id in sorted(inv.configured_areas):
            area = client.state.areas.get(area_id)
            name = getattr(area, "name", None) if area is not None else None
            print(f"area[{area_id}].name={name or 'MISSING'}")

    if zone_auth_required:
        print("zone_attribs=MISSING (auth required)")
    if not inv.configured_zones and zone_auth_required:
        print("configured_zones=MISSING (auth required)")
    else:
        print(f"configured_zones={len(inv.configured_zones)}")
        for zone_id in sorted(inv.configured_zones):
            zone = client.state.zones.get(zone_id)
            name = getattr(zone, "name", None) if zone is not None else None
            print(f"zone[{zone_id}].name={name or 'MISSING'}")

    await client.async_disconnect()

    if not (panel_info.model and panel_info.firmware and panel_info.serial):
        pytest.fail("Panel version info missing (model/firmware/serial).")

    if inv.configured_zone_blocks_remaining not in (None, 0):
        pytest.fail(f"Zone configured blocks remaining: {inv.configured_zone_blocks_remaining}")
    if inv.configured_area_blocks_remaining not in (None, 0):
        pytest.fail(f"Area configured blocks remaining: {inv.configured_area_blocks_remaining}")

    if area_auth_required or zone_auth_required:
        pytest.skip("Configured inventory requires authorization.")

    missing_zone_names = [
        zone_id
        for zone_id in sorted(inv.configured_zones)
        if not getattr(client.state.zones.get(zone_id), "name", None)
    ]
    if missing_zone_names:
        pytest.fail(f"Missing zone names for ids: {missing_zone_names}")

    missing_area_names = [
        area_id
        for area_id in sorted(inv.configured_areas)
        if not getattr(elk.state.areas.get(area_id), "name", None)
    ]
    if missing_area_names:
        pytest.fail(f"Missing area names for ids: {missing_area_names}")
