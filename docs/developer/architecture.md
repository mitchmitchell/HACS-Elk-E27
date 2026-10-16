# Architecture (0.2.0)

- **Coordinator owns the client** (`coordinator.py`, D3): `Elke27DataUpdateCoordinator`
  creates, connects and disconnects the elke27 client, subscribes to events, runs commands
  and publishes the `PanelSnapshot`. Platforms read `coordinator.data` and call coordinator
  methods; nothing else holds the client.
- **Reconnect supervisor** (`reconnect.py`, D2): `ReconnectSupervisor` retries with backoff
  after a link drop, raises the Repairs issue when reconnect keeps failing, and starts
  re-auth once when the link keys are rejected.
- **No `hub.py`:** the old hub module is removed with no shim (decision 13).
- **Identity** (`identity.py`, `panel_mac.py`, E1): a random 12-digit integration ID per
  entry, sent to the panel as the client serial.
- **Devices** (`entity.py`, E2): one device per area (`area_has_device`), zones under areas.
- **Post-command refresh** (D4): commands check the library's status refresh result and do
  one fallback read when it failed; counters are exposed in diagnostics (`diagnostics.py`, D5).
