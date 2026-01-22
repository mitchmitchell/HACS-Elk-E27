
  Public Client Surface (stable import path)

  - Public import path is elke27_lib.client and HA should use from elke27_lib.client import Elke27Client, Result only. I kept this as the single public entry point
    and avoided HA importing internals.
  - Client construction is lightweight and non‑blocking; feature module imports are deferred to connect() and run off‑loop in async contexts. Docs updated to make
    this explicit.

  Connect/Identity/Stateless Behavior

  - Elke27Client.connect() now requires explicit context every time (panel or session_config, plus protocol identity), and does not rely on any prior link() state or
    stored panel info. The Elk layer no longer stores panel/identity between connects.
  - Protocol identity is consistently named client_identity (no “identity” alias); error messages say “client_identity”.
  - The library explicitly does not store persistence or link keys on disk; HA owns panel info and persistence.

  Readiness Semantics

  - is_ready is now strictly defined and documented as: Session ACTIVE + panel_info present + table_info structure present. It’s monotonic until disconnect.
  - table_info may be placeholders; HA must tolerate missing counts.
  - Added bootstrap_complete_counts (Option A) to indicate when real table_info counts for all domains are available. This flips False→True when real counts arrive.
    (Optional “counts ready” event emitted.)

  Events / Subscription

  - Subscription callbacks receive semantic events only (no raw frames/bytes).
  - subscribe() / unsubscribe() are safe during dispatch; callbacks should not block.
  - Authorization required is surfaced as semantic event (and/or typed exception on command, depending on the command); no PIN prompting in library.

  Typed Error Taxonomy

  - Stable typed errors exposed: AuthorizationRequired, InvalidCredentials (or InvalidLinkKeys), ConnectionLost, ProtocolError/CryptoError. HA sees these via raised
    exceptions and/or semantic events.
  - Missing connect context now maps to a specific missing‑context error code, not AUTH_REQUIRED.

  Snapshots / Inventory Filtering

  - Snapshots are views (not deep copies) for panel_info, table_info, areas, zones, outputs, lights, thermostats.
  - Areas/zones snapshots are filtered to configured IDs only. No capacity slots are exposed to HA.
  - Configured inventory is fetched via area.get_configured / zone.get_configured with paging (block_id/block_count), reassembled in dispatcher with ADR‑0013 rules.
  - Authorization‑required on get_configured surfaces an AuthorizationRequired event; inventory remains partial and HA must tolerate missing configured sets.

  Name Loading

  - Names are loaded via per‑ID area.get_attribs / zone.get_attribs after configured inventory completes.
  - Added AreaState.name / ZoneState.name (Optional[str]) with normalization.
  - Rate‑limited/queued attrib requests to avoid floods; invalid_id is treated as terminal (no retries).

  Discovery Output

  - discovery returns panel identity only: panel_mac, panel_name, panel_serial, panel_host/port; no protocol client identity and no ambiguous “identity” key.

  Sequencing & Paging Reliability (HA‑visible stability)

  - JSON seq now wraps per ADR‑0013, never zero for requests.
  - Encrypted envelope sequence is per‑session and monotonic.
  - Paging reassembly is correlation‑keyed, out‑of‑order safe, and only dispatches fully assembled payloads.
  - Transfers time out and abort cleanly on disconnect; keepalive responses are internal.

  Recent Fixes Directly Impacting HA

  - Paged configured responses now treat string block_id/block_count as paged data, preventing fallback to bitmask and eliminating phantom zones 51‑64 on panels that
    send numeric strings.
  - Added guard for ids above known table sizes and invalid_id streak handling to stop probing.
  - Added inflight window and retry policy: invalid_id is terminal; timeouts retry with backoff.
  - Root‑empty responses now include payload in diagnostic events.
