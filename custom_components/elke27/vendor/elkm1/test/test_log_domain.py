from __future__ import annotations

import pytest

from elke27_lib.events import CsmSnapshotUpdated, TableCsmChanged
from elke27_lib.generators.log import (
    generator_log_clear,
    generator_log_get_attribs,
    generator_log_get_index,
    generator_log_get_list,
    generator_log_get_log,
    generator_log_get_table_info,
    generator_log_get_trouble,
    generator_log_realloc,
    generator_log_set_attribs,
)
from elke27_lib.handlers.log import (
    make_log_clear_handler,
    make_log_get_attribs_handler,
    make_log_get_index_handler,
    make_log_get_list_handler,
    make_log_get_log_handler,
    make_log_get_table_info_handler,
    make_log_get_trouble_handler,
    make_log_realloc_handler,
    make_log_set_attribs_handler,
)
from elke27_lib.states import PanelState


class _EmitSpy:
    def __init__(self) -> None:
        self.events = []

    def __call__(self, evt, ctx) -> None:
        self.events.append(evt)


class _Ctx:
    classification = "RESPONSE"


def test_log_generators() -> None:
    assert generator_log_get_index() == ({}, ("log", "get_index"))
    assert generator_log_get_table_info() == ({}, ("log", "get_table_info"))
    assert generator_log_get_trouble() == ({}, ("log", "get_trouble"))
    assert generator_log_get_attribs() == ({}, ("log", "get_attribs"))
    assert generator_log_get_log(log_id=1) == ({"log_id": 1}, ("log", "get_log"))
    assert generator_log_get_list(start=500, date=1741704120, cnt=10) == (
        {"start": 500, "date": 1741704120, "cnt": 10},
        ("log", "get_list"),
    )
    with pytest.raises(ValueError, match="log_set_attribs is disabled"):
        generator_log_set_attribs(log_flags={"arm_changed": True})
    with pytest.raises(ValueError, match="log_clear is disabled"):
        generator_log_clear(block_id=0)
    with pytest.raises(ValueError, match="log_realloc is disabled"):
        generator_log_realloc(table_elements=250)


def test_log_get_index_handler_updates_state() -> None:
    state = PanelState()
    emit = _EmitSpy()
    handler = make_log_get_index_handler(state, emit, now=lambda: 123.0)

    msg = {"log": {"get_index": {"newest": 5, "max": 500, "total": 500, "error_code": 0}}}
    assert handler(msg, _Ctx()) is True
    assert state.log_status["get_index"]["newest"] == 5


def test_log_get_table_info_updates_csm() -> None:
    state = PanelState()
    emit = _EmitSpy()
    handler = make_log_get_table_info_handler(state, emit, now=lambda: 123.0)

    msg = {
        "log": {
            "get_table_info": {
                "table_csm": 1177,
                "table_elements": 250,
                "tablesize": 20,
                "increment_size": 2,
                "element_size": 0,
                "error_code": 0,
            }
        }
    }
    assert handler(msg, _Ctx()) is True
    assert state.table_csm_by_domain["log"] == 1177
    assert any(evt.kind == TableCsmChanged.KIND for evt in emit.events)
    assert any(evt.kind == CsmSnapshotUpdated.KIND for evt in emit.events)


def test_log_get_trouble_handler_updates_state() -> None:
    state = PanelState()
    emit = _EmitSpy()
    handler = make_log_get_trouble_handler(state, emit, now=lambda: 123.0)

    msg = {"log": {"get_trouble": {"log_full": True, "error_code": 0}}}
    assert handler(msg, _Ctx()) is True
    assert state.log_status["get_trouble"]["log_full"] is True


def test_log_get_attribs_handler_updates_state() -> None:
    state = PanelState()
    emit = _EmitSpy()
    handler = make_log_get_attribs_handler(state, emit, now=lambda: 123.0)

    msg = {"log": {"get_attribs": {"log_flags": {"arm_changed": True}, "error_code": 0}}}
    assert handler(msg, _Ctx()) is True
    assert state.log_status["get_attribs"]["log_flags"]["arm_changed"] is True


def test_log_set_attribs_handler_updates_state() -> None:
    state = PanelState()
    emit = _EmitSpy()
    handler = make_log_set_attribs_handler(state, emit, now=lambda: 123.0)

    msg = {"log": {"set_attribs": {"error_code": 0}}}
    assert handler(msg, _Ctx()) is True
    assert state.log_status["set_attribs"]["error_code"] == 0


def test_log_get_list_handler_updates_state() -> None:
    state = PanelState()
    emit = _EmitSpy()
    handler = make_log_get_list_handler(state, emit, now=lambda: 123.0)

    msg = {"log": {"get_list": {"id": [500], "text": ["Entry"], "error_code": 0}}}
    assert handler(msg, _Ctx()) is True
    assert state.log_status["get_list"]["text"] == ["Entry"]


def test_log_get_log_handler_updates_state() -> None:
    state = PanelState()
    emit = _EmitSpy()
    handler = make_log_get_log_handler(state, emit, now=lambda: 123.0)

    msg = {"log": {"get_log": {"log_id": 1, "logdata": "Log Cleared", "error_code": 0}}}
    assert handler(msg, _Ctx()) is True
    assert state.log_status["get_log"]["logdata"] == "Log Cleared"


def test_log_clear_handler_updates_state() -> None:
    state = PanelState()
    emit = _EmitSpy()
    handler = make_log_clear_handler(state, emit, now=lambda: 123.0)

    msg = {"log": {"clear": {"log_id": 0, "log_count": 0, "error_code": 0}}}
    assert handler(msg, _Ctx()) is True
    assert state.log_status["clear"]["log_count"] == 0


def test_log_realloc_handler_updates_state() -> None:
    state = PanelState()
    emit = _EmitSpy()
    handler = make_log_realloc_handler(state, emit, now=lambda: 123.0)

    msg = {"log": {"realloc": {"table_elements": 250, "error_code": 0}}}
    assert handler(msg, _Ctx()) is True
    assert state.log_status["realloc"]["table_elements"] == 250
