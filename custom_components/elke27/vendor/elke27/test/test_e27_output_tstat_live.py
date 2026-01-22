from __future__ import annotations

import logging
import os
from dataclasses import dataclass

import pytest

from elke27_lib import linking
from elke27_lib.client import Elke27Client
from elke27_lib.const import E27ErrorCode
from test.helpers.error_codes import extract_error_code
from test.helpers.payload_validation import assert_payload_shape


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name, default)
    if value == "":
        return default
    return value


def _require_env(name: str) -> str:
    value = _env(name)
    if not value:
        pytest.skip(f"Missing {name} for live output/tstat tests.")
    return value


@dataclass(frozen=True, slots=True)
class _Credentials:
    access_code: str
    passphrase: str


@pytest.mark.integration
@pytest.mark.asyncio
async def test_live_output_and_tstat_status() -> None:
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
    output_id = int(_env("ELKE27_OUTPUT_ID", "1") or 1)
    tstat_id = int(_env("ELKE27_TSTAT_ID", "1") or 1)

    client_identity = linking.E27Identity(mn=mn, sn=sn, fwver=fwver, hwver=hwver, osver=osver)
    client = Elke27Client(features=["elke27_lib.features.output", "elke27_lib.features.tstat"])

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

    output_result = await client.async_execute(
        "output_get_status",
        output_id=output_id,
        timeout_s=timeout_s,
    )
    if not output_result.ok:
        await client.async_disconnect()
        pytest.fail(f"output_get_status failed: {output_result.error}")
    assert_payload_shape("output_get_status", output_result.data)
    output = client.outputs.get(output_id)
    print(f"Output {output_id} status: {getattr(output, 'status', None)}")

    bulk_result = await client.async_execute("output_get_all_outputs_status", timeout_s=timeout_s)
    if not bulk_result.ok:
        await client.async_disconnect()
        pytest.fail(f"output_get_all_outputs_status failed: {bulk_result.error}")
    assert_payload_shape("output_get_all_outputs_status", bulk_result.data)
    print("Outputs bulk status received.")

    tstat_result = await client.async_execute(
        "tstat_get_status",
        tstat_id=tstat_id,
        timeout_s=timeout_s,
    )
    if not tstat_result.ok:
        if extract_error_code(tstat_result.error) == E27ErrorCode.ELKERR_INVALID_ID:
            print("tstat.get_status returned invalid_id (no thermostats installed).")
            await client.async_disconnect()
            return
        await client.async_disconnect()
        pytest.fail(f"tstat.get_status failed: {tstat_result.error}")
    assert_payload_shape("tstat_get_status", tstat_result.data)
    tstat = client.thermostats.get(tstat_id)
    print(
        f"Tstat {tstat_id} temp: {getattr(tstat, 'temperature', None)} mode={getattr(tstat, 'mode', None)}"
    )

    await client.async_disconnect()
