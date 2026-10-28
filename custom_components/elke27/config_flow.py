"""Config flow for the Elke27 integration."""

from __future__ import annotations

import contextlib
from dataclasses import asdict, is_dataclass
import logging
from typing import TYPE_CHECKING, Any

from elke27_lib import ClientConfig, LinkKeys
from elke27_lib.client import Elke27Client
from elke27_lib.discovery import AIOELKDiscovery
from elke27_lib.errors import (
    E27Error,
    Elke27AuthError,
    Elke27ConnectionError,
    Elke27CryptoError,
    Elke27DisconnectedError,
    Elke27Error,
    Elke27InvalidArgument,
    Elke27LinkRequiredError,
    Elke27TimeoutError,
    InvalidCredentials,
)
import probatio

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import (
    config_validation as cv,
    issue_registry as ir,
    translation,
)
from homeassistant.helpers.device_registry import format_mac
from homeassistant.helpers.selector import selector

from .const import (
    CONF_INTEGRATION_ID,
    CONF_LINK_KEYS_JSON,
    CONF_PANEL,
    CONF_PANEL_MAC,
    DEFAULT_PORT,
    DOMAIN,
    READY_TIMEOUT,
)
from .identity import (
    build_client_identity,
    config_entry_unique_id,
    generate_integration_id,
    is_valid_integration_id,
    panel_identity_matches,
    panel_mac_from_info,
    panel_serial_tier,
)
from .migration import legacy_migration_pending, legacy_reauth_data_updates
from .panel_mac import (
    async_discover_panel_mac,
    async_reauth_mac_fill,
    normalize_panel_mac,
    panel_mac_from_discovered,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

_LOGGER = logging.getLogger(__name__)

# Elke27LinkSlotsFullError.code (elke27 0.3.15+): the panel has no free client slot.
LINK_SLOTS_FULL_CODE = "link_slots_full"


def _link_error_base(err: Elke27Error) -> str:
    """
    Map a remaining library error to a form error.

    elke27 0.3.15 (the pinned version) raises Elke27LinkSlotsFullError (code
    "link_slots_full") when the panel's client-link table is full. This still
    matches on the public error code; a follow-up can replace it with an
    ``except Elke27LinkSlotsFullError`` and drop this helper and the constant.
    """
    return "no_link_slots" if err.code == LINK_SLOTS_FULL_CODE else "unknown"


CONF_ACCESS_CODE = "access_code"
CONF_PASSPHRASE = "passphrase"
CONF_PANEL_INFO = "panel_info"
CONF_TABLE_INFO = "table_info"
CONF_RESCAN = "rescan"
CONF_SETUP_METHOD = "setup_method"
SETUP_METHOD_DISCOVER = "discover"
SETUP_METHOD_MANUAL = "manual"

STEP_MANUAL_DATA_SCHEMA = probatio.Schema(
    {
        probatio.Required(CONF_HOST): cv.string,
        probatio.Required(CONF_ACCESS_CODE): selector({"text": {"type": "password"}}),
        probatio.Required(CONF_PASSPHRASE): selector({"text": {"type": "password"}}),
    }
)

STEP_LINK_DATA_SCHEMA = probatio.Schema(
    {
        probatio.Required(CONF_ACCESS_CODE): selector({"text": {"type": "password"}}),
        probatio.Required(CONF_PASSPHRASE): selector({"text": {"type": "password"}}),
    }
)

STEP_REAUTH_DATA_SCHEMA = STEP_LINK_DATA_SCHEMA

STEP_USER_DATA_SCHEMA = probatio.Schema(
    {
        probatio.Required(CONF_SETUP_METHOD, default=SETUP_METHOD_DISCOVER): selector(
            {
                "select": {
                    "options": [SETUP_METHOD_DISCOVER, SETUP_METHOD_MANUAL],
                    "mode": "list",
                    "translation_key": CONF_SETUP_METHOD,
                }
            }
        )
    }
)


def _duplicate_unique_id_issue_id(entry_id: str) -> str:
    return f"duplicate_unique_id_{entry_id}"


@callback
def _async_try_backfill_config_entry_unique_id(
    hass: HomeAssistant,
    entry: ConfigEntry,
    candidate: str,
) -> None:
    """Assign unique_id when missing unless another entry already owns it."""
    existing = hass.config_entries.async_entry_for_domain_unique_id(DOMAIN, candidate)
    issue_id = _duplicate_unique_id_issue_id(entry.entry_id)
    if existing is not None and existing.entry_id != entry.entry_id:
        _LOGGER.warning(
            "Cannot assign unique_id %s to Elke27 entry %s: already used by %s",
            candidate,
            entry.entry_id,
            existing.entry_id,
        )
        ir.async_create_issue(
            hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key="duplicate_unique_id",
            translation_placeholders={
                "candidate": candidate,
                "other_entry": existing.title or existing.entry_id,
            },
        )
        return
    hass.config_entries.async_update_entry(entry, unique_id=candidate)
    ir.async_delete_issue(hass, DOMAIN, issue_id)


class Elke27ConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Elke27."""

    # 2: 0.2.x identity; 0.1.x (VERSION 1) entries are migrated in place, and an
    # older release refuses to load a VERSION 2 entry instead of mis-reading it.
    VERSION = 2
    MINOR_VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow."""
        self._selected_panel: Any | None = None
        self._selected_host: str | None = None
        self._selected_port: int | None = None
        # Drawn once per flow, so a retried link reuses the same client sn.
        self._integration_id: str | None = None
        self._discovered_panels: list[Any] | None = None
        self._manual_bootstrap_errors: dict[str, str] | None = None

    async def _async_enter_manual_from_empty_discovery(self) -> ConfigFlowResult:
        """Leave discover when no panel target exists; manual host entry is next."""
        self._manual_bootstrap_errors = {"base": "no_panels_found"}
        self._discovered_panels = []
        self._selected_panel = None
        self._selected_host = None
        self._selected_port = None
        return await self.async_step_manual()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        if user_input is not None:
            if user_input[CONF_SETUP_METHOD] == SETUP_METHOD_MANUAL:
                return await self.async_step_manual()
            return await self.async_step_discover()

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
        )

    async def async_step_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle manual setup."""
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST]
            port = DEFAULT_PORT
            self._selected_host = host
            self._selected_port = port
            self._selected_panel = None
            self._async_abort_entries_match({CONF_HOST: host, CONF_PORT: port})
            return await self._async_link_and_create_entry(
                access_code=user_input[CONF_ACCESS_CODE],
                passphrase=user_input[CONF_PASSPHRASE],
                errors=errors,
                step_id="manual",
                data_schema=STEP_MANUAL_DATA_SCHEMA,
            )

        if self._manual_bootstrap_errors:
            errors = {**self._manual_bootstrap_errors}
            self._manual_bootstrap_errors = None

        return self.async_show_form(
            step_id="manual",
            data_schema=STEP_MANUAL_DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_discover(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle discovery-based setup."""
        errors: dict[str, str] = {}
        if user_input is not None:
            data_schema = await self._async_discovery_schema()
            if CONF_PANEL in user_input:
                panel_idx_raw = user_input[CONF_PANEL]
                if panel_idx_raw == CONF_RESCAN:
                    self._discovered_panels = None
                    return await self.async_step_discover(None)
                panel_idx = int(panel_idx_raw)
                if not self._discovered_panels or panel_idx >= len(
                    self._discovered_panels
                ):
                    return await self._async_enter_manual_from_empty_discovery()
                panel = self._discovered_panels[panel_idx]
                host = getattr(panel, "panel_host", None) or getattr(
                    panel, "host", None
                )
                port = getattr(panel, "port", None) or DEFAULT_PORT
                if not host:
                    return await self._async_enter_manual_from_empty_discovery()
                self._selected_host = host
                self._selected_port = int(port)
                self._selected_panel = panel
            else:
                data_schema = STEP_LINK_DATA_SCHEMA
            if self._selected_panel is None:
                return await self._async_enter_manual_from_empty_discovery()
            if self._is_panel_configured(self._selected_panel):
                return self.async_abort(reason="already_configured")
            return await self._async_link_and_create_entry(
                access_code=user_input[CONF_ACCESS_CODE],
                passphrase=user_input[CONF_PASSPHRASE],
                errors=errors,
                step_id="discover",
                data_schema=data_schema,
            )

        if self._discovered_panels is None:
            discovery = AIOELKDiscovery()
            self._discovered_panels = self._dedupe_panels(await discovery.async_scan())
        if not self._discovered_panels:
            return await self._async_enter_manual_from_empty_discovery()
        if len(self._discovered_panels) == 1:
            panel = self._discovered_panels[0]
            self._selected_panel = panel
            self._selected_host = getattr(panel, "panel_host", None) or getattr(
                panel, "host", None
            )
            self._selected_port = int(getattr(panel, "port", None) or DEFAULT_PORT)
            return self.async_show_form(
                step_id="discover",
                data_schema=STEP_LINK_DATA_SCHEMA,
                errors=errors,
            )

        return self.async_show_form(
            step_id="discover",
            data_schema=await self._async_discovery_schema(),
            errors=errors,
        )

    async def _async_rescan_option_label(self) -> str:
        """Return the translated label for the rescan select option."""
        translations = await translation.async_get_translations(
            self.hass,
            self.hass.config.language,
            "selector",
            integrations=[DOMAIN],
        )
        key = f"component.{DOMAIN}.selector.panel.options.{CONF_RESCAN}"
        fallback = "Rescan for panels"
        return translations.get(key, fallback)

    async def _async_discovery_schema(self) -> probatio.Schema:
        """Build the discover-step schema with a translated rescan option."""
        return self._discovery_schema(await self._async_rescan_option_label())

    async def async_step_reauth(
        self, _entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Handle reauth when the stored link is missing or no longer accepted."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Relink the same panel using the access code and passphrase."""
        errors: dict[str, str] = {}
        if user_input is not None:
            entry = self._get_reauth_entry()
            self._selected_host = entry.data[CONF_HOST]
            self._selected_port = entry.data[CONF_PORT]
            self._selected_panel = entry.data.get(CONF_PANEL)
            return await self._async_link_and_create_entry(
                access_code=user_input[CONF_ACCESS_CODE],
                passphrase=user_input[CONF_PASSPHRASE],
                errors=errors,
                step_id="reauth_confirm",
                data_schema=STEP_REAUTH_DATA_SCHEMA,
                entry=entry,
            )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=STEP_REAUTH_DATA_SCHEMA,
            errors=errors,
        )

    def _flow_integration_id(self, entry: ConfigEntry | None) -> str:
        """
        Return the client sn for this link.

        Reauth keeps the entry's integration ID (the panel binds the link to
        it). A new entry, or one without a valid ID, gets one fresh ID per
        flow, so a retried link reuses the same sn.
        """
        existing_id = entry.data.get(CONF_INTEGRATION_ID) if entry is not None else None
        if is_valid_integration_id(existing_id):
            return existing_id
        if self._integration_id is None:
            self._integration_id = generate_integration_id()
        return self._integration_id

    async def _async_link_and_create_entry(
        self,
        access_code: str,
        passphrase: str,
        errors: dict[str, str],
        step_id: str,
        data_schema: probatio.Schema,
        entry: ConfigEntry | None = None,
    ) -> ConfigFlowResult:
        """Link, connect, fetch snapshots, and create/update the entry."""
        host = self._selected_host
        port = self._selected_port
        if host is None or port is None:
            _LOGGER.warning(
                "Elke27 link (%s) not started: entry has no host/port", step_id
            )
            errors["base"] = "unknown"
            return self.async_show_form(
                step_id=step_id,
                data_schema=data_schema,
                errors=errors,
            )

        integration_id = self._flow_integration_id(entry)
        client_identity = build_client_identity(integration_id)
        client = _create_client()
        link_keys: LinkKeys | None = None
        panel_info: dict[str, Any] = {}
        table_info: dict[str, Any] = {}
        try:
            link_keys = await client.async_link(
                host=host,
                port=port,
                access_code=access_code,
                passphrase=passphrase,
                client_identity=client_identity,
            )
            await client.async_connect(host=host, port=port, link_keys=link_keys)
            ready = await client.wait_ready(timeout_s=READY_TIMEOUT)
            if not ready:
                errors["base"] = "cannot_connect"
                return self.async_show_form(
                    step_id=step_id,
                    data_schema=data_schema,
                    errors=errors,
                )

            snapshot = client.get_snapshot()
            panel_info = asdict(snapshot.panel)
            table_info = asdict(snapshot.table_info)
        except InvalidCredentials, Elke27AuthError, Elke27CryptoError:
            errors["base"] = "invalid_auth"
        except Elke27ConnectionError, Elke27TimeoutError, Elke27DisconnectedError:
            errors["base"] = "cannot_connect"
        except OSError, TimeoutError, E27Error:
            errors["base"] = "cannot_connect"
        except Elke27LinkRequiredError:
            errors["base"] = "link_required"
        # Kept on purpose: host/port are guarded above (None -> form error) and
        # come from validated entry data, but the library also raises
        # Elke27InvalidArgument for an empty access code/passphrase or a
        # rejected client identity, and no stored 0.1.7 host/port value has been
        # shown to trigger it. Catching it keeps the form usable (no 500).
        except (Elke27Error, Elke27InvalidArgument) as err:
            # Log the exception type only: its message can carry host, port or
            # panel fields, and none of those belong in the log.
            _LOGGER.warning("Elke27 link (%s) failed: %s", step_id, type(err).__name__)
            errors["base"] = (
                _link_error_base(err) if isinstance(err, Elke27Error) else "unknown"
            )
        finally:
            with contextlib.suppress(OSError, TimeoutError, E27Error, Elke27Error):
                await client.async_disconnect()

        if errors or link_keys is None:
            return self.async_show_form(
                step_id=step_id,
                data_schema=data_schema,
                errors=errors,
            )

        # E1a: the panel MAC comes only from discovery. Use the discovered panel
        # when there is one, otherwise send a unicast discovery to the host. No
        # reply means no MAC (fall back to the next identity tier, no abort).
        # On reauth the selected panel is the saved discovery record, so its MAC
        # is stale: always rediscover the live panel at the host.
        panel_mac = (
            panel_mac_from_discovered(self._selected_panel) if entry is None else None
        )
        if panel_mac is None:
            panel_mac = await async_discover_panel_mac(host)
        if panel_mac is not None:
            panel_info = {**panel_info, "mac": format_mac(panel_mac)}

        link_keys_json = link_keys.to_json()
        data: dict[str, Any] = {
            CONF_HOST: host,
            CONF_PORT: port,
            CONF_LINK_KEYS_JSON: link_keys_json,
            CONF_INTEGRATION_ID: integration_id,
        }

        panel = self._selected_panel
        if panel is not None:
            data[CONF_PANEL] = _panel_to_dict(panel)

        options: dict[str, Any] = {
            CONF_PANEL_INFO: panel_info,
            CONF_TABLE_INFO: table_info,
        }

        if entry is not None:
            stored_mac = normalize_panel_mac(entry.data.get(CONF_PANEL_MAC))
            if stored_mac and panel_mac and stored_mac != panel_mac:
                return self.async_abort(reason="wrong_panel")
            mac_refused = False
            if panel_mac and not stored_mac:
                mac_fill = async_reauth_mac_fill(self.hass, entry, panel_mac)
                mac_refused = not mac_fill
                data.update(mac_fill)
            data.update(legacy_reauth_data_updates(entry, data, stored_mac))
            panel_unique_id = config_entry_unique_id(panel_info)
            if entry.unique_id is None:
                if (
                    entry.data.get(CONF_HOST) != host
                    or entry.data.get(CONF_PORT, DEFAULT_PORT) != port
                ):
                    return self.async_abort(reason="wrong_panel")
                stored_panel = entry.options.get(CONF_PANEL_INFO)
                if stored_panel and not panel_identity_matches(
                    panel_info, stored_panel
                ):
                    return self.async_abort(reason="wrong_panel")
                if panel_unique_id is not None and not mac_refused:
                    _async_try_backfill_config_entry_unique_id(
                        self.hass, entry, panel_unique_id
                    )
            else:
                candidates = _reauth_candidate_unique_ids(panel_info)
                if panel_mac is None and stored_mac:
                    # No discovery reply: trust the stored MAC for the check.
                    candidates.add(format_mac(stored_mac))
                if entry.unique_id not in candidates:
                    return self.async_abort(reason="wrong_panel")
            return self.async_update_reload_and_abort(
                entry,
                data_updates=data,
                options={**entry.options, **options},
                reason=(
                    "reauth_successful_upgrade"
                    if legacy_migration_pending(entry)
                    else "reauth_successful"
                ),
            )

        unique_id = config_entry_unique_id(panel_info)
        if unique_id is not None:
            await self.async_set_unique_id(unique_id)
            self._abort_if_unique_id_configured(
                updates={CONF_HOST: host, CONF_PORT: port}
            )
        if self._duplicate_panel_entry_exists(host, port, panel_info):
            return self.async_abort(reason="already_configured")

        if panel_mac is not None:
            data[CONF_PANEL_MAC] = panel_mac
        title = _panel_name(panel_info) or host

        result = self.async_create_entry(title=title, data=data, options=options)
        if "title" not in result:
            result["title"] = title
        return result

    def _discovery_schema(self, rescan_label: str) -> probatio.Schema:
        options = [
            {
                "value": str(idx),
                "label": _panel_label(
                    panel, already_configured=self._is_panel_configured(panel)
                ),
            }
            for idx, panel in enumerate(self._discovered_panels or [])
        ]
        if options:
            options.insert(0, {"value": CONF_RESCAN, "label": rescan_label})
        else:
            options = [{"value": CONF_RESCAN, "label": rescan_label}]
        return probatio.Schema(
            {
                probatio.Required(CONF_PANEL): selector(
                    {
                        "select": {
                            "options": options,
                            "mode": "list",
                        }
                    }
                ),
                probatio.Required(CONF_ACCESS_CODE): selector(
                    {"text": {"type": "password"}}
                ),
                probatio.Required(CONF_PASSPHRASE): selector(
                    {"text": {"type": "password"}}
                ),
            }
        )

    def _dedupe_panels(self, panels: list[Any]) -> list[Any]:
        deduped: list[Any] = []
        seen: set[tuple[str, str]] = set()
        for panel in panels:
            key = self._panel_key(panel)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(panel)
        return deduped

    def _panel_key(self, panel: Any) -> tuple[str, str]:
        mac = getattr(panel, "panel_mac", None) or getattr(panel, "mac", None)
        serial = getattr(panel, "panel_serial", None) or getattr(panel, "serial", None)
        host = getattr(panel, "panel_host", None) or getattr(panel, "host", None)
        port = getattr(panel, "port", None) or DEFAULT_PORT
        if mac:
            return ("mac", str(mac))
        if serial:
            return ("serial", str(serial))
        return ("host", f"{host}:{port}")

    def _is_panel_configured(self, panel: Any) -> bool:
        host = getattr(panel, "panel_host", None) or getattr(panel, "host", None)
        port = getattr(panel, "port", None) or DEFAULT_PORT
        if not host:
            return False
        entries = self.hass.config_entries.async_entries(DOMAIN)
        for entry in entries:
            if entry.data.get(CONF_HOST) == host and entry.data.get(CONF_PORT) == port:
                return True
        return False

    def _duplicate_panel_entry_exists(
        self,
        host: str,
        port: int,
        panel_info: dict[str, Any],
    ) -> bool:
        """Return True when another entry already represents this panel."""
        identity = config_entry_unique_id(panel_info)
        live_mac = normalize_panel_mac(panel_mac_from_info(panel_info))
        for entry in self.hass.config_entries.async_entries(DOMAIN):
            if identity is not None and entry.unique_id == identity:
                return True
            if live_mac is not None and (
                normalize_panel_mac(entry.data.get(CONF_PANEL_MAC)) == live_mac
            ):
                return True
            if (
                entry.data.get(CONF_HOST) == host
                and entry.data.get(CONF_PORT, DEFAULT_PORT) == port
            ):
                return True
            if entry.unique_id is not None:
                continue
            stored_panel = entry.options.get(CONF_PANEL_INFO)
            if (
                stored_panel
                and identity is not None
                and config_entry_unique_id(stored_panel) == identity
            ):
                return True
        return False


