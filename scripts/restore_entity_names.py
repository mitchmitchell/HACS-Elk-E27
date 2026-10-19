#!/usr/bin/env python3
r"""
Rename Elk E27 entities back to their old displayed names, with undo.

Fallback for the 0.1.x -> 0.2.x upgrade (design: grokbots/architecture/Elk/
design-prod-upgrade-zone-migration.md §6, Mitch's answer 4). After the upgrade
the entity IDs are unchanged but displayed names are area-style ("Main House
Front Door"). This sets each entity's registry name to the name saved before
the upgrade. It uses Home Assistant's websocket API only
(``config/entity_registry/list`` and ``config/entity_registry/update``); it
never edits ``.storage``.

Dry run is the default: nothing is written without ``--apply``.

    # What would change (no writes):
    HA_TOKEN=... scripts/restore_entity_names.py --url ws://ha.local:8123 names.csv
    # Write, saving a map of every previous name:
    HA_TOKEN=... scripts/restore_entity_names.py --url ... names.csv \
        --apply --map applied-map.json
    # Preview, then restore, the previous names from that map:
    HA_TOKEN=... scripts/restore_entity_names.py --url ... --undo applied-map.json
    HA_TOKEN=... scripts/restore_entity_names.py --url ... --undo applied-map.json \
        --apply

The names file is CSV with ``entity_id`` and ``name`` columns (extra columns
are ignored), or, with ``--from-markdown``, the Markdown names table Home
Automation saved before the upgrade (vault: grokbots/home/
elk-entity-names-pre-0.2.1.md). A registry name replaces the whole displayed
name, so from that table the default is the exact old displayed name (device
name plus original_name, e.g. "<device> Front Door"); ``--original-name`` takes
the bare override-or-original_name instead ("Front Door"). ``--to-csv out.csv``
only converts, with no connection:

    scripts/restore_entity_names.py --from-markdown names.md --to-csv names.csv
    HA_TOKEN=... scripts/restore_entity_names.py --url ... --from-markdown names.md
 The access token comes from the ``HA_TOKEN`` environment variable or a
hidden prompt. It is never printed, logged or written to a file; the saved map
holds entity IDs and names only. No alarm codes or PINs are handled.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
from dataclasses import dataclass
import getpass
import json
import os
from pathlib import Path
import sys
from typing import Any, Protocol, Self

PLATFORM = "elke27"
MAP_VERSION = 1


class Transport(Protocol):
    """Send one websocket command and return its result."""

    async def call(self, message: dict[str, Any]) -> Any:
        """Send ``message`` (without an id) and return the result payload."""


@dataclass(frozen=True, slots=True)
class Change:
    """One planned rename: ``entity_id`` from ``before`` to ``after``."""

    entity_id: str
    before: str | None
    after: str | None


@dataclass(frozen=True, slots=True)
class Plan:
    """The changes to make, and the rows that are skipped with the reason."""

    changes: list[Change]
    unchanged: list[str]
    skipped: list[tuple[str, str]]


def read_names(path: Path) -> dict[str, str]:
    """Read ``entity_id,name`` rows. Later duplicates are an error."""
    names: dict[str, str] = {}
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or not {"entity_id", "name"} <= set(reader.fieldnames):
            msg = f"{path}: needs entity_id and name columns"
            raise ValueError(msg)
        for row in reader:
            entity_id = (row["entity_id"] or "").strip()
            name = (row["name"] or "").strip()
            if not entity_id or not name:
                continue
            if entity_id in names:
                msg = f"{path}: duplicate entity_id {entity_id}"
                raise ValueError(msg)
            names[entity_id] = name
    return names


def _cells(line: str) -> list[str]:
    return [
        cell.strip().strip("`").strip() for cell in line.strip().strip("|").split("|")
    ]


def read_names_markdown(path: Path, *, original_name: bool = False) -> dict[str, str]:
    """
    Read the names table saved before the upgrade (Markdown, one row per entity).

    Uses the first table whose header has an ``entity_id`` column. A registry
    name replaces the entity's whole displayed name in Home Assistant, so by
    default the name is the old displayed name exactly: the ``displayed name
    (friendly_name)`` cell (device name plus original_name, e.g.
    "<device> Front Door"). If that cell is empty it is rebuilt from the
    ``registry name (override)`` cell, else ``device`` + ``original_name``.
    With ``original_name`` the bare override-or-original_name is used instead
    (e.g. "Front Door", with no device name).
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    header: list[str] | None = None
    names: dict[str, str] = {}
    for line in lines:
        if not line.lstrip().startswith("|"):
            if header is not None and names:
                break
            continue
        cells = _cells(line)
        if header is None:
            if "entity_id" in cells:
                header = [cell.lower() for cell in cells]
            continue
        if set("".join(cells)) <= set("-: "):
            continue
        row = dict(zip(header, cells, strict=False))
        entity_id = row.get("entity_id", "")
        override = row.get("registry name (override)", "")
        bare = override or row.get("original_name", "")
        if original_name:
            name = bare
        else:
            device = row.get("device", "")
            rebuilt = override or (f"{device} {bare}".strip() if bare else "")
            name = row.get("displayed name (friendly_name)", "") or rebuilt
        if not entity_id or not name:
            continue
        if entity_id in names:
            msg = f"{path}: duplicate entity_id {entity_id}"
            raise ValueError(msg)
        names[entity_id] = name
    if header is None:
        msg = f"{path}: no Markdown table with an entity_id column"
        raise ValueError(msg)
    return names


