# ruff: noqa: S101, D103, TC001
"""
A user-condition panel refusal (not ready, in alarm, ...), through HA.

Each refusal reaches the caller as a translated ServiceValidationError with
its own key, handled exactly like a rejected code: never re-wrapped, zones
this call bypassed are rolled back first, the automatic-arming event and
notification still fire, and nothing is logged at ERROR level.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from elke27_lib import AreaState, ZoneState
from elke27_lib.errors import Elke27PanelError
import pytest

from custom_components.elke27.errors import code_rejected_error, command_error
from homeassistant.const import ATTR_CODE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from tests.conftest import FakeClient, Setup, command_result, snapshot_with

ALARM = "alarm_control_panel.house"
CODE = "1234"


# 11028 (not allowed while armed) is a user error too, but it keeps the
# "already armed" handling: no rollback. It is tested on its own below.
# Literal panel codes and keys, not read from the mapping under test.
USER_CONDITION_CODES = [
    (11015, "not_ready"),
    (11023, "not_bypassable"),
    (11027, "in_alarm"),
    (11038, "code_rejected"),
]


@pytest.fixture(
    autouse=True, params=USER_CONDITION_CODES, ids=lambda c: f"panel_{c[0]}"
)
def code_and_key(request: pytest.FixtureRequest) -> tuple[int, str]:
    """Run every test once per user-condition panel error code."""
    return request.param


@pytest.fixture
def rejected_code(code_and_key: tuple[int, str]) -> int:
    """Return the panel error code under test."""
    return code_and_key[0]


@pytest.fixture(autouse=True)
def _expected_key(code_and_key: tuple[int, str]) -> None:
    """Set the translation key the refusal must carry."""
    global EXPECTED_KEY  # noqa: PLW0603
    EXPECTED_KEY = code_and_key[1]


EXPECTED_KEY = ""


class ScriptedClient(FakeClient):
    """Write outcomes popped in order; ``None`` means accepted."""

    def __init__(
        self, outcomes: list[BaseException | object | None], rejected_code: int
    ) -> None:
        """Script each write's outcome; REJECT raises ``rejected_code``."""
        super().__init__(command_result(refresh=True))
        self.rejected_code = rejected_code
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
        if outcome is REJECT:
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


# Scripted refusal: ScriptedClient raises its rejected_code panel error.
REJECT = object()


def _assert_code_rejected(err: pytest.ExceptionInfo[ServiceValidationError]) -> None:
    assert type(err.value) is ServiceValidationError
    assert err.value.translation_key == EXPECTED_KEY
    assert CODE not in str(err.value)


async def test_custom_bypass_rejected_code_rolls_back_and_raises_code_rejected(
    rejected_code: int, hass: HomeAssistant, setup: Setup
) -> None:
    # Zone 1 bypass accepted, zone 2 bypass refused, the rollback of zone 1 ok.
    client = ScriptedClient([None, REJECT, None], rejected_code)
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
    rejected_code: int, hass: HomeAssistant, setup: Setup
) -> None:
    client = ScriptedClient([None, None, REJECT], rejected_code)
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
        ([REJECT], [("bypass", 1, True)]),
        # Both bypasses accepted, the arm refused: both rolled back.
        (
            [None, None, REJECT],
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
    rejected_code: int,
    hass: HomeAssistant,
    setup: Setup,
    outcomes: list[BaseException | None],
    expected: list[tuple[str, int | None, bool | None]],
) -> None:
    client = ScriptedClient(outcomes, rejected_code)
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
    rejected_code: int,
    hass: HomeAssistant,
    setup: Setup,
    caplog: pytest.LogCaptureFixture,
    service: str,
) -> None:
    # Both bypasses accepted, the arm refused, then the un-bypass of zone 2
    # fails (the panel refuses the same code) while zone 1 is undone.
    client = ScriptedClient([None, None, REJECT, REJECT, None], rejected_code)
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
        ([None, REJECT, None], "bypass", 2, [1], [1], []),
        # Both bypassed, the arm refused, zone 2's rollback refused.
        ([None, None, REJECT, REJECT, None], "arm", None, [1, 2], [1], [2]),
    ],
    ids=["bypass_stage", "arm_stage_failed_rollback"],
)
async def test_arm_automatic_rejected_code_still_notifies_and_fires_event(
    rejected_code: int,
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
    client = ScriptedClient(outcomes, rejected_code)
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
    rejected_code: int, hass: HomeAssistant, setup: Setup
) -> None:
    client = ScriptedClient([None, None, REJECT, REJECT, None], rejected_code)
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
    rejected_code: int, hass: HomeAssistant, setup: Setup
) -> None:
    client = ScriptedClient([None, None, REJECT], rejected_code)
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
    rejected_code: int,
    hass: HomeAssistant,
    setup: Setup,
    caplog: pytest.LogCaptureFixture,
    domain: str,
    service: str,
    target: str,
    extra: dict[str, Any],
) -> None:
    client = ScriptedClient([REJECT, None, None], rejected_code)
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
    rejected_code: int,
    hass: HomeAssistant,
    setup: Setup,
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = ScriptedClient([None, None, REJECT, REJECT, None], rejected_code)
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


def test_command_error_maps_each_code_to_its_key(
    code_and_key: tuple[int, str], caplog: pytest.LogCaptureFixture
) -> None:
    code, key = code_and_key
    caplog.set_level(logging.DEBUG)
    panel_err = Elke27PanelError(code, "refused")
    result = command_error(panel_err)
    assert type(result) is ServiceValidationError
    assert result.translation_domain == "elke27"
    assert result.translation_key == key
    assert code_rejected_error(panel_err) is not None
    assert code_rejected_error(result) is result
    _assert_no_error_logs(caplog)


def test_every_key_has_an_english_message(code_and_key: tuple[int, str]) -> None:
    root = Path(__file__).parent.parent / "custom_components" / "elke27"
    for name in ("strings.json", "translations/en.json"):
        data = json.loads((root / name).read_text())
        assert data["exceptions"][code_and_key[1]]["message"]


def test_unmapped_code_stays_home_assistant_error() -> None:
    result = command_error(Elke27PanelError(11033, "other"))
    assert type(result) is HomeAssistantError


def test_not_allowed_armed_is_user_error_without_rollback(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    panel_err = Elke27PanelError(11028, "refused")
    result = command_error(panel_err)
    assert type(result) is ServiceValidationError
    assert result.translation_key == "not_allowed_armed"
    # Not a rollback refusal: an armed area may rely on its bypasses.
    assert code_rejected_error(panel_err) is None
    assert code_rejected_error(result) is None
    _assert_no_error_logs(caplog)


async def test_arm_away_not_allowed_armed_without_error_log(
    hass: HomeAssistant, setup: Setup, caplog: pytest.LogCaptureFixture
) -> None:
    client = ScriptedClient([REJECT], 11028)
    await setup(client)
    caplog.set_level(logging.DEBUG)
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            "alarm_control_panel",
            "alarm_arm_away",
            {"entity_id": ALARM, ATTR_CODE: CODE},
            blocking=True,
        )
    assert err.value.translation_key == "not_allowed_armed"
    await hass.async_block_till_done()
    _assert_no_error_logs(caplog)
