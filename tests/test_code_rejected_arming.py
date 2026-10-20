# ruff: noqa: S101, D103, TC001
"""
A code the panel rejects (any code-rejected panel error), through HA.

The refusal reaches the caller as ServiceValidationError(code_rejected),
never re-wrapped as a zone-bypass or automatic-arming failure. Zones this
call bypassed are rolled back first; a failed rollback is logged by zone
number only and never replaces the refusal.
"""

from __future__ import annotations

import logging
from typing import Any

from elke27_lib import AreaState, ZoneState
from elke27_lib.errors import Elke27PanelError
import pytest

from custom_components.elke27.errors import PANEL_ERROR_CODES_REJECTED
from homeassistant.const import ATTR_CODE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from tests.conftest import FakeClient, Setup, command_result, snapshot_with

ALARM = "alarm_control_panel.house"
CODE = "1234"


REJECTED_CODES = sorted(PANEL_ERROR_CODES_REJECTED)


@pytest.fixture(autouse=True, params=REJECTED_CODES, ids=lambda c: f"panel_{c}")
def rejected_code(request: pytest.FixtureRequest) -> int:
    """Run every test once per code-rejected panel error code."""
    ScriptedClient.rejected_code = request.param
    return int(request.param)


class ScriptedClient(FakeClient):
    """Write outcomes popped in order; ``None`` means accepted."""

    rejected_code = 11008

    def __init__(self, outcomes: list[BaseException | None]) -> None:
        """Script the outcome of each write, in order."""
        super().__init__(command_result(refresh=True))
        self.outcomes = list(outcomes)
        self.snapshot = snapshot_with(
            self.snapshot,
            areas={1: AreaState(area_id=1, name="House", ready=False)},
            zones={
                1: ZoneState(zone_id=1, name="Front Door", area_id=1, open=True),
                2: ZoneState(zone_id=2, name="Back Door", area_id=1, open=True),
            },
        )

    async def _write(self, kind: str, **params: Any) -> Any:
        self.calls.append((kind, params))
        outcome = self.outcomes.pop(0) if self.outcomes else None
        if isinstance(outcome, _PlaceholderRejectedError):
            raise Elke27PanelError(self.rejected_code, "not authorized")
        if outcome is not None:
            raise outcome
        return self.write_result

    def writes(self) -> list[tuple[str, int | None, bool | None]]:
        """Bypass and arm writes as (kind, zone, bypassed)."""
        return [
            (kind, params.get("zone_id"), params.get("bypassed"))
            for kind, params in self.calls
            if kind in {"bypass", "arm"}
        ]


class _PlaceholderRejectedError(Elke27PanelError):
    """Scripted refusal; ScriptedClient raises the parametrized panel code."""


def _rejected() -> Elke27PanelError:
    return _PlaceholderRejectedError(0, "not authorized")


def _assert_code_rejected(err: pytest.ExceptionInfo[ServiceValidationError]) -> None:
    assert type(err.value) is ServiceValidationError
    assert err.value.translation_key == "code_rejected"
    assert CODE not in str(err.value)


async def test_custom_bypass_rejected_code_rolls_back_and_raises_code_rejected(
    hass: HomeAssistant, setup: Setup
) -> None:
    # Zone 1 bypass accepted, zone 2 bypass refused, the rollback of zone 1 ok.
    client = ScriptedClient([None, _rejected(), None])
    await setup(client)
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            "alarm_control_panel",
            "alarm_arm_custom_bypass",
            {"entity_id": ALARM, ATTR_CODE: CODE},
            blocking=True,
        )
    _assert_code_rejected(err)
    assert client.writes() == [
        ("bypass", 1, True),
        ("bypass", 2, True),
        ("bypass", 1, False),
    ]


async def test_custom_bypass_arm_rejected_rolls_back_bypasses(
    hass: HomeAssistant, setup: Setup
) -> None:
    client = ScriptedClient([None, None, _rejected()])
    await setup(client)
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            "alarm_control_panel",
            "alarm_arm_custom_bypass",
            {"entity_id": ALARM, ATTR_CODE: CODE},
            blocking=True,
        )
    _assert_code_rejected(err)
    assert client.writes() == [
        ("bypass", 1, True),
        ("bypass", 2, True),
        ("arm", None, None),
        ("bypass", 2, False),
        ("bypass", 1, False),
    ]


