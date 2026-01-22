import asyncio

import pytest

from elke27_lib import linking
from elke27_lib.client import Elke27Client
from elke27_lib.events import (
    UNSET_AT,
    UNSET_CLASSIFICATION,
    UNSET_ROUTE,
    UNSET_SEQ,
    UNSET_SESSION_ID,
    AreaConfiguredInventoryReady,
    OutputConfiguredInventoryReady,
    ZoneConfiguredInventoryReady,
)
from elke27_lib.session import Session, SessionConfig, SessionState
from test.helpers.internal import get_kernel, get_private, set_private


@pytest.mark.asyncio
async def test_wait_ready_times_out_when_not_ready() -> None:
    client = Elke27Client()

    result = await client.wait_ready(timeout_s=0.05)

    assert result is False


@pytest.mark.asyncio
async def test_wait_ready_returns_true_after_ready_signal() -> None:
    client = Elke27Client()

    task = asyncio.create_task(client.wait_ready(timeout_s=0.5))
    await asyncio.sleep(0)

    identity = linking.E27Identity(mn="1", sn="1", fwver="1", hwver="1", osver="1")
    session = Session(
        SessionConfig(host="localhost", port=1),
        client_identity=identity,
        link_key_hex="00",
    )
    session.state = SessionState.ACTIVE
    kernel = get_kernel(client)
    set_private(kernel, "_session", session)
    client.state.panel.session_id = 1
    client.state.table_info_by_domain = {
        "area": {"table_elements": 1},
        "zone": {"table_elements": 1},
        "output": {"table_elements": 1},
        "tstat": {"table_elements": 1},
    }

    handle_event = get_private(client, "_handle_kernel_event")
    handle_event(
        AreaConfiguredInventoryReady(
            kind=AreaConfiguredInventoryReady.KIND,
            at=UNSET_AT,
            seq=UNSET_SEQ,
            classification=UNSET_CLASSIFICATION,
            route=UNSET_ROUTE,
            session_id=UNSET_SESSION_ID,
        )
    )
    handle_event(
        ZoneConfiguredInventoryReady(
            kind=ZoneConfiguredInventoryReady.KIND,
            at=UNSET_AT,
            seq=UNSET_SEQ,
            classification=UNSET_CLASSIFICATION,
            route=UNSET_ROUTE,
            session_id=UNSET_SESSION_ID,
        )
    )
    handle_event(
        OutputConfiguredInventoryReady(
            kind=OutputConfiguredInventoryReady.KIND,
            at=UNSET_AT,
            seq=UNSET_SEQ,
            classification=UNSET_CLASSIFICATION,
            route=UNSET_ROUTE,
            session_id=UNSET_SESSION_ID,
        )
    )

    assert await task is True
