# Client Contract Summary

Public import path:
- `from elke27_lib.client import Elke27Client, Result`

Client class:
- `Elke27Client`
- Client construction is non-blocking; feature module loading happens during `connect()`
  and is executed off-loop when called from async contexts (e.g., Home Assistant).
Discovery returns panel identity (panel_mac/panel_name/panel_serial/panel_host/port/tls_port);
connect uses client_identity for HELLO.

Connect / disconnect:
- `connect(link_keys, *, panel=None, client_identity=Elke27Identity, session_config=None)` is async and returns `Result[None]`.
- `disconnect()` is async (alias of `close()`) and returns `Result[None]`.

Readiness:
- `is_ready` and `wait_ready(timeout_s=...)`.
- Ready = Session ACTIVE + `panel_info.session_id` present + `table_info` present.
- `table_info` counts may be placeholders (MISSING) initially; HA must tolerate missing values.
- Actual table_info population is asynchronous and arrives via events/updates.
- `bootstrap_complete_counts` is False until real counts arrive for all core domains.

Events:
- `subscribe(callback, kinds=None)` / `unsubscribe(callback)`.
- Callbacks receive semantic `Event` objects only (no raw frames/bytes).
- Callbacks must be non-blocking; subscribe/unsubscribe are safe during dispatch.
- Optional event: `bootstrap_counts_ready` when all domain counts are known.

Snapshots (read-only views):
- `panel_info`
- `table_info`
- `areas`
- `zones`
- `outputs`
- `lights`
- `thermostats`

Configured inventory filtering:
- `areas` and `zones` snapshots include only configured ids reported by the panel.
- `outputs`, `lights`, and `thermostats` snapshots are filtered to `table_info` element counts when known.
- Inventory is populated via `area.get_configured` / `zone.get_configured` (paged by block_id/block_count).
- While paging is in progress, snapshots include the configured ids known so far (may be empty).
- If auth is required for configured inventory (error_code 11008), snapshots may remain empty until authenticated.
- Optional events: `area_configured_inventory_ready` / `zone_configured_inventory_ready` fire when paging completes.
- After inventory completes, the library issues per-id `get_attribs` requests to populate `name`.
- Attribute requests are filtered to configured ids (and capped by table_info table_elements when known); toggle via `Elke27Client(filter_attribs_to_configured=...)`.
- `name` values are normalized (trimmed); empty names become `None`.

Commands:
- Inventory-driven calls via `request(route, **kwargs)` (may have helper methods).
- Returns `Result[T]` with `Result.ok / Result.data / Result.error`.
- Use `Result.unwrap()` to raise typed errors.

Typed errors:
- `AuthorizationRequired`: session established, but auth/PIN required.
- `InvalidCredentials` / `InvalidLinkKeys`: cannot establish session/bootstrap.
- `MissingContext`: panel/client_identity/session context missing for connect.
- `ConnectionLost`: socket dropped/reset while connected.
- `ProtocolError` / `CryptoError` (CryptoFailure): framing/CRC/crypto/parse failure.

AuthorizationRequired can also be emitted as a semantic event (`AuthorizationRequiredEvent`)
when the panel responds with error_code `11008` for auth-gated calls (e.g., `network.get_ssid`).
