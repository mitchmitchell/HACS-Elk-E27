import asyncio

import pytest

from elke27_lib.client import Elke27Client
from elke27_lib.errors import NotAuthenticatedError
from elke27_lib.generators.registry import COMMANDS, CommandSpec
from elke27_lib.permissions import PermissionLevel


class _FakeSession:
    def __init__(self) -> None:
        self.sent = []

    def send_json(self, msg, *, priority=None, on_sent=None, on_fail=None):
        self.sent.append(msg)
        if on_sent is not None:
            on_sent(0.0)


@pytest.mark.asyncio
async def test_async_execute_requires_session_for_area_set_status():
    client = Elke27Client()
    kernel = client._kernel
    kernel._session = _FakeSession()

    result = await client.async_execute("area_set_status", area_id=1, chime=True)

    assert result.ok is False
    assert isinstance(result.error, NotAuthenticatedError)
    assert "area_set_status" in str(result.error)


@pytest.mark.asyncio
async def test_async_execute_does_not_block_master_command(monkeypatch):
    client = Elke27Client()
    kernel = client._kernel
    kernel._session = _FakeSession()
    kernel.state.panel.session_id = 1
    kernel.state.areas = {1: type("_Area", (), {"arm_state": "disarmed"})()}

    command_key = "flag_set_attribs"

    def _gen_flag_set_attribs(**kwargs):
        return {"flag_id": kwargs["flag_id"]}, ("flag", "set_attribs")

    def _handler_flag_set_attribs(msg, ctx):
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

    sent = kernel._session.sent[0]
    seq = sent["seq"]
    kernel._on_message({"seq": seq, "flag": {"set_attribs": {"error_code": 0}}})

    result = await task
    assert result.ok is True