@pytest.mark.parametrize(
    ("outcomes", "expected"),
    [
        # The first bypass is refused: nothing to roll back.
        ([_rejected()], [("bypass", 1, True)]),
        # Both bypasses accepted, the arm refused: both rolled back.
        (
            [None, None, _rejected()],
            [
                ("bypass", 1, True),
                ("bypass", 2, True),
                ("arm", None, None),
                ("bypass", 2, False),
                ("bypass", 1, False),
            ],
        ),
    ],
    ids=["bypass_refused", "arm_refused"],
)
async def test_arm_automatic_rejected_code_raises_code_rejected(
    hass: HomeAssistant,
    setup: Setup,
    outcomes: list[BaseException | None],
    expected: list[tuple[str, int | None, bool | None]],
) -> None:
    client = ScriptedClient(outcomes)
    await setup(client)
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            "elke27",
            "alarm_arm_automatic",
            {"entity_id": ALARM, "mode": "away", ATTR_CODE: CODE},
            blocking=True,
        )
    _assert_code_rejected(err)
    assert client.writes() == expected


@pytest.mark.parametrize("service", ["custom_bypass", "arm_automatic"])
async def test_rollback_failure_still_raises_code_rejected(
    hass: HomeAssistant,
    setup: Setup,
    caplog: pytest.LogCaptureFixture,
    service: str,
) -> None:
    # Both bypasses accepted, the arm refused, then the un-bypass of zone 2
    # fails (the panel refuses the same code) while zone 1 is undone.
    client = ScriptedClient([None, None, _rejected(), _rejected(), None])
    await setup(client)
    caplog.set_level(logging.WARNING, logger="custom_components.elke27")
    if service == "custom_bypass":
        call = ("alarm_control_panel", "alarm_arm_custom_bypass", {})
    else:
        call = ("elke27", "alarm_arm_automatic", {"mode": "away"})
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            call[0],
            call[1],
            {"entity_id": ALARM, ATTR_CODE: CODE, **call[2]},
            blocking=True,
        )
    _assert_code_rejected(err)
    messages = [r.getMessage() for r in caplog.records]
    assert any(
        m == "Code rejected; zones still bypassed after rollback: 2" for m in messages
    )
    for message in messages:
        assert CODE not in message
        assert "Back Door" not in message


def _arm_automatic_reports(
    hass: HomeAssistant,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    events: list[dict[str, Any]] = []
    hass.bus.async_listen(
        "elke27_arm_automatic_failed", lambda event: events.append(dict(event.data))
    )
    return events, []


@pytest.mark.parametrize(
    ("outcomes", "stage", "zone_id", "bypassed", "rolled_back", "still"),
    [
        # Zone 1 bypassed, zone 2's bypass refused, zone 1 rolled back.
        ([None, _rejected(), None], "bypass", 2, [1], [1], []),
        # Both bypassed, the arm refused, zone 2's rollback refused.
        ([None, None, _rejected(), _rejected(), None], "arm", None, [1, 2], [1], [2]),
    ],
    ids=["bypass_stage", "arm_stage_failed_rollback"],
)
async def test_arm_automatic_rejected_code_still_notifies_and_fires_event(
    hass: HomeAssistant,
    setup: Setup,
    caplog: pytest.LogCaptureFixture,
    outcomes: list[BaseException | None],
    stage: str,
    zone_id: int | None,
    bypassed: list[int],
    rolled_back: list[int],
    still: list[int],
) -> None:
    client = ScriptedClient(outcomes)
    await setup(client)
    caplog.set_level(logging.DEBUG, logger="custom_components.elke27")
    events, _ = _arm_automatic_reports(hass)
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            "elke27",
            "alarm_arm_automatic",
            {"entity_id": ALARM, "mode": "away", ATTR_CODE: CODE},
            blocking=True,
        )
    _assert_code_rejected(err)
    await hass.async_block_till_done()
    assert len(events) == 1
    data = events[0]
    assert data["stage"] == stage
    assert data["outcome"] == "not_armed"
    assert data["zone_id"] == zone_id
    assert data["bypassed_zone_ids"] == bypassed
    assert data["rolled_back_zone_ids"] == rolled_back
    assert data["still_bypassed_zone_ids"] == still
    assert CODE not in str(data)
    notifications = [
        n
        for key, n in hass.data["persistent_notification"].items()
        if key.startswith("elke27_arm_automatic_")
    ]
    assert len(notifications) == 1
    assert CODE not in notifications[0]["message"]
    for record in caplog.records:
        assert CODE not in record.getMessage()


