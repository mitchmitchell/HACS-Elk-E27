import asyncio
from collections.abc import Callable
from typing import Any, cast

import pytest

from elke27_lib.client import Elke27Client
from elke27_lib.const import E27ErrorCode
from elke27_lib.dispatcher import DispatchContext
from elke27_lib.events import Event
from elke27_lib.handlers.rule import make_rule_get_rules_handler
from elke27_lib.states import PanelState
from test.helpers.dispatch import make_ctx
from test.helpers.internal import get_kernel, get_private


class _FakeSession:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    def send_json(
        self,
        msg: dict[str, Any],
        *,
        priority: object = None,
        on_sent: Callable[[float], None] | None = None,
        on_fail: Callable[[BaseException], None] | None = None,
    ) -> None:
        del priority, on_fail
        self.sent.append(msg)
        if on_sent is not None:
            on_sent(0.0)


def _set_session(kernel: object, session: _FakeSession) -> None:
    cast(Any, kernel)._session = session


async def _wait_for_sent(session: _FakeSession, count: int, *, timeout_s: float = 0.1) -> None:
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout_s
    while len(session.sent) < count and loop.time() < end:
        await asyncio.sleep(0)


class _EmitSpy:
    def __init__(self) -> None:
        self.events: list[Event] = []

    def __call__(self, evt: Event, _ctx: DispatchContext) -> None:
        self.events.append(evt)


_Ctx = make_ctx


@pytest.mark.asyncio
async def test_rule_get_rules_paging_merges_blocks():
    client = Elke27Client()
    kernel = get_kernel(client)
    fake_session = _FakeSession()
    _set_session(kernel, fake_session)
    kernel.state.panel.session_id = 1

    task = asyncio.create_task(client.async_execute("rule_get_rules"))
    await asyncio.sleep(0)

    sent0 = fake_session.sent[0]
    seq0 = sent0["seq"]
    assert sent0["rule"]["get_rules"]["block_id"] == 0

    on_message = get_private(kernel, "_on_message")
    on_message({"seq": seq0, "rule": {"get_rules": {"block_id": 0, "block_count": 2}}})
    await _wait_for_sent(fake_session, 2)

    sent1 = fake_session.sent[1]
    seq1 = sent1["seq"]
    assert sent1["rule"]["get_rules"]["block_id"] == 1

    on_message = get_private(kernel, "_on_message")
    on_message(
        {
            "seq": seq1,
            "rule": {"get_rules": {"block_id": 1, "block_count": 2, "data": "AAA"}},
        }
    )
    await _wait_for_sent(fake_session, 3)

    sent2 = fake_session.sent[2]
    seq2 = sent2["seq"]
    assert sent2["rule"]["get_rules"]["block_id"] == 2

    on_message = get_private(kernel, "_on_message")
    on_message(
        {
            "seq": seq2,
            "rule": {"get_rules": {"block_id": 2, "block_count": 2, "data": "BBB"}},
        }
    )

    result = await task
    assert result.ok is True
    assert result.data == {
        "rules": [{"block_id": 1, "data": "AAA"}, {"block_id": 2, "data": "BBB"}],
        "block_count": 2,
    }
    pending = get_private(kernel, "_pending_responses")
    assert pending.pending_count() == 0


def test_rule_get_rules_handler_stores_rules() -> None:
    state = PanelState()
    emit = _EmitSpy()
    handler = make_rule_get_rules_handler(state, emit, now=lambda: 123.0)

    msg0 = {
        "rule": {
            "get_rules": {"block_id": 0, "block_count": 3, "error_code": E27ErrorCode.ELKERR_NONE}
        }
    }
    assert handler(msg0, _Ctx()) is True
    assert state.rules_block_count == 3
    assert state.rules == {}

    msg1 = {
        "rule": {
            "get_rules": {
                "block_id": 1,
                "block_count": 3,
                "data": "AAA",
                "error_code": E27ErrorCode.ELKERR_NONE,
            }
        }
    }
    assert handler(msg1, _Ctx()) is True
    msg2 = {
        "rule": {
            "get_rules": {
                "block_id": 2,
                "block_count": 3,
                "data": "BBB",
                "error_code": E27ErrorCode.ELKERR_NONE,
            }
        }
    }
    assert handler(msg2, _Ctx()) is True

    assert state.rules[1]["data"] == "AAA"
    assert state.rules[2]["data"] == "BBB"
