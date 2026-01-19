from __future__ import annotations

from elke27_lib.generators.bus_ios import generator_bus_ios_get_trouble
from elke27_lib.generators.control import generator_control_get_trouble
from elke27_lib.generators.log import generator_log_get_trouble
from elke27_lib.handlers.bus_ios import make_bus_ios_get_trouble_handler
from elke27_lib.handlers.control import make_control_get_trouble_handler
from elke27_lib.handlers.log import make_log_get_trouble_handler
from elke27_lib.states import PanelState


class _EmitSpy:
    def __init__(self) -> None:
        self.events = []

    def __call__(self, evt, ctx) -> None:
        self.events.append(evt)


class _Ctx:
    classification = "RESPONSE"


def test_generator_control_get_trouble() -> None:
    payload, response_key = generator_control_get_trouble()
    assert payload == {}
    assert response_key == ("control", "get_trouble")


def test_generator_log_get_trouble() -> None:
    payload, response_key = generator_log_get_trouble()
    assert payload == {}
    assert response_key == ("log", "get_trouble")


def test_generator_bus_ios_get_trouble() -> None:
    payload, response_key = generator_bus_ios_get_trouble()
    assert payload == {}
    assert response_key == ("bus_io_dev", "get_trouble")


def test_control_get_trouble_handler_updates_state() -> None:
    state = PanelState()
    emit = _EmitSpy()
    handler = make_control_get_trouble_handler(state, emit, now=lambda: 123.0)

    msg = {"control": {"get_trouble": {"modtbl": ["system"], "error_code": 0}}}
    assert handler(msg, _Ctx()) is True
    assert state.control_status["get_trouble"]["modtbl"] == ["system"]


def test_log_get_trouble_handler_updates_state() -> None:
    state = PanelState()
    emit = _EmitSpy()
    handler = make_log_get_trouble_handler(state, emit, now=lambda: 123.0)

    msg = {"log": {"get_trouble": {"log_full": True, "error_code": 0}}}
    assert handler(msg, _Ctx()) is True
    assert state.log_status["get_trouble"]["log_full"] is True


def test_bus_ios_get_trouble_handler_updates_state() -> None:
    state = PanelState()
    emit = _EmitSpy()
    handler = make_bus_ios_get_trouble_handler(state, emit, now=lambda: 123.0)

    msg = {"bus_io_dev": {"get_trouble": {"MISSING": ["11000001"], "error_code": 0}}}
    assert handler(msg, _Ctx()) is True
    assert state.bus_io_status["get_trouble"]["MISSING"] == ["11000001"]
