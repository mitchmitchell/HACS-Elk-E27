# ruff: noqa: S101, TC001, TC002
"""Setup primes the barrier domain, so covers exist after the first refresh."""

from __future__ import annotations

import dataclasses
from types import MappingProxyType

from elke27_lib.types import BarrierState
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.elke27.coordinator import PRIMED_DOMAINS
from custom_components.elke27.entity import unique_base
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from tests.conftest import ClientHarness, panel_snapshot


async def test_covers_exist_after_first_refresh(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: ClientHarness
) -> None:
    """Barriers appear only after the barrier domain refresh; setup does it."""
    assert "barrier" in PRIMED_DOMAINS
    # The CSM alone has no barriers; the barrier domain refresh fills them in.
    mock_client.snapshot = panel_snapshot()
    refreshed: list[str] = []

    async def _refresh(domain: str) -> None:
        refreshed.append(domain)
        if domain == "barrier":
            mock_client.snapshot = dataclasses.replace(
                mock_client.snapshot,
                barriers=MappingProxyType(
                    {
                        1: BarrierState(barrier_id=1, name="Garage", is_open=False),
                        2: BarrierState(barrier_id=2, name=None, is_open=True),
                    }
                ),
            )

    mock_client.client.async_refresh_domain_config.side_effect = _refresh
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert "barrier" in refreshed
    coordinator = mock_config_entry.runtime_data.coordinator
    base = unique_base(coordinator, mock_config_entry)
    registry = er.async_get(hass)
    entities = er.async_entries_for_config_entry(registry, mock_config_entry.entry_id)
    covers = {e.unique_id: e for e in entities if e.domain == Platform.COVER}
    assert set(covers) == {f"{base}:1", f"{base}:2"}
    assert hass.states.get(covers[f"{base}:1"].entity_id).state == "closed"
    assert hass.states.get(covers[f"{base}:2"].entity_id).state == "open"