def write_csv(path: Path, names: dict[str, str]) -> None:
    """Write ``names`` as the entity_id,name CSV this script reads."""
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["entity_id", "name"])
        writer.writerows(sorted(names.items()))


def build_plan(registry: list[dict[str, Any]], wanted: dict[str, str | None]) -> Plan:
    """
    Compare the live registry with ``wanted`` (entity_id -> name or None).

    Only entities of this integration are touched. Wanted IDs that are missing
    or belong to another integration are skipped and reported, never written.
    """
    live = {row["entity_id"]: row for row in registry}
    changes: list[Change] = []
    unchanged: list[str] = []
    skipped: list[tuple[str, str]] = []
    for entity_id in sorted(wanted):
        row = live.get(entity_id)
        if row is None:
            skipped.append((entity_id, "not in the entity registry"))
        elif row.get("platform") != PLATFORM:
            skipped.append(
                (entity_id, f"belongs to {row.get('platform')}, not {PLATFORM}")
            )
        elif row.get("name") == wanted[entity_id]:
            unchanged.append(entity_id)
        else:
            changes.append(Change(entity_id, row.get("name"), wanted[entity_id]))
    return Plan(changes, unchanged, skipped)


def format_plan(plan: Plan, *, apply: bool) -> str:
    """Return the human-readable plan."""
    lines = [("APPLY" if apply else "DRY RUN (no writes; add --apply to write)")]
    lines.extend(
        f"  rename {c.entity_id}: {c.before!r} -> {c.after!r}" for c in plan.changes
    )
    lines.extend(f"  skip   {e}: {why}" for e, why in plan.skipped)
    lines.append(
        f"{len(plan.changes)} to change, {len(plan.unchanged)} already correct, "
        f"{len(plan.skipped)} skipped"
    )
    return "\n".join(lines)


async def list_entities(transport: Transport) -> list[dict[str, Any]]:
    """Return the entity registry (read only)."""
    result = await transport.call({"type": "config/entity_registry/list"})
    return list(result)


async def apply_changes(transport: Transport, changes: list[Change]) -> None:
    """
    Write ``changes`` one by one, in order; the first error is re-raised.

    There is no automatic rollback: the outcome of a failed write is unknown
    (the connection may drop after Home Assistant applied it), so recovery is
    always ``--undo`` with the map saved before the first write.
    """
    for change in changes:
        await transport.call(
            {
                "type": "config/entity_registry/update",
                "entity_id": change.entity_id,
                "name": change.after,
            }
        )


