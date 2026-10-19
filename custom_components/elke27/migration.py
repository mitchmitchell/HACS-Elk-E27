"""
Upgrade 0.1.x config entries in place (config entry VERSION 1 -> 2).

Design: grokbots/architecture/Elk/design-prod-upgrade-zone-migration.md §2.

Phase A (``async_migrate_legacy_entry``) runs from ``async_migrate_entry``
with no panel I/O. It classifies the entry, builds a plan of the legacy
entity registry rows and marks the migration pending. It clears an entry
unique_id that is not a panel identity, so the re-link can match the panel.

Phase B (``async_apply_legacy_migration``) runs in ``async_setup_entry`` after
the re-link and the first refresh, before platforms are forwarded. It moves
the panel device identifier and rewrites every planned unique_id to the 0.2.x
format, all or nothing. On any conflict it writes nothing, raises the
``legacy_migration_blocked`` Repairs issue and keeps the migration pending.

Entity ids, registry ids, names, icons, areas, labels, aliases and
disabled/hidden state are never touched (only unique_id, plus the area pin of
§2.3 d), so automations, dashboards and recorder history keep working.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import logging
import re
from typing import TYPE_CHECKING, Any

from homeassistant.const import Platform
from homeassistant.core import callback
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers import (
    device_registry as dr,
    entity_registry as er,
    issue_registry as ir,
)
from homeassistant.helpers.device_registry import format_mac
from homeassistant.util import dt as dt_util

from .const import (
    CONF_INTEGRATION_ID,
    CONF_INTEGRATION_SERIAL,
    CONF_LEGACY_MIGRATION,
    CONF_PANEL_MAC,
    CONF_PANEL_MAC_FILLED,
    DOMAIN,
    ISSUE_LEGACY_DUPLICATES,
    ISSUE_LEGACY_MIGRATION_BLOCKED,
    ISSUE_LEGACY_MIGRATION_PARTIAL,
    ISSUE_LEGACY_MIGRATION_WRITE_FAILED,
    MIGRATION_STATE_DONE,
    MIGRATION_STATE_PENDING,
)
from .entity import build_unique_id
from .identity import (
    is_placeholder_serial,
    is_valid_integration_id,
    panel_mac_from_info,
    panel_serial_tier,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

# Same marker the diagnostics component uses; kept local so this module does
# not import the diagnostics component at load time.
REDACTED = "**REDACTED**"

# Right-anchored: a 0.1.8/0.1.9 base is a formatted MAC and contains colons.
LEGACY_UNIQUE_ID_RE = re.compile(
    r"^(?P<base>.+):(?P<kind>area|zone|output|light|lock|tstat|panel):(?P<n>\d+)$"
)

# 0.1.x kind -> the only platform that used it. Any other pairing is unparseable.
KIND_DOMAIN: dict[str, str] = {
    "area": Platform.ALARM_CONTROL_PANEL,
    "zone": Platform.BINARY_SENSOR,
    "output": Platform.SWITCH,
    "light": Platform.LIGHT,
    "lock": Platform.LOCK,
    "tstat": Platform.CLIMATE,
    "panel": Platform.SENSOR,
}

# 0.1.x panel sensor number -> 0.2.x sensor description key.
PANEL_SENSOR_KEYS: dict[int, str] = {1: "panel_name", 2: "panel_ready"}

# Entities that move to a per-area device and so lose the panel device's area
# unless it is pinned on the entity (design §2.3 d, Mitch's answer 2).
AREA_PIN_DOMAINS = frozenset({Platform.ALARM_CONTROL_PANEL, Platform.BINARY_SENSOR})

_AREA_DEVICE_RE = re.compile(r":area:\d+$")
_FORMATTED_MAC_RE = re.compile(r"^[0-9a-f]{2}(?::[0-9a-f]{2}){5}$")
_MAX_LISTED = 20


@dataclass(frozen=True, slots=True)
class LegacyRow:
    """One parsed 0.1.x entity registry row."""

    entity_registry_id: str
    entity_id: str
    domain: str
    base: str
    kind: str
    n: int
    old_unique_id: str


def parse_legacy_unique_id(domain: str, unique_id: str) -> tuple[str, str, int] | None:
    """
    Return (base, kind, n) for a 0.1.x unique_id on ``domain``, else None.

    A kind that the platform never used (for example ``zone`` on a sensor) is
    treated as unparseable.
    """
    match = LEGACY_UNIQUE_ID_RE.match(unique_id)
    if match is None:
        return None
    kind = match.group("kind")
    if KIND_DOMAIN[kind] != domain:
        return None
    return match.group("base"), kind, int(match.group("n"))


def new_unique_id(key: str, kind: str, n: int) -> str | None:
    """Return the 0.2.x unique_id for a 0.1.x (kind, n), or None if it has none."""
    if kind == "panel":
        sensor_key = PANEL_SENSOR_KEYS.get(n)
        return build_unique_id(key, sensor_key) if sensor_key else None
    return build_unique_id(key, n)


def legacy_migration_state(entry: ConfigEntry) -> dict[str, Any] | None:
    """Return the stored migration record, or None for entries never migrated."""
    value = entry.data.get(CONF_LEGACY_MIGRATION)
    return value if isinstance(value, dict) else None


def legacy_migration_pending(entry: ConfigEntry) -> bool:
    """Return True while Phase B still has to run for this entry."""
    state = legacy_migration_state(entry)
    return state is not None and state.get("state") == MIGRATION_STATE_PENDING


def legacy_reauth_data_updates(
    entry: ConfigEntry, data: Mapping[str, Any], stored_mac: str | None
) -> dict[str, Any]:
    """
    Return the extra data a re-link writes while the upgrade is pending.

    The upgrade rewrites every unique_id anyway, so the entry keys on the
    discovery MAC (tier 1), exactly like a fresh 0.2.x entry, instead of
    treating the MAC as filled in later (design §2.3 step 2).
    """
    if legacy_migration_pending(entry) and (CONF_PANEL_MAC in data or stored_mac):
        return {CONF_PANEL_MAC_FILLED: False}
    return {}


def blocked_issue_id(entry_id: str) -> str:
    """Return the Repairs issue id for a blocked migration."""
    return f"{ISSUE_LEGACY_MIGRATION_BLOCKED}_{entry_id}"


def duplicates_issue_id(entry_id: str) -> str:
    """Return the Repairs issue id for duplicate legacy entities."""
    return f"{ISSUE_LEGACY_DUPLICATES}_{entry_id}"


def _legacy_rows(hass: HomeAssistant, entry: ConfigEntry) -> list[LegacyRow]:
    rows: list[LegacyRow] = []
    registry = er.async_get(hass)
    for reg in er.async_entries_for_config_entry(registry, entry.entry_id):
        if reg.platform != DOMAIN:
            continue
        parsed = parse_legacy_unique_id(reg.domain, reg.unique_id)
        if parsed is None:
            continue
        base, kind, n = parsed
        rows.append(
            LegacyRow(reg.id, reg.entity_id, reg.domain, base, kind, n, reg.unique_id)
        )
    return rows


def _entry_unique_id_is_panel_identity(entry: ConfigEntry) -> bool:
    """
    Return True when the entry unique_id is a panel MAC or real panel serial.

    0.1.7 used ``integration_serial`` (derived on the HA host) when the panel
    gave no MAC. That value would make the 0.2.x re-link abort as wrong_panel.
    """
    unique_id = entry.unique_id
    if not unique_id or unique_id == entry.data.get(CONF_INTEGRATION_SERIAL):
        return False
    if _FORMATTED_MAC_RE.match(unique_id):
        return True
    stored_panel = entry.options.get("panel_info") or {}
    if not isinstance(stored_panel, dict):
        return False
    stored_mac = panel_mac_from_info(stored_panel)
    if stored_mac and format_mac(str(stored_mac)) == unique_id:
        return True
    serial = panel_serial_tier(stored_panel)
    return bool(serial) and serial == unique_id and not is_placeholder_serial(serial)


async def async_migrate_legacy_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """
    Phase A: migrate a VERSION 1 entry to VERSION 2 without panel I/O.

    A fresh 0.2.0 entry (valid integration ID, no legacy unique_ids) only gets
    the version bump. Anything else is a 0.1.x entry: its plan is stored and
    marked pending for Phase B.
    """
    rows = _order_by_newest(hass, entry, _legacy_rows(hass, entry))
    if is_valid_integration_id(entry.data.get(CONF_INTEGRATION_ID)) and not rows:
        hass.config_entries.async_update_entry(entry, version=2, minor_version=1)
        _LOGGER.debug("Elke27 entry: 0.2.0 entry, version bump only")
        return True

    plan, duplicates, unmapped = _build_plan(rows)
    record: dict[str, Any] = {
        "from": "0.1.x",
        "state": MIGRATION_STATE_PENDING,
        "plan": plan,
        "duplicates": duplicates,
        # 0.1.x rows with no 0.2.x counterpart (panel sensors other than 1 and 2).
        "skipped": [
            {
                "entity_registry_id": row.entity_registry_id,
                "entity_id": row.entity_id,
                "old_unique_id": row.old_unique_id,
            }
            for row in unmapped
        ],
    }
    updates: dict[str, Any] = {
        "version": 2,
        "minor_version": 1,
        "data": {**entry.data, CONF_LEGACY_MIGRATION: record},
    }
    if entry.unique_id is not None and not _entry_unique_id_is_panel_identity(entry):
        updates["unique_id"] = None
    hass.config_entries.async_update_entry(entry, **updates)

    for row in unmapped:
        _LOGGER.warning(
            "Elke27 upgrade: %s (%s) has no 0.2.x counterpart and is left unchanged",
            row.entity_id,
            f"{REDACTED}:{row.kind}:{row.n}",
        )
    if duplicates:
        _async_raise_duplicates_issue(hass, entry, duplicates)
    _LOGGER.info(
        "Elke27 entry upgraded to version 2: %d entities planned, %d duplicates, "
        "%d without a counterpart; re-link the panel to finish",
        len(plan),
        len(duplicates),
        len(unmapped),
    )
    return True


def _build_plan(
    rows: list[LegacyRow],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[LegacyRow]]:
    """Return (plan, duplicates, unmapped) for the parsed legacy rows."""
    base_count = Counter(row.base for row in rows)
    groups: dict[tuple[str, str, int], list[LegacyRow]] = {}
    unmapped: list[LegacyRow] = []
    for row in rows:
        if new_unique_id("key", row.kind, row.n) is None:
            unmapped.append(row)
            continue
        groups.setdefault((row.domain, row.kind, row.n), []).append(row)
    plan: list[dict[str, Any]] = []
    duplicates: list[dict[str, Any]] = []
    for group in groups.values():
        # Rows arrive newest first; the stable sort keeps that order on ties.
        group.sort(key=lambda row: base_count[row.base], reverse=True)
        keep, *extra = group
        plan.append(
            {
                "entity_registry_id": keep.entity_registry_id,
                "domain": keep.domain,
                "kind": keep.kind,
                "n": keep.n,
                "old_unique_id": keep.old_unique_id,
            }
        )
        duplicates.extend(
            {
                "entity_registry_id": row.entity_registry_id,
                "entity_id": row.entity_id,
                "old_unique_id": row.old_unique_id,
            }
            for row in extra
        )
    plan.sort(key=lambda row: (row["domain"], row["kind"], row["n"]))
    return plan, duplicates, unmapped


def _order_by_newest(
    hass: HomeAssistant, entry: ConfigEntry, rows: list[LegacyRow]
) -> list[LegacyRow]:
    """Return ``rows`` newest registry change first (duplicate tie-break)."""
    registry = er.async_get(hass)
    modified = {
        reg.id: reg.modified_at
        for reg in er.async_entries_for_config_entry(registry, entry.entry_id)
    }
    return sorted(rows, key=lambda row: modified[row.entity_registry_id], reverse=True)


@callback
def _async_raise_duplicates_issue(
    hass: HomeAssistant, entry: ConfigEntry, duplicates: list[dict[str, Any]]
) -> None:
    ir.async_create_issue(
        hass,
        DOMAIN,
        duplicates_issue_id(entry.entry_id),
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_LEGACY_DUPLICATES,
        translation_placeholders={
            "entities": _listing(row["entity_id"] for row in duplicates),
        },
    )


def _listing(items: Any) -> str:
    values = sorted(str(item) for item in items)
    shown = ", ".join(values[:_MAX_LISTED])
    if len(values) > _MAX_LISTED:
        shown += f" (+{len(values) - _MAX_LISTED} more)"
    return shown


@callback
def async_refresh_duplicates_issue(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Drop the duplicates issue once the user has removed every listed entity."""
    state = legacy_migration_state(entry)
    if state is None or not state.get("duplicates"):
        return
    registry = er.async_get(hass)
    remaining = {
        reg.id for reg in er.async_entries_for_config_entry(registry, entry.entry_id)
    }
    if not any(row["entity_registry_id"] in remaining for row in state["duplicates"]):
        ir.async_delete_issue(hass, DOMAIN, duplicates_issue_id(entry.entry_id))


