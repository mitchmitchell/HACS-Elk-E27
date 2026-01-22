from __future__ import annotations

from collections.abc import Callable, Mapping

import elke27_lib.client as client_mod
from elke27_lib.const import E27ErrorCode
from elke27_lib.dispatcher import DispatchContext, PagedBlock
from elke27_lib.events import Event
from elke27_lib.handlers.user import (
    make_user_get_attribs_handler,
    make_user_get_configured_handler,
)
from elke27_lib.states import PanelState
from test.helpers.dispatch import make_ctx
from test.helpers.internal import get_private


class _EmitSpy:
    def __init__(self) -> None:
        self.events: list[Event] = []

    def __call__(self, evt: Event, _ctx: DispatchContext) -> None:
        self.events.append(evt)


_Ctx = make_ctx


def test_user_get_configured_merges_blocks() -> None:
    blocks = [
        PagedBlock(1, {"users": [1, 2]}),
        PagedBlock(2, {"users": [3]}),
    ]
    merge_fn: Callable[[list[PagedBlock], int], Mapping[str, object]] = get_private(
        client_mod, "_merge_configured_users"
    )
    merged = merge_fn(blocks, 2)
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
