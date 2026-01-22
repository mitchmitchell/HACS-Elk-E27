# Elke27Client Contract (Stable During Refactor)

This document defines the stable, immutable public contract for the Elke27 client
API for the duration of the refactor. Home Assistant will consume this verbatim.

Treat the client contract below as immutable. Do not rename methods/properties,
do not add required args, and do not change return types without flagging it as
a breaking change.

## Public Surface (Single Import Path)

- Home Assistant must import only:
  - `from elke27_lib.client import Elke27Client, Result`
- Public names are stable; do not import `elk.py` or other internals.

## Boundary Rules (No Cross-Layer Imports)

- Library: do not import Home Assistant or reference HA types.
- Home Assistant: do not import internal library modules (`session`, `dispatcher`,
  `features`, etc.). Only import the public client object.

## Lifecycle

- `connect(link_keys, *, panel=None, client_identity=Elke27Identity, session_config=None)` establishes the session
  and transitions the client to ready.
- `disconnect()` closes the session cleanly.
- Client construction is non-blocking; feature module loading happens during `connect()` and
  is executed off-loop when called from async contexts (e.g., Home Assistant).
- Discovery returns panel identity (panel_mac/panel_name/panel_serial/panel_host/port/tls_port);
  connect uses client_identity for HELLO.
- Readiness source of truth:
  - Ready = Session ACTIVE AND `panel_info.session_id` present AND `table_info` present.
- `table_info` counts may be placeholders (MISSING) initially; HA must tolerate missing values.
- Actual table_info population is asynchronous and arrives via events/updates.
- `bootstrap_complete_counts` is False until real counts arrive for all core domains.
- Readiness API:
  - Use `is_ready` + `wait_ready(timeout_s=...)` (no separate "ready" event contract).

## Events

- `subscribe(callback, kinds=None)` registers an event callback.
- `unsubscribe(callback)` removes the callback.
- Callbacks receive semantic events only (no raw protocol payloads).
- Every event has:
  - `kind` (string)
  - `domain` (string)
  - Standard stamped header fields (route/seq/session_id) as defined by the event base type.
- Ordering/dispatch:
  - callbacks must not block
  - subscribe/unsubscribe must be safe during dispatch
- Optional event: `bootstrap_counts_ready` when all domain counts are known.

## State Snapshots (read-only views)

- `panel_info`
- `table_info`
- `areas`
- `zones`
- `outputs`
- `lights`
- `thermostats`
- Snapshots return read-only views or dataclasses; do not deep-copy large structures.

## Configured Inventory Filtering

- `areas` and `zones` snapshots are filtered to configured ids reported by the panel.
- `outputs`, `lights`, and `thermostats` snapshots are filtered to `table_info` element counts when known.
- Inventory is populated via `area.get_configured` / `zone.get_configured` using paging:
  - Start with `block_id=1`
  - Response includes `block_count`
  - Continue requesting `block_id=2..block_count`
- While paging is in progress, snapshots include configured ids known so far (may be empty).
- If the panel requires authorization (error_code `11008`), inventory may be unavailable until authenticated.
- Optional events: `area_configured_inventory_ready` / `zone_configured_inventory_ready` signal completion.
- After inventory completes, the library issues per-id `get_attribs` requests to populate `name`.
- Attribute requests are filtered to configured ids (and capped by table_info table_elements when known); toggle via `Elke27Client(filter_attribs_to_configured=...)`.
- `name` fields are trimmed; empty names are normalized to `None`.

## Commands

- Inventory-driven methods are exposed via `request(route, **kwargs)` and may also have
  explicit helper methods (e.g. `get_zone_status(id)`).
- Commands return `Result[T]`:
  - `Result(ok: bool, data: T | None, error: E27Error | None)`
- Commands raise typed errors when `Result.unwrap()` is used.

## Errors (typed)

- `AuthorizationRequired`: credentials valid, session established, but operation requires authentication/PIN.
- `InvalidCredentials` / `InvalidLinkKeys`: cannot establish session/bootstrap.
- `InvalidPin`: PIN rejected by the panel during authenticate.
- `MissingContext`: panel/client_identity/session context missing for connect.
- `ConnectionLost`: socket dropped/reset while connected.
- `ProtocolError` / `CryptoError`: framing/CRC/crypto/parse failure.

Errors are surfaced via raised exceptions from command helpers or `Result.unwrap()`.
AuthorizationRequired may also be emitted as a semantic event (`AuthorizationRequiredEvent`)
when the panel responds with error_code `11008` for auth-gated calls.

## Threading Model Rules

- Library: no new threads unless already used; if background tasks are required,
  use the existing concurrency model.
- Subscribe callbacks must be invoked from the same dispatch thread/task context
  (or via a queue); do not call Home Assistant APIs.
- Home Assistant: all I/O and library calls are async; never block the event loop.
- Session auto-receive uses `asyncio.to_thread(...)` when a running event loop exists.
- Dedicated receiver thread fallback is disabled by default; enable
  `SessionConfig.auto_receive_thread_fallback=True` to allow it in non-async contexts.

## Persistence

- The library must not write files or store link keys on disk.
- Persistence is Home Assistant's responsibility.
