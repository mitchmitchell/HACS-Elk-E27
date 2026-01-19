from __future__ import annotations

from elke27_lib.client import _merge_configured_users
from elke27_lib.const import E27ErrorCode
from elke27_lib.handlers.user import (
    make_user_get_attribs_handler,
    make_user_get_configured_handler,
)
from elke27_lib.states import PanelState


class _EmitSpy:
    def __init__(self) -> None:
        self.events = []

    def __call__(self, evt, ctx) -> None:
        self.events.append(evt)


class _Ctx:
    pass


def test_user_get_configured_merges_blocks() -> None:
    blocks = [
        _Block(1, {"users": [1, 2]}),
        _Block(2, {"users": [3]}),
    ]
    merged = _merge_configured_users(blocks, 2)
    assert merged == {"users": [1, 2, 3], "block_count": 2}


def test_user_handlers_store_snapshot() -> None:
    state = PanelState()
    emit = _EmitSpy()
    cfg_handler = make_user_get_configured_handler(state, emit, now=lambda: 123.0)
    attr_handler = make_user_get_attribs_handler(state, emit, now=lambda: 123.0)

    msg = {
        "user": {
            "get_configured": {
                "block_id": 1,
                "block_count": 1,
                "users": [1, 2],
                "error_code": E27ErrorCode.ELKERR_NONE,
            }
        }
    }
    assert cfg_handler(msg, _Ctx()) is True
    assert state.inventory.configured_users == {1, 2}

    msg = {
        "user": {
            "get_attribs": {
                "user_id": 1,
                "name": "Master User",
                "group_id": 2,
                "error_code": E27ErrorCode.ELKERR_NONE,
            }
        }
    }
    assert attr_handler(msg, _Ctx()) is True
    assert state.users[1].name == "Master User"
    assert state.users[1].group_id == 2


class _Block:
    def __init__(self, block_id: int, payload: dict) -> None:
        self.block_id = block_id
        self.payload = payload
