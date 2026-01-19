import asyncio

import pytest

from elke27_lib.client import Elke27Client
from elke27_lib.const import E27ErrorCode
from elke27_lib.errors import (
    Elke27PermissionError,
    Elke27PinRequiredError,
    Elke27ProtocolError,
)
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
@pytest.mark.parametrize("arm_state", ["DISARMED", "ARMED_AWAY", "ARMED_STAY"])
async def test_area_set_arm_state_payload(arm_state):
    client = Elke27Client()
    kernel = client._kernel
    kernel._session = _FakeSession()
    kernel.state.panel.session_id = 1

    task = asyncio.create_task(
        client.async_execute("area_set_arm_state", area_id=1, arm_state=arm_state, pin=1234)
    )
    await asyncio.sleep(0)

    sent = kernel._session.sent[0]
    assert sent["area"]["set_arm_state"] == {"area_id": 1, "arm_state": arm_state, "pin": 1234}

    kernel._on_message(
        {"seq": sent["seq"], "area": {"set_arm_state": {"error_code": E27ErrorCode.ELKERR_NONE}}}
    )
    result = await task
    assert result.ok is True


@pytest.mark.asyncio
async def test_command_requires_pin_before_send(monkeypatch):
    client = Elke27Client()
    kernel = client._kernel
    kernel._session = _FakeSession()
    kernel.state.panel.session_id = 1
    kernel.state.areas = {1: type("_Area", (), {"arm_state": "disarmed"})()}

    result = await client.async_execute("user_get_attribs", user_id=1)
    assert result.ok is False
    assert isinstance(result.error, Elke27PinRequiredError)
    assert kernel._session.sent == []


@pytest.mark.asyncio
async def test_command_requires_disarmed_before_send(monkeypatch):
    client = Elke27Client()
    kernel = client._kernel
    kernel._session = _FakeSession()
    kernel.state.panel.session_id = 1
    kernel.state.areas = {1: type("_Area", (), {"arm_state": "armed_away"})()}

    def _gen_flag_set_attribs(**kwargs):
        return {"flag_id": kwargs["flag_id"]}, ("flag", "set_attribs")

    def _handler_flag_set_attribs(msg, ctx):
        return True

    spec = CommandSpec(
        key="flag_set_attribs",
        domain="flag",
        command="set_attribs",
        generator=_gen_flag_set_attribs,
        handler=_handler_flag_set_attribs,
        min_permission=PermissionLevel.PLT_MASTER_USER_DISARMED,
        response_mode="single",
    )
    spec.generator.__name__ = "generator_flag_set_attribs"
    monkeypatch.setitem(COMMANDS, "flag_set_attribs", spec)

    result = await client.async_execute("flag_set_attribs", flag_id=1, pin="1234")
    assert result.ok is False
    assert isinstance(result.error, Elke27PermissionError)
    assert "disarmed" in str(result.error).lower()
    assert kernel._session.sent == []


@pytest.mark.asyncio
async def test_missing_permission_metadata_fails_closed(monkeypatch):
    client = Elke27Client()
    kernel = client._kernel
    kernel._session = _FakeSession()
    kernel.state.panel.session_id = 1

    def _gen_missing_perm(**kwargs):
        return {"value": kwargs["value"]}, ("area", "get_status")

    def _handler_missing_perm(msg, ctx):
        return True

    spec = CommandSpec(
        key="missing_perm",
        domain="area",
        command="get_status",
        generator=_gen_missing_perm,
        handler=_handler_missing_perm,
        min_permission=PermissionLevel.PLT_ENCRYPTION_KEY,
        response_mode="single",
    )
    spec.generator.__name__ = "generator_missing_perm"
    monkeypatch.setitem(COMMANDS, "missing_perm", spec)

    result = await client.async_execute("missing_perm", value=1)
    assert result.ok is False
    assert isinstance(result.error, Elke27ProtocolError)
    assert kernel._session.sent == []


@pytest.mark.asyncio
async def test_area_set_arm_state_ack_vs_broadcast():
    client = Elke27Client()
    kernel = client._kernel
    kernel._session = _FakeSession()
    kernel.state.panel.session_id = 1

    task = asyncio.create_task(
        client.async_execute("area_set_arm_state", area_id=1, arm_state="ARMED_AWAY", pin=1234)
    )
    await asyncio.sleep(0)

    sent = kernel._session.sent[0]
    seq = sent["seq"]

    kernel._on_message({"seq": 0, "area": {"get_status": {"area_id": 1, "arm_state": "arming"}}})
    await asyncio.sleep(0)
    assert not task.done()

    kernel._on_message({"seq": seq, "area": {"set_arm_state": {"error_code": E27ErrorCode.ELKERR_NONE}}})
    result = await task
    assert result.ok is True
