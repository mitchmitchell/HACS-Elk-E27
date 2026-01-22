# Elke27 v2 Public API (HA-first)

This document describes the v2 public API surface for `elke27_lib`. The v2
surface is async-only, exceptions-only, and intentionally hides protocol and
session details.

## Design guarantees

- Async-only API methods for I/O and commands.
- Exceptions-only error reporting (no Result return types).
- Immutable, typed events.
- Immutable, atomic snapshots; replace snapshots wholesale on updates.
- No protocol/session objects exposed in the public API.
- No secrets logged or emitted in diagnostics.

## Usage example

```python
from elke27_lib import (
    Elke27Client,
    ClientConfig,
    ArmMode,
    redact_for_diagnostics,
)

config = ClientConfig()
client = Elke27Client(config)

async def main():
    # discovery -> link -> connect -> wait_ready
    panels = await client.async_discover(timeout_s=10.0)
    panel = panels[0]
    link_keys = await client.async_link(
        panel.host,
        panel.port,
        access_code="1234",
        passphrase="secret",
        client_identity={"mn": "HA", "sn": "SN", "fwver": "0", "hwver": "0", "osver": "0"},
        timeout_s=10.0,
    )
    await client.async_connect(panel.host, panel.port, link_keys)
    ready = client.wait_ready(timeout_s=10.0)
    if not ready:
        raise RuntimeError("Client not ready")

    # subscribe to events
    def on_event(evt):
        print(evt.event_type, evt.data)

    unsubscribe = client.subscribe(on_event)

    # async event stream
    async for event in client.events():
        print(event.event_type, event.seq, event.timestamp)
        break

    # read snapshot
    snapshot = client.snapshot
    print(snapshot.panel, snapshot.table_info)

    # issue commands (no optimistic updates; state updates arrive via events/snapshot)
    await client.async_set_output(output_id=1, on=True)
    await client.async_arm_area(area_id=1, mode=ArmMode.ARMED_STAY, pin="1234")
    await client.async_disarm_area(area_id=1, pin="1234")

    # cleanup
    unsubscribe()
    await client.async_disconnect()

# The v2 surface is currently a spec-first skeleton and may raise
# NotImplementedError until wired to the protocol layer.
```

## Notes

- `access_code` is only used for linking (integration authentication).
- `pin` is the runtime user PIN for authorization on privileged commands.
- Events are immutable; treat each event as a point-in-time observation.
- Snapshot updates are atomic: a new immutable PanelSnapshot is published each time.
- Snapshots include a monotonic version field and updated_at timestamp.
- Event queue is bounded; when full, the oldest event is dropped to make room.
- Subscriber callbacks are invoked synchronously and must not block.
- Diagnostic helpers such as `redact_for_diagnostics` remove likely secrets
  from structured data before logging.