class PartialRollbackError(HomeAssistantError):
    """Undo after a failed Phase B write could not revert every write."""

    def __init__(
        self, failed: int, total: int, entity_ids: list[str] | None = None
    ) -> None:
        """Keep counts only in the message; ``entity_ids`` lists rows not undone."""
        super().__init__(
            f"undo incomplete: {failed} of {total} writes not reverted; "
            "reload to finish (restoring the pre-upgrade backup is also safe)"
        )
        self.failed = failed
        # Entity IDs of rows whose undo failed; failures with no entity ID
        # (the panel device move) are only counted.
        self.entity_ids: list[str] = list(entity_ids or [])


@dataclass(slots=True)
class _Change:
    entity_id: str
    old_unique_id: str
    new_unique_id: str
    pin_area: str | None


@dataclass(slots=True)
class _Plan:
    changes: list[_Change]
    device_id: str | None
    old_identifiers: set[tuple[str, str]]
    new_identifiers: set[tuple[str, str]] | None
    # One item per conflict: the entity_id it concerns, or None when it has
    # none (device identifiers, registry errors). Never a unique_id or key.
    conflicts: list[str | None]
    skipped: int


def _conflict_listing(conflicts: list[str | None]) -> str:
    """Entity IDs only; conflicts with no entity ID are shown as a count."""
    listed = _listing(c for c in conflicts if c is not None)
    others = sum(1 for c in conflicts if c is None)
    if not others:
        return listed
    counted = f"{others} without an entity ID"
    return f"{listed}; {counted}" if listed else counted


