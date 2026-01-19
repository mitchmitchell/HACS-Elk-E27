from __future__ import annotations

from elke27_lib.events import AreaTroublesUpdated
from elke27_lib.kernel import E27Kernel


def test_area_get_trouble_route_emits_troubles_event() -> None:
    kernel = E27Kernel(features=("elke27_lib.features.area",))
    kernel.load_features_blocking()

    msg = {
        "seq": 0,
        "area": {
            "get_trouble": {
                "area_id": 1,
                "troubles": ["Low batt"],
                "error_code": 0,
            }
        },
    }
    kernel._on_message(msg)

    events = kernel.drain_events()
    assert any(evt.kind == AreaTroublesUpdated.KIND and evt.area_id == 1 for evt in events)
