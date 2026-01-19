"""Live test: fetch configured areas and names."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

import pytest

from elke27_lib import linking
from elke27_lib.client import Elke27Client
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
        pytest.skip(f"Missing {name} for live area info test.")
    return value


@dataclass(frozen=True, slots=True)
class _Credentials:
    access_code: str
    passphrase: str


@pytest.mark.integration
@pytest.mark.asyncio
async def test_live_area_info() -> None:
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

    client_identity = linking.E27Identity(mn=mn, sn=sn, fwver=fwver, hwver=hwver, osver=osver)
    client = Elke27Client(features=["elke27_lib.features.area"])

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

    configured_result = await client.async_execute("area_get_configured", timeout_s=timeout_s)
    if not configured_result.ok:
        await client.async_disconnect()
        pytest.fail(f"area_get_configured failed: {configured_result.error}")
    assert_payload_shape("area_get_configured", configured_result.data)

    inv = client.state.inventory
    if not inv.configured_areas:
        await client.async_disconnect()
        pytest.fail("No configured areas received.")

    for area_id in sorted(inv.configured_areas):
        result = await client.async_execute("area_get_attribs", area_id=area_id, timeout_s=timeout_s)
        if not result.ok:
            if isinstance(result.error, AuthorizationRequired):
                await client.async_disconnect()
                pytest.skip("area_get_attribs requires authorization.")
            await client.async_disconnect()
            pytest.fail(f"area_get_attribs failed: {result.error}")
        assert_payload_shape("area_get_attribs", result.data)
        area = client.state.areas.get(area_id)
        name = getattr(area, "name", None) if area is not None else None
        print(f"area[{area_id}].name={name or 'MISSING'}")

    print(f"configured_areas={len(inv.configured_areas)}")
    await client.async_disconnect()

    missing_names = [
        area_id
        for area_id in sorted(inv.configured_areas)
        if not getattr(client.state.areas.get(area_id), "name", None)
    ]
    if missing_names:
        pytest.fail(f"Missing area names for ids: {missing_names}")

    expected_name = "Area 1"
    area_one = client.state.areas.get(1)
    area_one_name = getattr(area_one, "name", None) if area_one is not None else None
    assert area_one_name == expected_name
