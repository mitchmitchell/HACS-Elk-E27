from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from elke27_lib import (
    ClientConfig,
    Elke27Client,
    Elke27Event,
    EventType,
    PanelInfo,
    PanelSnapshot,
    TableInfo,
)
from elke27_lib.events import (
    UNSET_AT,
    UNSET_CLASSIFICATION,
    UNSET_ROUTE,
    UNSET_SEQ,
    UNSET_SESSION_ID,
    AreaStatusUpdated,
    CsmSnapshotUpdated,
    DomainCsmChanged,
    TableCsmChanged,
)
from elke27_lib.handlers.zone import (
    make_zone_get_attribs_handler,
    make_zone_get_defs_handler,
)
from elke27_lib.types import CsmSnapshot


def _make_event(seq: int) -> Elke27Event:
    return Elke27Event(
        event_type=EventType.SYSTEM,
        data={"seq": seq},
        seq=seq,
        timestamp=datetime.now(UTC),
        raw_type="test",
    )


def test_snapshot_atomic_replacement_and_version() -> None:
    client = Elke27Client()
    initial = client.snapshot

    client._replace_snapshot(panel_info=PanelInfo(model="M1"), table_info=TableInfo())
    snap1 = client.snapshot
    client._replace_snapshot(panel_info=PanelInfo(model="M2"))
    snap2 = client.snapshot

    assert isinstance(initial, PanelSnapshot)
    assert initial.version == 0
    assert initial.zone_definitions == {}
    assert initial.output_definitions == {}
    assert snap1.version == 1
    assert snap2.version == 2
    assert initial.panel.model is None
    assert snap1.panel.model == "M1"
    assert snap2.panel.model == "M2"


def test_event_queue_drop_oldest() -> None:
    client = Elke27Client(config=ClientConfig(event_queue_size=2))
    evt1 = _make_event(1)
    evt2 = _make_event(2)
    evt3 = _make_event(3)

    client._enqueue_event(evt1)
    client._enqueue_event(evt2)
    client._enqueue_event(evt3)

    assert client._event_queue.qsize() == 2
    first = client._event_queue.get_nowait()
    second = client._event_queue.get_nowait()
    assert first.seq == 2
    assert second.seq == 3


@pytest.mark.asyncio
async def test_events_iterator_terminates_on_disconnect() -> None:
    client = Elke27Client(config=ClientConfig(event_queue_size=2))
    evt = _make_event(1)
    client._enqueue_event(evt)
    client._signal_event_stream_end()

    seen = []
    async for item in client.events():
        seen.append(item)

    assert len(seen) == 1
    assert seen[0].seq == 1


def test_subscriber_callback_exception_does_not_break_queue() -> None:
    client = Elke27Client(config=ClientConfig(event_queue_size=2))

    def _bad_callback(evt: Elke27Event) -> None:
        raise RuntimeError("boom")

    client.subscribe(_bad_callback)

    evt = AreaStatusUpdated(
        kind=AreaStatusUpdated.KIND,
        at=UNSET_AT,
        seq=UNSET_SEQ,
        classification=UNSET_CLASSIFICATION,
        route=UNSET_ROUTE,
        session_id=UNSET_SESSION_ID,
        area_id=1,
        changed_fields=(),
    )
    client._handle_kernel_event(evt)

    queued = client._event_queue.get_nowait()
    assert isinstance(queued, Elke27Event)
    json.dumps(queued.data)


def test_typed_subscriber_receives_event() -> None:
    client = Elke27Client(config=ClientConfig(event_queue_size=2))
    seen = []

    def _typed_cb(evt) -> None:
        seen.append(evt)

    client.subscribe_typed(_typed_cb)

    evt = AreaStatusUpdated(
        kind=AreaStatusUpdated.KIND,
        at=UNSET_AT,
        seq=UNSET_SEQ,
        classification=UNSET_CLASSIFICATION,
        route=UNSET_ROUTE,
        session_id=UNSET_SESSION_ID,
        area_id=1,
        changed_fields=(),
    )
    client._handle_kernel_event(evt)
    assert len(seen) == 1
    assert isinstance(seen[0], AreaStatusUpdated)