def issue_variant(base: str, conflicts: list[str | None]) -> tuple[str, dict[str, str]]:
    """
    Pick the Repairs text variant so no placeholder is ever empty or zero.

    ``base`` alone: only entities; ``base_devices``: none are entities;
    ``base_mixed``: both. Placeholders hold entity IDs and counts only.
    """
    entities = [c for c in conflicts if c is not None]
    others = len(conflicts) - len(entities)
    placeholders = {"count": str(len(conflicts))}
    if entities:
        placeholders["conflicts"] = _listing(entities)
    if entities and others:
        placeholders["other"] = str(others)
        return f"{base}_mixed", placeholders
    if entities:
        return base, placeholders
    return f"{base}_devices", placeholders


def _panel_devices(hass: HomeAssistant, entry: ConfigEntry) -> list[dr.DeviceEntry]:
    """Return the entry's devices whose elke27 identifiers are not area devices."""
    registry = dr.async_get(hass)
    found = []
    for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
        ours = [ident for domain, ident in device.identifiers if domain == DOMAIN]
        if ours and not any(_AREA_DEVICE_RE.search(ident) for ident in ours):
            found.append(device)
    return found


def _plan_device(
    hass: HomeAssistant, entry: ConfigEntry, key: str, plan: _Plan
) -> str | None:
    """Fill the panel device move into ``plan``; return the device's area."""
    devices = _panel_devices(hass, entry)
    if len(devices) > 1:
        plan.conflicts.append(None)  # more than one panel device
        return None
    if not devices:
        return None
    device = devices[0]
    target = (DOMAIN, key)
    owner = dr.async_get(hass).async_get_device(identifiers={target})
    if owner is not None and owner.id != device.id:
        plan.conflicts.append(None)  # the panel key is owned by another device
        return device.area_id
    if {ident for ident in device.identifiers if ident[0] == DOMAIN} != {target}:
        plan.device_id = device.id
        plan.old_identifiers = set(device.identifiers)
        plan.new_identifiers = {
            ident for ident in device.identifiers if ident[0] != DOMAIN
        } | {target}
    return device.area_id