def write_map(path: Path, changes: list[Change]) -> None:
    """Save before/after names (no secrets) so ``--undo`` can reverse them."""
    payload = {
        "version": MAP_VERSION,
        "platform": PLATFORM,
        "changes": [
            {"entity_id": c.entity_id, "before": c.before, "after": c.after}
            for c in changes
        ],
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def map_exists(path: Path) -> bool:
    """Return True if a map file is already saved at ``path``."""
    return path.is_file()


def remove_map(path: Path) -> None:
    """Delete a map after a successful ``--undo`` (nothing left to undo)."""
    path.unlink(missing_ok=True)


def read_map(path: Path) -> list[Change]:
    """Read a saved map back into changes (in the order they were applied)."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("version") != MAP_VERSION or payload.get("platform") != PLATFORM:
        msg = f"{path}: not an applied-map from this script"
        raise ValueError(msg)
    return [Change(c["entity_id"], c["before"], c["after"]) for c in payload["changes"]]


async def run_rename(
    transport: Transport,
    names: dict[str, str],
    *,
    apply: bool,
    map_path: Path | None,
    force: bool = False,
) -> Plan:
    """
    Plan, and with ``apply`` write, the renames to ``names``.

    With ``apply`` the undo map listing every planned rename is written before
    the first write and is never deleted here, on success or failure; only a
    successful ``--undo`` removes it. An existing map is never overwritten
    unless ``force`` is set.
    """
    plan = build_plan(await list_entities(transport), dict(names))
    if apply and plan.changes:
        if map_path is None:
            msg = "--apply needs --map <file> so the change can be undone"
            raise ValueError(msg)
        if not force and map_exists(map_path):
            msg = f"{map_path} already exists; undo it first or pass --force"
            raise ValueError(msg)
        # An unwritable path fails here with nothing changed.
        write_map(map_path, plan.changes)
        await apply_changes(transport, plan.changes)
    return plan


async def run_undo(transport: Transport, map_path: Path, *, apply: bool) -> Plan:
    """
    Plan, and with ``apply`` write, the restore of the names in ``map_path``.

    Idempotent: entities already at their old name are skipped, so it can be
    re-run after a partial apply or a failed undo. The map is removed only
    after a successful ``--undo --apply``.
    """
    saved = read_map(map_path)
    wanted: dict[str, str | None] = {c.entity_id: c.before for c in saved}
    plan = build_plan(await list_entities(transport), wanted)
    if apply:
        order = [s.entity_id for s in saved]
        # Newest first: the reverse of the order the map was written in.
        ordered = sorted(
            plan.changes, key=lambda c: order.index(c.entity_id), reverse=True
        )
        await apply_changes(transport, ordered)
        remove_map(map_path)
    return plan


class WebsocketTransport:
    """Home Assistant websocket client (aiohttp), authenticated with a token."""

    def __init__(self, url: str, token: str) -> None:
        """Remember the endpoint; the token is only ever sent to it."""
        self._url = url.rstrip("/") + "/api/websocket"
        self._token = token
        self._next_id = 0
        self._session: Any = None
        self._ws: Any = None

    async def __aenter__(self) -> Self:
        """Connect and authenticate."""
        import aiohttp  # noqa: PLC0415  # only needed for a real run

        self._session = aiohttp.ClientSession()
        self._ws = await self._session.ws_connect(self._url)
        first = await self._ws.receive_json()
        if first.get("type") != "auth_required":
            msg = "unexpected reply from Home Assistant"
            raise RuntimeError(msg)
        await self._ws.send_json({"type": "auth", "access_token": self._token})
        reply = await self._ws.receive_json()
        if reply.get("type") != "auth_ok":
            msg = "Home Assistant rejected the token"
            raise PermissionError(msg)
        return self

    async def __aexit__(self, *_exc: object) -> None:
        """Close the connection."""
        if self._ws is not None:
            await self._ws.close()
        if self._session is not None:
            await self._session.close()

    async def call(self, message: dict[str, Any]) -> Any:
        """Send one command and wait for its result."""
        self._next_id += 1
        await self._ws.send_json({**message, "id": self._next_id})
        while True:
            reply = await self._ws.receive_json()
            if reply.get("id") != self._next_id:
                continue
            if not reply.get("success"):
                error = reply.get("error", {})
                msg = f"{message['type']} failed: {error.get('message', 'unknown')}"
                raise RuntimeError(msg)
            return reply.get("result")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse the command line."""
    parser = argparse.ArgumentParser(
        description="Rename Elk E27 entities back to saved names (dry run by default)."
    )
    parser.add_argument(
        "names",
        nargs="?",
        type=Path,
        help="CSV with entity_id,name columns, or with --from-markdown the saved "
        "names table (grokbots/home/elk-entity-names-pre-0.2.1.md)",
    )
    parser.add_argument(
        "--from-markdown",
        action="store_true",
        help="read NAMES as the Markdown names table instead of CSV",
    )
    parser.add_argument(
        "--original-name",
        action="store_true",
        help="with --from-markdown: use the bare override or original_name "
        "(no device name) instead of the exact old displayed name",
    )
    parser.add_argument(
        "--to-csv",
        type=Path,
        metavar="CSV",
        help="only convert NAMES to an entity_id,name CSV and exit (no connection)",
    )
    parser.add_argument("--url", help="e.g. ws://homeassistant.local:8123")
    parser.add_argument("--apply", action="store_true", help="write (default: dry run)")
    parser.add_argument("--map", type=Path, help="where --apply saves the undo map")
    parser.add_argument(
        "--force",
        action="store_true",
        help="let --apply overwrite an existing --map file",
    )
    parser.add_argument(
        "--undo", type=Path, metavar="MAP", help="restore from a saved map"
    )
    args = parser.parse_args(argv)
    if (args.names is None) == (args.undo is None):
        parser.error("give either a names CSV or --undo MAP")
    if args.to_csv is None and not args.url:
        parser.error("--url is required")
    return args


def load_names(args: argparse.Namespace) -> dict[str, str]:
    """Read the names file in the format chosen on the command line."""
    if args.from_markdown:
        return read_names_markdown(args.names, original_name=args.original_name)
    return read_names(args.names)


async def amain(args: argparse.Namespace) -> int:
    """Run the chosen mode against Home Assistant."""
    token = os.environ.get("HA_TOKEN") or getpass.getpass("Home Assistant token: ")
    async with WebsocketTransport(args.url, token) as transport:
        if args.undo is not None:
            plan = await run_undo(transport, args.undo, apply=args.apply)
        else:
            plan = await run_rename(
                transport,
                load_names(args),
                apply=args.apply,
                map_path=args.map,
                force=args.force,
            )
    print(format_plan(plan, apply=args.apply))  # noqa: T201  # CLI output
    return 0


def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    args = parse_args(argv)
    try:
        if args.to_csv is not None:
            names = load_names(args)
            write_csv(args.to_csv, names)
            print(f"wrote {len(names)} rows to {args.to_csv}")  # noqa: T201  # CLI output
            return 0
        return asyncio.run(amain(args))
    except (ValueError, RuntimeError, PermissionError, OSError) as err:
        print(f"error: {err}", file=sys.stderr)  # noqa: T201  # CLI output
        return 1


if __name__ == "__main__":
    sys.exit(main())