def test_typed_subscriber_receives_csm_events() -> None:
    client = Elke27Client(config=ClientConfig(event_queue_size=2))
    seen = []

    def _typed_cb(evt) -> None:
        seen.append(evt)

    client.subscribe_typed(_typed_cb)
    snapshot = CsmSnapshot(
        domain_csms={"zone": 1},
        table_csms={"zone": 2},
        version=1,
        updated_at=datetime.now(UTC),
    )

    events = [
        DomainCsmChanged(
            kind=DomainCsmChanged.KIND,
            at=UNSET_AT,
            seq=UNSET_SEQ,
            classification=UNSET_CLASSIFICATION,
            route=UNSET_ROUTE,
            session_id=UNSET_SESSION_ID,
            domain="zone",
            old=None,
            new=1,
        ),
        TableCsmChanged(
            kind=TableCsmChanged.KIND,
            at=UNSET_AT,
            seq=UNSET_SEQ,
            classification=UNSET_CLASSIFICATION,
            route=UNSET_ROUTE,
            session_id=UNSET_SESSION_ID,
            domain="zone",
            old=None,
            new=2,
        ),
        CsmSnapshotUpdated(
            kind=CsmSnapshotUpdated.KIND,
            at=UNSET_AT,
            seq=UNSET_SEQ,
            classification=UNSET_CLASSIFICATION,
            route=UNSET_ROUTE,
            session_id=UNSET_SESSION_ID,
            snapshot=snapshot,
        ),
    ]
    for evt in events:
        client._handle_kernel_event(evt)

    assert [type(item) for item in seen] == [type(evt) for evt in events]


def test_area_bypass_count_zero_refreshes_bypassed_zones() -> None:
    client = Elke27Client(config=ClientConfig(event_queue_size=2))
    state = client._kernel.state
    area = state.get_or_create_area(1)
    area.num_bypassed_zones = 0
    zone = state.get_or_create_zone(33)
    zone.area_id = 1
    zone.bypassed = True

    recorded: list[tuple[tuple[str, str], dict]] = []

    def _fake_request(route, **kwargs):
        recorded.append((route, dict(kwargs)))
        return 1

    client._kernel.request = _fake_request  # type: ignore[assignment]
    client._kernel.requests.get = lambda route: _fake_request  # type: ignore[assignment]

    evt = AreaStatusUpdated(
        kind=AreaStatusUpdated.KIND,
        at=UNSET_AT,
        seq=UNSET_SEQ,
        classification=UNSET_CLASSIFICATION,
        route=UNSET_ROUTE,
        session_id=UNSET_SESSION_ID,
        area_id=1,
        changed_fields=("num_bypassed_zones",),
    )
    client._handle_kernel_event(evt)

    assert (("zone", "get_all_zones_status"), {}) in recorded


def test_area_bypass_count_nonzero_refreshes_non_bypassed_zones() -> None:
    client = Elke27Client(config=ClientConfig(event_queue_size=2))
    state = client._kernel.state
    area = state.get_or_create_area(1)
    area.num_bypassed_zones = 1
    zone_bypassed = state.get_or_create_zone(33)
    zone_bypassed.area_id = 1
    zone_bypassed.bypassed = True
    zone_open = state.get_or_create_zone(34)
    zone_open.area_id = 1
    zone_open.bypassed = False

    recorded: list[tuple[tuple[str, str], dict]] = []

    def _fake_request(route, **kwargs):
        recorded.append((route, dict(kwargs)))
        return 1

    client._kernel.request = _fake_request  # type: ignore[assignment]
    client._kernel.requests.get = lambda route: _fake_request  # type: ignore[assignment]

    evt = AreaStatusUpdated(
        kind=AreaStatusUpdated.KIND,
        at=UNSET_AT,
        seq=UNSET_SEQ,
        classification=UNSET_CLASSIFICATION,
        route=UNSET_ROUTE,
        session_id=UNSET_SESSION_ID,
        area_id=1,
        changed_fields=("num_bypassed_zones",),
    )
    client._handle_kernel_event(evt)

    assert (("zone", "get_all_zones_status"), {}) in recorded