def _build_phase_b(hass: HomeAssistant, entry: ConfigEntry, key: str) -> _Plan:
    """Pre-scan with no writes: what Phase B would change, and any conflict."""
    state = legacy_migration_state(entry) or {}
    plan = _Plan([], None, set(), None, [], 0)
    device_area = _plan_device(hass, entry, key, plan)
    registry = er.async_get(hass)
    current = {
        reg.id: reg
        for reg in er.async_entries_for_config_entry(registry, entry.entry_id)
    }
    targets: set[tuple[str, str]] = set()
    for row in state.get("plan", []):
        reg = current.get(row["entity_registry_id"])
        target = new_unique_id(key, row["kind"], int(row["n"]))
        if reg is None or target is None:
            # Removed by the user since Phase A: nothing to keep.
            plan.skipped += 1
            continue
        pin = (
            device_area
            if reg.domain in AREA_PIN_DOMAINS and reg.area_id is None
            else None
        )
        if reg.unique_id == target:
            if pin is not None:
                plan.changes.append(_Change(reg.entity_id, target, target, pin))
            continue
        if reg.unique_id != row["old_unique_id"] or (reg.domain, target) in targets:
            plan.skipped += 1
            continue
        owner = registry.async_get_entity_id(reg.domain, DOMAIN, target)
        if owner is not None and owner != reg.entity_id:
            plan.conflicts.append(owner)
            continue
        targets.add((reg.domain, target))
        plan.changes.append(_Change(reg.entity_id, reg.unique_id, target, pin))
    return plan


