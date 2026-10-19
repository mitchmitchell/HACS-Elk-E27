"""Repairs fix flows for the 0.1.x upgrade issues."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import voluptuous as vol

from homeassistant.components.repairs import RepairsFlow
from homeassistant.helpers import entity_registry as er, issue_registry as ir

from .const import DOMAIN
from .migration import (
    ambiguous_keepers,
    async_rebuild_legacy_record,
    blocking_entities,
    record_issue_id,
)

if TYPE_CHECKING:
    from homeassistant import data_entry_flow
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


def _bullets(items: Any) -> str:
    return "".join(f"\n- {item}" for item in items)


class _EntryFlow(RepairsFlow):
    """Base: the issue's config entry, and finish by reloading it."""

    def __init__(self, issue_id: str, entry_id: str) -> None:
        self._issue_id = issue_id
        self._entry_id = entry_id

    def _entry(self) -> ConfigEntry | None:
        return self.hass.config_entries.async_get_entry(self._entry_id)

    def _finish(self) -> data_entry_flow.FlowResult:
        ir.async_delete_issue(self.hass, DOMAIN, self._issue_id)
        if self._entry() is not None:
            self.hass.config_entries.async_schedule_reload(self._entry_id)
        return self.async_create_entry(data={})


class RetryFlow(_EntryFlow):
    """Interrupted or write-failed upgrade: one click reloads (retries)."""

    async def async_step_init(
        self,
        user_input: dict[str, Any] | None = None,  # noqa: ARG002  # issue data
    ) -> data_entry_flow.FlowResult:
        """Start at the confirm step (init receives the issue data)."""
        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> data_entry_flow.FlowResult:
        """Confirm, then reload; the upgrade picks up where it stopped."""
        if user_input is not None:
            return self._finish()
        return self.async_show_form(step_id="confirm", data_schema=vol.Schema({}))


class RecordRebuildFlow(_EntryFlow):
    """Unreadable upgrade notes: rebuild them from the current entities."""

    async def async_step_init(
        self,
        user_input: dict[str, Any] | None = None,  # noqa: ARG002  # issue data
    ) -> data_entry_flow.FlowResult:
        """Start at the confirm step (init receives the issue data)."""
        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> data_entry_flow.FlowResult:
        """Confirm the rebuild; ask again if a duplicate's keeper is unclear."""
        entry = self._entry()
        if entry is None:
            return self.async_abort(reason="entry_missing")
        if user_input is not None:
            if ambiguous_keepers(self.hass, entry):
                return await self.async_step_keepers()
            return self._rebuild(entry)
        return self.async_show_form(step_id="confirm", data_schema=vol.Schema({}))

    async def async_step_keepers(
        self, user_input: dict[str, Any] | None = None
    ) -> data_entry_flow.FlowResult:
        """Show which entity of each duplicate set is kept and which is left."""
        entry = self._entry()
        if entry is None:
            return self.async_abort(reason="entry_missing")
        choices = ambiguous_keepers(self.hass, entry)
        if user_input is not None:
            return self._rebuild(entry)
        return self.async_show_form(
            step_id="keepers",
            data_schema=vol.Schema({}),
            description_placeholders={
                "keep": _bullets(c.keep for c in choices),
                "leave": _bullets(e for c in choices for e in c.leave),
            },
        )

    def _rebuild(self, entry: ConfigEntry) -> data_entry_flow.FlowResult:
        async_rebuild_legacy_record(self.hass, entry)
        return self._finish()


class BlockedUpgradeFlow(_EntryFlow):
    """Blocked upgrade: confirm deleting the leftover entities, then retry."""

    async def async_step_init(
        self,
        user_input: dict[str, Any] | None = None,  # noqa: ARG002  # issue data
    ) -> data_entry_flow.FlowResult:
        """Route to the confirm step, or to a plain retry if nothing is ours."""
        entry = self._entry()
        if entry is None:
            return self.async_abort(reason="entry_missing")
        if not blocking_entities(self.hass, entry):
            return await self.async_step_retry()
        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> data_entry_flow.FlowResult:
        """List every entity to delete; submitting this step is the confirm."""
        entry = self._entry()
        if entry is None:
            return self.async_abort(reason="entry_missing")
        if user_input is not None:
            self._delete(entry)
            return self._finish()
        shown = blocking_entities(self.hass, entry) or []
        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema({}),
            description_placeholders={
                "entities": _bullets(reg.entity_id for reg in shown)
            },
        )

    async def async_step_retry(
        self, user_input: dict[str, Any] | None = None
    ) -> data_entry_flow.FlowResult:
        """Nothing this flow may delete: confirm, then retry only."""
        if user_input is not None:
            return self._finish()
        return self.async_show_form(step_id="retry", data_schema=vol.Schema({}))

    def _delete(self, entry: ConfigEntry) -> None:
        # Re-checked at submit: still this entry's, this platform's, and still
        # holding a planned 0.2.x target. Anything else is left alone.
        targets = blocking_entities(self.hass, entry) or []
        registry = er.async_get(self.hass)
        for reg in targets:
            registry.async_remove(reg.entity_id)
        _LOGGER.info("Elke27 upgrade: deleted %d leftover entities", len(targets))


async def async_create_fix_flow(
    hass: HomeAssistant,  # noqa: ARG001  # Home Assistant signature
    issue_id: str,
    data: dict[str, str | int | float | None] | None,
) -> RepairsFlow:
    """Create the fix flow for one of the upgrade issues."""
    entry_id = str((data or {}).get("entry_id"))
    flow = (data or {}).get("flow")
    if flow == "rebuild" or issue_id == record_issue_id(entry_id):
        return RecordRebuildFlow(issue_id, entry_id)
    if flow == "blocked":
        return BlockedUpgradeFlow(issue_id, entry_id)
    return RetryFlow(issue_id, entry_id)
