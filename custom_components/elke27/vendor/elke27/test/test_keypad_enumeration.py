from __future__ import annotations

from collections.abc import Callable, Mapping

import elke27_lib.client as client_mod
from elke27_lib.const import E27ErrorCode
from elke27_lib.dispatcher import DispatchContext, PagedBlock
from elke27_lib.events import Event
from elke27_lib.handlers.keypad import (
    make_keypad_get_attribs_handler,
    make_keypad_get_configured_handler,
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


def test_keypad_get_configured_merges_blocks() -> None:
    blocks = [
        PagedBlock(1, {"keypads": [1, 2]}),
        PagedBlock(2, {"keypads": [3]}),
    ]
    merge_fn: Callable[[list[PagedBlock], int], Mapping[str, object]] = get_private(
        client_mod, "_merge_configured_keypads"
    )
    merged = merge_fn(blocks, 2)
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