async def test_custom_bypass_failed_rollback_notifies_still_bypassed_zones(
    hass: HomeAssistant, setup: Setup
) -> None:
    client = ScriptedClient([None, None, _rejected(), _rejected(), None])
    await setup(client)
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            "alarm_control_panel",
            "alarm_arm_custom_bypass",
            {"entity_id": ALARM, ATTR_CODE: CODE},
            blocking=True,
        )
    _assert_code_rejected(err)
    await hass.async_block_till_done()
    notifications = [
        n
        for key, n in hass.data["persistent_notification"].items()
        if key.startswith("elke27_code_rejected_bypass_")
    ]
    assert len(notifications) == 1
    message = notifications[0]["message"]
    assert "zones 2." in message
    assert CODE not in message
    assert "Back Door" not in message
    assert "Front Door" not in message


async def test_custom_bypass_full_rollback_creates_no_notification(
    hass: HomeAssistant, setup: Setup
) -> None:
    client = ScriptedClient([None, None, _rejected()])
    await setup(client)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            "alarm_control_panel",
            "alarm_arm_custom_bypass",
            {"entity_id": ALARM, ATTR_CODE: CODE},
            blocking=True,
        )
    await hass.async_block_till_done()
    assert not any(
        key.startswith("elke27_code_rejected_bypass_")
        for key in hass.data.get("persistent_notification", {})
    )


def _assert_no_error_logs(caplog: pytest.LogCaptureFixture) -> None:
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


@pytest.mark.parametrize(
    ("domain", "service", "target", "extra"),
    [
        ("alarm_control_panel", "alarm_disarm", ALARM, {}),
        ("alarm_control_panel", "alarm_arm_away", ALARM, {}),
        ("alarm_control_panel", "alarm_arm_custom_bypass", ALARM, {}),
        ("elke27", "alarm_arm_automatic", ALARM, {"mode": "away"}),
        ("elke27", "zone_bypass", "binary_sensor.house_front_door", {"bypass": True}),
    ],
    ids=["disarm", "arm", "custom_bypass", "arm_automatic", "zone_bypass"],
)
async def test_every_code_path_raises_code_rejected_without_error_log(
    hass: HomeAssistant,
    setup: Setup,
    caplog: pytest.LogCaptureFixture,
    domain: str,
    service: str,
    target: str,
    extra: dict[str, Any],
) -> None:
    client = ScriptedClient([_rejected(), None, None])
    await setup(client)
    caplog.set_level(logging.DEBUG)
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            domain,
            service,
            {"entity_id": target, ATTR_CODE: CODE, **extra},
            blocking=True,
        )
    _assert_code_rejected(err)
    await hass.async_block_till_done()
    _assert_no_error_logs(caplog)


async def test_custom_bypass_rollback_raises_code_rejected_without_error_log(
    hass: HomeAssistant, setup: Setup, caplog: pytest.LogCaptureFixture
) -> None:
    client = ScriptedClient([None, None, _rejected(), _rejected(), None])
    await setup(client)
    caplog.set_level(logging.DEBUG)
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            "alarm_control_panel",
            "alarm_arm_custom_bypass",
            {"entity_id": ALARM, ATTR_CODE: CODE},
            blocking=True,
        )
    _assert_code_rejected(err)
    await hass.async_block_till_done()
    _assert_no_error_logs(caplog)
