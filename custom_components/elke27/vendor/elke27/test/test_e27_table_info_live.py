from __future__ import annotations

import logging
import os
from dataclasses import dataclass

import pytest

from elke27_lib import linking
from elke27_lib.client import Elke27Client
from test.helpers.payload_validation import assert_payload_shape


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name, default)
    if value == "":
        return default
    return value


def _require_env(name: str) -> str:
    value = _env(name)
    if not value:
        pytest.skip(f"Missing {name} for live table_info tests.")
    return value


@dataclass(frozen=True, slots=True)
class _Credentials:
    access_code: str
    passphrase: str


@pytest.mark.integration
@pytest.mark.asyncio
async def test_live_table_info_calls() -> None:
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
    timeout_s = float(_env("ELKE27_TIMEOUT_S", "10.0") or 10.0)

    client_identity = linking.E27Identity(mn=mn, sn=sn, fwver=fwver, hwver=hwver, osver=osver)
    client = Elke27Client(
        features=[
            "elke27_lib.features.area",
            "elke27_lib.features.zone",
            "elke27_lib.features.output",
            "elke27_lib.features.tstat",
        ]
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

    area_info = await client.async_execute("area_get_table_info", timeout_s=timeout_s)
    if not area_info.ok or area_info.data is None:
        await client.async_disconnect()
        pytest.fail(f"area_get_table_info failed: {area_info.error}")
    assert_payload_shape("area_get_table_info", area_info.data)
    print(
        f"Area table: elements={area_info.data.get('table_elements')} "
        f"increment={area_info.data.get('increment_size')}"
    )

    zone_info = await client.async_execute("zone_get_table_info", timeout_s=timeout_s)
    if not zone_info.ok or zone_info.data is None:
        await client.async_disconnect()
        pytest.fail(f"zone_get_table_info failed: {zone_info.error}")
    assert_payload_shape("zone_get_table_info", zone_info.data)
    print(
        f"Zone table: elements={zone_info.data.get('table_elements')} "
        f"increment={zone_info.data.get('increment_size')}"
    )

    output_info = await client.async_execute("output_get_table_info", timeout_s=timeout_s)
    if not output_info.ok or output_info.data is None:
        await client.async_disconnect()
        pytest.fail(f"output_get_table_info failed: {output_info.error}")
    assert_payload_shape("output_get_table_info", output_info.data)
    print(
        f"Output table: elements={output_info.data.get('table_elements')} "
        f"increment={output_info.data.get('increment_size')}"
    )

    tstat_info = await client.async_execute("tstat_get_table_info", timeout_s=timeout_s)
    if not tstat_info.ok or tstat_info.data is None:
        await client.async_disconnect()
        pytest.fail(f"tstat_get_table_info failed: {tstat_info.error}")
    assert_payload_shape("tstat_get_table_info", tstat_info.data)
    print(
        f"Tstat table: elements={tstat_info.data.get('table_elements')} "
        f"increment={tstat_info.data.get('increment_size')}"
    )

    await client.async_disconnect()
