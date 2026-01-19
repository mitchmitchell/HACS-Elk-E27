from __future__ import annotations

from elke27_lib.client import _merge_configured_keypads
from elke27_lib.const import E27ErrorCode
from elke27_lib.handlers.keypad import make_keypad_get_attribs_handler, make_keypad_get_configured_handler
from elke27_lib.states import PanelState


class _EmitSpy:
    def __init__(self) -> None:
        self.events = []

    def __call__(self, evt, ctx) -> None:
        self.events.append(evt)


class _Ctx:
    pass


def test_keypad_get_configured_merges_blocks() -> None:
    blocks = [
        _Block(1, {"keypads": [1, 2]}),
        _Block(2, {"keypads": [3]}),
    ]
    merged = _merge_configured_keypads(blocks, 2)
    assert merged == {"keypads": [1, 2, 3], "block_count": 2}


def test_keypad_handlers_store_snapshot() -> None:
    state = PanelState()
    emit = _EmitSpy()
    cfg_handler = make_keypad_get_configured_handler(state, emit, now=lambda: 123.0)
    attr_handler = make_keypad_get_attribs_handler(state, emit, now=lambda: 123.0)

    msg = {
        "keypad": {
            "get_configured": {
                "block_id": 1,
                "block_count": 1,
                "keypads": [1],
                "error_code": E27ErrorCode.ELKERR_NONE,
            }
        }
    }
    assert cfg_handler(msg, _Ctx()) is True
    assert state.inventory.configured_keypads == {1}

    msg = {
        "keypad": {
            "get_attribs": {
                "keypad_id": 1,
                "name": "Main Keypad",
                "area": 1,
                "error_code": E27ErrorCode.ELKERR_NONE,
            }
        }
    }
    assert attr_handler(msg, _Ctx()) is True
    assert state.keypads[1].name == "Main Keypad"
    assert state.keypads[1].area == 1


class _Block:
    def __init__(self, block_id: int, payload: dict) -> None:
        self.block_id = block_id
        self.payload = payload
