from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from typing import Any, cast

import pytest
from _pytest.monkeypatch import MonkeyPatch

from elke27_lib.client import Elke27Client
from elke27_lib.errors import NotAuthenticatedError
from elke27_lib.generators.registry import COMMANDS, CommandSpec
from elke27_lib.permissions import PermissionLevel
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


@pytest.mark.asyncio
async def test_async_execute_requires_session_for_area_set_status() -> None:
    client = Elke27Client()
    kernel = get_kernel(client)
    fake_session = _FakeSession()
    _set_session(kernel, fake_session)

    result = await client.async_execute("area_set_status", area_id=1, chime=True)

    assert result.ok is False
    assert isinstance(result.error, NotAuthenticatedError)
    assert "area_set_status" in str(result.error)


@pytest.mark.asyncio
async def test_async_execute_does_not_block_master_command(monkeypatch: MonkeyPatch) -> None:
    client = Elke27Client()
    kernel = get_kernel(client)
    fake_session = _FakeSession()
    _set_session(kernel, fake_session)
    kernel.state.panel.session_id = 1
    area = kernel.state.get_or_create_area(1)
    area.arm_state = "disarmed"

    command_key = "flag_set_attribs"

    def _gen_flag_set_attribs(**kwargs: object) -> tuple[dict[str, object], tuple[str, str]]:
        return {"flag_id": kwargs["flag_id"]}, ("flag", "set_attribs")

    def _handler_flag_set_attribs(_msg: Mapping[str, object], _ctx: object) -> bool:
        return True

    spec = CommandSpec(
        key=command_key,
        domain="flag",
        command="set_attribs",
        generator=_gen_flag_set_attribs,
        handler=_handler_flag_set_attribs,
        min_permission=PermissionLevel.PLT_MASTER_USER_DISARMED,
        response_mode="single",
    )
    spec.generator.__name__ = "generator_flag_set_attribs"
    monkeypatch.setitem(COMMANDS, command_key, spec)

    task = asyncio.create_task(client.async_execute(command_key, flag_id=1, pin="1234"))
    await asyncio.sleep(0)

    sent = fake_session.sent[0]
    seq = sent["seq"]
    on_message = get_private(kernel, "_on_message")
    on_message({"seq": seq, "flag": {"set_attribs": {"error_code": 0}}})

    result = await task
    assert result.ok is True
