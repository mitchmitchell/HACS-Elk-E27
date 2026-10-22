"""Repairs fix flows for the 0.1.x upgrade issues."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, cast

import probatio

from homeassistant.components.repairs import RepairsFlow, RepairsFlowResult
from homeassistant.helpers import entity_registry as er, issue_registry as ir

from .const import DOMAIN
from .migration import (
    ambiguous_keepers,
    async_rebuild_legacy_record,
    blocking_entities,
    split_blocking_entities,
)

if TYPE_CHECKING:
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

    def _issue_placeholders(self) -> dict[str, str]:
        """
        Return the issue's placeholders (counts and entity lists).

        A fixable issue has no description of its own (hassfest), so its fix
        flow's steps show the counts and lists the issue was raised with.
        """
        issue = ir.async_get(self.hass).async_get_issue(DOMAIN, self._issue_id)
        if issue is None or not issue.translation_placeholders:
            return {}
        return dict(issue.translation_placeholders)

    def _show(
        self, step_id: str, placeholders: dict[str, str] | None = None
    ) -> RepairsFlowResult:
        """Show a step with the issue's placeholders plus the step's own."""
        return self.async_show_form(
            step_id=step_id,
            data_schema=probatio.Schema({}),
            description_placeholders={
                **self._issue_placeholders(),
                **(placeholders or {}),
            },
        )

    def _finish(self) -> RepairsFlowResult:
        ir.async_delete_issue(self.hass, DOMAIN, self._issue_id)
        if self._entry() is not None:
            self.hass.config_entries.async_schedule_reload(self._entry_id)
        return self.async_create_entry(data={})


class RetryFlow(_EntryFlow):
    """Interrupted or write-failed upgrade: one click reloads (retries)."""

    async def async_step_init(
        self,
        user_input: dict[str, Any] | None = None,  # noqa: ARG002  # issue data
    ) -> RepairsFlowResult:
        """Start at the confirm step (init receives the issue data)."""
        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> RepairsFlowResult:
        """Confirm, then reload; the upgrade picks up where it stopped."""
        if user_input is not None:
            return self._finish()
        return self._show("confirm")


class RecordRebuildFlow(_EntryFlow):
    """Unreadable upgrade notes: rebuild them from the current entities."""

    async def async_step_init(
        self,
        user_input: dict[str, Any] | None = None,  # noqa: ARG002  # issue data
    ) -> RepairsFlowResult:
        """Start at the confirm step (init receives the issue data)."""
        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> RepairsFlowResult:
        """Confirm the rebuild; ask again if a duplicate's keeper is unclear."""
        entry = self._entry()
        if entry is None:
            return self.async_abort(reason="entry_missing")
        if user_input is not None:
            if ambiguous_keepers(self.hass, entry):
                return await self.async_step_keepers()
            return self._rebuild(entry)
        return self._show("confirm")

    async def async_step_keepers(
        self, user_input: dict[str, Any] | None = None
    ) -> RepairsFlowResult:
        """Show which entity of each duplicate set is kept and which is left."""
        entry = self._entry()
        if entry is None:
            return self.async_abort(reason="entry_missing")
        choices = ambiguous_keepers(self.hass, entry)
        if user_input is not None:
            return self._rebuild(entry)
        return self._show(
            "keepers",
            {
                "keep": _bullets(c.keep for c in choices),
                "leave": _bullets(e for c in choices for e in c.leave),
            },
        )

    def _rebuild(self, entry: ConfigEntry) -> RepairsFlowResult:
        async_rebuild_legacy_record(self.hass, entry)
        return self._finish()


class BlockedUpgradeFlow(_EntryFlow):
    """Blocked upgrade: confirm deleting the leftover entities, then retry."""

    _shown: frozenset[str] = frozenset()

    async def async_step_init(
        self,
        user_input: dict[str, Any] | None = None,  # noqa: ARG002  # issue data
    ) -> RepairsFlowResult:
        """Route to the confirm step, or to a plain retry if nothing is ours."""
        entry = self._entry()
        if entry is None:
            return self.async_abort(reason="entry_missing")
        split = split_blocking_entities(self.hass, entry)
        if split is None or not split[0]:
            return await self.async_step_retry()
        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> RepairsFlowResult:
        """List every entity to delete; submitting this step is the confirm."""
        entry = self._entry()
        if entry is None:
            return self.async_abort(reason="entry_missing")
        if user_input is not None and self._shown:
            unshown = self._delete(entry, self._shown)
            if not unshown:
                return self._finish()
        shown, others = split_blocking_entities(self.hass, entry) or ([], [])
        if not shown:
            return self._finish()
        # Only what this form lists for deletion may be deleted on submit.
        self._shown = frozenset(reg.id for reg in shown)
        placeholders = {
            "count": str(len(shown)),
            "entities": _bullets(reg.entity_id for reg in shown),
        }
        if others:
            # Listed in the notice but not ours: say so, so the counts agree.
            placeholders["kept_count"] = str(len(others))
            placeholders["kept"] = _bullets(reg.entity_id for reg in others)
        return self._show("confirm_partial" if others else "confirm", placeholders)

    async def async_step_confirm_partial(
        self, user_input: dict[str, Any] | None = None
    ) -> RepairsFlowResult:
        """Confirm step that also lists leftovers to remove by hand."""
        return await self.async_step_confirm(user_input)

    async def async_step_retry(
        self, user_input: dict[str, Any] | None = None
    ) -> RepairsFlowResult:
        """Nothing this flow may delete: confirm, then retry only."""
        if user_input is not None:
            return self._finish()
        entry = self._entry()
        split = split_blocking_entities(self.hass, entry) if entry else None
        if split is not None and split[1]:
            return self._show(
                "retry_manual",
                {
                    "count": str(len(split[1])),
                    "entities": _bullets(reg.entity_id for reg in split[1]),
                },
            )
        return self._show("retry")

    async def async_step_retry_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> RepairsFlowResult:
        """Retry step that lists leftovers this flow can't delete."""
        return await self.async_step_retry(user_input)

    def _delete(self, entry: ConfigEntry, shown: frozenset[str]) -> bool:
        """Delete shown leftovers that still block; True if new ones appeared."""
        # Re-checked at submit: still this entry's, this platform's, and still
        # holding a planned 0.2.x target. Only entities listed on the confirm
        # form are deleted; anything else is left alone and re-confirmed.
        current = blocking_entities(self.hass, entry) or []
        targets = [reg for reg in current if reg.id in shown]
        registry = er.async_get(self.hass)
        for reg in targets:
            registry.async_remove(reg.entity_id)
        _LOGGER.info("Elke27 upgrade: leftover entities deleted: %d", len(targets))
        return len(targets) < len(current)


async def async_create_fix_flow(
    hass: HomeAssistant,  # noqa: ARG001  # Home Assistant signature
    issue_id: str,
    data: dict[str, str | int | float | None] | None,
) -> RepairsFlow:
    """Create the fix flow for one of the upgrade issues."""
    # Every fixable issue is created with entry_id and flow in its data.
    issue_data = cast("dict[str, str]", data)
    entry_id = issue_data["entry_id"]
    flow = issue_data["flow"]
    if flow == "rebuild":
        return RecordRebuildFlow(issue_id, entry_id)
    if flow == "blocked":
        return BlockedUpgradeFlow(issue_id, entry_id)
    return RetryFlow(issue_id, entry_id)
