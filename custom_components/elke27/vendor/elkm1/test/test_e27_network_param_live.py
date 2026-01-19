from __future__ import annotations

import logging
import os
from dataclasses import dataclass

import pytest

from elke27_lib import linking
from elke27_lib.client import Elke27Client
from elke27_lib.errors import AuthorizationRequired, Elke27PinRequiredError
from test.helpers.payload_validation import assert_payload_shape


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name, default)
    if value == "":
        return default
    return value


def _require_env(name: str) -> str:
    value = _env(name)
    if not value:
        pytest.skip(f"Missing {name} for live network tests.")
    return value


@dataclass(frozen=True, slots=True)
class _Credentials:
    access_code: str
    passphrase: str


@pytest.mark.integration
@pytest.mark.asyncio
async def test_live_network_param_getters() -> None:
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
    client = Elke27Client(features=["elke27_lib.features.network_param"])

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

    pin_value = _env("ELKE27_PIN")
    if not pin_value:
        await client.async_disconnect()
        pytest.skip("ELKE27_PIN not set; network_param_get_ssid requires installer PIN.")

    result = await client.async_execute(
        "network_param_get_ssid",
        pin=pin_value,
        timeout_s=timeout_s,
    )

    if not result.ok:
        if isinstance(result.error, AuthorizationRequired):
            print("Authorization required for SSID.")
        elif isinstance(result.error, Elke27PinRequiredError):
            pytest.skip(f"network_param_get_ssid auth not available: {result.error}")
        else:
            await client.async_disconnect()
            pytest.fail(f"network_param_get_ssid failed: {result.error}")
    else:
        assert_payload_shape("network_param_get_ssid", result.data)
        print("network_param_get_ssid succeeded.")

    await client.async_disconnect()
