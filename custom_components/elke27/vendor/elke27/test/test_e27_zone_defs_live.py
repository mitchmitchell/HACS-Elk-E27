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
        pytest.skip(f"Missing {name} for live zone defs test.")
    return value


@dataclass(frozen=True, slots=True)
class _Credentials:
    access_code: str
    passphrase: str


@pytest.mark.integration
@pytest.mark.asyncio
async def test_live_zone_defs_and_flags() -> None:
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
    client = Elke27Client(features=["elke27_lib.features.zone"])

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

    defs_result = await client.async_execute("zone_get_defs", block_id=1, timeout_s=timeout_s)
    if not defs_result.ok:
        await client.async_disconnect()
        pytest.fail(f"zone_get_defs failed: {defs_result.error}")
    assert_payload_shape("zone_get_defs", defs_result.data)
    if not client.state.zone_defs_by_id:
        await client.async_disconnect()
        pytest.fail("zone_get_defs returned no definitions.")

    definition = _first_definition(client)
    if not definition:
        await client.async_disconnect()
        pytest.skip("No zone definitions available to query flags.")

    flags_result = await client.async_execute(
        "zone_get_def_flags", definition=definition, timeout_s=timeout_s
    )
    if not flags_result.ok:
        await client.async_disconnect()
        pytest.fail(f"zone_get_def_flags failed: {flags_result.error}")
    assert_payload_shape("zone_get_def_flags", flags_result.data)
    if definition not in client.state.zone_def_flags_by_name:
        await client.async_disconnect()
        pytest.fail("zone_get_def_flags returned no flag data.")

    await client.async_disconnect()


def _first_definition(client: Elke27Client) -> str | None:
    for entry in client.state.zone_defs_by_id.values():
        definition = entry.get("definition")
        if isinstance(definition, str) and definition.strip():
            return definition
    return None
