from __future__ import annotations

import pytest

from elke27_lib.client import Elke27Client
from elke27_lib.linking import E27Identity
from elke27_lib.types import LinkKeys
from test.conftest import LiveCredentials, get_env


@pytest.mark.live_e27
async def test_live_link_only() -> None:
    if get_env("ELKE27_LIVE") != "1":
        pytest.skip("ELKE27_LIVE not set; skipping live E27 tests.")

    host = get_env("ELKE27_HOST")
    access_code = get_env("ELKE27_ACCESS_CODE")
    passphrase = get_env("ELKE27_PASSPHRASE")
    if not host or not access_code or not passphrase:
        pytest.skip("Missing E27 live env vars; source ~/elk-e27-env-vars.sh")

    port = int(get_env("ELKE27_PORT") or "2101")
    identity = E27Identity(
        mn=get_env("ELKE27_MN") or "CODEx",
        sn=get_env("ELKE27_SN") or "LIVE",
        fwver=get_env("ELKE27_FWVER") or "0",
        hwver=get_env("ELKE27_HWVER") or "0",
        osver=get_env("ELKE27_OSVER") or "0",
    )
    creds = LiveCredentials(access_code=access_code, passphrase=passphrase)

    client = Elke27Client()
    try:
        link_keys = await client.async_link(
            host=host,
            port=port,
            access_code=creds.access_code,
            passphrase=creds.passphrase,
            client_identity=identity,
        )
    finally:
        await client.async_disconnect()

    assert isinstance(link_keys, LinkKeys)
    assert link_keys.tempkey_hex
    assert link_keys.linkkey_hex
    assert link_keys.linkhmac_hex
