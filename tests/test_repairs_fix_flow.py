# ruff: noqa: S101
"""async_create_fix_flow picks the flow from the issue's stored data."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from custom_components.elke27.migration import record_issue_id
from custom_components.elke27.repairs import (
    BlockedUpgradeFlow,
    RecordRebuildFlow,
    RetryFlow,
    async_create_fix_flow,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


@pytest.mark.parametrize(
    ("flow", "expected"),
    [
        ("rebuild", RecordRebuildFlow),
        ("blocked", BlockedUpgradeFlow),
        ("retry", RetryFlow),
    ],
)
async def test_fix_flow_follows_issue_data(
    hass: HomeAssistant, flow: str, expected: type
) -> None:
    """Each stored flow value opens its own fix flow."""
    created = await async_create_fix_flow(
        hass, "some_issue", {"entry_id": "abc", "flow": flow}
    )
    assert type(created) is expected


async def test_fix_flow_uses_data_not_issue_id(hass: HomeAssistant) -> None:
    """The flow comes from data["flow"], not from the issue id's shape."""
    created = await async_create_fix_flow(
        hass, record_issue_id("abc"), {"entry_id": "abc", "flow": "retry"}
    )
    assert type(created) is RetryFlow
