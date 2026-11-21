# ruff: noqa: S101, D103, FBT001
"""Persistent zone-bypass notifications use strings.json copy."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

from elke27_lib import ZoneState
import pytest

from custom_components.elke27.const import DOMAIN
from custom_components.elke27.zone_notifications import (
    notify_still_changed_zones_by_panel,
)
from homeassistant.components import persistent_notification
from homeassistant.helpers import translation
from tests.conftest import FakeClient, command_result

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from tests.conftest import Setup

_COMPONENT = Path(__file__).parent.parent / "custom_components" / "elke27"
_STRINGS = json.loads((_COMPONENT / "strings.json").read_text(encoding="utf-8"))


def _catalog_still_changed(*, bypass: bool, zone_ids: str) -> tuple[str, str]:
    key = "still_bypassed" if bypass else "still_unbypassed"
    block = _STRINGS["notifications"][key]
    return block["title"], block["message"].format(zone_ids=zone_ids)


def test_notify_still_changed_reads_notifications_translations() -> None:
    created: list[tuple[str, str]] = []

    def _record_create(
        _hass: object, *, title: str, message: str, notification_id: str
    ) -> None:
        del notification_id
        created.append((title, message))

    fake = {
        "component.elke27.notifications.still_bypassed.title": "TITLE",
        "component.elke27.notifications.still_bypassed.message": "MESSAGE {zone_ids}",
    }
    seen: dict[str, str] = {}

    def _cached(
        _hass: object,
        language: str,
        category: str,
        integration: str | None = None,
    ) -> dict[str, str]:
        seen["language"] = language
        seen["category"] = category
        seen["integration"] = integration or ""
        return fake

    mock_hass = MagicMock()
    mock_hass.config.language = "en"

    zones = (ZoneState(zone_id=1, name="", area_id=1, open=False, bypassed=True),)

    with (
        patch.object(translation, "async_get_cached_translations", side_effect=_cached),
        patch.object(
            persistent_notification,
            "async_create",
            side_effect=_record_create,
        ),
    ):
        notify_still_changed_zones_by_panel(
            mock_hass,
            zones_by_config_entry={"entry": zones},
            bypass=True,
            notification_id_prefix="prefix",
        )

    assert seen["category"] == "notifications"
    assert seen["integration"] == DOMAIN
    assert created == [("TITLE", "MESSAGE 1")]


@pytest.mark.parametrize("bypass", [True, False], ids=["bypassed", "unbypassed"])
async def test_notify_still_changed_renders_catalog_english(
    hass: HomeAssistant,
    setup: Setup,
    bypass: bool,
) -> None:
    client = FakeClient(command_result(True))
    await setup(client)
    await translation.async_load_integrations(hass, {DOMAIN})
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    zones = (
        ZoneState(zone_id=2, name="Zone B", area_id=1, open=False, bypassed=bypass),
    )
    zone_ids = "2"
    notify_still_changed_zones_by_panel(
        hass,
        zones_by_config_entry={entry.entry_id: zones},
        bypass=bypass,
        notification_id_prefix="catalog_test",
    )
    notice = hass.data["persistent_notification"][
        f"elke27_catalog_test_{entry.entry_id}"
    ]
    title, message = _catalog_still_changed(bypass=bypass, zone_ids=zone_ids)
    assert notice["title"] == title
    assert notice["message"] == message
