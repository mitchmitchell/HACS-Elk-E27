"""Configured inventory filtering tests."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from elke27_lib.client import Elke27Client
from elke27_lib.const import E27ErrorCode
from elke27_lib.dispatcher import DispatchContext
from elke27_lib.events import (
    UNSET_AT,
    UNSET_CLASSIFICATION,
    UNSET_ROUTE,
    UNSET_SEQ,
    UNSET_SESSION_ID,
    AreaConfiguredInventoryReady,
    AuthorizationRequiredEvent,
    Event,
    KeypadConfiguredInventoryReady,
    OutputConfiguredInventoryReady,
    UserConfiguredInventoryReady,
    ZoneConfiguredInventoryReady,
)
from elke27_lib.handlers import area as area_handler
from elke27_lib.handlers import zone as zone_handler
from elke27_lib.handlers.area import (
    make_area_get_attribs_handler,
    make_area_get_configured_handler,
)
from elke27_lib.handlers.keypad import make_keypad_get_configured_handler
from elke27_lib.handlers.user import make_user_get_configured_handler
from elke27_lib.handlers.zone import (
    make_zone_get_attribs_handler,
    make_zone_get_configured_handler,
)
from elke27_lib.states import PanelState
from test.helpers.dispatch import make_ctx
from test.helpers.internal import get_kernel, get_private


class _EmitSpy:
    def __init__(self) -> None:
        self.events: list[Event] = []

    def __call__(self, evt: Event, _ctx: DispatchContext) -> None:
        self.events.append(evt)


_Ctx = make_ctx


def test_zone_configured_paging_and_completion() -> None:
    state = PanelState()
    emit = _EmitSpy()
    handler = make_zone_get_configured_handler(state, emit, now=lambda: 123.0)

    msg2 = {"zone": {"get_configured": {"block_count": 2, "zones": [1, 2, 28]}}}
    assert handler(msg2, _Ctx()) is True
    assert state.inventory.configured_zones == {1, 2, 28}
    assert state.inventory.configured_zones_complete is True
    assert any(isinstance(e, ZoneConfiguredInventoryReady) for e in emit.events)


def test_area_configured_paging_and_completion() -> None:
    state = PanelState()
    emit = _EmitSpy()
    handler = make_area_get_configured_handler(state, emit, now=lambda: 123.0)

    msg2 = {"area": {"get_configured": {"block_id": 1, "block_count": 2, "areas": [1, 6]}}}
    assert handler(msg2, _Ctx()) is True
    assert state.inventory.configured_areas == {1, 6}
    assert state.inventory.configured_areas_complete is True
    assert any(isinstance(e, AreaConfiguredInventoryReady) for e in emit.events)


def test_user_configured_paging_and_completion() -> None:
    state = PanelState()
    emit = _EmitSpy()
    handler = make_user_get_configured_handler(state, emit, now=lambda: 123.0)

    msg = {"user": {"get_configured": {"block_id": 1, "block_count": 1, "users": [1, 2]}}}
    assert handler(msg, _Ctx()) is True
    assert state.inventory.configured_users == {1, 2}
    assert state.inventory.configured_users_complete is True
    assert any(isinstance(e, UserConfiguredInventoryReady) for e in emit.events)


def test_keypad_configured_paging_and_completion() -> None:
    state = PanelState()
    emit = _EmitSpy()
    handler = make_keypad_get_configured_handler(state, emit, now=lambda: 123.0)

    msg = {"keypad": {"get_configured": {"block_id": 1, "block_count": 1, "keypads": [1]}}}
    assert handler(msg, _Ctx()) is True
    assert state.inventory.configured_keypads == {1}
    assert state.inventory.configured_keypads_complete is True
    assert any(isinstance(e, KeypadConfiguredInventoryReady) for e in emit.events)


def test_zone_configured_auth_required_emits_event() -> None:
    state = PanelState()
    emit = _EmitSpy()
    handler = make_zone_get_configured_handler(state, emit, now=lambda: 123.0)

    msg = {"zone": {"get_configured": {"error_code": E27ErrorCode.ELKERR_NOAUTH}}}
    assert handler(msg, _Ctx()) is True
    assert any(isinstance(e, AuthorizationRequiredEvent) for e in emit.events)
    assert state.inventory.configured_zones == set()


def test_client_filters_to_configured_inventory() -> None:
    client = Elke27Client()
    kernel = get_kernel(client)
    kernel.state.inventory.configured_areas = {1}
    kernel.state.inventory.configured_zones = {2}
    kernel.state.get_or_create_area(1)
    kernel.state.get_or_create_area(2)
    kernel.state.get_or_create_zone(2)
    kernel.state.get_or_create_zone(3)

    assert list(client.areas) == [1]
    assert list(client.zones) == [2]


def test_area_get_attribs_name_normalization() -> None:
    state = PanelState()
    emit = _EmitSpy()
    handler = make_area_get_attribs_handler(state, emit, now=lambda: 123.0)

    msg = {"area": {"get_attribs": {"area_id": 1, "name": "  Main  "}}}
    assert handler(msg, _Ctx()) is True
    assert state.areas[1].name == "Main"

    msg = {"area": {"get_attribs": {"area_id": 1, "name": "   "}}}
    assert handler(msg, _Ctx()) is True
    assert state.areas[1].name is None


def test_zone_get_attribs_name_normalization() -> None:
    state = PanelState()
    emit = _EmitSpy()
    handler = make_zone_get_attribs_handler(state, emit, now=lambda: 123.0)

    msg = {"zone": {"get_attribs": {"zone_id": 1, "name": "\tFront  "}}}
    assert handler(msg, _Ctx()) is True
    assert state.zones[1].name == "Front"

    msg = {"zone": {"get_attribs": {"zone_id": 1, "name": ""}}}
    assert handler(msg, _Ctx()) is True
    assert state.zones[1].name is None


def test_attribs_request_queue_respects_limit() -> None:
    client = Elke27Client()
    kernel = get_kernel(client)
    sent: list[tuple[tuple[str, str], dict[str, object]]] = []

    def _request(route: tuple[str, str], **kwargs: object):
        sent.append((route, dict(kwargs)))
        return 1

    kernel.request = _request

    inv = kernel.state.inventory
    inv.configured_areas = {1, 2, 3}
    inv.configured_zones = {1, 2}
    inv.configured_outputs = {1, 2}
    inv.configured_users = {1}
    inv.configured_keypads = {1}

    queue_bootstrap = get_private(client, "_queue_bootstrap_attribs")
    queue_bootstrap("area")
    queue_bootstrap("zone")
    queue_bootstrap("output")
    queue_bootstrap("user")
    queue_bootstrap("keypad")

    assert sent == [
        (("area", "get_attribs"), {"area_id": 1}),
        (("area", "get_attribs"), {"area_id": 2}),
        (("area", "get_attribs"), {"area_id": 3}),
        (("zone", "get_attribs"), {"zone_id": 1}),
        (("zone", "get_attribs"), {"zone_id": 2}),
        (("output", "get_attribs"), {"output_id": 1}),
        (("output", "get_attribs"), {"output_id": 2}),
        (("user", "get_attribs"), {"user_id": 1}),
        (("keypad", "get_attribs"), {"keypad_id": 1}),
    ]


def test_client_queues_output_attribs_on_inventory_ready() -> None:
    client = Elke27Client()
    kernel = get_kernel(client)
    inv = kernel.state.inventory
    inv.configured_outputs = {1, 2}

    sent: list[tuple[tuple[str, str], dict[str, object]]] = []

    def _request(route: tuple[str, str], **kwargs: object):
        sent.append((route, dict(kwargs)))
        return 1

    kernel.request = _request

    handle_event = get_private(client, "_handle_kernel_event")
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
    assert sent == [
        (("output", "get_attribs"), {"output_id": 1}),
        (("output", "get_attribs"), {"output_id": 2}),
        (("output", "get_status"), {"output_id": 1}),
        (("output", "get_status"), {"output_id": 2}),
    ]


def test_client_queues_user_keypad_attribs_on_inventory_ready() -> None:
    client = Elke27Client()
    kernel = get_kernel(client)
    inv = kernel.state.inventory
    inv.configured_users = {1}
    inv.configured_keypads = {2}

    sent: list[tuple[tuple[str, str], dict[str, object]]] = []

    def _request(route: tuple[str, str], **kwargs: object):
        sent.append((route, dict(kwargs)))
        return 1

    kernel.request = _request

    handle_event = get_private(client, "_handle_kernel_event")
    handle_event(
        UserConfiguredInventoryReady(
            kind=UserConfiguredInventoryReady.KIND,
            at=UNSET_AT,
            seq=UNSET_SEQ,
            classification=UNSET_CLASSIFICATION,
            route=UNSET_ROUTE,
            session_id=UNSET_SESSION_ID,
        )
    )
    handle_event(
        KeypadConfiguredInventoryReady(
            kind=KeypadConfiguredInventoryReady.KIND,
            at=UNSET_AT,
            seq=UNSET_SEQ,
            classification=UNSET_CLASSIFICATION,
            route=UNSET_ROUTE,
            session_id=UNSET_SESSION_ID,
        )
    )
    assert sent == [
        (("user", "get_attribs"), {"user_id": 1}),
        (("keypad", "get_attribs"), {"keypad_id": 2}),
    ]


def test_area_get_attribs_auth_required_event() -> None:
    state = PanelState()
    emit = _EmitSpy()
    handler = make_area_get_attribs_handler(state, emit, now=lambda: 123.0)

    msg = {"area": {"get_attribs": {"area_id": 1, "error_code": E27ErrorCode.ELKERR_NOAUTH}}}
    assert handler(msg, _Ctx()) is True
    assert any(isinstance(e, AuthorizationRequiredEvent) for e in emit.events)


def test_zone_get_attribs_auth_required_event() -> None:
    state = PanelState()
    emit = _EmitSpy()
    handler = make_zone_get_attribs_handler(state, emit, now=lambda: 123.0)

    msg = {"zone": {"get_attribs": {"zone_id": 1, "error_code": E27ErrorCode.ELKERR_NOAUTH}}}
    assert handler(msg, _Ctx()) is True
    assert any(isinstance(e, AuthorizationRequiredEvent) for e in emit.events)


def test_zone_configured_ignores_bitmask_when_paged() -> None:
    payload = {
        "block_id": "1",
        "block_count": "2",
        "zones": [1, 2],
        "zone_mask": 0xFFFFFFFF,
    }
    warnings: list[str] = []
    extract_zone_ids: Callable[[Mapping[str, object], list[str]], list[int]] = get_private(
        zone_handler, "_extract_configured_zone_ids"
    )
    ids = extract_zone_ids(payload, warnings)
    assert ids == [1, 2]
    assert any("bitmask ignored" in w for w in warnings)


def test_area_configured_ignores_bitmask_when_paged() -> None:
    payload = {
        "block_id": "1",
        "block_count": "2",
        "areas": [1, 2],
        "area_mask": 0xFFFFFFFF,
    }
    warnings: list[str] = []
    extract_area_ids: Callable[[Mapping[str, object], list[str]], list[int]] = get_private(
        area_handler, "_extract_configured_area_ids"
    )
    ids = extract_area_ids(payload, warnings)
    assert ids == [1, 2]
    assert any("bitmask ignored" in w for w in warnings)
