import asyncio
from types import SimpleNamespace

import pytest

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
from elke27_lib.session import SessionState


@pytest.mark.asyncio
async def test_wait_ready_times_out_when_not_ready():
    client = Elke27Client()

    result = await client.wait_ready(timeout_s=0.05)

    assert result is False


@pytest.mark.asyncio
async def test_wait_ready_returns_true_after_ready_signal():
    client = Elke27Client()

    task = asyncio.create_task(client.wait_ready(timeout_s=0.5))
    await asyncio.sleep(0)

    client._kernel._session = SimpleNamespace(state=SessionState.ACTIVE)
    client._kernel.state.panel.session_id = 1
    client._kernel.state.table_info_by_domain = {
        "area": {"table_elements": 1},
        "zone": {"table_elements": 1},
        "output": {"table_elements": 1},
        "tstat": {"table_elements": 1},
    }

    client._handle_kernel_event(
        AreaConfiguredInventoryReady(
            kind=AreaConfiguredInventoryReady.KIND,
            at=UNSET_AT,
            seq=UNSET_SEQ,
            classification=UNSET_CLASSIFICATION,
            route=UNSET_ROUTE,
            session_id=UNSET_SESSION_ID,
        )
    )
    client._handle_kernel_event(
        ZoneConfiguredInventoryReady(
            kind=ZoneConfiguredInventoryReady.KIND,
            at=UNSET_AT,
            seq=UNSET_SEQ,
            classification=UNSET_CLASSIFICATION,
            route=UNSET_ROUTE,
            session_id=UNSET_SESSION_ID,
        )
    )
    client._handle_kernel_event(
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