def _create_client() -> Elke27Client:
    """Create a configured client instance."""
    return Elke27Client(ClientConfig())


def _panel_to_dict(panel: Any | None) -> dict[str, Any]:
    """Normalize a discovered panel entry into a dict."""
    if panel is None:
        return {}
    if is_dataclass(panel) and not isinstance(panel, type):
        return _normalize_panel_keys(asdict(panel))
    if isinstance(panel, dict):
        return _normalize_panel_keys(dict(panel))
    return _normalize_panel_keys(
        {
            key: getattr(panel, key, None)
            for key in ("host", "port", "name", "model", "mac")
            if getattr(panel, key, None) is not None
        }
    )


def _normalize_panel_keys(panel: dict[str, Any]) -> dict[str, Any]:
    """Normalize discovery panel keys to the expected schema."""
    normalized = dict(panel)
    if "host" not in normalized and "ip" in normalized:
        normalized["host"] = normalized.get("ip")
    if "host" not in normalized and "panel_host" in normalized:
        normalized["host"] = normalized.get("panel_host")
    if "port" not in normalized and "panel_port" in normalized:
        normalized["port"] = normalized.get("panel_port")
    if "name" not in normalized and "panel_name" in normalized:
        normalized["name"] = normalized.get("panel_name")
    if "mac" not in normalized and "panel_mac" in normalized:
        normalized["mac"] = normalized.get("panel_mac")
    if "serial" not in normalized and "panel_serial" in normalized:
        normalized["serial"] = normalized.get("panel_serial")
    if "model" not in normalized and "panel_model" in normalized:
        normalized["model"] = normalized.get("panel_model")
    return normalized


