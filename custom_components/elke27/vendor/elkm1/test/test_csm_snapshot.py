from __future__ import annotations

from elke27_lib.events import (
    CsmSnapshotUpdated,
    DomainCsmChanged,
    TableCsmChanged,
    ZoneTableInfoUpdated,
)
from elke27_lib.handlers.control import make_control_authenticate_handler
from elke27_lib.handlers.zone import make_zone_get_table_info_handler
from elke27_lib.states import PanelState


class _EmitSpy:
    def __init__(self) -> None:
        self.events = []

    def __call__(self, evt, ctx) -> None:
        self.events.append(evt)


class _Ctx:
    response_match = None


def test_authenticate_updates_domain_csm_and_snapshot() -> None:
    state = PanelState()
    emit = _EmitSpy()
    handler = make_control_authenticate_handler(state, emit, now=lambda: 123.0)

    msg = {
        "control": {
            "authenticate": {
                "error_code": 0,
                "zone_csm": 10,
                "area_csm": "11",
            }
        }
    }
    assert handler(msg, _Ctx()) is True
    assert state.domain_csm_by_name == {"zone": 10, "area": 11}
    assert state.csm_snapshot is not None
    assert state.csm_snapshot.version == 1

    domain_events = [evt for evt in emit.events if isinstance(evt, DomainCsmChanged)]
    snapshot_events = [evt for evt in emit.events if isinstance(evt, CsmSnapshotUpdated)]

    assert {evt.domain for evt in domain_events} == {"zone", "area"}
    assert len(snapshot_events) == 1


def test_table_info_csm_changes_emit_snapshot_once() -> None:
    state = PanelState()
    emit = _EmitSpy()
    handler = make_zone_get_table_info_handler(state, emit, now=lambda: 123.0)

    msg = {
        "zone": {
            "get_table_info": {
                "table_elements": 5,
                "increment_size": 1,
                "table_csm": 7,
            }
        }
    }
    assert handler(msg, _Ctx()) is True
    assert state.table_csm_by_domain["zone"] == 7
    assert state.csm_snapshot is not None
    assert state.csm_snapshot.version == 1

    table_events = [evt for evt in emit.events if isinstance(evt, TableCsmChanged)]
    snapshot_events = [evt for evt in emit.events if isinstance(evt, CsmSnapshotUpdated)]
    table_info_events = [evt for evt in emit.events if isinstance(evt, ZoneTableInfoUpdated)]
    assert len(table_events) == 1
    assert len(snapshot_events) == 1
    assert table_info_events[0].table_csm == 7

    emit.events.clear()
    assert handler(msg, _Ctx()) is True
    table_events = [evt for evt in emit.events if isinstance(evt, TableCsmChanged)]
    snapshot_events = [evt for evt in emit.events if isinstance(evt, CsmSnapshotUpdated)]
    table_info_events = [evt for evt in emit.events if isinstance(evt, ZoneTableInfoUpdated)]
    assert len(table_events) == 0
    assert len(snapshot_events) == 0
    assert table_info_events[0].table_csm == 7
