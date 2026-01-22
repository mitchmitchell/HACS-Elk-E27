from elke27_lib.events import ApiError, AuthorizationRequiredEvent, DispatchRoutingError
from elke27_lib.kernel import E27Kernel
from test.helpers.internal import get_private


def test_root_error_envelope_emits_authorization_event() -> None:
    kernel = E27Kernel()

    msg = {"seq": 0, "error_code": 11008, "error_message": "no authorization"}
    on_message = get_private(kernel, "_on_message")
    on_message(msg)

    events = kernel.drain_events()
    kinds = [evt.kind for evt in events]
    auth_events = [evt for evt in events if evt.kind == AuthorizationRequiredEvent.KIND]

    assert AuthorizationRequiredEvent.KIND in kinds
    assert ApiError.KIND not in kinds
    assert DispatchRoutingError.KIND not in kinds
    assert auth_events[0].classification == "BROADCAST"
