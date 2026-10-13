"""Constants for the Elke27 integration."""

DOMAIN = "elke27"

DEFAULT_PORT = 2101
READY_TIMEOUT = 30

CONF_LINK_KEYS_JSON = "link_keys_json"
CONF_PANEL = "panel"
CONF_INTEGRATION_SERIAL = "integration_serial"
# Panel MAC from UDP discovery (MAC_ADDR), 12 uppercase hex, no separators.
CONF_PANEL_MAC = "panel_mac"
# True when CONF_PANEL_MAC was filled in on an entry created without it (0.1.8
# entries, or reauth). Such entries keep their original entity identity base.
CONF_PANEL_MAC_FILLED = "panel_mac_filled"
DISCOVER_HOST_TIMEOUT = 3.0
ISSUE_PANEL_MAC_MISMATCH = "panel_mac_mismatch"

MANUFACTURER_NUMBER = 222
INTEGRATION_SERIAL_LENGTH = 12

ISSUE_RECONNECT_FAILED = "reconnect_failed"
RECONNECT_NON_TRANSPORT_ISSUE_THRESHOLD = 3
