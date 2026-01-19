from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

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
from elke27_lib.events import AreaStatusUpdated, UNSET_AT, UNSET_CLASSIFICATION, UNSET_ROUTE, UNSET_SEQ, UNSET_SESSION_ID


def _make_event(seq: int) -> Elke27Event:
    return Elke27Event(
        event_type=EventType.SYSTEM,
        data={"seq": seq},
        seq=seq,
        timestamp=datetime.now(timezone.utc),
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