@callback
def _async_update_row(
    ent_reg: er.EntityRegistry,
    change: _Change,
    current: str,
    target: str,
    *,
    pin: bool,
) -> None:
    """
    Move one row from ``current`` to ``target`` unique_id (and set/clear its pin).

    A row already at ``target`` (an earlier attempt got that far) gets only
    the area write: HA rejects new_unique_id equal to the entity's own as a
    conflict, which would block the upgrade on every attempt.
    """
    updates: dict[str, Any] = {}
    if current != target:
        updates["new_unique_id"] = target
    if change.pin_area is not None:
        updates["area_id"] = change.pin_area if pin else None
    if updates:
        ent_reg.async_update_entity(change.entity_id, **updates)


@callback
def _async_write(hass: HomeAssistant, plan: _Plan) -> None:
    """Apply ``plan``; on any error undo what was written and re-raise."""
    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)
    undo: list[_Change] = []
    device_moved = False
    try:
        if plan.device_id is not None and plan.new_identifiers is not None:
            dev_reg.async_update_device(
                plan.device_id, new_identifiers=plan.new_identifiers
            )
            device_moved = True
        for change in plan.changes:
            _async_update_row(
                ent_reg, change, change.old_unique_id, change.new_unique_id, pin=True
            )
            undo.append(change)
    except (ValueError, HomeAssistantError) as err:
        failed = 0
        not_undone: list[str] = []
        for change in reversed(undo):
            try:
                _async_update_row(
                    ent_reg,
                    change,
                    change.new_unique_id,
                    change.old_unique_id,
                    pin=False,
                )
            except (ValueError, HomeAssistantError):
                failed += 1
                not_undone.append(change.entity_id)
        if device_moved and plan.device_id is not None:
            try:
                dev_reg.async_update_device(
                    plan.device_id, new_identifiers=plan.old_identifiers
                )
            except (ValueError, HomeAssistantError):
                failed += 1
        if failed:
            raise PartialRollbackError(
                failed, len(undo) + int(device_moved), not_undone
            ) from err
        raise


