from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import TypeVar

import pytest
from _pytest.monkeypatch import MonkeyPatch

from elke27_lib import ArmMode, ClientConfig, Elke27Client
from elke27_lib.client import Result
from elke27_lib.errors import (
    E27ProvisioningRequired,
    Elke27DisconnectedError,
    Elke27InvalidArgument,
    Elke27LinkRequiredError,
    Elke27PermissionError,
    PermissionDeniedError,
)


class _FakePanel:
    connected: bool

    def __init__(self, connected: bool = True) -> None:
        self.connected = connected


class _FakeState:
    panel: _FakePanel

    def __init__(self) -> None:
        self.panel = _FakePanel()


class _FakeKernel:
    sent: list[tuple[str, dict[str, object]]]
    state: _FakeState

    def __init__(self) -> None:
        self.sent = []
        self.state = _FakeState()

    def subscribe(
        self, _callback: Callable[[object], None], _kinds: Iterable[str] | None = None
    ) -> int:
        return 1

    def unsubscribe(self, _token: int) -> bool:
        return True


T = TypeVar("T")


def _ok(value: T) -> Result[T]:
    return Result(ok=True, data=value, error=None)


def _err(error: BaseException) -> Result[T]:
    return Result(ok=False, data=None, error=error)


@pytest.mark.asyncio
async def test_async_set_output_calls_kernel_and_no_snapshot_mutation():
    kernel = _FakeKernel()
    client = Elke27Client(kernel=kernel, config=ClientConfig(event_queue_size=1))
    client._connected = True
    before = client.snapshot

    async def _ok_execute(command_key: str, **params: object) -> Result[dict[str, object]]:
        kernel.sent.append((command_key, dict(params)))
        return _ok({})

    client.async_execute = _ok_execute

    await client.async_set_output(3, on=True)

    assert kernel.sent == [("output_set_status", {"output_id": 3, "status": "ON"})]
    assert client.snapshot is before


@pytest.mark.asyncio
async def test_async_set_output_requires_connection():
    kernel = _FakeKernel()
    kernel.state.panel.connected = False
    client = Elke27Client(kernel=kernel)
    with pytest.raises(Elke27DisconnectedError):
        await client.async_set_output(1, on=False)


@pytest.mark.asyncio
async def test_async_arm_disarm_calls_async_execute(monkeypatch: MonkeyPatch):
    client = Elke27Client()
    before = client.snapshot

    async def _ok_execute(*_args: object, **_kwargs: object) -> Result[dict[str, object]]:
        return _ok({})

    monkeypatch.setattr(client, "async_execute", _ok_execute)

    await client.async_arm_area(1, mode=ArmMode.ARMED_AWAY, pin="1234")
    await client.async_disarm_area(1, pin="1234")
    assert client.snapshot is before


@pytest.mark.asyncio
async def test_argument_validation():
    client = Elke27Client()
    with pytest.raises(Elke27InvalidArgument):
        await client.async_set_output(0, on=True)
    with pytest.raises(Elke27InvalidArgument):
        await client.async_arm_area(0, mode=ArmMode.ARMED_AWAY, pin="1234")
    with pytest.raises(Elke27InvalidArgument):
        await client.async_arm_area(1, mode=ArmMode.ARMED_NIGHT, pin="1234")
    with pytest.raises(Elke27InvalidArgument):
        await client.async_disarm_area(1, pin="")


@pytest.mark.asyncio
async def test_error_mapping_for_arm_disarm(monkeypatch: MonkeyPatch):
    client = Elke27Client()

    async def _link_required(*_args: object, **_kwargs: object) -> Result[dict[str, object]]:
        return _err(E27ProvisioningRequired("missing link"))

    monkeypatch.setattr(client, "async_execute", _link_required)
    with pytest.raises(Elke27LinkRequiredError):
        await client.async_arm_area(1, mode=ArmMode.ARMED_STAY, pin="1234")

    async def _perm_error(*_args: object, **_kwargs: object) -> Result[dict[str, object]]:
        return _err(PermissionDeniedError("no"))

    monkeypatch.setattr(client, "async_execute", _perm_error)
    with pytest.raises(Elke27PermissionError) as exc_info:
        await client.async_disarm_area(1, pin="1234")
    assert exc_info.value.is_transient is False
