# ruff: noqa: S101, D103, TC001
"""
A code the panel rejects (11008) while bypassing or arming, through HA.

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

from homeassistant.const import ATTR_CODE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from tests.conftest import FakeClient, Setup, command_result, snapshot_with

ALARM = "alarm_control_panel.house"
CODE = "1234"


class ScriptedClient(FakeClient):
    """Write outcomes popped in order; ``None`` means accepted."""

    def __init__(self, outcomes: list[BaseException | None]) -> None:
        """Script the outcome of each write, in order."""
        super().__init__(command_result(refresh=True))
        self.outcomes = outcomes
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


def _rejected() -> Elke27PanelError:
    return Elke27PanelError(11008, "not authorized")


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