@callback
def async_apply_legacy_migration(
    hass: HomeAssistant, entry: ConfigEntry, key: str
) -> None:
    """
    Phase B: rewrite the legacy registry rows to the 0.2.x identity ``key``.

    Call after the re-link and the first refresh, before forwarding platforms.
    All or nothing: on a conflict nothing is written, the migration stays
    pending, the ``legacy_migration_blocked`` Repairs issue names the
    conflicts, and ConfigEntryNotReady stops the platforms from loading.
    """
    plan = _build_phase_b(hass, entry, key)
    partial = False
    write_failed = False
    if not plan.conflicts:
        try:
            _async_write(hass, plan)
        except PartialRollbackError as err:
            partial = True
            plan.conflicts.extend(err.entity_ids)
            plan.conflicts.extend([None] * (err.failed - len(err.entity_ids)))
            # Counts only, no identifiers (the error text carries none).
            _LOGGER.error(  # noqa: TRY400  # a traceback would carry identifiers
                "Elke27 upgrade: registry partly migrated, %s", err
            )
        except (ValueError, HomeAssistantError):
            # Not a conflict: a registry write failed and was fully undone.
            # The error text can carry identifiers: never shown or logged.
            write_failed = True
            _LOGGER.error(  # noqa: TRY400  # a traceback would carry identifiers
                "Elke27 upgrade: a registry write failed, all %d planned "
                "change(s) undone; reload to retry",
                len(plan.changes),
            )
    if write_failed:
        ir.async_create_issue(
            hass,
            DOMAIN,
            blocked_issue_id(entry.entry_id),
            is_fixable=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key=ISSUE_LEGACY_MIGRATION_WRITE_FAILED,
        )
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key=ISSUE_LEGACY_MIGRATION_WRITE_FAILED,
        )
    if plan.conflicts and not partial:
        _LOGGER.error(
            "Elke27 upgrade blocked, registry unchanged: %d conflict(s): %s",
            len(plan.conflicts),
            _conflict_listing(plan.conflicts),
        )
    if plan.conflicts:
        base = (
            ISSUE_LEGACY_MIGRATION_PARTIAL
            if partial
            else ISSUE_LEGACY_MIGRATION_BLOCKED
        )
        issue_key, placeholders = issue_variant(base, plan.conflicts)
        ir.async_create_issue(
            hass,
            DOMAIN,
            blocked_issue_id(entry.entry_id),
            is_fixable=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key=issue_key,
            translation_placeholders=placeholders,
        )
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key=base,
            translation_placeholders={"count": str(len(plan.conflicts))},
        )

    state = dict(legacy_migration_state(entry) or {})
    state["state"] = MIGRATION_STATE_DONE
    state["done_at"] = dt_util.utcnow().isoformat()
    data = {k: v for k, v in entry.data.items() if k != CONF_INTEGRATION_SERIAL}
    data[CONF_LEGACY_MIGRATION] = state
    hass.config_entries.async_update_entry(entry, data=data)
    ir.async_delete_issue(hass, DOMAIN, blocked_issue_id(entry.entry_id))
    _LOGGER.info(
        "Elke27 upgrade done: %d entities rewritten (%d area pins), "
        "panel device %s, %d skipped",
        sum(1 for c in plan.changes if c.old_unique_id != c.new_unique_id),
        sum(1 for c in plan.changes if c.pin_area is not None),
        "moved" if plan.device_id else "unchanged",
        plan.skipped,
    )
