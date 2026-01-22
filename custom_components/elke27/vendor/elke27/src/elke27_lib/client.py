"""
Stable client facade for Elke27.

This wraps the internal kernel/Session/Dispatcher with:
- structured results
- normalized typed errors
- readiness signaling
- event subscription helpers

See docs/CLIENT_CONTRACT.md for the stable client contract.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
import queue
import threading
import time
import types
import types as types_mod
from collections.abc import (
    AsyncIterator,
    Callable,
    Collection,
    Iterable,
    Iterator,
    Mapping,
    Sequence,
)
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import (
    TYPE_CHECKING,
    Any,
    Generic,
    TypeVar,
    cast,
)

if TYPE_CHECKING:
    from typing_extensions import override
else:

    def override(func):  # type: ignore[no-redef]
        return func


from . import discovery as discovery_mod
from . import linking as linking_mod
from .dispatcher import PagedBlock, RouteKey
from .errors import (
    AuthorizationRequired,
    ConnectionLost,
    CryptoError,
    E27AuthFailed,
    E27Error,
    E27ErrorContext,
    E27LinkInvalid,
    E27MissingContext,
    E27NotReady,
    E27ProtocolError,
    E27ProvisioningRequired,
    E27ProvisioningTimeout,
    E27Timeout,
    E27TransportError,
    Elke27AuthError,
    Elke27ConnectionError,
    Elke27CryptoError,
    Elke27DisconnectedError,
    Elke27Error,
    Elke27InvalidArgument,
    Elke27LinkRequiredError,
    Elke27PermissionError,
    Elke27PinRequiredError,
    Elke27TimeoutError,
    InvalidCredentials,
    InvalidLinkKeys,
    InvalidPin,
    InvalidPinError,
    MissingContext,
    NotAuthenticatedError,
    PanelNotDisarmedError,
    PermissionDeniedError,
    ProtocolError,
)
from .errors import (
    Elke27ProtocolError as Elke27ProtocolErrorV2,
)
from .events import (
    AreaAttribsUpdated,
    AreaConfiguredInventoryReady,
    AreaStatusUpdated,
    AreaTableInfoUpdated,
    AreaTroublesUpdated,
    ConnectionStateChanged,
    CsmSnapshotUpdated,
    Event,
    KeypadConfiguredInventoryReady,
    OutputConfiguredInventoryReady,
    OutputsStatusBulkUpdated,
    OutputStatusUpdated,
    OutputTableInfoUpdated,
    PanelVersionInfoUpdated,
    TstatTableInfoUpdated,
    UserConfiguredInventoryReady,
    ZoneAttribsUpdated,
    ZoneConfiguredInventoryReady,
    ZoneDefFlagsUpdated,
    ZoneDefsUpdated,
    ZonesStatusBulkUpdated,
    ZoneStatusUpdated,
    ZoneTableInfoUpdated,
)
from .generators.registry import COMMANDS, CommandSpec, MergeStrategy
from .handlers.area import make_area_configured_merge
from .handlers.zone import make_zone_configured_merge
from .kernel import (
    DiscoverResult,
    E27Kernel,
    KernelError,
    KernelInvalidPanelError,
    KernelMissingContextError,
    KernelNotLinkedError,
)
from .linking import E27Identity, E27LinkKeys
from .outbound import OutboundPriority
from .permissions import (
    PermissionLevel,
    canonical_generator_key,
    permission_for_generator,
    requires_disarmed,
    requires_pin,
)
from .redact import redact_for_diagnostics
from .session import (
    SessionConfig,
    SessionIOError,
    SessionNotReadyError,
    SessionProtocolError,
)
from .states import PanelState, update_csm_snapshot
from .types import (
    AreaState as V2AreaState,
)
from .types import (
    ArmMode,
    ClientConfig,
    CsmSnapshot,
    DiscoveredPanel,
    Elke27Event,
    EventType,
    LinkKeys,
    OutputDefinition,
    PanelInfo,
    PanelSnapshot,
    TableInfo,
    ZoneDefinition,
)
from .types import (
    OutputState as V2OutputState,
)
from .types import (
    ZoneState as V2ZoneState,
)

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class Result(Generic[T]):
    ok: bool
    data: T | None = None
    error: BaseException | None = None

    @classmethod
    def success(cls: type[Result[T]], value: T) -> Result[T]:
        return cls(ok=True, data=value, error=None)

    @classmethod
    def failure(cls: type[Result[T]], error: BaseException) -> Result[T]:
        return cls(ok=False, data=None, error=error)

    def unwrap(self) -> T:
        if self.ok:
            if self.data is None:
                raise E27Error("Result missing expected data.")
            return self.data
        if self.error is not None:
            raise self.error
        raise E27Error("Unknown error.")


def _ok(value: T) -> Result[T]:
    return Result(ok=True, data=value, error=None)


def _err(error: BaseException) -> Result[T]:
    return Result(ok=False, data=None, error=error)


__all__ = ["Elke27Client", "Result", "E27Identity", "E27LinkKeys"]


_CLIENT_EXCEPTIONS = (
    E27Error,
    KernelError,
    SessionNotReadyError,
    SessionIOError,
    SessionProtocolError,
    OSError,
    TimeoutError,
    ValueError,
    TypeError,
    KeyError,
    RuntimeError,
)


def _iter_causes(exc: BaseException) -> Iterable[BaseException]:
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


class _FilteredMapping(Mapping[int, Any]):
    def __init__(self, source: Mapping[int, Any], allowed: Collection[int]) -> None:
        self._source: Mapping[int, Any] = source
        self._allowed: Collection[int] = allowed

    @override
    def __getitem__(self, key: int) -> Any:
        if key not in self._allowed:
            raise KeyError(key)
        return self._source[key]

    @override
    def __iter__(self) -> Iterator[int]:
        for key in self._source:
            if key in self._allowed:
                yield key

    @override
    def __len__(self) -> int:
        return sum(1 for key in self._source if key in self._allowed)


def _configured_ids_from_table(state: PanelState, domain: str) -> Collection[int]:
    info = state.table_info_by_domain.get(domain)
    if not isinstance(info, Mapping):
        return ()
    table_elements = info.get("table_elements")
    if not isinstance(table_elements, int) or table_elements < 1:
        return ()
    return range(1, table_elements + 1)


def _table_elements_for_domain(state: PanelState, domain: str) -> int | None:
    info = state.table_info_by_domain.get(domain)
    if not isinstance(info, Mapping):
        return None
    value = info.get("table_elements")
    if isinstance(value, int):
        return value
    return None


class Elke27Client:
    """
    Stable client API for consumers (e.g., Home Assistant).

    This facade intentionally avoids HA-specific concepts.
    """

    def __init__(
        self,
        config: ClientConfig | None = None,
        *,
        kernel: E27Kernel | None = None,
        now_monotonic: Callable[[], float] | None = None,
        event_queue_maxlen: int | None = None,
        features: Sequence[str] | None = None,
        logger: logging.Logger | None = None,
        filter_attribs_to_configured: bool = True,
    ) -> None:
        self._log: logging.Logger = logger or logging.getLogger(__name__)
        self._feature_modules: Sequence[str] | None = features
        self._v2_config: ClientConfig | None = config
        self._v2_client_identity: linking_mod.E27Identity | None = None
        self._connected: bool = False
        self._event_loop: asyncio.AbstractEventLoop | None = None
        queue_size = config.event_queue_size if config and config.event_queue_size > 0 else 256
        self._event_queue: asyncio.Queue[Elke27Event | None] = asyncio.Queue(maxsize=queue_size)
        self._event_seq_counter: int = 0
        self._event_session_id: int | None = None
        self._subscriber_callbacks: list[Callable[[Elke27Event], None]] = []
        self._typed_subscriber_callbacks: list[Callable[[Event], None]] = []
        self._subscriber_lock: threading.Lock = threading.Lock()
        self._subscriber_error_types: set[type] = set()
        self._kernel_event_token: int | None = None
        self._snapshot: PanelSnapshot = PanelSnapshot.empty()
        self._snapshot_version: int = 0
        self._last_auth_pin: int | None = None
        self._pending_bypass_by_area: dict[int, float] = {}
        if event_queue_maxlen is None:
            event_queue_maxlen = config.event_queue_maxlen if config is not None else 0
        request_timeout_s = config.request_timeout_s if config is not None else 5.0
        if logger is None and config is not None and config.logger_name:
            self._log = logging.getLogger(config.logger_name)
        if kernel is None:
            outbound_min_interval_s = config.outbound_min_interval_s if config is not None else 0.05
            outbound_max_burst = config.outbound_max_burst if config is not None else 1
            self._kernel: E27Kernel = E27Kernel(
                now_monotonic=now_monotonic or time.monotonic,
                event_queue_maxlen=event_queue_maxlen,
                features=features,
                logger=self._log,
                request_timeout_s=request_timeout_s,
                outbound_min_interval_s=outbound_min_interval_s,
                outbound_max_burst=outbound_max_burst,
                filter_attribs_to_configured=filter_attribs_to_configured,
            )
        else:
            self._kernel = kernel
        self._auth_role: str | None = None
        self._ready_event: asyncio.Event = asyncio.Event()
        self._inventory_ready: dict[str, bool] = {
            "area": False,
            "zone": False,
            "output": False,
        }
        self._status_pending: dict[str, set[int]] = {
            "area": set(),
            "zone": set(),
            "output": set(),
        }
        self._status_ready: dict[str, bool] = {
            "area": False,
            "zone": False,
            "output": False,
        }
        self._ensure_kernel_subscription()

    def _ensure_kernel_subscription(self) -> None:
        if self._kernel_event_token is not None:
            return
        self._kernel_event_token = self._kernel.subscribe(self._on_kernel_event)

    @property
    def state(self):
        return self._kernel.state

    @property
    def ready(self) -> bool:
        return self._kernel.ready and self._bootstrap_ready()

    @property
    def is_ready(self) -> bool:
        return self.ready

    def set_authenticated_role(self, role: str | None) -> None:
        """
        Set the current authenticated role ("any_user", "master", "installer") or None.
        """
        self._auth_role = role

    @property
    def bootstrap_complete_counts(self) -> bool:
        if self._kernel.state.bootstrap_counts_ready:
            return True
        table_info = self._kernel.state.table_info_by_domain
        for domain in ("area", "zone", "output", "tstat"):
            info = table_info.get(domain)
            if not isinstance(info, Mapping):
                return False
            if info.get("table_elements") is None:
                return False
        return False

    async def wait_ready(self, timeout_s: float) -> bool:
        if self.ready:
            return True
        try:
            await asyncio.wait_for(self._ready_event.wait(), timeout=timeout_s)
        except TimeoutError:
            return False
        return True

    def subscribe(
        self,
        callback: Callable[[Elke27Event], None],
        *,
        kinds: Iterable[str] | None = None,
    ) -> Callable[[], bool]:
        del kinds
        with self._subscriber_lock:
            if callback in self._subscriber_callbacks:
                return lambda: self.unsubscribe(callback)
            self._subscriber_callbacks.append(callback)
        return lambda: self.unsubscribe(callback)

    def unsubscribe(self, callback: Callable[[Elke27Event], None]) -> bool:
        with self._subscriber_lock:
            if callback not in self._subscriber_callbacks:
                return False
            self._subscriber_callbacks.remove(callback)
        return True

    def subscribe_typed(
        self,
        callback: Callable[[Event], None],
        *,
        kinds: Iterable[str] | None = None,
    ) -> Callable[[], bool]:
        del kinds
        with self._subscriber_lock:
            if callback in self._typed_subscriber_callbacks:
                return lambda: self.unsubscribe_typed(callback)
            self._typed_subscriber_callbacks.append(callback)
        return lambda: self.unsubscribe_typed(callback)

    def unsubscribe_typed(self, callback: Callable[[Event], None]) -> bool:
        with self._subscriber_lock:
            if callback not in self._typed_subscriber_callbacks:
                return False
            self._typed_subscriber_callbacks.remove(callback)
        return True

    def drain_events(self) -> list[Event]:
        return self._kernel.drain_events()

    def iter_events(self) -> Iterable[Event]:
        return self._kernel.iter_events()

    # --- v2 public API (async-only, exceptions-only) ---
    @staticmethod
    def _default_identity() -> linking_mod.E27Identity:
        return linking_mod.E27Identity(
            mn="222", sn="000000001", fwver="0.1", hwver="0.1", osver="0.1"
        )

    def _coerce_identity(
        self, identity: object | linking_mod.E27Identity | None
    ) -> linking_mod.E27Identity:
        if identity is None:
            return self._default_identity()
        if isinstance(identity, linking_mod.E27Identity):
            return identity
        if not isinstance(identity, Mapping):
            raise Elke27InvalidArgument("client_identity must be a mapping with mn/sn fields.")
        identity_map = cast(Mapping[str, object], identity)
        mn = str(identity_map.get("mn") or "222")
        sn = str(identity_map.get("sn") or "00000001")
        fwver = str(identity_map.get("fwver") or "0.1")
        hwver = str(identity_map.get("hwver") or "0.1")
        osver = str(identity_map.get("osver") or "0.1")
        if not mn or not sn:
            raise Elke27InvalidArgument("client_identity requires non-empty mn and sn.")
        return linking_mod.E27Identity(mn=mn, sn=sn, fwver=fwver, hwver=hwver, osver=osver)

    @staticmethod
    def _coerce_link_keys(link_keys: LinkKeys) -> linking_mod.E27LinkKeys:
        return linking_mod.E27LinkKeys(
            tempkey_hex=link_keys.tempkey_hex,
            linkkey_hex=link_keys.linkkey_hex,
            linkhmac_hex=link_keys.linkhmac_hex,
        )

    def _raise_v2_error(self, exc: BaseException, *, phase: str) -> None:
        del phase
        for err in _iter_causes(exc):
            if isinstance(err, E27ProvisioningRequired):
                raise Elke27LinkRequiredError(
                    "Linking required to perform this operation."
                ) from None
            if isinstance(err, KernelMissingContextError):
                raise Elke27InvalidArgument("Missing required context for operation.") from None
            if isinstance(
                err, (E27ProvisioningTimeout, InvalidCredentials, E27AuthFailed, InvalidPinError)
            ):
                raise Elke27AuthError("Authentication failed for provisioning.") from None
            if isinstance(err, E27LinkInvalid):
                raise Elke27CryptoError("Link credentials appear invalid.") from None
            if isinstance(err, CryptoError):
                raise Elke27CryptoError("Cryptographic error.") from None
            if isinstance(err, E27ProtocolError):
                raise Elke27ProtocolErrorV2("Protocol error.") from None
            if isinstance(err, (E27TransportError, OSError, ConnectionError)):
                raise Elke27ConnectionError("Connection error.") from None
            if isinstance(err, (TimeoutError, asyncio.TimeoutError)):
                raise Elke27TimeoutError("Operation timed out.") from None
            if isinstance(
                err, (KernelError, E27MissingContext, KernelInvalidPanelError, KernelNotLinkedError)
            ):
                raise Elke27ProtocolErrorV2("Protocol error.") from None

        raise Elke27ProtocolErrorV2("Operation failed.") from None

    def _raise_v2_command_error(self, err: BaseException) -> None:
        if isinstance(err, Elke27Error):
            raise err
        if isinstance(err, E27ProvisioningRequired):
            raise Elke27LinkRequiredError("Linking required to perform this operation.") from None
        if isinstance(err, PanelNotDisarmedError):
            raise Elke27PermissionError("This action requires all areas to be disarmed.") from None
        if isinstance(err, (NotAuthenticatedError, PermissionDeniedError)):
            raise Elke27PermissionError("Permission denied for this operation.") from None
        if isinstance(err, (E27AuthFailed, InvalidPinError, InvalidCredentials)):
            raise Elke27AuthError("Authentication failed for this operation.") from None
        if isinstance(err, (E27Timeout, E27TransportError, TimeoutError, asyncio.TimeoutError)):
            raise Elke27TimeoutError("Operation timed out.") from None
        if isinstance(err, E27NotReady):
            raise Elke27ConnectionError("Panel not ready.") from None
        if isinstance(err, CryptoError):
            raise Elke27CryptoError("Cryptographic error.") from None
        if isinstance(err, E27ProtocolError):
            raise Elke27ProtocolErrorV2("Protocol error.") from None
        raise Elke27ProtocolErrorV2("Operation failed.") from None

    @staticmethod
    def _arm_mode_from_string(value: str | None) -> ArmMode | None:
        if not isinstance(value, str):
            return None
        lowered = value.lower()
        if "disarm" in lowered:
            return ArmMode.DISARMED
        if "stay" in lowered:
            return ArmMode.ARMED_STAY
        if "away" in lowered:
            return ArmMode.ARMED_AWAY
        if "night" in lowered:
            return ArmMode.ARMED_NIGHT
        return None

    def _build_panel_info(self) -> PanelInfo:
        panel = self._kernel.state.panel
        return PanelInfo(
            mac=None,
            model=panel.model,
            firmware=panel.firmware,
            serial=panel.serial,
        )

    def _build_table_info(self) -> TableInfo:
        return TableInfo(
            areas=_table_elements_for_domain(self._kernel.state, "area"),
            zones=_table_elements_for_domain(self._kernel.state, "zone"),
            outputs=_table_elements_for_domain(self._kernel.state, "output"),
            tstats=_table_elements_for_domain(self._kernel.state, "tstat"),
        )

    def _build_area_map(self) -> Mapping[int, V2AreaState]:
        out: dict[int, V2AreaState] = {}
        for area_id, area in self._kernel.state.areas.items():
            arm_value = area.arm_state or area.armed_state
            out[area_id] = V2AreaState(
                area_id=area_id,
                name=area.name,
                arm_mode=self._arm_mode_from_string(arm_value),
                ready=area.ready,
                alarm_active=area.alarm_state is not None
                and str(area.alarm_state).lower() != "no_alarm_active",
                chime=area.chime,
            )
        return types_mod.MappingProxyType(out)

    def _build_zone_map(self) -> Mapping[int, V2ZoneState]:
        out: dict[int, V2ZoneState] = {}
        for zone_id, zone in self._kernel.state.zones.items():
            out[zone_id] = V2ZoneState(
                zone_id=zone_id,
                name=zone.name,
                open=zone.violated,
                bypassed=zone.bypassed,
                trouble=zone.trouble,
                alarm=zone.alarm,
                tamper=zone.tamper,
                low_battery=zone.low_battery,
            )
        return types_mod.MappingProxyType(out)

    def _build_zone_definitions(self) -> Mapping[int, ZoneDefinition]:
        out: dict[int, ZoneDefinition] = {}
        state = self._kernel.state
        for zone_id, zone in state.zones.items():
            definition = _resolve_zone_definition(state, zone.definition)
            zone_type = None
            kind = None
            zone_type_val = zone.attribs.get("zone_type") or zone.attribs.get("type")
            if isinstance(zone_type_val, str):
                zone_type = zone_type_val
            kind_val = zone.attribs.get("kind")
            if isinstance(kind_val, str):
                kind = kind_val
            out[zone_id] = ZoneDefinition(
                zone_id=zone_id,
                name=zone.name,
                definition=definition,
                zone_type=zone_type,
                kind=kind,
            )
        return types_mod.MappingProxyType(out)

    def _build_output_map(self) -> Mapping[int, V2OutputState]:
        out: dict[int, V2OutputState] = {}
        for output_id, output in self._kernel.state.outputs.items():
            out[output_id] = V2OutputState(
                output_id=output_id,
                name=output.name,
                state=output.on,
            )
        return types_mod.MappingProxyType(out)

    def _build_output_definitions(self) -> Mapping[int, OutputDefinition]:
        out: dict[int, OutputDefinition] = {}
        for output_id, output in self._kernel.state.outputs.items():
            out[output_id] = OutputDefinition(
                output_id=output_id,
                name=output.name,
            )
        return types_mod.MappingProxyType(out)

    def _replace_snapshot(
        self,
        *,
        panel_info: PanelInfo | None = None,
        table_info: TableInfo | None = None,
        areas: Mapping[int, V2AreaState] | None = None,
        zones: Mapping[int, V2ZoneState] | None = None,
        zone_definitions: Mapping[int, ZoneDefinition] | None = None,
        outputs: Mapping[int, V2OutputState] | None = None,
        output_definitions: Mapping[int, OutputDefinition] | None = None,
    ) -> None:
        self._snapshot_version += 1
        now = datetime.now(UTC)
        self._snapshot = PanelSnapshot(
            panel=panel_info or self._snapshot.panel,
            table_info=table_info or self._snapshot.table_info,
            areas=areas or self._snapshot.areas,
            zones=zones or self._snapshot.zones,
            zone_definitions=zone_definitions or self._snapshot.zone_definitions,
            outputs=outputs or self._snapshot.outputs,
            output_definitions=output_definitions or self._snapshot.output_definitions,
            version=self._snapshot_version,
            updated_at=now,
        )
        self._maybe_set_ready()

    def _bootstrap_ready(self) -> bool:
        return all(self._inventory_ready.values()) and all(self._status_ready.values())

    def _maybe_set_ready(self) -> None:
        if self.is_ready and not self._ready_event.is_set():
            self._ready_event.set()

    def _reset_ready_event(self) -> None:
        self._ready_event = asyncio.Event()

    def _reset_bootstrap_state(self) -> None:
        self._inventory_ready = {"area": False, "zone": False, "output": False}
        self._status_pending = {"area": set(), "zone": set(), "output": set()}
        self._status_ready = {"area": False, "zone": False, "output": False}
        self._reset_ready_event()

    def _mark_inventory_ready(self, domain: str) -> None:
        if self._inventory_ready.get(domain):
            return
        self._inventory_ready[domain] = True
        inv = self._kernel.state.inventory
        if domain == "area":
            configured = inv.configured_areas
        elif domain == "zone":
            configured = inv.configured_zones
        else:
            configured = inv.configured_outputs
        pending = self._status_pending[domain]
        pending.clear()
        pending.update(configured)
        if not pending:
            self._status_ready[domain] = True
            self._maybe_set_ready()
            return
        self._status_ready[domain] = False
        self._queue_bootstrap_attribs(domain)
        self._request_initial_statuses(domain, pending)

    def _request_initial_statuses(self, domain: str, ids: set[int]) -> None:
        if domain == "area":
            for area_id in sorted(ids):
                try:
                    self._kernel.request(("area", "get_status"), area_id=area_id)
                except (E27Error, KeyError, RuntimeError, TypeError, ValueError):
                    continue
            return
        if domain == "zone":
            for zone_id in sorted(ids):
                try:
                    self._kernel.request(("zone", "get_status"), zone_id=zone_id)
                except (E27Error, KeyError, RuntimeError, TypeError, ValueError):
                    continue
            return
        if domain == "output":
            for output_id in sorted(ids):
                try:
                    self._kernel.request(("output", "get_status"), output_id=output_id)
                except (E27Error, KeyError, RuntimeError, TypeError, ValueError):
                    continue
            return

    def _queue_bootstrap_attribs(self, domain: str) -> None:
        inv = self._kernel.state.inventory
        if domain == "area" and inv.configured_areas:
            for area_id in sorted(inv.configured_areas):
                try:
                    self._kernel.request(("area", "get_attribs"), area_id=area_id)
                except (E27Error, KeyError, RuntimeError, TypeError, ValueError):
                    continue
        elif domain == "zone" and inv.configured_zones:
            for zone_id in sorted(inv.configured_zones):
                try:
                    self._kernel.request(("zone", "get_attribs"), zone_id=zone_id)
                except (E27Error, KeyError, RuntimeError, TypeError, ValueError):
                    continue
        elif domain == "output" and inv.configured_outputs:
            for output_id in sorted(inv.configured_outputs):
                try:
                    self._kernel.request(("output", "get_attribs"), output_id=output_id)
                except (E27Error, KeyError, RuntimeError, TypeError, ValueError):
                    continue
        elif domain == "user" and inv.configured_users:
            for user_id in sorted(inv.configured_users):
                try:
                    self._kernel.request(("user", "get_attribs"), user_id=user_id)
                except (E27Error, KeyError, RuntimeError, TypeError, ValueError):
                    continue
        elif domain == "keypad" and inv.configured_keypads:
            for keypad_id in sorted(inv.configured_keypads):
                try:
                    self._kernel.request(("keypad", "get_attribs"), keypad_id=keypad_id)
                except (E27Error, KeyError, RuntimeError, TypeError, ValueError):
                    continue

    def _refresh_bypassed_zones_for_area(self, area_id: int) -> None:
        if area_id < 1:
            return
        requested = 0
        for zone in self._kernel.state.zones.values():
            if zone.area_id == area_id and zone.bypassed is True:
                self._safe_request(("zone", "get_status"), zone_id=zone.zone_id)
                requested += 1
        if requested and self._log.isEnabledFor(logging.DEBUG):
            self._log.debug(
                "Requested zone.get_status for %s bypassed zones in area_id=%s",
                requested,
                area_id,
            )

    def _refresh_unbypassed_zones_for_area(self, area_id: int) -> None:
        if area_id < 1:
            return
        requested = 0
        for zone in self._kernel.state.zones.values():
            if zone.area_id == area_id and zone.bypassed is not True:
                self._safe_request(("zone", "get_status"), zone_id=zone.zone_id)
                requested += 1
        if requested and self._log.isEnabledFor(logging.DEBUG):
            self._log.debug(
                "Requested zone.get_status for %s non-bypassed zones in area_id=%s",
                requested,
                area_id,
            )

    def _refresh_all_zone_statuses_for_bypass_change(self, area_id: int) -> None:
        if area_id < 1:
            return
        self._safe_request(("zone", "get_all_zones_status"))

    def _record_local_zone_bypass(self, zone_id: int) -> None:
        zone = self._kernel.state.zones.get(zone_id)
        if zone is None or zone.area_id is None:
            return
        self._pending_bypass_by_area[zone.area_id] = self._kernel.now()

    def _should_suppress_area_bypass_refresh(self, area_id: int) -> bool:
        ts = self._pending_bypass_by_area.get(area_id)
        if ts is None:
            return False
        now = self._kernel.now()
        if now - ts <= 5.0:
            return True
        self._pending_bypass_by_area.pop(area_id, None)
        return False

    def _mark_status_seen(self, domain: str, ids: Iterable[int]) -> None:
        pending = self._status_pending.get(domain)
        if pending is None:
            return
        pending.difference_update(ids)
        if not pending:
            self._status_ready[domain] = True
        self._maybe_set_ready()

    def _enqueue_event(self, event: Elke27Event) -> None:
        if self._event_queue.full():
            with contextlib.suppress(asyncio.QueueEmpty):
                self._event_queue.get_nowait()
        with contextlib.suppress(asyncio.QueueFull):
            self._event_queue.put_nowait(event)

    def _signal_event_stream_end(self) -> None:
        sentinel: Elke27Event | None = None
        if self._event_queue.full():
            with contextlib.suppress(asyncio.QueueEmpty):
                self._event_queue.get_nowait()
        with contextlib.suppress(asyncio.QueueFull):
            self._event_queue.put_nowait(sentinel)

    def _map_event_type(self, evt: Event) -> EventType:
        if evt.kind == ConnectionStateChanged.KIND:
            return EventType.CONNECTION
        if "area" in evt.kind:
            return EventType.AREA
        if "zone" in evt.kind:
            return EventType.ZONE
        if "output" in evt.kind:
            return EventType.OUTPUT
        if "panel" in evt.kind or "table_info" in evt.kind or "csm" in evt.kind:
            return EventType.PANEL
        return EventType.SYSTEM

    def _next_event_seq(self, evt: Event) -> int:
        session_id = evt.session_id
        if session_id != self._event_session_id:
            self._event_session_id = session_id
            self._event_seq_counter = 0
        if isinstance(evt.seq, int) and evt.seq >= 0:
            return evt.seq
        self._event_seq_counter += 1
        return self._event_seq_counter

    def _handle_kernel_event(self, evt: Event) -> None:
        event_type = self._map_event_type(evt)
        data = redact_for_diagnostics(asdict(evt))
        seq = self._next_event_seq(evt)
        timestamp = datetime.now(UTC)

        if isinstance(evt, ConnectionStateChanged):
            if evt.connected:
                self._log.warning(
                    "Panel connection restored (reason=%s error_type=%s)",
                    evt.reason,
                    evt.error_type,
                )
                ready_evt = Elke27Event(
                    event_type=EventType.READY,
                    data={"connected": True},
                    seq=seq,
                    timestamp=timestamp,
                    raw_type=evt.kind,
                )
                self._enqueue_event(ready_evt)
                self._replace_snapshot(
                    panel_info=self._build_panel_info(),
                    table_info=self._build_table_info(),
                    areas=self._build_area_map(),
                    zones=self._build_zone_map(),
                    zone_definitions=self._build_zone_definitions(),
                    outputs=self._build_output_map(),
                    output_definitions=self._build_output_definitions(),
                )
            else:
                self._log.error(
                    "Panel connection lost (reason=%s error_type=%s)",
                    evt.reason,
                    evt.error_type,
                )
                disconnected_evt = Elke27Event(
                    event_type=EventType.DISCONNECTED,
                    data={"connected": False, "reason": evt.reason},
                    seq=seq,
                    timestamp=timestamp,
                    raw_type=evt.kind,
                )
                self._enqueue_event(disconnected_evt)
                self._signal_event_stream_end()
                self._reset_bootstrap_state()

        v2_evt = Elke27Event(
            event_type=event_type,
            data=data,
            seq=seq,
            timestamp=timestamp,
            raw_type=evt.kind,
        )
        self._enqueue_event(v2_evt)

        skip_snapshot_update = False
        if isinstance(evt, AreaConfiguredInventoryReady):
            self._mark_inventory_ready("area")
        elif isinstance(evt, ZoneConfiguredInventoryReady):
            self._mark_inventory_ready("zone")
        elif isinstance(evt, OutputConfiguredInventoryReady):
            self._mark_inventory_ready("output")
        elif isinstance(evt, UserConfiguredInventoryReady):
            self._queue_bootstrap_attribs("user")
        elif isinstance(evt, KeypadConfiguredInventoryReady):
            self._queue_bootstrap_attribs("keypad")
        elif isinstance(evt, AreaStatusUpdated):
            self._mark_status_seen("area", [evt.area_id])
            if not evt.changed_fields:
                self._refresh_all_zone_statuses_for_bypass_change(evt.area_id)
                skip_snapshot_update = True
            if "num_bypassed_zones" in evt.changed_fields:
                suppress_refresh = self._should_suppress_area_bypass_refresh(evt.area_id)
                area = self._kernel.state.areas.get(evt.area_id)
                if (
                    area is not None
                    and not suppress_refresh
                    and area.num_bypassed_zones is not None
                ):
                    self._log.warning(
                        "Area %s bypass count changed; no refresh (packet loss fix)",
                        evt.area_id,
                    )
        elif isinstance(evt, AreaTroublesUpdated):
            # Defensive: unexpected broadcasts can hide missed zone status updates.
            if getattr(evt, "classification", None) == "BROADCAST":
                if isinstance(evt.area_id, int):
                    self._log.warning(
                        "Area %s troubles broadcast received; refreshing zone status in bulk",
                        evt.area_id,
                    )
                    self._refresh_all_zone_statuses_for_bypass_change(evt.area_id)
        elif isinstance(evt, ZoneStatusUpdated):
            self._mark_status_seen("zone", [evt.zone_id])
        elif isinstance(evt, ZonesStatusBulkUpdated):
            self._mark_status_seen("zone", evt.updated_ids)
        elif isinstance(evt, OutputStatusUpdated):
            self._mark_status_seen("output", [evt.output_id])
        elif isinstance(evt, OutputsStatusBulkUpdated):
            self._mark_status_seen("output", evt.updated_ids)

        if evt.kind in {
            PanelVersionInfoUpdated.KIND,
            AreaTableInfoUpdated.KIND,
            ZoneTableInfoUpdated.KIND,
            OutputTableInfoUpdated.KIND,
            TstatTableInfoUpdated.KIND,
            AreaStatusUpdated.KIND,
            AreaAttribsUpdated.KIND,
            ZoneAttribsUpdated.KIND,
            ZoneDefsUpdated.KIND,
            ZoneDefFlagsUpdated.KIND,
            ZonesStatusBulkUpdated.KIND,
            OutputStatusUpdated.KIND,
            OutputsStatusBulkUpdated.KIND,
            ZoneStatusUpdated.KIND,
        }:
            if evt.kind == AreaStatusUpdated.KIND and skip_snapshot_update:
                self._maybe_set_ready()
            else:
                self._replace_snapshot(
                    panel_info=self._build_panel_info(),
                    table_info=self._build_table_info(),
                    areas=self._build_area_map(),
                    zones=self._build_zone_map(),
                    zone_definitions=self._build_zone_definitions(),
                    outputs=self._build_output_map(),
                    output_definitions=self._build_output_definitions(),
                )
        self._maybe_set_ready()

        with self._subscriber_lock:
            callbacks = list(self._subscriber_callbacks)
            typed_callbacks = list(self._typed_subscriber_callbacks)
        if self._log.isEnabledFor(logging.DEBUG) and evt.kind == ZoneStatusUpdated.KIND:
            self._log.debug(
                "Dispatching %s to %d typed subscribers (zone_id=%s changed=%s)",
                evt.kind,
                len(typed_callbacks),
                getattr(evt, "zone_id", None),
                getattr(evt, "changed_fields", None),
            )
        for cb in callbacks:
            try:
                cb(v2_evt)
            except Exception as exc:  # noqa: BLE001
                exc_type = type(exc)
                if exc_type not in self._subscriber_error_types:
                    self._subscriber_error_types.add(exc_type)
                    self._log.warning("Subscriber callback failed: %s", exc_type.__name__)
        for cb in typed_callbacks:
            try:
                cb(evt)
            except Exception as exc:  # noqa: BLE001
                exc_type = type(exc)
                if exc_type not in self._subscriber_error_types:
                    self._subscriber_error_types.add(exc_type)
                    self._log.warning("Subscriber callback failed: %s", exc_type.__name__)

    def _on_kernel_event(self, evt: Event) -> None:
        if self._event_loop is None:
            return
        with contextlib.suppress(RuntimeError):
            self._event_loop.call_soon_threadsafe(self._handle_kernel_event, evt)

    async def async_discover(
        self,
        *,
        timeout_s: float | None = None,
        address: str | None = None,
    ) -> list[DiscoveredPanel]:
        """Discover panels on the network (v2 public API)."""
        timeout = int(timeout_s) if timeout_s is not None else 10
        try:
            result = await E27Kernel.discover(timeout=timeout, address=address)
        except BaseException as exc:  # noqa: BLE001
            self._raise_v2_error(exc, phase="discover")
            raise AssertionError("unreachable") from exc
        panels: list[DiscoveredPanel] = []
        for panel in result.panels:
            panels.append(
                DiscoveredPanel(
                    host=panel.panel_host,
                    port=int(panel.port),
                    tls_port=int(panel.tls_port) if panel.tls_port else None,
                    panel_name=panel.panel_name or None,
                    panel_serial=panel.panel_serial or None,
                    panel_mac=panel.panel_mac or None,
                )
            )
        return panels

    async def async_link(
        self,
        host: str,
        port: int,
        *,
        access_code: str,
        passphrase: str,
        client_identity: Mapping[str, str] | linking_mod.E27Identity | None = None,
        timeout_s: float | None = None,
    ) -> LinkKeys:
        """Provision link keys for a panel (v2 public API)."""
        if not host:
            raise Elke27InvalidArgument("host must be a non-empty string.")
        if port <= 0:
            raise Elke27InvalidArgument("port must be a positive integer.")
        if not access_code:
            raise Elke27InvalidArgument("access_code must be a non-empty string.")
        if not passphrase:
            raise Elke27InvalidArgument("passphrase must be a non-empty string.")
        if client_identity is None:
            raise Elke27InvalidArgument("client_identity is required for linking.")

        identity = self._coerce_identity(client_identity)
        self._v2_client_identity = identity
        panel = {"host": host, "port": port}

        @dataclass(frozen=True)
        class _Credentials:
            access_code: str
            passphrase: str

        creds = _Credentials(access_code=access_code, passphrase=passphrase)
        timeout_value = float(timeout_s) if timeout_s is not None else 10.0
        try:
            link_keys = await self._kernel.link(panel, identity, creds, timeout_s=timeout_value)
        except BaseException as exc:  # noqa: BLE001
            self._raise_v2_error(exc, phase="link")
            raise AssertionError("unreachable") from exc
        return LinkKeys(
            tempkey_hex=link_keys.tempkey_hex,
            linkkey_hex=link_keys.linkkey_hex,
            linkhmac_hex=link_keys.linkhmac_hex,
        )

    async def async_connect(self, host: str, port: int, link_keys: LinkKeys) -> None:
        """Connect to a panel using link keys (v2 public API)."""
        if not host:
            raise Elke27InvalidArgument("host must be a non-empty string.")
        if port <= 0:
            raise Elke27InvalidArgument("port must be a positive integer.")
        self._reset_bootstrap_state()
        self._event_loop = asyncio.get_running_loop()
        self._ensure_kernel_subscription()
        identity = self._v2_client_identity or self._default_identity()
        session_cfg = SessionConfig(host=host, port=port, wire_log=True)
        connect_exc: BaseException | None = None
        for attempt in range(2):
            try:
                await self._kernel.connect(
                    self._coerce_link_keys(link_keys),
                    panel={"host": host, "port": port},
                    client_identity=identity,
                    session_config=session_cfg,
                )
                connect_exc = None
                break
            except BaseException as exc:  # noqa: BLE001
                connect_exc = exc
                self._log.error(
                    "Connect failed (attempt %s/2): %s",
                    attempt + 1,
                    exc,
                    exc_info=True,
                )
        if connect_exc is not None:
            self._raise_v2_error(connect_exc, phase="connect")
        self._connected = True
        if self._snapshot.version == 0:
            self._replace_snapshot(
                panel_info=self._build_panel_info(),
                table_info=self._build_table_info(),
                areas=self._build_area_map(),
                zones=self._build_zone_map(),
                zone_definitions=self._build_zone_definitions(),
                outputs=self._build_output_map(),
                output_definitions=self._build_output_definitions(),
            )
        self._maybe_set_ready()

    async def async_disconnect(self) -> None:
        """Disconnect the current session (v2 public API)."""
        try:
            await self._kernel.close()
        except BaseException as exc:  # noqa: BLE001
            self._raise_v2_error(exc, phase="disconnect")
        self._connected = False
        self._reset_bootstrap_state()
        self._signal_event_stream_end()

    def events(self) -> AsyncIterator[Elke27Event]:
        """Async iterator of v2 events (v2 public API)."""

        async def _iter() -> AsyncIterator[Elke27Event]:
            while True:
                evt = await self._event_queue.get()
                if evt is None:
                    break
                yield evt

        return _iter()

    @property
    def snapshot(self) -> PanelSnapshot:
        """Return the latest immutable snapshot (v2 public API)."""
        return self._snapshot

    def get_csm_snapshot(self) -> CsmSnapshot | None:
        """Return the latest CSM snapshot if available (v2 public API)."""
        return self._kernel.state.csm_snapshot

    async def async_refresh_csm(self) -> CsmSnapshot:
        """
        Refresh CSM data by requesting domain/table snapshots (v2 public API).
        """
        if not self._connected or not self._kernel.state.panel.connected:
            raise Elke27DisconnectedError("Client is not connected.")

        loop = asyncio.get_running_loop()
        done = asyncio.Event()
        captured: CsmSnapshot | None = None

        def _on_evt(evt: Event) -> None:
            nonlocal captured
            if isinstance(evt, CsmSnapshotUpdated):
                captured = evt.snapshot
                loop.call_soon_threadsafe(done.set)

        token = self._kernel.subscribe(_on_evt, kinds={CsmSnapshotUpdated.KIND})
        try:
            try:
                self._kernel.request_csm_refresh(auth_pin=self._last_auth_pin)
            except BaseException as exc:  # noqa: BLE001
                self._raise_v2_error(exc, phase="refresh_csm")
            timeout_value = getattr(self._kernel, "_request_timeout_s", 5.0)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(done.wait(), timeout=timeout_value)
        finally:
            self._kernel.unsubscribe(token)

        snapshot = captured or self._kernel.state.csm_snapshot
        if snapshot is None:
            snapshot = update_csm_snapshot(self._kernel.state) or self._kernel.state.csm_snapshot
        if snapshot is None:
            raise Elke27ProtocolErrorV2("CSM snapshot not available.")
        return snapshot

    async def async_refresh_domain_config(self, domain: str) -> None:
        """
        Refresh domain configuration with paging (v2 public API).
        """
        if not domain.strip():
            raise Elke27InvalidArgument("domain must be a non-empty string.")
        if not self._connected or not self._kernel.state.panel.connected:
            raise Elke27DisconnectedError("Client is not connected.")

        domain_key = domain.strip().lower()
        if domain_key == "area":
            self._refresh_area_config()
        elif domain_key == "zone":
            self._refresh_zone_config()
        elif domain_key == "output":
            self._refresh_output_config()
        elif domain_key == "tstat":
            self._refresh_tstat_config()
        else:
            raise Elke27InvalidArgument(f"Unsupported domain for refresh: {domain}")

    def _refresh_area_config(self) -> None:
        self._safe_request(("area", "get_table_info"))
        self._safe_request(("area", "get_configured"), block_id=1)

    def _refresh_zone_config(self) -> None:
        self._safe_request(("zone", "get_table_info"))
        self._safe_request(("zone", "get_configured"), block_id=1)
        self._safe_request(("zone", "get_defs"), block_id=1)
        if self._kernel.state.inventory.configured_zones:
            for zone_id in sorted(self._kernel.state.inventory.configured_zones):
                self._safe_request(("zone", "get_attribs"), zone_id=zone_id)

    def _refresh_output_config(self) -> None:
        self._safe_request(("output", "get_table_info"))
        self._safe_request(("output", "get_configured"), block_id=1)

    def _refresh_tstat_config(self) -> None:
        self._safe_request(("tstat", "get_table_info"))

    def _safe_request(self, route: RouteKey, /, **kwargs: Any) -> None:
        if self._kernel.requests.get(route) is None:
            return
        try:
            self._kernel.request(route, **kwargs)
        except BaseException as exc:  # noqa: BLE001
            self._raise_v2_error(exc, phase="refresh_domain_config")

    async def async_set_output(self, output_id: int, *, on: bool) -> None:
        """Set an output on or off (v2 public API)."""
        if output_id < 1:
            raise Elke27InvalidArgument("output_id must be a positive integer.")
        if not self._connected or not self._kernel.state.panel.connected:
            raise Elke27DisconnectedError("Client is not connected.")
        status = "ON" if on else "OFF"
        result = await self.async_execute("output_set_status", output_id=output_id, status=status)
        if not result.ok:
            if result.error is not None:
                self._raise_v2_command_error(result.error)
            raise Elke27ProtocolErrorV2("Failed to set output.")

    async def async_arm_area(
        self,
        area_id: int,
        *,
        mode: ArmMode,
        pin: str | None = None,
    ) -> None:
        """Arm an area using the requested mode (v2 public API)."""
        if area_id < 1:
            raise Elke27InvalidArgument("area_id must be a positive integer.")
        if mode is ArmMode.DISARMED:
            if not pin:
                raise Elke27InvalidArgument("PIN is required to disarm.")
            await self.async_disarm_area(area_id, pin=pin)
            return
        if mode is ArmMode.ARMED_NIGHT:
            raise Elke27InvalidArgument("ARMED_NIGHT is not supported by the current protocol.")
        if not pin:
            raise Elke27InvalidArgument("PIN is required to arm.")
        if not pin.isdigit():
            raise Elke27InvalidArgument("PIN must be a non-empty digit string.")
        arm_state = "ARMED_STAY" if mode is ArmMode.ARMED_STAY else "ARMED_AWAY"
        result = await self.async_execute(
            "area_set_arm_state",
            area_id=area_id,
            arm_state=arm_state,
            pin=pin,
        )
        if not result.ok:
            if result.error is not None:
                self._raise_v2_command_error(result.error)
            raise Elke27ProtocolErrorV2("Failed to arm area.")

    async def async_disarm_area(self, area_id: int, *, pin: str) -> None:
        """Disarm an area (v2 public API)."""
        if area_id < 1:
            raise Elke27InvalidArgument("area_id must be a positive integer.")
        if not pin:
            raise Elke27InvalidArgument("PIN must be a non-empty digit string.")
        if not pin.isdigit():
            raise Elke27InvalidArgument("PIN must be a non-empty digit string.")
        result = await self.async_execute(
            "area_set_arm_state",
            area_id=area_id,
            arm_state="DISARMED",
            pin=pin,
        )
        if not result.ok:
            if result.error is not None:
                self._raise_v2_command_error(result.error)
            raise Elke27ProtocolErrorV2("Failed to disarm area.")

    async def discover(
        self, *, timeout: int = 10, address: str | None = None
    ) -> Result[DiscoverResult]:
        try:
            panels: DiscoverResult = await E27Kernel.discover(timeout=timeout, address=address)
            return Result(ok=True, data=panels, error=None)
        except _CLIENT_EXCEPTIONS as exc:
            return Result(ok=False, data=None, error=self._normalize_error(exc, phase="discover"))

    async def link(
        self,
        panel: discovery_mod.E27System | dict[str, Any],
        client_identity: E27Identity,
        credentials: Any,
        *,
        timeout_s: float = 10.0,
    ) -> Result[Any]:
        try:
            keys: E27LinkKeys = await self._kernel.link(
                panel, client_identity, credentials, timeout_s=timeout_s
            )
            return Result(ok=True, data=keys, error=None)
        except _CLIENT_EXCEPTIONS as exc:
            return Result(ok=False, data=None, error=self._normalize_error(exc, phase="link"))

    async def connect(
        self,
        link_keys: Any,
        *,
        panel: discovery_mod.E27System | dict[str, Any] | None = None,
        client_identity: E27Identity | None = None,
        session_config: SessionConfig | None = None,
    ) -> Result[None]:
        try:
            await asyncio.to_thread(self._kernel.load_features_blocking, self._feature_modules)
            await self._kernel.connect(
                link_keys,
                panel=panel,
                client_identity=client_identity,
                session_config=session_config,
            )
            return _ok(None)
        except _CLIENT_EXCEPTIONS as exc:
            return _err(self._normalize_error(exc, phase="connect"))

    async def close(self) -> Result[None]:
        try:
            await self._kernel.close()
            return _ok(None)
        except _CLIENT_EXCEPTIONS as exc:
            return _err(self._normalize_error(exc, phase="close"))

    async def disconnect(self) -> Result[None]:
        return await self.close()

    def request(
        self,
        route: RouteKey,
        /,
        *,
        pending: bool = True,
        opaque: Any = None,
        **kwargs: Any,
    ) -> Result[int]:
        if route == ("control", "authenticate"):
            return self._request_authenticate(route, pending=pending, opaque=opaque, **kwargs)
        try:
            seq: int = self._kernel.request(route, pending=pending, opaque=opaque, **kwargs)
            return _ok(seq)
        except _CLIENT_EXCEPTIONS as exc:
            detail = f"route={route[0]}.{route[1]}"
            return _err(self._normalize_error(exc, phase="request", detail=detail))

    async def async_execute(
        self,
        command_key: str,
        /,
        *,
        timeout_s: float | None = None,
        **params: Any,
    ) -> Result[Mapping[str, Any]]:
        if command_key == "control_authenticate":
            try:
                permission_level = permission_for_generator(command_key)
            except Elke27ProtocolErrorV2 as exc:
                return _err(exc)

            permission_error = self._enforce_permissions(command_key, permission_level)
            if permission_error is not None:
                return _err(permission_error)

            pin_value = params.get("pin")
            if pin_value is None or (isinstance(pin_value, str) and not pin_value):
                return _err(Elke27PinRequiredError())
            if isinstance(pin_value, str):
                if not pin_value.isdigit():
                    return _err(InvalidPinError("PIN must be a non-empty digit string."))
                pin_int = int(pin_value)
            elif isinstance(pin_value, int):
                if pin_value <= 0:
                    return _err(InvalidPinError("PIN must be a positive integer."))
                pin_int = pin_value
            else:
                return _err(InvalidPinError("PIN must be a non-empty digit string."))

            self._last_auth_pin = pin_int
            return await self._async_authenticate(pin=pin_int, timeout_s=timeout_s)

        spec = COMMANDS.get(command_key)
        if spec is None:
            return _err(ProtocolError(f"Unknown command_key={command_key!r}"))

        canonical_key = canonical_generator_key(spec.generator.__name__)
        try:
            permission_level = permission_for_generator(canonical_key)
        except Elke27ProtocolErrorV2 as exc:
            return _err(exc)

        permission_error = self._enforce_permissions(command_key, permission_level)
        if permission_error is not None:
            return _err(permission_error)

        if requires_disarmed(permission_level) and not self._all_areas_disarmed():
            return _err(Elke27PermissionError("This action requires all areas to be disarmed."))

        if requires_pin(permission_level):
            pin_value = params.get("pin")
            if pin_value is None or (isinstance(pin_value, str) and not pin_value):
                return _err(Elke27PinRequiredError())
            if isinstance(pin_value, str):
                if not pin_value.isdigit():
                    return _err(InvalidPinError("PIN must be a non-empty digit string."))
            elif isinstance(pin_value, int):
                if pin_value <= 0:
                    return _err(InvalidPinError("PIN must be a positive integer."))
            else:
                return _err(InvalidPinError("PIN must be a non-empty digit string."))

        if spec.response_mode == "single":
            if spec.key == "area_get_attribs":
                configured = self._kernel.state.inventory.configured_areas
                if not configured:
                    configured_result = await self.async_execute("area_get_configured")
                    if not configured_result.ok:
                        return _err(
                            configured_result.error or ProtocolError("area_get_configured failed.")
                        )
            if spec.key == "zone_get_attribs":
                configured = self._kernel.state.inventory.configured_zones
                if not configured:
                    configured_result = await self.async_execute("zone_get_configured")
                    if not configured_result.ok:
                        return _err(
                            configured_result.error or ProtocolError("zone_get_configured failed.")
                        )
            if spec.key == "output_get_attribs":
                inv = self._kernel.state.inventory
                if not inv.configured_outputs and not inv.configured_outputs_complete:
                    configured_result = await self.async_execute("output_get_configured")
                    if not configured_result.ok:
                        return _err(
                            configured_result.error
                            or ProtocolError("output_get_configured failed.")
                        )
                    outputs = (
                        configured_result.data.get("outputs") if configured_result.data else None
                    )
                    if isinstance(outputs, list):
                        outputs_list = cast(list[object], outputs)
                        inv.configured_outputs = {
                            item for item in outputs_list if isinstance(item, int) and item >= 1
                        }
                    inv.configured_outputs_complete = True
            if spec.key == "user_get_attribs":
                inv = self._kernel.state.inventory
                if not inv.configured_users and not inv.configured_users_complete:
                    configured_result = await self.async_execute("user_get_configured")
                    if not configured_result.ok:
                        return _err(
                            configured_result.error or ProtocolError("user_get_configured failed.")
                        )
                    users = configured_result.data.get("users") if configured_result.data else None
                    if isinstance(users, list):
                        users_list = cast(list[object], users)
                        inv.configured_users = {
                            item for item in users_list if isinstance(item, int) and item >= 1
                        }
                    inv.configured_users_complete = True
            if spec.key == "keypad_get_attribs":
                inv = self._kernel.state.inventory
                if not inv.configured_keypads and not inv.configured_keypads_complete:
                    configured_result = await self.async_execute("keypad_get_configured")
                    if not configured_result.ok:
                        return _err(
                            configured_result.error
                            or ProtocolError("keypad_get_configured failed.")
                        )
                    keypads = (
                        configured_result.data.get("keypads") if configured_result.data else None
                    )
                    if isinstance(keypads, list):
                        keypads_list = cast(list[object], keypads)
                        inv.configured_keypads = {
                            item for item in keypads_list if isinstance(item, int) and item >= 1
                        }
                    inv.configured_keypads_complete = True
            params_for_generator = self._coerce_pin_for_generator(spec, params)
            try:
                payload, expected_route = spec.generator(**params_for_generator)
            except NotImplementedError as exc:
                return _err(exc)
            except _CLIENT_EXCEPTIONS as exc:
                detail = f"command_key={command_key}"
                return _err(self._normalize_error(exc, phase="execute", detail=detail))

            if spec.key == "zone_set_status":
                zone_id = params.get("zone_id")
                if isinstance(zone_id, int) and zone_id > 0:
                    self._record_local_zone_bypass(zone_id)

            loop = asyncio.get_running_loop()
            seq = self._kernel.next_seq()
            future = self._kernel.pending_responses.create(
                seq,
                command_key=command_key,
                expected_route=expected_route,
                loop=loop,
            )
            sent_event = asyncio.Event()
            self._kernel.register_sent_event(seq, sent_event)
            timeout_value = (
                timeout_s
                if timeout_s is not None
                else getattr(self._kernel, "_request_timeout_s", 5.0)
            )

            try:
                self._kernel.send_request_with_seq(
                    seq,
                    spec.domain,
                    spec.command,
                    payload,
                    pending=False,
                    opaque=None,
                    expected_route=expected_route,
                    timeout_s=timeout_value,
                )
            except _CLIENT_EXCEPTIONS as exc:
                self._kernel.pending_responses.drop(seq)
                detail = f"command_key={command_key} seq={seq}"
                return _err(self._normalize_error(exc, phase="execute", detail=detail))

            try:
                await sent_event.wait()
                msg = await asyncio.wait_for(future, timeout=timeout_value)
            except TimeoutError:
                self._kernel.pending_responses.drop(seq)
                return _err(
                    E27Timeout(f"async_execute timeout waiting for {command_key} seq={seq}")
                )
            except asyncio.CancelledError:
                self._kernel.pending_responses.drop(seq)
                raise
            except _CLIENT_EXCEPTIONS as exc:
                self._kernel.pending_responses.drop(seq)
                detail = f"command_key={command_key} seq={seq}"
                return _err(self._normalize_error(exc, phase="execute", detail=detail))

            if not self._has_expected_payload(msg, expected_route):
                return _err(
                    ProtocolError(
                        f"{command_key} missing response payload for {expected_route[0]}.{expected_route[1]}"
                    )
                )

            error_code = self._extract_error_code(msg, expected_route)
            if error_code is not None:
                if error_code == 11008:
                    return _err(
                        AuthorizationRequired("Authorization is required for this operation.")
                    )
                return _err(E27Error(f"{command_key} failed with error_code={error_code}"))

            response_payload = self._extract_response_payload(msg, expected_route)
            return _ok(response_payload)

        if spec.response_mode != "paged_blocks":
            return _err(ProtocolError(f"Command {command_key!r} has unsupported response_mode."))

        if spec.block_field is None or spec.block_count_field is None:
            return _err(ProtocolError(f"Command {command_key!r} is missing paging metadata."))

        merge_fn = self._resolve_merge_strategy(spec.merge_strategy)
        if merge_fn is None:
            return _err(ProtocolError(f"Command {command_key!r} is missing merge_strategy."))

        timeout_value = (
            timeout_s if timeout_s is not None else getattr(self._kernel, "_request_timeout_s", 5.0)
        )
        block_id = spec.first_block
        block_count: int | None = None
        blocks: list[PagedBlock] = []

        while True:
            params_with_block = dict(params)
            params_with_block[spec.block_field] = block_id
            params_for_generator = self._coerce_pin_for_generator(spec, params_with_block)

            try:
                payload, expected_route = spec.generator(**params_for_generator)
            except NotImplementedError as exc:
                return _err(exc)
            except _CLIENT_EXCEPTIONS as exc:
                detail = f"command_key={command_key}"
                return _err(self._normalize_error(exc, phase="execute", detail=detail))

            loop = asyncio.get_running_loop()
            seq = self._kernel.next_seq()
            future = self._kernel.pending_responses.create(
                seq,
                command_key=command_key,
                expected_route=expected_route,
                loop=loop,
            )
            sent_event = asyncio.Event()
            self._kernel.register_sent_event(seq, sent_event)
            timeout_value = (
                timeout_s
                if timeout_s is not None
                else getattr(self._kernel, "_request_timeout_s", 5.0)
            )
            try:
                self._kernel.send_request_with_seq(
                    seq,
                    spec.domain,
                    spec.command,
                    payload,
                    pending=False,
                    opaque=None,
                    expected_route=expected_route,
                    timeout_s=timeout_value,
                )
            except _CLIENT_EXCEPTIONS as exc:
                self._kernel.pending_responses.drop(seq)
                detail = f"command_key={command_key} seq={seq}"
                return _err(self._normalize_error(exc, phase="execute", detail=detail))

            try:
                await sent_event.wait()
                msg = await asyncio.wait_for(future, timeout=timeout_value)
            except TimeoutError:
                self._kernel.pending_responses.drop(seq)
                return _err(
                    E27Timeout(f"async_execute timeout waiting for {command_key} seq={seq}")
                )
            except asyncio.CancelledError:
                self._kernel.pending_responses.drop(seq)
                raise
            except _CLIENT_EXCEPTIONS as exc:
                self._kernel.pending_responses.drop(seq)
                detail = f"command_key={command_key} seq={seq}"
                return _err(self._normalize_error(exc, phase="execute", detail=detail))

            if not self._has_expected_payload(msg, expected_route):
                return _err(
                    ProtocolError(
                        f"{command_key} missing response payload for {expected_route[0]}.{expected_route[1]}"
                    )
                )

            error_code = self._extract_error_code(msg, expected_route)
            if error_code is not None:
                if error_code == 11008:
                    return _err(
                        AuthorizationRequired("Authorization is required for this operation.")
                    )
                return _err(E27Error(f"{command_key} failed with error_code={error_code}"))

            response_payload = self._extract_response_payload(msg, expected_route)
            response_block_count = self._coerce_block_count(
                response_payload.get(spec.block_count_field)
            )
            if block_count is None:
                block_count = response_block_count
                if block_count is None:
                    return _err(ProtocolError(f"{command_key} missing block_count in response."))
            elif response_block_count is not None and response_block_count != block_count:
                return _err(ProtocolError(f"{command_key} block_count mismatch in response."))

            blocks.append(PagedBlock(block_id=block_id, payload=response_payload))

            if block_id >= block_count:
                break
            block_id += 1

        try:
            merged_payload = cast(Mapping[str, Any], merge_fn(blocks, block_count or len(blocks)))
        except Exception as exc:
            return _err(ProtocolError(f"{command_key} merge failed: {exc}"))

        return _ok(merged_payload)

    def _request_authenticate(
        self,
        route: RouteKey,
        /,
        *,
        pending: bool = True,
        opaque: Any = None,
        **kwargs: Any,
    ) -> Result[int]:
        auth_queue: queue.Queue[dict[str, object]] | Any = (
            opaque if opaque is not None else queue.Queue(maxsize=1)
        )
        if not hasattr(auth_queue, "get"):
            return _err(ProtocolError("Authenticate opaque must support get()."))
        pin_value = kwargs.get("pin")
        if isinstance(pin_value, str) and pin_value.isdigit():
            self._last_auth_pin = int(pin_value)
        elif isinstance(pin_value, int):
            self._last_auth_pin = pin_value
        try:
            seq = self._kernel.request(route, pending=pending, opaque=auth_queue, **kwargs)
        except _CLIENT_EXCEPTIONS as exc:
            detail = f"route={route[0]}.{route[1]}"
            return _err(self._normalize_error(exc, phase="request", detail=detail))

        try:
            result = auth_queue.get(timeout=10.0)
        except queue.Empty:
            return _err(E27Timeout("Authenticate response timed out."))

        if not isinstance(result, dict):
            return _err(ProtocolError("Authenticate response was invalid."))

        result_payload = cast(dict[str, object], result)
        success = result_payload.get("success")
        error_code = result_payload.get("error_code")
        if success is True:
            return _ok(seq)
        if isinstance(error_code, int):
            return _err(InvalidPin(f"Authenticate failed with error_code={error_code}"))
        return _err(InvalidPin("Authenticate failed with unknown error."))

    async def _async_authenticate(
        self, *, pin: int, timeout_s: float | None
    ) -> Result[Mapping[str, Any]]:
        loop = asyncio.get_running_loop()
        seq = self._kernel.next_seq()
        expected_route: RouteKey = ("authenticate", "__root__")
        future = self._kernel.pending_responses.create(
            seq,
            command_key="control_authenticate",
            expected_route=expected_route,
            loop=loop,
        )
        sent_event = asyncio.Event()
        self._kernel.register_sent_event(seq, sent_event)

        try:
            timeout_value = (
                timeout_s
                if timeout_s is not None
                else getattr(self._kernel, "_request_timeout_s", 5.0)
            )
            self._kernel.send_request_with_seq(
                seq,
                "authenticate",
                "__root__",
                {"pin": pin},
                pending=False,
                opaque=None,
                expected_route=expected_route,
                priority=OutboundPriority.HIGH,
                timeout_s=timeout_value,
            )
        except _CLIENT_EXCEPTIONS as exc:
            self._kernel.pending_responses.drop(seq)
            detail = "route=authenticate.__root__"
            return _err(self._normalize_error(exc, phase="request", detail=detail))

        try:
            await sent_event.wait()
            msg = await asyncio.wait_for(future, timeout=timeout_value)
        except TimeoutError:
            self._kernel.pending_responses.drop(seq)
            return _err(E27Timeout("Authenticate response timed out."))
        except asyncio.CancelledError:
            self._kernel.pending_responses.drop(seq)
            raise
        except _CLIENT_EXCEPTIONS as exc:
            self._kernel.pending_responses.drop(seq)
            return _err(self._normalize_error(exc, phase="authenticate"))

        if not self._has_expected_payload(msg, expected_route):
            return _err(
                ProtocolError(
                    "control_authenticate missing response payload for authenticate.__root__"
                )
            )

        error_code = self._extract_error_code(msg, expected_route)
        if error_code is not None:
            if error_code == 11008:
                return _err(AuthorizationRequired("Authorization is required for this operation."))
            return _err(E27Error(f"control_authenticate failed with error_code={error_code}"))

        response_payload = self._extract_response_payload(msg, expected_route)
        return _ok(response_payload)

    def pump_once(self, *, timeout_s: float = 0.5) -> Result[dict[str, Any] | None]:
        try:
            msg = self._kernel.session.pump_once(timeout_s=timeout_s)
            return _ok(msg)
        except _CLIENT_EXCEPTIONS as exc:
            return _err(self._normalize_error(exc, phase="pump"))

    @property
    def panel_info(self) -> Any:
        return self._kernel.state.panel

    @property
    def table_info(self) -> Mapping[str, dict[str, object]]:
        return types.MappingProxyType(self._kernel.state.table_info_by_domain)

    @property
    def areas(self) -> Mapping[int, Any]:
        return _FilteredMapping(
            self._kernel.state.areas, self._kernel.state.inventory.configured_areas
        )

    @property
    def zones(self) -> Mapping[int, Any]:
        return _FilteredMapping(
            self._kernel.state.zones, self._kernel.state.inventory.configured_zones
        )

    @property
    def outputs(self) -> Mapping[int, Any]:
        return _FilteredMapping(
            self._kernel.state.outputs,
            _configured_ids_from_table(self._kernel.state, "output"),
        )

    @property
    def lights(self) -> Mapping[int, Any]:
        return _FilteredMapping(
            self._kernel.state.outputs,
            _configured_ids_from_table(self._kernel.state, "output"),
        )

    @property
    def thermostats(self) -> Mapping[int, Any]:
        return _FilteredMapping(
            self._kernel.state.tstats,
            _configured_ids_from_table(self._kernel.state, "tstat"),
        )

    def _normalize_error(
        self,
        exc: BaseException,
        *,
        phase: str | None = None,
        detail: str | None = None,
    ) -> E27Error:
        context = self._error_context(phase=phase, detail=detail)

        for err in _iter_causes(exc):
            if isinstance(err, E27ProvisioningRequired):
                return AuthorizationRequired(
                    str(err) or "Authorization required.", context=context, cause=err
                )
            if isinstance(err, E27LinkInvalid):
                return InvalidLinkKeys(str(err) or "Invalid link keys.", context=context, cause=err)
            if isinstance(err, (E27AuthFailed, E27ProvisioningTimeout)):
                return InvalidCredentials(
                    str(err) or "Invalid credentials.", context=context, cause=err
                )
            if isinstance(err, E27MissingContext):
                return err
            if isinstance(err, E27ProtocolError):
                return CryptoError(str(err) or "Protocol error.", context=context, cause=err)
            if isinstance(err, E27TransportError):
                return ConnectionLost(str(err) or "Connection lost.", context=context, cause=err)
            if isinstance(err, E27Error):
                return err
            if isinstance(err, KernelNotLinkedError):
                return MissingContext(
                    "Missing required connection context.",
                    context=context,
                    cause=err,
                )
            if isinstance(err, KernelMissingContextError):
                return MissingContext(
                    str(err) or "Missing required connection context.", context=context, cause=err
                )
            if isinstance(err, KernelInvalidPanelError):
                return ProtocolError(str(err) or "Invalid panel entry.", context=context, cause=err)
            if isinstance(err, SessionNotReadyError):
                return E27NotReady(
                    str(err) or "E27 session is not ready.", context=context, cause=err
                )
            if isinstance(err, (OSError, SessionIOError)):
                return ConnectionLost(str(err) or "Connection lost.", context=context, cause=err)
            if isinstance(err, (SessionProtocolError, ValueError)):
                return ProtocolError(str(err) or "Protocol error.", context=context, cause=err)
            if isinstance(err, TimeoutError):
                return E27Timeout(
                    str(err) or "E27 operation timed out.", context=context, cause=err
                )
            if isinstance(err, KernelError):
                if err.__cause__ is not None or err.__context__ is not None:
                    continue
                return E27Error(str(err) or "E27 operation failed.", context=context, cause=err)

        return E27Error(str(exc) or "E27 operation failed.", context=context, cause=exc)

    def _error_context(self, *, phase: str | None, detail: str | None) -> E27ErrorContext:
        host: str | None = None
        port: int | None = None

        session = getattr(self._kernel, "_session", None)
        if session is not None:
            host = session.cfg.host
            port = session.cfg.port

        return E27ErrorContext(
            host=host,
            port=port,
            phase=phase,
            detail=detail,
            session_id=self._kernel.state.panel.session_id,
        )

    @staticmethod
    def _extract_error_code(msg: Mapping[str, Any], expected_route: RouteKey) -> int | None:
        domain, command = expected_route
        domain_obj = msg.get(domain)
        if not isinstance(domain_obj, Mapping):
            return None
        domain_map = cast(Mapping[str, Any], domain_obj)
        payload = domain_map.get(command)
        if isinstance(payload, Mapping):
            payload_map = cast(Mapping[str, Any], payload)
            error_code = payload_map.get("error_code")
            if isinstance(error_code, int) and error_code != 0:
                return error_code
        error_code = domain_map.get("error_code")
        if isinstance(error_code, int) and error_code != 0:
            return error_code
        return None

    @staticmethod
    def _has_expected_payload(msg: Mapping[str, Any], expected_route: RouteKey) -> bool:
        domain, command = expected_route
        domain_obj = msg.get(domain)
        if not isinstance(domain_obj, Mapping):
            return False
        domain_map = cast(Mapping[str, Any], domain_obj)
        if command == "__root__":
            return True
        if command in domain_map:
            return True
        return "error_code" in domain_map

    @staticmethod
    def _extract_response_payload(
        msg: Mapping[str, Any], expected_route: RouteKey
    ) -> Mapping[str, Any]:
        domain, command = expected_route
        domain_obj = msg.get(domain)
        if isinstance(domain_obj, Mapping):
            domain_map = cast(Mapping[str, Any], domain_obj)
            if command == "__root__":
                return dict(domain_map)
            payload = domain_map.get(command)
            if isinstance(payload, Mapping):
                payload_map = cast(Mapping[str, Any], payload)
                return dict(payload_map)
            if payload is not None:
                error_code = domain_map.get("error_code")
                if isinstance(error_code, int):
                    return {"value": payload, "error_code": error_code}
                return {"value": payload}
            return dict(domain_map)
        return dict(msg)

    def _enforce_permissions(
        self,
        command_key: str,
        min_permission: PermissionLevel,
    ) -> E27Error | None:
        del min_permission
        if self._kernel.state.panel.session_id is None:
            return NotAuthenticatedError(f"{command_key}: missing session/encryption key.")
        return None

    @staticmethod
    def _coerce_pin_for_generator(spec: CommandSpec, params: Mapping[str, Any]) -> dict[str, Any]:
        coerced = dict(params)
        pin_value = coerced.get("pin")
        try:
            signature = inspect.signature(spec.generator)
        except (TypeError, ValueError):
            signature = None

        if signature is not None:
            accepts_kwargs = any(
                param.kind is inspect.Parameter.VAR_KEYWORD
                for param in signature.parameters.values()
            )
            pin_param = signature.parameters.get("pin")
            if pin_param is None and "pin" in coerced and not accepts_kwargs:
                coerced.pop("pin", None)
            elif (
                pin_param is not None
                and isinstance(pin_value, str)
                and pin_value.isdigit()
                and pin_param.annotation is int
            ):
                coerced["pin"] = int(pin_value)
        return coerced

    def _all_areas_disarmed(self) -> bool:
        areas = list(self._kernel.state.areas.values())
        if not areas:
            return False
        for area in areas:
            state = area.arm_state or area.armed_state
            if not isinstance(state, str):
                return False
            if state.lower() != "disarmed":
                return False
        return True

    def _resolve_merge_strategy(
        self,
        strategy: MergeStrategy | None,
    ) -> Callable[..., object] | None:
        if callable(strategy):
            return strategy
        if strategy == "area_configured":
            return make_area_configured_merge(self._kernel.state)
        if strategy == "zone_configured":
            return make_zone_configured_merge(self._kernel.state)
        if strategy == "output_configured":
            return _merge_configured_outputs
        if strategy == "output_all_status":
            return _merge_output_status_strings
        if strategy == "rule_blocks":
            return _merge_rule_blocks
        if strategy == "user_configured":
            return _merge_configured_users
        if strategy == "keypad_configured":
            return _merge_configured_keypads
        return None

    @staticmethod
    def _coerce_block_count(value: Any) -> int | None:
        if isinstance(value, int):
            return value if value >= 1 else None
        if isinstance(value, str) and value.isdigit():
            count = int(value)
            return count if count >= 1 else None
        return None


def _resolve_zone_definition(state: Any, value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, int):
        entry = getattr(state, "zone_defs_by_id", {}).get(value)
        if isinstance(entry, Mapping):
            entry_map = cast(Mapping[str, Any], entry)
            definition = entry_map.get("definition")
            if definition is not None:
                return str(definition)
    return None


def _merge_output_status_strings(blocks: list[PagedBlock], block_count: int) -> Mapping[str, Any]:
    status_parts: list[str] = []
    for block in blocks:
        status = block.payload.get("status")
        if isinstance(status, str):
            status_parts.append(status)
    return {"status": "".join(status_parts), "block_count": block_count}


def _merge_configured_outputs(blocks: list[PagedBlock], block_count: int) -> Mapping[str, Any]:
    keys = ("outputs", "output_ids", "configured_outputs", "configured_output_ids")
    merged: list[int] = []
    for block in blocks:
        for key in keys:
            value = block.payload.get(key)
            if isinstance(value, list):
                value_list = cast(list[object], value)
                for item in value_list:
                    if isinstance(item, int):
                        merged.append(item)
    return {"outputs": sorted(set(merged)), "block_count": block_count}


def _merge_configured_users(blocks: list[PagedBlock], block_count: int) -> Mapping[str, Any]:
    keys = ("users", "user_ids", "configured_users", "configured_user_ids")
    merged: list[int] = []
    for block in blocks:
        for key in keys:
            value = block.payload.get(key)
            if isinstance(value, list):
                value_list = cast(list[object], value)
                for item in value_list:
                    if isinstance(item, int):
                        merged.append(item)
    return {"users": sorted(set(merged)), "block_count": block_count}


def _merge_configured_keypads(blocks: list[PagedBlock], block_count: int) -> Mapping[str, Any]:
    keys = ("keypads", "keypad_ids", "configured_keypads", "configured_keypad_ids")
    merged: list[int] = []
    for block in blocks:
        for key in keys:
            value = block.payload.get(key)
            if isinstance(value, list):
                value_list = cast(list[object], value)
                for item in value_list:
                    if isinstance(item, int):
                        merged.append(item)
    return {"keypads": sorted(set(merged)), "block_count": block_count}


def _merge_rule_blocks(blocks: list[PagedBlock], block_count: int) -> Mapping[str, Any]:
    merged: dict[int, dict[str, object]] = {}
    for block in blocks:
        if block.block_id == 0:
            continue
        data = block.payload.get("data")
        if isinstance(data, str):
            merged[block.block_id] = {"block_id": block.block_id, "data": data}
    rules = [merged[key] for key in sorted(merged)]
    return {"rules": rules, "block_count": block_count}
