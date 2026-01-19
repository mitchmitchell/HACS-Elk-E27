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
import inspect
import logging
import queue
import types
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import types as types_mod
from typing import Any, AsyncIterator, Callable, Collection, Generic, Iterable, Mapping, Optional, Sequence, TypeVar

from .dispatcher import PagedBlock, RouteKey
from .outbound import OutboundPriority
from . import discovery as discovery_mod
from . import linking as linking_mod
from .errors import (
    CryptoError,
    E27AuthFailed,
    E27LinkInvalid,
    E27MissingContext,
    E27NotReady,
    E27ProtocolError,
    E27ProvisioningRequired,
    E27ProvisioningTimeout,
    E27Timeout,
    E27TransportError,
    InvalidCredentials,
    InvalidPinError,
    NotAuthenticatedError,
    PanelNotDisarmedError,
    PermissionDeniedError,
)
from .redact import redact_for_diagnostics
from .errors import (
    Elke27Error,
    Elke27AuthError,
    Elke27ConnectionError,
    Elke27CryptoError,
    Elke27DisconnectedError,
    Elke27InvalidArgument,
    Elke27LinkRequiredError,
    Elke27PinRequiredError,
    Elke27PermissionError,
    Elke27ProtocolError as Elke27ProtocolErrorV2,
    Elke27TimeoutError,
)
from .kernel import (
    DiscoverResult,
    E27Kernel,
    KernelError,
    KernelInvalidPanelError,
    KernelMissingContextError,
    KernelNotLinkedError,
)
from .linking import E27Identity, E27LinkKeys
from .errors import (
    AuthorizationRequired,
    ConnectionLost,
    CryptoError,
    E27Error,
    E27ErrorContext,
    E27AuthFailed,
    E27LinkInvalid,
    E27MissingContext,
    E27NotReady,
    E27ProvisioningRequired,
    E27ProvisioningTimeout,
    E27ProtocolError,
    E27TransportError,
    E27Timeout,
    InvalidPin,
    InvalidCredentials,
    InvalidLinkKeys,
    MissingContext,
    NotAuthenticatedError,
    InvalidPinError,
    PanelNotDisarmedError,
    PermissionDeniedError,
    ProtocolError,
)
from .events import (
    AreaAttribsUpdated,
    AreaConfiguredInventoryReady,
    AreaStatusUpdated,
    AreaTableInfoUpdated,
    ConnectionStateChanged,
    Event,
    KeypadConfiguredInventoryReady,
    OutputConfiguredInventoryReady,
    OutputStatusUpdated,
    OutputTableInfoUpdated,
    OutputsStatusBulkUpdated,
    PanelVersionInfoUpdated,
    TstatTableInfoUpdated,
    UserConfiguredInventoryReady,
    ZoneAttribsUpdated,
    ZoneConfiguredInventoryReady,
    ZoneStatusUpdated,
    ZoneTableInfoUpdated,
    ZonesStatusBulkUpdated,
)
from .generators.registry import COMMANDS
from .handlers.area import make_area_configured_merge
from .handlers.zone import make_zone_configured_merge
from .permissions import (
    PermissionLevel,
    canonical_generator_key,
    permission_for_generator,
    requires_disarmed,
    requires_pin,
)
from .session import SessionConfig, SessionNotReadyError, SessionIOError, SessionProtocolError
from .types import (
    ArmMode,
    ClientConfig,
    DiscoveredPanel,
    Elke27Event,
    EventType,
    LinkKeys,
    PanelSnapshot,
    PanelInfo,
    TableInfo,
    AreaState as V2AreaState,
    ZoneState as V2ZoneState,
    OutputState as V2OutputState,
)

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class Result(Generic[T]):
    ok: bool
    data: Optional[T] = None
    error: Optional[BaseException] = None

    @classmethod
    def success(cls, value: T) -> "Result[T]":
        return cls(ok=True, data=value, error=None)

    @classmethod
    def failure(cls, error: BaseException) -> "Result[T]":
        return cls(ok=False, data=None, error=error)

    def unwrap(self) -> T:
        if self.ok:
            return self.data  # type: ignore[return-value]
        if self.error is not None:
            raise self.error
        raise E27Error("Unknown error.")


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
    current: Optional[BaseException] = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


class _FilteredMapping(Mapping[int, Any]):
    def __init__(self, source: Mapping[int, Any], allowed: Collection[int]) -> None:
        self._source = source
        self._allowed = allowed

    def __getitem__(self, key: int) -> Any:
        if key not in self._allowed:
            raise KeyError(key)
        return self._source[key]

    def __iter__(self):
        for key in self._source:
            if key in self._allowed:
                yield key

    def __len__(self) -> int:
        return sum(1 for key in self._source if key in self._allowed)


def _configured_ids_from_table(state: "PanelState", domain: str) -> Collection[int]:
    info = state.table_info_by_domain.get(domain)
    if not isinstance(info, Mapping):
        return ()
    table_elements = info.get("table_elements")
    if not isinstance(table_elements, int) or table_elements < 1:
        return ()
    return range(1, table_elements + 1)


