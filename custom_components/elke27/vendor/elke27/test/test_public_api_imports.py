from __future__ import annotations

import dataclasses
from collections.abc import Callable, Iterable
from enum import Enum

import pytest
from _pytest.monkeypatch import MonkeyPatch

from elke27_lib import (
    AreaState,
    ArmMode,
    ClientConfig,
    CsmSnapshot,
    DiscoveredPanel,
    Elke27Client,
    Elke27Event,
    EventType,
    LinkKeys,
    OutputDefinition,
    OutputState,
    PanelInfo,
    PanelSnapshot,
    TableInfo,
    ZoneDefinition,
    ZoneState,
    redact_for_diagnostics,
)
from elke27_lib.discovery import E27System
from elke27_lib.errors import E27ProvisioningTimeout, Elke27AuthError
from elke27_lib.events import (
    UNSET_AT,
    UNSET_CLASSIFICATION,
    UNSET_ROUTE,
    UNSET_SEQ,
    UNSET_SESSION_ID,
    AreaConfiguredInventoryReady,
    OutputConfiguredInventoryReady,
    ZoneConfiguredInventoryReady,
)
from elke27_lib.kernel import DiscoverResult
from elke27_lib.linking import E27LinkKeys
from elke27_lib.states import InventoryState


def test_public_api_types_are_dataclasses_or_enums() -> None:
    assert dataclasses.is_dataclass(ClientConfig)
    assert dataclasses.is_dataclass(DiscoveredPanel)
    assert dataclasses.is_dataclass(LinkKeys)
    assert dataclasses.is_dataclass(Elke27Event)
    assert dataclasses.is_dataclass(PanelInfo)
    assert dataclasses.is_dataclass(TableInfo)
    assert dataclasses.is_dataclass(CsmSnapshot)
    assert dataclasses.is_dataclass(AreaState)
    assert dataclasses.is_dataclass(ZoneState)
    assert dataclasses.is_dataclass(OutputState)
    assert dataclasses.is_dataclass(PanelSnapshot)
    assert dataclasses.is_dataclass(ZoneDefinition)
    assert dataclasses.is_dataclass(OutputDefinition)
    assert issubclass(EventType, Enum)
    assert issubclass(ArmMode, Enum)


def test_public_api_client_instantiation_and_snapshot() -> None:
    client = Elke27Client()
    snapshot = client.snapshot
    assert isinstance(snapshot, PanelSnapshot)
    assert snapshot.version == 0


def test_redaction_returns_json_serializable() -> None:
    data = {"access_code": "1234", "nested": {"token": "abc"}, "count": 1}
    redacted = redact_for_diagnostics(data)
    assert redacted["access_code"] == "***"
    assert redacted["nested"]["token"] == "***"
    assert redacted["count"] == 1


@pytest.mark.asyncio
async def test_async_discover_maps_panels(monkeypatch: MonkeyPatch) -> None:
    async def _fake_discover(*_args: object, **_kwargs: object) -> DiscoverResult:
        return DiscoverResult(
            panels=[
                E27System(
                    panel_mac="aa:bb:cc",
                    panel_host="1.2.3.4",
                    panel_name="Panel",
                    panel_serial="SN",
                    port=2101,
                    tls_port=2601,
                )
            ]
        )

    monkeypatch.setattr("elke27_lib.kernel.E27Kernel.discover", _fake_discover)
    client = Elke27Client()
    panels = await client.async_discover()
    assert len(panels) == 1
    assert panels[0].host == "1.2.3.4"
    assert panels[0].port == 2101
    assert panels[0].tls_port == 2601


@pytest.mark.asyncio
async def test_async_link_roundtrip_and_auth_error(monkeypatch: MonkeyPatch) -> None:
    client = Elke27Client()

    async def _fake_link(*_args: object, **_kwargs: object) -> E27LinkKeys:
        return E27LinkKeys(tempkey_hex="t", linkkey_hex="k", linkhmac_hex="h")

    kernel = client._kernel
    monkeypatch.setattr(kernel, "link", _fake_link)
    keys = await client.async_link(
        "1.2.3.4",
        2101,
        access_code="1234",
        passphrase="pass",
        client_identity={"mn": "222", "sn": "00000001"},
    )
    assert keys.to_json() == {"tempkey_hex": "t", "linkkey_hex": "k", "linkhmac_hex": "h"}
    assert LinkKeys.from_json(keys.to_json()) == keys

    async def _fake_link_error(*_args: object, **_kwargs: object) -> E27LinkKeys:
        raise E27ProvisioningTimeout("auth failed")

    monkeypatch.setattr(kernel, "link", _fake_link_error)
    with pytest.raises(Elke27AuthError):
        await client.async_link(
            "1.2.3.4",
            2101,
            access_code="1234",
            passphrase="pass",
            client_identity={"mn": "222", "sn": "00000001"},
        )


@pytest.mark.asyncio
async def test_async_connect_disconnect_and_wait_ready(monkeypatch: MonkeyPatch) -> None:
    del monkeypatch

    class _FakeKernel:
        _ready: bool
        state: object

        def __init__(self) -> None:
            self._ready = False
            self.state = type(
                "_State",
                (),
                {
                    "panel": type(
                        "_Panel", (), {"model": None, "firmware": None, "serial": None}
                    )(),
                    "table_info_by_domain": {
                        "area": {"table_elements": None},
                        "zone": {"table_elements": None},
                        "output": {"table_elements": None},
                        "tstat": {"table_elements": None},
                    },
                    "areas": {},
                    "zones": {},
                    "outputs": {},
                    "inventory": InventoryState(),
                },
            )()

        @property
        def ready(self) -> bool:
            return self._ready

        async def connect(self, *_args: object, **_kwargs: object) -> None:
            self._ready = True

        async def close(self) -> None:
            self._ready = False

        def subscribe(
            self, _callback: Callable[[object], None], _kinds: Iterable[str] | None = None
        ) -> int:
            return 1

        def unsubscribe(self, _token: int) -> bool:
            return True

        def drain_events(self) -> list[object]:
            return []

    client = Elke27Client(kernel=_FakeKernel())
    await client.async_connect(
        "1.2.3.4", 2101, LinkKeys(tempkey_hex="t", linkkey_hex="k", linkhmac_hex="h")
    )
    assert client.is_ready is False
    handle_event = client._handle_kernel_event
    handle_event(
        AreaConfiguredInventoryReady(
            kind=AreaConfiguredInventoryReady.KIND,
            at=UNSET_AT,
            seq=UNSET_SEQ,
            classification=UNSET_CLASSIFICATION,
            route=UNSET_ROUTE,
            session_id=UNSET_SESSION_ID,
        )
    )
    handle_event(
        ZoneConfiguredInventoryReady(
            kind=ZoneConfiguredInventoryReady.KIND,
            at=UNSET_AT,
            seq=UNSET_SEQ,
            classification=UNSET_CLASSIFICATION,
            route=UNSET_ROUTE,
            session_id=UNSET_SESSION_ID,
        )
    )
    handle_event(
        OutputConfiguredInventoryReady(
            kind=OutputConfiguredInventoryReady.KIND,
            at=UNSET_AT,
            seq=UNSET_SEQ,
            classification=UNSET_CLASSIFICATION,
            route=UNSET_ROUTE,
            session_id=UNSET_SESSION_ID,
        )
    )
    assert await client.wait_ready(timeout_s=0.01) is True
    await client.async_disconnect()
    assert client.is_ready is False