def test_area_bypass_refresh_suppressed_after_local_command() -> None:
    client = Elke27Client(config=ClientConfig(event_queue_size=2))
    state = client._kernel.state
    area = state.get_or_create_area(1)
    area.num_bypassed_zones = 1
    zone = state.get_or_create_zone(33)
    zone.area_id = 1

    recorded: list[tuple[tuple[str, str], dict]] = []

    def _fake_request(route, **kwargs):
        recorded.append((route, dict(kwargs)))
        return 1

    client._kernel.request = _fake_request  # type: ignore[assignment]
    client._kernel.requests.get = lambda route: _fake_request  # type: ignore[assignment]
    client._kernel.now = lambda: 100.0  # type: ignore[assignment]
    client._record_local_zone_bypass(33)

    evt = AreaStatusUpdated(
        kind=AreaStatusUpdated.KIND,
        at=UNSET_AT,
        seq=UNSET_SEQ,
        classification=UNSET_CLASSIFICATION,
        route=UNSET_ROUTE,
        session_id=UNSET_SESSION_ID,
        area_id=1,
        changed_fields=("num_bypassed_zones",),
    )
    client._handle_kernel_event(evt)

    assert recorded == []


def test_area_status_no_change_refreshes_bulk_status() -> None:
    client = Elke27Client(config=ClientConfig(event_queue_size=2))
    recorded: list[tuple[tuple[str, str], dict]] = []

    def _fake_request(route, **kwargs):
        recorded.append((route, dict(kwargs)))
        return 1

    client._kernel.request = _fake_request  # type: ignore[assignment]
    client._kernel.requests.get = lambda route: _fake_request  # type: ignore[assignment]

    evt = AreaStatusUpdated(
        kind=AreaStatusUpdated.KIND,
        at=UNSET_AT,
        seq=UNSET_SEQ,
        classification=UNSET_CLASSIFICATION,
        route=UNSET_ROUTE,
        session_id=UNSET_SESSION_ID,
        area_id=1,
        changed_fields=(),
    )
    client._handle_kernel_event(evt)

    assert (("zone", "get_all_zones_status"), {}) in recorded


def test_snapshot_includes_zone_definitions() -> None:
    client = Elke27Client(config=ClientConfig(event_queue_size=2))
    state = client._kernel.state
    zone = state.get_or_create_zone(1)
    zone.name = "Front Door"
    zone.definition = 1
    zone.attribs["zone_type"] = "Window"
    zone.attribs["kind"] = "Door"
    state.zone_defs_by_id[1] = {"definition": "BURG PERIM INST"}

    client._replace_snapshot(
        panel_info=client._build_panel_info(),
        table_info=client._build_table_info(),
        areas=client._build_area_map(),
        zones=client._build_zone_map(),
        zone_definitions=client._build_zone_definitions(),
        outputs=client._build_output_map(),
        output_definitions=client._build_output_definitions(),
    )
    snapshot = client.snapshot
    zone_def = snapshot.zone_definitions[1]
    assert zone_def.name == "Front Door"
    assert zone_def.definition == "BURG PERIM INST"
    assert zone_def.zone_type == "Window"
    assert zone_def.kind == "Door"


