"""Constants for the Elke27 integration."""

DOMAIN = "elke27"

DEFAULT_PORT = 2101
READY_TIMEOUT = 30

CONF_LINK_KEYS_JSON = "link_keys_json"
CONF_PANEL = "panel"
# Flow-generated client sn sent to the panel: 12 digits, no leading zero.
CONF_INTEGRATION_ID = "integration_id"
# Panel MAC from UDP discovery (MAC_ADDR), 12 uppercase hex, no separators.
CONF_PANEL_MAC = "panel_mac"
# True when CONF_PANEL_MAC was filled in on an entry created without it (0.1.8
# entries, or reauth). Such entries keep their original entity identity base.
CONF_PANEL_MAC_FILLED = "panel_mac_filled"
# 0.1.x client sn derived on the HA host; only read by the 0.1.x -> 0.2.x upgrade,
# which removes it once the registry is migrated.
CONF_INTEGRATION_SERIAL = "integration_serial"
# Upgrade record of a 0.1.x entry: {"from", "state", "plan", "duplicates", "done_at"}.
CONF_LEGACY_MIGRATION = "legacy_registry_migration"
MIGRATION_STATE_PENDING = "pending"
MIGRATION_STATE_DONE = "done"
ISSUE_LEGACY_MIGRATION_BLOCKED = "legacy_migration_blocked"
ISSUE_LEGACY_DUPLICATES = "legacy_duplicates"
# Same issue id as blocked; different text when an undo could not revert every write.
ISSUE_LEGACY_MIGRATION_PARTIAL = "legacy_migration_partial"
# Same issue id as blocked; a registry write failed and every write was undone.
ISSUE_LEGACY_MIGRATION_WRITE_FAILED = "legacy_migration_write_failed"
DISCOVER_HOST_TIMEOUT = 3.0
ISSUE_PANEL_MAC_MISMATCH = "panel_mac_mismatch"

MANUFACTURER_NUMBER = 222
INTEGRATION_ID_LENGTH = 12

ISSUE_RECONNECT_FAILED = "reconnect_failed"
RECONNECT_NON_TRANSPORT_ISSUE_THRESHOLD = 3
# Reconnect backoff: 2**n seconds, capped here before jitter (x0.8-1.2), so
# the panel is retried at least about every 72 s during a long outage (#79).
RECONNECT_BACKOFF_MAX = 60