class Elke27Client:
    """
    Stable client API for consumers (e.g., Home Assistant).

    This facade intentionally avoids HA-specific concepts.
    """

    def __init__(
        self,
        config: ClientConfig | None = None,
        *,
        kernel: Optional[E27Kernel] = None,
        now_monotonic: Callable[[], float] | None = None,
        event_queue_maxlen: Optional[int] = None,
        features: Optional[Sequence[str]] = None,
        logger: Optional[logging.Logger] = None,
        filter_attribs_to_configured: bool = True,
    ) -> None:
        self._log = logger or logging.getLogger(__name__)
        self._feature_modules = features
        self._v2_config = config
        self._v2_client_identity: Optional[linking_mod.E27Identity] = None
        self._connected = False
        self._event_loop: Optional[asyncio.AbstractEventLoop] = None
        queue_size = config.event_queue_size if config and config.event_queue_size > 0 else 256
        self._event_queue = asyncio.Queue(maxsize=queue_size)
        self._event_seq_counter: int = 0
        self._event_session_id: Optional[int] = None
        self._subscriber_callbacks: list[Callable[[Elke27Event], None]] = []
        self._subscriber_lock = threading.Lock()
        self._subscriber_error_types: set[type] = set()
        self._kernel_event_token: Optional[int] = None
        self._snapshot = PanelSnapshot.empty()
        self._snapshot_version = 0
        if event_queue_maxlen is None:
            event_queue_maxlen = config.event_queue_maxlen if config is not None else 0
        request_timeout_s = config.request_timeout_s if config is not None else 5.0
        if logger is None and config is not None and config.logger_name:
            self._log = logging.getLogger(config.logger_name)
        if kernel is None:
            outbound_min_interval_s = config.outbound_min_interval_s if config is not None else 0.05
            outbound_max_burst = config.outbound_max_burst if config is not None else 1
            self._kernel = E27Kernel(
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
        self._auth_role: Optional[str] = None
        self._ready_event = asyncio.Event()
        self._inventory_ready = {"area": False, "zone": False, "output": False}
        self._status_pending: dict[str, set[int]] = {
            "area": set(),
            "zone": set(),
            "output": set(),
        }
        self._status_ready = {"area": False, "zone": False, "output": False}
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

    def set_authenticated_role(self, role: Optional[str]) -> None:
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
        except asyncio.TimeoutError:
            return False
        return True

    def subscribe(self, callback: Callable[[Elke27Event], None], *, kinds: Optional[Iterable[str]] = None) -> Callable[[], None]:
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

    def drain_events(self) -> list[Event]:
        return self._kernel.drain_events()

    def iter_events(self) -> Iterable[Event]:
        return self._kernel.iter_events()

    # --- v2 public API (async-only, exceptions-only) ---
    @staticmethod
    def _default_identity() -> linking_mod.E27Identity:
        return linking_mod.E27Identity(mn="222", sn="000000001", fwver="0.1", hwver="0.1", osver="0.1")

    def _coerce_identity(self, identity: Optional[Mapping[str, str] | linking_mod.E27Identity]) -> linking_mod.E27Identity:
        if identity is None:
            return self._default_identity()
        if isinstance(identity, linking_mod.E27Identity):
            return identity
        if not isinstance(identity, Mapping):
            raise Elke27InvalidArgument("client_identity must be a mapping with mn/sn fields.")
        mn = str(identity.get("mn") or "222")
        sn = str(identity.get("sn") or "00000001")
        fwver = str(identity.get("fwver") or "0.1")
        hwver = str(identity.get("hwver") or "0.1")
        osver = str(identity.get("osver") or "0.1")
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
                raise Elke27LinkRequiredError("Linking required to perform this operation.") from None
            if isinstance(err, KernelMissingContextError):
                raise Elke27InvalidArgument("Missing required context for operation.") from None
            if isinstance(err, (E27ProvisioningTimeout, InvalidCredentials, E27AuthFailed, InvalidPinError)):
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
            if isinstance(err, (KernelError, E27MissingContext, KernelInvalidPanelError, KernelNotLinkedError)):
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
    def _arm_mode_from_string(value: Optional[str]) -> Optional[ArmMode]:
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
        table_info = self._kernel.state.table_info_by_domain
        return TableInfo(
            areas=table_info.get("area", {}).get("table_elements"),
            zones=table_info.get("zone", {}).get("table_elements"),
            outputs=table_info.get("output", {}).get("table_elements"),
            tstats=table_info.get("tstat", {}).get("table_elements"),
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
                alarm_active=area.alarm_state is not None and str(area.alarm_state).lower() != "no_alarm_active",
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

    def _build_output_map(self) -> Mapping[int, V2OutputState]:
        out: dict[int, V2OutputState] = {}
        for output_id, output in self._kernel.state.outputs.items():
            out[output_id] = V2OutputState(
                output_id=output_id,
                name=output.name,
                state=output.on,
            )
        return types_mod.MappingProxyType(out)

    def _replace_snapshot(
        self,
        *,
        panel_info: Optional[PanelInfo] = None,
        table_info: Optional[TableInfo] = None,
        areas: Optional[Mapping[int, V2AreaState]] = None,
        zones: Optional[Mapping[int, V2ZoneState]] = None,
        outputs: Optional[Mapping[int, V2OutputState]] = None,
    ) -> None:
        self._snapshot_version += 1
        now = datetime.now(timezone.utc)
        self._snapshot = PanelSnapshot(
            panel=panel_info or self._snapshot.panel,
            table_info=table_info or self._snapshot.table_info,
            areas=areas or self._snapshot.areas,
            zones=zones or self._snapshot.zones,
            outputs=outputs or self._snapshot.outputs,
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
        route_map = {
            "area": ("area", "get_status"),
            "zone": ("zone", "get_status"),
            "output": ("output", "get_status"),
        }
        param_map = {
            "area": "area_id",
            "zone": "zone_id",
            "output": "output_id",
        }
        route = route_map.get(domain)
        param = param_map.get(domain)
        if route is None or param is None:
            return
        for entity_id in sorted(ids):
            try:
                self._kernel.request(route, **{param: entity_id})
            except (E27Error, KeyError, RuntimeError, TypeError, ValueError):
                continue

    def _queue_bootstrap_attribs(self, domain: str) -> None:
        inv = self._kernel.state.inventory
        if domain == "area":
            if inv.configured_areas:
                for area_id in sorted(inv.configured_areas):
                    try:
                        self._kernel.request(("area", "get_attribs"), area_id=area_id)
                    except (E27Error, KeyError, RuntimeError, TypeError, ValueError):
                        continue
        elif domain == "zone":
            if inv.configured_zones:
                for zone_id in sorted(inv.configured_zones):
                    try:
                        self._kernel.request(("zone", "get_attribs"), zone_id=zone_id)
                    except (E27Error, KeyError, RuntimeError, TypeError, ValueError):
                        continue
        elif domain == "output":
            if inv.configured_outputs:
                for output_id in sorted(inv.configured_outputs):
                    try:
                        self._kernel.request(("output", "get_attribs"), output_id=output_id)
                    except (E27Error, KeyError, RuntimeError, TypeError, ValueError):
                        continue
        elif domain == "user":
            if inv.configured_users:
                for user_id in sorted(inv.configured_users):
                    try:
                        self._kernel.request(("user", "get_attribs"), user_id=user_id)
                    except (E27Error, KeyError, RuntimeError, TypeError, ValueError):
                        continue
        elif domain == "keypad":
            if inv.configured_keypads:
                for keypad_id in sorted(inv.configured_keypads):
                    try:
                        self._kernel.request(("keypad", "get_attribs"), keypad_id=keypad_id)
                    except (E27Error, KeyError, RuntimeError, TypeError, ValueError):
                        continue

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
            try:
                self._event_queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        try:
            self._event_queue.put_nowait(event)
        except asyncio.QueueFull:
            pass

    def _signal_event_stream_end(self) -> None:
        sentinel: Optional[Elke27Event] = None
        if self._event_queue.full():
            try:
                self._event_queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        try:
            self._event_queue.put_nowait(sentinel)
        except asyncio.QueueFull:
            pass

    def _map_event_type(self, evt: Event) -> EventType:
        if evt.kind == ConnectionStateChanged.KIND:
            return EventType.CONNECTION
        if "area" in evt.kind:
            return EventType.AREA
        if "zone" in evt.kind:
            return EventType.ZONE
        if "output" in evt.kind:
            return EventType.OUTPUT
        if "panel" in evt.kind or "table_info" in evt.kind:
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
        timestamp = datetime.now(timezone.utc)

        if isinstance(evt, ConnectionStateChanged):
            if evt.connected:
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
                    outputs=self._build_output_map(),
                )
            else:
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

        if evt.kind == AreaConfiguredInventoryReady.KIND:
            self._mark_inventory_ready("area")
        elif evt.kind == ZoneConfiguredInventoryReady.KIND:
            self._mark_inventory_ready("zone")
        elif evt.kind == OutputConfiguredInventoryReady.KIND:
            self._mark_inventory_ready("output")
        elif evt.kind == UserConfiguredInventoryReady.KIND:
            self._queue_bootstrap_attribs("user")
        elif evt.kind == KeypadConfiguredInventoryReady.KIND:
            self._queue_bootstrap_attribs("keypad")
        elif evt.kind == AreaStatusUpdated.KIND:
            self._mark_status_seen("area", [evt.area_id])
        elif evt.kind == ZoneStatusUpdated.KIND:
            self._mark_status_seen("zone", [evt.zone_id])
        elif evt.kind == ZonesStatusBulkUpdated.KIND:
            self._mark_status_seen("zone", evt.updated_ids)
        elif evt.kind == OutputStatusUpdated.KIND:
            self._mark_status_seen("output", [evt.output_id])
        elif evt.kind == OutputsStatusBulkUpdated.KIND:
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
            ZonesStatusBulkUpdated.KIND,
            OutputStatusUpdated.KIND,
            OutputsStatusBulkUpdated.KIND,
            ZoneStatusUpdated.KIND,
        }:
            self._replace_snapshot(
                panel_info=self._build_panel_info(),
                table_info=self._build_table_info(),
                areas=self._build_area_map(),
                zones=self._build_zone_map(),
                outputs=self._build_output_map(),
            )
        self._maybe_set_ready()

        with self._subscriber_lock:
            callbacks = list(self._subscriber_callbacks)
        for cb in callbacks:
            try:
                cb(v2_evt)
            except Exception as exc:  # noqa: BLE001
                exc_type = type(exc)
                if exc_type not in self._subscriber_error_types:
                    self._subscriber_error_types.add(exc_type)
                    self._log.warning("Subscriber callback failed: %s", exc_type.__name__)

    def _on_kernel_event(self, evt: Event) -> None:
        if self._event_loop is None:
            return
        try:
            self._event_loop.call_soon_threadsafe(self._handle_kernel_event, evt)
        except RuntimeError:
            pass

    async def async_discover(
        self,
        *,
        timeout_s: Optional[float] = None,
        address: Optional[str] = None,
    ) -> list[DiscoveredPanel]:
        """Discover panels on the network (v2 public API)."""
        timeout = int(timeout_s) if timeout_s is not None else 10
        try:
            result = await E27Kernel.discover(timeout=timeout, address=address)
        except BaseException as exc:  # noqa: BLE001
            self._raise_v2_error(exc, phase="discover")
        panels: list[DiscoveredPanel] = []
        for panel in result.panels:
            if isinstance(panel, discovery_mod.E27System):
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
        client_identity: Optional[Mapping[str, str] | linking_mod.E27Identity] = None,
        timeout_s: Optional[float] = None,
    ) -> LinkKeys:
        """Provision link keys for a panel (v2 public API)."""
        if not isinstance(host, str) or not host:
            raise Elke27InvalidArgument("host must be a non-empty string.")
        if not isinstance(port, int) or port <= 0:
            raise Elke27InvalidArgument("port must be a positive integer.")
        if not isinstance(access_code, str) or not access_code:
            raise Elke27InvalidArgument("access_code must be a non-empty string.")
        if not isinstance(passphrase, str) or not passphrase:
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
        return LinkKeys(
            tempkey_hex=link_keys.tempkey_hex,
            linkkey_hex=link_keys.linkkey_hex,
            linkhmac_hex=link_keys.linkhmac_hex,
        )

    async def async_connect(self, host: str, port: int, link_keys: LinkKeys) -> None:
        """Connect to a panel using link keys (v2 public API)."""
        if not isinstance(host, str) or not host:
            raise Elke27InvalidArgument("host must be a non-empty string.")
        if not isinstance(port, int) or port <= 0:
            raise Elke27InvalidArgument("port must be a positive integer.")
        self._reset_bootstrap_state()
        self._event_loop = asyncio.get_running_loop()
        self._ensure_kernel_subscription()
        identity = self._v2_client_identity or self._default_identity()
        session_cfg = SessionConfig(host=host, port=port)
        try:
            await self._kernel.connect(
                self._coerce_link_keys(link_keys),
                panel={"host": host, "port": port},
                client_identity=identity,
                session_config=session_cfg,
            )
        except BaseException as exc:  # noqa: BLE001
            self._raise_v2_error(exc, phase="connect")
        self._connected = True
        if self._snapshot.version == 0:
            self._replace_snapshot(
                panel_info=self._build_panel_info(),
                table_info=self._build_table_info(),
                areas=self._build_area_map(),
                zones=self._build_zone_map(),
                outputs=self._build_output_map(),
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

    async def async_set_output(self, output_id: int, *, on: bool) -> None:
        """Set an output on or off (v2 public API)."""
        if not isinstance(output_id, int) or output_id < 1:
            raise Elke27InvalidArgument("output_id must be a positive integer.")
        if not isinstance(on, bool):
            raise Elke27InvalidArgument("on must be a boolean.")
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
        if not isinstance(area_id, int) or area_id < 1:
            raise Elke27InvalidArgument("area_id must be a positive integer.")
        if not isinstance(mode, ArmMode):
            raise Elke27InvalidArgument("mode must be an ArmMode.")
        if mode is ArmMode.DISARMED:
            if not pin:
                raise Elke27InvalidArgument("PIN is required to disarm.")
            await self.async_disarm_area(area_id, pin=pin)
            return
        if mode is ArmMode.ARMED_NIGHT:
            raise Elke27InvalidArgument("ARMED_NIGHT is not supported by the current protocol.")
        if not pin:
            raise Elke27InvalidArgument("PIN is required to arm.")
        if not isinstance(pin, str) or not pin.isdigit():
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
        if not isinstance(area_id, int) or area_id < 1:
            raise Elke27InvalidArgument("area_id must be a positive integer.")
        if not isinstance(pin, str) or not pin:
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

    async def discover(self, *, timeout: int = 10, address: str | None = None) -> Result[DiscoverResult]:
        try:
            panels = await E27Kernel.discover(timeout=timeout, address=address)
            return Result.success(panels)
        except _CLIENT_EXCEPTIONS as exc:
            return Result.failure(self._normalize_error(exc, phase="discover"))

    async def link(
        self,
        panel: Mapping[str, Any] | Any,
        client_identity: E27Identity,
        credentials: Any,
        *,
        timeout_s: float = 10.0,
    ) -> Result[Any]:
        try:
            keys = await self._kernel.link(panel, client_identity, credentials, timeout_s=timeout_s)
            return Result.success(keys)
        except _CLIENT_EXCEPTIONS as exc:
            return Result.failure(self._normalize_error(exc, phase="link"))

    async def connect(
        self,
        link_keys: Any,
        *,
        panel: Mapping[str, Any] | None = None,
        client_identity: E27Identity | None = None,
        session_config: Optional[SessionConfig] = None,
    ) -> Result[None]:
        try:
            await asyncio.to_thread(self._kernel.load_features_blocking, self._feature_modules)
            await self._kernel.connect(
                link_keys,
                panel=panel,
                client_identity=client_identity,
                session_config=session_config,
            )
            return Result.success(None)
        except _CLIENT_EXCEPTIONS as exc:
            return Result.failure(self._normalize_error(exc, phase="connect"))

    async def close(self) -> Result[None]:
        try:
            await self._kernel.close()
            return Result.success(None)
        except _CLIENT_EXCEPTIONS as exc:
            return Result.failure(self._normalize_error(exc, phase="close"))

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
            seq = self._kernel.request(route, pending=pending, opaque=opaque, **kwargs)
            return Result.success(seq)
        except _CLIENT_EXCEPTIONS as exc:
            detail = f"route={route[0]}.{route[1]}"
            return Result.failure(self._normalize_error(exc, phase="request", detail=detail))

    async def async_execute(
        self,
        command_key: str,
        /,
        *,
        timeout_s: Optional[float] = None,
        **params: Any,
    ) -> Result[Mapping[str, Any]]:
        if command_key == "control_authenticate":
            try:
                permission_level = permission_for_generator(command_key)
            except Elke27ProtocolErrorV2 as exc:
                return Result.failure(exc)

            permission_error = self._enforce_permissions(command_key, permission_level)
            if permission_error is not None:
                return Result.failure(permission_error)

            pin_value = params.get("pin")
            if pin_value is None or (isinstance(pin_value, str) and not pin_value):
                return Result.failure(Elke27PinRequiredError())
            if isinstance(pin_value, str):
                if not pin_value.isdigit():
                    return Result.failure(InvalidPinError("PIN must be a non-empty digit string."))
                pin_int = int(pin_value)
            elif isinstance(pin_value, int):
                if pin_value <= 0:
                    return Result.failure(InvalidPinError("PIN must be a positive integer."))
                pin_int = pin_value
            else:
                return Result.failure(InvalidPinError("PIN must be a non-empty digit string."))

            return await self._async_authenticate(pin=pin_int, timeout_s=timeout_s)

        spec = COMMANDS.get(command_key)
        if spec is None:
            return Result.failure(ProtocolError(f"Unknown command_key={command_key!r}"))

        canonical_key = canonical_generator_key(spec.generator.__name__)
        try:
            permission_level = permission_for_generator(canonical_key)
        except Elke27ProtocolErrorV2 as exc:
            return Result.failure(exc)

        permission_error = self._enforce_permissions(command_key, permission_level)
        if permission_error is not None:
            return Result.failure(permission_error)

        if requires_disarmed(permission_level):
            if not self._all_areas_disarmed():
                return Result.failure(
                    Elke27PermissionError("This action requires all areas to be disarmed.")
                )

        if requires_pin(permission_level):
            pin_value = params.get("pin")
            if pin_value is None or (isinstance(pin_value, str) and not pin_value):
                return Result.failure(Elke27PinRequiredError())
            if isinstance(pin_value, str):
                if not pin_value.isdigit():
                    return Result.failure(InvalidPinError("PIN must be a non-empty digit string."))
            elif isinstance(pin_value, int):
                if pin_value <= 0:
                    return Result.failure(InvalidPinError("PIN must be a positive integer."))
            else:
                return Result.failure(InvalidPinError("PIN must be a non-empty digit string."))

        if spec.response_mode == "single":
            if spec.key == "area_get_attribs":
                configured = self._kernel.state.inventory.configured_areas
                if not configured:
                    configured_result = await self.async_execute("area_get_configured")
                    if not configured_result.ok:
                        return Result.failure(
                            configured_result.error or ProtocolError("area_get_configured failed.")
                        )
            if spec.key == "zone_get_attribs":
                configured = self._kernel.state.inventory.configured_zones
                if not configured:
                    configured_result = await self.async_execute("zone_get_configured")
                    if not configured_result.ok:
                        return Result.failure(
                            configured_result.error or ProtocolError("zone_get_configured failed.")
                        )
            if spec.key == "output_get_attribs":
                inv = self._kernel.state.inventory
                if not inv.configured_outputs and not inv.configured_outputs_complete:
                    configured_result = await self.async_execute("output_get_configured")
                    if not configured_result.ok:
                        return Result.failure(
                            configured_result.error or ProtocolError("output_get_configured failed.")
                        )
                    outputs = configured_result.data.get("outputs") if configured_result.data else None
                    if isinstance(outputs, list):
                        inv.configured_outputs = {item for item in outputs if isinstance(item, int) and item >= 1}
                    inv.configured_outputs_complete = True
            if spec.key == "user_get_attribs":
                inv = self._kernel.state.inventory
                if not inv.configured_users and not inv.configured_users_complete:
                    configured_result = await self.async_execute("user_get_configured")
                    if not configured_result.ok:
                        return Result.failure(
                            configured_result.error or ProtocolError("user_get_configured failed.")
                        )
                    users = configured_result.data.get("users") if configured_result.data else None
                    if isinstance(users, list):
                        inv.configured_users = {item for item in users if isinstance(item, int) and item >= 1}
                    inv.configured_users_complete = True
            if spec.key == "keypad_get_attribs":
                inv = self._kernel.state.inventory
                if not inv.configured_keypads and not inv.configured_keypads_complete:
                    configured_result = await self.async_execute("keypad_get_configured")
                    if not configured_result.ok:
                        return Result.failure(
                            configured_result.error or ProtocolError("keypad_get_configured failed.")
                        )
                    keypads = configured_result.data.get("keypads") if configured_result.data else None
                    if isinstance(keypads, list):
                        inv.configured_keypads = {item for item in keypads if isinstance(item, int) and item >= 1}
                    inv.configured_keypads_complete = True
            params_for_generator = self._coerce_pin_for_generator(spec, params)
            try:
                payload, expected_route = spec.generator(**params_for_generator)
            except NotImplementedError as exc:
                return Result.failure(exc)
            except _CLIENT_EXCEPTIONS as exc:
                detail = f"command_key={command_key}"
                return Result.failure(self._normalize_error(exc, phase="execute", detail=detail))

            loop = asyncio.get_running_loop()
            seq = self._kernel._next_seq()
            future = self._kernel._pending_responses.create(
                seq,
                command_key=command_key,
                expected_route=expected_route,
                loop=loop,
            )
            sent_event = asyncio.Event()
            self._kernel._register_sent_event(seq, sent_event)
            timeout_value = timeout_s if timeout_s is not None else getattr(self._kernel, "_request_timeout_s", 5.0)

            try:
                self._kernel._send_request_with_seq(
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
                self._kernel._pending_responses.drop(seq)
                detail = f"command_key={command_key} seq={seq}"
                return Result.failure(self._normalize_error(exc, phase="execute", detail=detail))

            try:
                await sent_event.wait()
                msg = await asyncio.wait_for(future, timeout=timeout_value)
            except asyncio.TimeoutError:
                self._kernel._pending_responses.drop(seq)
                return Result.failure(E27Timeout(f"async_execute timeout waiting for {command_key} seq={seq}"))
            except asyncio.CancelledError:
                self._kernel._pending_responses.drop(seq)
                raise
            except _CLIENT_EXCEPTIONS as exc:
                self._kernel._pending_responses.drop(seq)
                detail = f"command_key={command_key} seq={seq}"
                return Result.failure(self._normalize_error(exc, phase="execute", detail=detail))

            if not self._has_expected_payload(msg, expected_route):
                return Result.failure(
                    ProtocolError(f"{command_key} missing response payload for {expected_route[0]}.{expected_route[1]}")
                )

            error_code = self._extract_error_code(msg, expected_route)
            if error_code is not None:
                if error_code == 11008:
                    return Result.failure(AuthorizationRequired("Authorization is required for this operation."))
                return Result.failure(E27Error(f"{command_key} failed with error_code={error_code}"))

            response_payload = self._extract_response_payload(msg, expected_route)
            return Result.success(response_payload)

        if spec.response_mode != "paged_blocks":
            return Result.failure(ProtocolError(f"Command {command_key!r} has unsupported response_mode."))

        if spec.block_field is None or spec.block_count_field is None:
            return Result.failure(ProtocolError(f"Command {command_key!r} is missing paging metadata."))

        merge_fn = self._resolve_merge_strategy(spec.merge_strategy)
        if merge_fn is None:
            return Result.failure(ProtocolError(f"Command {command_key!r} is missing merge_strategy."))

        timeout_value = timeout_s if timeout_s is not None else getattr(self._kernel, "_request_timeout_s", 5.0)
        block_id = spec.first_block
        block_count: Optional[int] = None
        blocks: list["PagedBlock"] = []

        while True:
            params_with_block = dict(params)
            params_with_block[spec.block_field] = block_id
            params_for_generator = self._coerce_pin_for_generator(spec, params_with_block)

            try:
                payload, expected_route = spec.generator(**params_for_generator)
            except NotImplementedError as exc:
                return Result.failure(exc)
            except _CLIENT_EXCEPTIONS as exc:
                detail = f"command_key={command_key}"
                return Result.failure(self._normalize_error(exc, phase="execute", detail=detail))

            loop = asyncio.get_running_loop()
            seq = self._kernel._next_seq()
            future = self._kernel._pending_responses.create(
                seq,
                command_key=command_key,
                expected_route=expected_route,
                loop=loop,
            )
            sent_event = asyncio.Event()
            self._kernel._register_sent_event(seq, sent_event)
            timeout_value = timeout_s if timeout_s is not None else getattr(self._kernel, "_request_timeout_s", 5.0)
            try:
                self._kernel._send_request_with_seq(
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
                self._kernel._pending_responses.drop(seq)
                detail = f"command_key={command_key} seq={seq}"
                return Result.failure(self._normalize_error(exc, phase="execute", detail=detail))

            try:
                await sent_event.wait()
                msg = await asyncio.wait_for(future, timeout=timeout_value)
            except asyncio.TimeoutError:
                self._kernel._pending_responses.drop(seq)
                return Result.failure(E27Timeout(f"async_execute timeout waiting for {command_key} seq={seq}"))
            except asyncio.CancelledError:
                self._kernel._pending_responses.drop(seq)
                raise
            except _CLIENT_EXCEPTIONS as exc:
                self._kernel._pending_responses.drop(seq)
                detail = f"command_key={command_key} seq={seq}"
                return Result.failure(self._normalize_error(exc, phase="execute", detail=detail))

            if not self._has_expected_payload(msg, expected_route):
                return Result.failure(
                    ProtocolError(f"{command_key} missing response payload for {expected_route[0]}.{expected_route[1]}")
                )

            error_code = self._extract_error_code(msg, expected_route)
            if error_code is not None:
                if error_code == 11008:
                    return Result.failure(AuthorizationRequired("Authorization is required for this operation."))
                return Result.failure(E27Error(f"{command_key} failed with error_code={error_code}"))

            response_payload = self._extract_response_payload(msg, expected_route)
            response_block_count = self._coerce_block_count(response_payload.get(spec.block_count_field))
            if block_count is None:
                block_count = response_block_count
                if block_count is None:
                    return Result.failure(ProtocolError(f"{command_key} missing block_count in response."))
            elif response_block_count is not None and response_block_count != block_count:
                return Result.failure(ProtocolError(f"{command_key} block_count mismatch in response."))

            blocks.append(PagedBlock(block_id=block_id, payload=response_payload))

            if block_id >= block_count:
                break
            block_id += 1

        try:
            merged_payload = merge_fn(blocks, block_count or len(blocks))
        except Exception as exc:
            return Result.failure(ProtocolError(f"{command_key} merge failed: {exc}"))

        return Result.success(merged_payload)

    def _request_authenticate(
        self,
        route: RouteKey,
        /,
        *,
        pending: bool = True,
        opaque: Any = None,
        **kwargs: Any,
    ) -> Result[int]:
        auth_queue = opaque if opaque is not None else queue.Queue(maxsize=1)
        if not hasattr(auth_queue, "get"):
            return Result.failure(ProtocolError("Authenticate opaque must support get()."))
        try:
            seq = self._kernel.request(route, pending=True, opaque=auth_queue, **kwargs)
        except _CLIENT_EXCEPTIONS as exc:
            detail = f"route={route[0]}.{route[1]}"
            return Result.failure(self._normalize_error(exc, phase="request", detail=detail))

        try:
            result = auth_queue.get(timeout=10.0)
        except queue.Empty:
            return Result.failure(E27Timeout("Authenticate response timed out."))

        if not isinstance(result, dict):
            return Result.failure(ProtocolError("Authenticate response was invalid."))

        success = result.get("success")
        error_code = result.get("error_code")
        if success is True:
            return Result.success(seq)
        if isinstance(error_code, int):
            return Result.failure(InvalidPin(f"Authenticate failed with error_code={error_code}"))
        return Result.failure(InvalidPin("Authenticate failed with unknown error."))

    async def _async_authenticate(self, *, pin: int, timeout_s: Optional[float]) -> Result[Mapping[str, Any]]:
        loop = asyncio.get_running_loop()
        seq = self._kernel._next_seq()
        expected_route: RouteKey = ("authenticate", "__root__")
        future = self._kernel._pending_responses.create(
            seq,
            command_key="control_authenticate",
            expected_route=expected_route,
            loop=loop,
        )
        sent_event = asyncio.Event()
        self._kernel._register_sent_event(seq, sent_event)

        try:
            timeout_value = timeout_s if timeout_s is not None else getattr(self._kernel, "_request_timeout_s", 5.0)
            self._kernel._send_request_with_seq(
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
            self._kernel._pending_responses.drop(seq)
            detail = "route=authenticate.__root__"
            return Result.failure(self._normalize_error(exc, phase="request", detail=detail))

        try:
            await sent_event.wait()
            msg = await asyncio.wait_for(future, timeout=timeout_value)
        except asyncio.TimeoutError:
            self._kernel._pending_responses.drop(seq)
            return Result.failure(E27Timeout("Authenticate response timed out."))
        except asyncio.CancelledError:
            self._kernel._pending_responses.drop(seq)
            raise
        except _CLIENT_EXCEPTIONS as exc:
            self._kernel._pending_responses.drop(seq)
            return Result.failure(self._normalize_error(exc, phase="authenticate"))

        if not self._has_expected_payload(msg, expected_route):
            return Result.failure(
                ProtocolError("control_authenticate missing response payload for authenticate.__root__")
            )

        error_code = self._extract_error_code(msg, expected_route)
        if error_code is not None:
            if error_code == 11008:
                return Result.failure(AuthorizationRequired("Authorization is required for this operation."))
            return Result.failure(E27Error(f"control_authenticate failed with error_code={error_code}"))

        response_payload = self._extract_response_payload(msg, expected_route)
        return Result.success(response_payload)


    def pump_once(self, *, timeout_s: float = 0.5) -> Result[Optional[dict[str, Any]]]:
        try:
            msg = self._kernel.session.pump_once(timeout_s=timeout_s)
            return Result.success(msg)
        except _CLIENT_EXCEPTIONS as exc:
            return Result.failure(self._normalize_error(exc, phase="pump"))

    @property
    def panel_info(self) -> Any:
        return self._kernel.state.panel

    @property
    def table_info(self) -> Mapping[str, dict[str, object]]:
        return types.MappingProxyType(self._kernel.state.table_info_by_domain)

    @property
    def areas(self) -> Mapping[int, Any]:
        return _FilteredMapping(self._kernel.state.areas, self._kernel.state.inventory.configured_areas)

    @property
    def zones(self) -> Mapping[int, Any]:
        return _FilteredMapping(self._kernel.state.zones, self._kernel.state.inventory.configured_zones)

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
        phase: Optional[str] = None,
        detail: Optional[str] = None,
    ) -> E27Error:
        context = self._error_context(phase=phase, detail=detail)

        for err in _iter_causes(exc):
            if isinstance(err, E27ProvisioningRequired):
                return AuthorizationRequired(str(err) or "Authorization required.", context=context, cause=err)
            if isinstance(err, E27LinkInvalid):
                return InvalidLinkKeys(str(err) or "Invalid link keys.", context=context, cause=err)
            if isinstance(err, (E27AuthFailed, E27ProvisioningTimeout)):
                return InvalidCredentials(str(err) or "Invalid credentials.", context=context, cause=err)
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
                return MissingContext(str(err) or "Missing required connection context.", context=context, cause=err)
            if isinstance(err, KernelInvalidPanelError):
                return ProtocolError(str(err) or "Invalid panel entry.", context=context, cause=err)
            if isinstance(err, SessionNotReadyError):
                return E27NotReady(str(err) or "E27 session is not ready.", context=context, cause=err)
            if isinstance(err, (OSError, SessionIOError)):
                return ConnectionLost(str(err) or "Connection lost.", context=context, cause=err)
            if isinstance(err, (SessionProtocolError, ValueError)):
                return ProtocolError(str(err) or "Protocol error.", context=context, cause=err)
            if isinstance(err, TimeoutError):
                return E27Timeout(str(err) or "E27 operation timed out.", context=context, cause=err)
            if isinstance(err, KernelError):
                if err.__cause__ is not None or err.__context__ is not None:
                    continue
                return E27Error(str(err) or "E27 operation failed.", context=context, cause=err)

        return E27Error(str(exc) or "E27 operation failed.", context=context, cause=exc)

    def _error_context(self, *, phase: Optional[str], detail: Optional[str]) -> E27ErrorContext:
        host: Optional[str] = None
        port: Optional[int] = None

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
    def _extract_error_code(msg: Mapping[str, Any], expected_route: RouteKey) -> Optional[int]:
        domain, command = expected_route
        domain_obj = msg.get(domain)
        if not isinstance(domain_obj, Mapping):
            return None
        payload = domain_obj.get(command)
        if isinstance(payload, Mapping):
            error_code = payload.get("error_code")
            if isinstance(error_code, int) and error_code != 0:
                return error_code
        error_code = domain_obj.get("error_code")
        if isinstance(error_code, int) and error_code != 0:
            return error_code
        return None

    @staticmethod
    def _has_expected_payload(msg: Mapping[str, Any], expected_route: RouteKey) -> bool:
        domain, command = expected_route
        domain_obj = msg.get(domain)
        if not isinstance(domain_obj, Mapping):
            return False
        if command == "__root__":
            return True
        if command in domain_obj:
            return True
        return "error_code" in domain_obj

    @staticmethod
    def _extract_response_payload(msg: Mapping[str, Any], expected_route: RouteKey) -> Mapping[str, Any]:
        domain, command = expected_route
        domain_obj = msg.get(domain)
        if isinstance(domain_obj, Mapping):
            if command == "__root__":
                return dict(domain_obj)
            payload = domain_obj.get(command)
            if isinstance(payload, Mapping):
                return dict(payload)
            if payload is not None:
                error_code = domain_obj.get("error_code")
                if isinstance(error_code, int):
                    return {"value": payload, "error_code": error_code}
                return {"value": payload}
            return dict(domain_obj)
        return dict(msg)

    def _enforce_permissions(
        self,
        command_key: str,
        min_permission: PermissionLevel,
    ) -> Optional[E27Error]:
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
            elif pin_param is not None and isinstance(pin_value, str) and pin_value.isdigit():
                if pin_param.annotation is int:
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

    def _resolve_merge_strategy(self, strategy):
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
    def _coerce_block_count(value: Any) -> Optional[int]:
        if isinstance(value, int):
            return value if value >= 1 else None
        if isinstance(value, str) and value.isdigit():
            count = int(value)
            return count if count >= 1 else None
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
                for item in value:
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
                for item in value:
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
                for item in value:
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