def _reauth_candidate_unique_ids(panel_info: dict[str, Any]) -> set[str]:
    """Return config-entry unique_id values that may match during reauth."""
    candidates: set[str] = set()
    unique_id = config_entry_unique_id(panel_info)
    if unique_id:
        candidates.add(unique_id)
    serial = panel_serial_tier(panel_info)
    if serial and serial != unique_id:
        candidates.add(serial)
    mac = panel_mac_from_info(panel_info)
    if mac:
        candidates.add(format_mac(str(mac)))
    return candidates


def _panel_name(panel_info: dict[str, Any]) -> str | None:
    return (
        panel_info.get("panel_name")
        or panel_info.get("name")
        or panel_info.get("serial")
        or panel_info.get("panel_serial")
    )


def _panel_label(panel: Any, *, already_configured: bool = False) -> str:
    name = (
        getattr(panel, "panel_name", None)
        or getattr(panel, "name", None)
        or getattr(panel, "panel_serial", None)
        or getattr(panel, "serial", None)
    )
    host = getattr(panel, "panel_host", None) or getattr(panel, "host", None)
    model = getattr(panel, "panel_model", None) or getattr(panel, "model", None)
    mac = getattr(panel, "panel_mac", None) or getattr(panel, "mac", None)
    details = ", ".join(
        value
        for value in (
            str(model) if model else None,
            str(mac) if mac else None,
            str(host) if host else None,
        )
        if value
    )
    base = str(name) if name else "Panel"
    label = f"{base} ({details})" if details else base
    if already_configured:
        return f"{label} (already configured)"
    return label