def test_zone_attribs_handler_updates_snapshot_definitions() -> None:
    client = Elke27Client(config=ClientConfig(event_queue_size=2))
    state = client._kernel.state
    emitted = []

    def _emit(evt, ctx) -> None:
        emitted.append(evt)

    handler = make_zone_get_attribs_handler(state, _emit, now=lambda: 123.0)

    class _Ctx:
        pass

    msg = {
        "zone": {
            "get_attribs": {
                "zone_id": 1,
                "name": "Front",
                "definition": "BURG PERIM INST",
                "zone_type": "Window",
            }
        }
    }
    assert handler(msg, _Ctx()) is True
    for evt in emitted:
        client._handle_kernel_event(evt)

    zone_def = client.snapshot.zone_definitions[1]
    assert zone_def.name == "Front"
    assert zone_def.definition == "BURG PERIM INST"
    assert zone_def.zone_type == "Window"


def test_refresh_zone_config_requests_definitions_and_attribs() -> None:
    client = Elke27Client(config=ClientConfig(event_queue_size=2))
    state = client._kernel.state
    state.inventory.configured_zones = {1, 2}
    recorded: list[tuple[tuple[str, str], dict]] = []

    def _fake_request(route, **kwargs):
        recorded.append((route, dict(kwargs)))
        return 1

    client._kernel.request = _fake_request  # type: ignore[assignment]
    client._kernel.requests.get = lambda route: _fake_request  # type: ignore[assignment]
    client._refresh_zone_config()

    assert ("zone", "get_table_info") in [route for route, _ in recorded]
    assert ("zone", "get_configured") in [route for route, _ in recorded]
    assert ("zone", "get_defs") in [route for route, _ in recorded]
    attrib_routes = [route for route, _ in recorded if route == ("zone", "get_attribs")]
    assert len(attrib_routes) == 2


def test_bootstrap_zone_definitions_not_none() -> None:
    client = Elke27Client(config=ClientConfig(event_queue_size=2))
    state = client._kernel.state
    emitted = []

    def _emit(evt, ctx) -> None:
        emitted.append(evt)

    defs_handler = make_zone_get_defs_handler(state, _emit, now=lambda: 123.0)
    attribs_handler = make_zone_get_attribs_handler(state, _emit, now=lambda: 123.0)

    class _Ctx:
        pass

    defs_msg = {"zone": {"get_defs": {"definitions": ["BURG PERIM INST"], "block_id": 1}}}
    attribs_msg = {
        "zone": {
            "get_attribs": {
                "zone_id": 1,
                "name": "Front",
                "definition": "BURG PERIM INST",
            }
        }
    }
    assert defs_handler(defs_msg, _Ctx()) is True
    assert attribs_handler(attribs_msg, _Ctx()) is True
    for evt in emitted:
        client._handle_kernel_event(evt)

    zone_def = client.snapshot.zone_definitions[1]
    assert zone_def.definition is not None


def test_refresh_zone_config_updates_snapshot_after_handlers() -> None:
    client = Elke27Client(config=ClientConfig(event_queue_size=2))
    state = client._kernel.state
    emitted = []

    def _emit(evt, ctx) -> None:
        emitted.append(evt)

    defs_handler = make_zone_get_defs_handler(state, _emit, now=lambda: 123.0)
    attribs_handler = make_zone_get_attribs_handler(state, _emit, now=lambda: 123.0)

    class _Ctx:
        pass

    defs_msg = {"zone": {"get_defs": {"definitions": ["BURG PERIM INST"], "block_id": 1}}}
    attribs_msg = {
        "zone": {
            "get_attribs": {
                "zone_id": 1,
                "name": "Front",
                "definition": "BURG PERIM INST",
                "zone_type": "Window",
            }
        }
    }
    assert defs_handler(defs_msg, _Ctx()) is True
    assert attribs_handler(attribs_msg, _Ctx()) is True
    for evt in emitted:
        client._handle_kernel_event(evt)

    zone_def = client.snapshot.zone_definitions[1]
    assert zone_def.name == "Front"
    assert zone_def.definition == "BURG PERIM INST"
    assert zone_def.zone_type == "Window"
