Update/clarification: There is NO requirement for backward compatibility with M1 users for this work. This refactor applies only to the Elke27 integration/library path.

Goal: Refactor the Home Assistant “Elke27” integration to split the current monolithic hub.py into:
  - a smaller hub.py (connection/session + thin facade)
  - a new coordinator.py (Home Assistant DataUpdateCoordinator + event ingestion)

Core principles:
- Keep changes small and testable; avoid unrelated refactors.
- Coordinator owns HA update signaling and refresh cadence/coalescing.
- Hub owns only connection + library client lifecycle + thin “refresh primitives”.
- The Elke27 library remains responsible for CSM parsing/diffing/semantic events; HA must not parse raw payload dicts.

Inputs:
- Use the existing Elke27 integration codebase and current hub.py.
- Follow the architecture contract:
  - library emits stable CSM snapshot + semantic change events
  - HA coordinator consumes those events and triggers selective refresh

Work to do (file-by-file):

1) Create coordinator.py
- Add class Elke27DataUpdateCoordinator(DataUpdateCoordinator[PanelSnapshot or appropriate snapshot type])
- Responsibilities:
  a) subscribe to library event bus on startup
  b) keep latest snapshot in coordinator.data
  c) trigger selective refresh when CSM indicates config changes
  d) call async_set_updated_data(...) when snapshot changes or relevant events arrive
  e) expose async_refresh_now() for manual refresh/service flows

Coordinator behavior details:
- At init: read current library snapshot if available; else data=None.
- Subscribe to events:
  - CsmSnapshotUpdated (primary)
  - DomainCsmChanged/TableCsmChanged (optional)
  - Inventory/config updated events per domain (zone/area/output/tstat) to push new snapshot.
- On CsmSnapshotUpdated:
  - Determine diffs using library-provided diff OR compare snapshots (but DO NOT parse raw dict payloads).
  - If any tracked domain/table changed:
    - await hub.refresh_domain_config(domain) (or hub.refresh_changed_domains(list))
    - ensure refresh uses library’s single-in-flight request pipeline (ADR-0111 / DDR-0037)
  - Then push updated data via async_set_updated_data(hub.get_snapshot()).
- Event storms:
  - Coalesce refresh triggers using an asyncio.Lock + short debounce (0.25–0.5s).
  - If a refresh is already running, merge additional “changed domains” into a pending set.

Polling:
- Prefer event-driven updates.
- If a safety poll is needed, implement coordinator.update_method as a lightweight:
  - await hub.refresh_csm()
  - then async_set_updated_data(hub.get_snapshot())
  - Use a conservative interval; do NOT run full bootstrap as polling.

2) Shrink hub.py
- Hub responsibilities after refactor:
  - create/manage library client + kernel/session lifecycle (connect/disconnect/auth/bootstrap)
  - expose stable methods used by coordinator/platforms:
    - async_connect()/async_disconnect()
    - async_start()/async_stop() (if present)
    - get_snapshot() -> snapshot type used by coordinator
    - refresh_csm() -> await library.refresh_csm()
    - refresh_domain_config(domain: str) -> await library.refresh_<domain>_config() or dispatcher
    - register/unregister event listeners (thin wrappers if needed)
  - expose minimal properties:
    - hub.client
    - hub.connected
    - hub.device_info / identifiers (if applicable)
- Hub must NOT:
  - implement DataUpdateCoordinator logic
  - call async_set_updated_data
  - implement throttling/in-flight enforcement (library owns it)

3) Wire coordinator into __init__.py
- In async_setup_entry:
  - create hub
  - await hub.async_connect()/async_start()
  - create coordinator with hub reference
  - start coordinator (subscribe events) and optionally run a lightweight first refresh (CSM pass)
- Store in hass.data[DOMAIN][entry_id]:
  - "hub": hub
  - "coordinator": coordinator

4) Update platforms/entities to use coordinator
- Entities should read from coordinator.data instead of hub internal state.
- Replace any direct hub polling patterns with coordinator async updates.
- For actions/services:
  - call hub methods that enqueue commands via library
  - optionally call coordinator.async_refresh_now() if UI should update immediately

5) Add minimal HA tests
- Tests to cover:
  - coordinator created and stored on setup
  - coordinator subscribes to library events and calls async_set_updated_data on snapshot change
  - on CsmSnapshotUpdated indicating “zone changed”, coordinator calls hub.refresh_domain_config("zone")
  - no refresh when no diffs
  - coalescing: multiple CSM events trigger one refresh pipeline

Implementation notes:
- Do not parse raw dict payloads in HA to find *_csm or table_csm.
- Prefer library-provided snapshot/diff + semantic events.
- Keep logging clear, English, and actionable.

Deliverable:
- Provide git diff patches as downloadable files:
  - new coordinator.py
  - modified hub.py
  - modified __init__.py
  - any touched platform/entity files
  - tests
- No backward-compat constraints with M1 code paths.
