import pytest

from elke27_lib.client import Elke27Client
from elke27_lib.events import (
    AreaConfiguredInventoryReady,
    AreaStatusUpdated,
    OutputConfiguredInventoryReady,
    OutputStatusUpdated,
    UNSET_AT,
    UNSET_CLASSIFICATION,
    UNSET_ROUTE,
    UNSET_SEQ,
    UNSET_SESSION_ID,
    ZoneConfiguredInventoryReady,
    ZoneStatusUpdated,
)
from elke27_lib.states import PanelState


class _FakeKernel:
    def __init__(self) -> None:
        self.state = PanelState()
        self.state.panel.session_id = 1
        self.state.table_info_by_domain = {
            "area": {"table_elements": 1},
            "zone": {"table_elements": 1},
            "output": {"table_elements": 1},
            "tstat": {"table_elements": 1},
        }
        self.requests: list[tuple[tuple[str, str], dict]] = []

    @property
    def ready(self) -> bool:
        return True

    def request(self, route, **kwargs):
        self.requests.append((route, kwargs))

    def subscribe(self, callback, kinds=None):
        return 1

    def drain_events(self):
        return []

    def iter_events(self):
        return iter(())


def _inventory_ready_event(evt_cls):
    return evt_cls(
        kind=evt_cls.KIND,
        at=UNSET_AT,
        seq=UNSET_SEQ,
        classification=UNSET_CLASSIFICATION,
        route=UNSET_ROUTE,
        session_id=UNSET_SESSION_ID,
    )


@pytest.mark.asyncio
async def test_wait_ready_requires_inventory_and_status():
    kernel = _FakeKernel()
    client = Elke27Client(kernel=kernel)

    assert await client.wait_ready(timeout_s=0.01) is False

    kernel.state.inventory.configured_areas = {1}
    kernel.state.inventory.configured_zones = {1}
    kernel.state.inventory.configured_outputs = {1}

    client._handle_kernel_event(_inventory_ready_event(AreaConfiguredInventoryReady))
    client._handle_kernel_event(_inventory_ready_event(ZoneConfiguredInventoryReady))
    client._handle_kernel_event(_inventory_ready_event(OutputConfiguredInventoryReady))

    assert await client.wait_ready(timeout_s=0.01) is False

    kernel.state.get_or_create_area(1)
    kernel.state.get_or_create_zone(1)
    kernel.state.get_or_create_output(1)

    client._handle_kernel_event(
        AreaStatusUpdated(
            kind=AreaStatusUpdated.KIND,
            at=UNSET_AT,
            seq=UNSET_SEQ,
            classification=UNSET_CLASSIFICATION,
            route=UNSET_ROUTE,
            session_id=UNSET_SESSION_ID,
            area_id=1,
            changed_fields=("arm_state",),
        )
    )
    client._handle_kernel_event(
        ZoneStatusUpdated(
            kind=ZoneStatusUpdated.KIND,
            at=UNSET_AT,
            seq=UNSET_SEQ,
            classification=UNSET_CLASSIFICATION,
            route=UNSET_ROUTE,
            session_id=UNSET_SESSION_ID,
            zone_id=1,
            changed_fields=("violated",),
        )
    )
    client._handle_kernel_event(
        OutputStatusUpdated(
            kind=OutputStatusUpdated.KIND,
            at=UNSET_AT,
            seq=UNSET_SEQ,
            classification=UNSET_CLASSIFICATION,
            route=UNSET_ROUTE,
            session_id=UNSET_SESSION_ID,
            output_id=1,
            status="ON",
            on=True,
        )
    )

    assert await client.wait_ready(timeout_s=0.1) is True


def test_bootstrap_populates_snapshot_maps():
    kernel = _FakeKernel()
    client = Elke27Client(kernel=kernel)

    kernel.state.inventory.configured_areas = {1}
    kernel.state.inventory.configured_zones = {1}
    kernel.state.inventory.configured_outputs = {1}

    client._handle_kernel_event(_inventory_ready_event(AreaConfiguredInventoryReady))
    client._handle_kernel_event(_inventory_ready_event(ZoneConfiguredInventoryReady))
    client._handle_kernel_event(_inventory_ready_event(OutputConfiguredInventoryReady))

    assert (("area", "get_status"), {"area_id": 1}) in kernel.requests
    assert (("zone", "get_status"), {"zone_id": 1}) in kernel.requests
    assert (("output", "get_status"), {"output_id": 1}) in kernel.requests

    kernel.state.get_or_create_area(1)
    kernel.state.get_or_create_zone(1)
    kernel.state.get_or_create_output(1)

    client._handle_kernel_event(
        AreaStatusUpdated(
            kind=AreaStatusUpdated.KIND,
            at=UNSET_AT,
            seq=UNSET_SEQ,
            classification=UNSET_CLASSIFICATION,
            route=UNSET_ROUTE,
            session_id=UNSET_SESSION_ID,
            area_id=1,
            changed_fields=("arm_state",),
        )
    )
    client._handle_kernel_event(
        ZoneStatusUpdated(
            kind=ZoneStatusUpdated.KIND,
            at=UNSET_AT,
            seq=UNSET_SEQ,
            classification=UNSET_CLASSIFICATION,
            route=UNSET_ROUTE,
            session_id=UNSET_SESSION_ID,
            zone_id=1,
            changed_fields=("violated",),
        )
    )
    client._handle_kernel_event(
        OutputStatusUpdated(
            kind=OutputStatusUpdated.KIND,
            at=UNSET_AT,
            seq=UNSET_SEQ,
            classification=UNSET_CLASSIFICATION,
            route=UNSET_ROUTE,
            session_id=UNSET_SESSION_ID,
            output_id=1,
            status="ON",
            on=True,
        )
    )

    assert client.snapshot.areas
    assert client.snapshot.zones
    assert client.snapshot.outputs
