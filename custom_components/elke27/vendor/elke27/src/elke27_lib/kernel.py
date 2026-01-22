"""
kernel.py — Internal kernel used by Elke27Client.

Goals:
- kernel.py stays stable as new message types are added.
- Features register:
    - inbound handlers (dispatcher routes)
    - outbound request builders (request registry)
- No outbound policy enforcement (by design for this phase).
  "No writes" is achieved simply by not registering write request builders.

Kernel responsibilities:
- Own Session, Dispatcher, PanelState, pending registry, event queue.
- Provide register_handler()/register_request()/request() APIs for features.
- Wire Session -> kernel -> Dispatcher.
- Stamp event headers and enqueue via emit(evt, ctx).

Notes
- This module intentionally keeps a small surface area.
- link() performs provisioning only; connect() requires explicit panel + client_identity.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import json
import logging
import socket
import threading
import time
from collections import deque
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from enum import Enum
from typing import (
    Any,
    cast,
)

LOG = logging.getLogger(__name__)
from . import discovery, linking
from . import session as session_mod
from .const import REDACT_DIAGNOSTICS
from .dispatcher import (
    DispatchContext,
    Dispatcher,
    MessageKind,
    PagedBlock,
    PagedTransferKey,
    PendingRequest,
    RouteKey,
)
from .errors import ConnectionLost, E27Error, E27Timeout
from .events import (
    UNSET_AT,
    UNSET_CLASSIFICATION,
    UNSET_ROUTE,
    UNSET_SEQ,
    UNSET_SESSION_ID,
    ApiError,
    AuthorizationRequiredEvent,
    ConnectionStateChanged,
    DispatchRoutingError,
    Event,
    stamp_event,
)
from .outbound import OutboundPriority
from .pending import PendingResponseManager
from .states import PanelState

RequestBuilder = Callable[..., Mapping[str, Any] | bool]  # returns payload dict or flag


class RequestRegistry:
    """
    Maps a route key (domain,name) to a request payload builder callable.

    No policy enforcement here: if it's registered, it is allowed by definition.
    """

    def __init__(self) -> None:
        self._builders: dict[RouteKey, RequestBuilder] = {}

    def register(self, route: RouteKey, builder: RequestBuilder) -> None:
        self._builders[route] = builder

    def get(self, route: RouteKey) -> RequestBuilder | None:
        return self._builders.get(route)

    def require(self, route: RouteKey) -> RequestBuilder:
        b = self._builders.get(route)
        if b is None:
            raise KeyError(f"No request builder registered for route {route!r}")
        return b


class KernelError(RuntimeError):
    """Base exception for E27Kernel facade failures."""


class KernelNotLinkedError(KernelError):
    """Raised when connect() is called before link() established client identity/panel context."""


class KernelInvalidPanelError(KernelError):
    """Raised when a panel entry is missing required connection fields."""


class KernelMissingContextError(KernelError):
    """Raised when connect() is missing required panel/client_identity context."""


@dataclass(frozen=True, slots=True)
class _Subscriber:
    callback: Callable[[Event], None]
    kinds: set[str] | None = None


class _RequestState(str, Enum):
    IDLE = "idle"
    IN_FLIGHT = "in_flight"


@dataclass
class _QueuedRequest:
    seq: int
    domain: str
    name: str
    payload: Any
    pending: bool
    opaque: Any
    expected_route: RouteKey | None
    priority: OutboundPriority
    timeout_s: float


@dataclass(frozen=True, slots=True)
class DiscoverResult:
    """Wrapper for discovery results to keep the public contract explicit."""

    panels: list[discovery.E27System]


_REDACT_KEYS = {"access_code", "accesscode", "passphrase", "pin"}


def _as_mapping(obj: object) -> Mapping[str, Any] | None:
    if isinstance(obj, Mapping):
        return cast(Mapping[str, Any], obj)
    return None


def _redact_value(value: Any) -> Any:
    if not REDACT_DIAGNOSTICS:
        return value
    if isinstance(value, Mapping):
        mapping = cast(Mapping[str, Any], value)
        return {
            key: ("***" if str(key).lower() in _REDACT_KEYS else _redact_value(val))
            for key, val in mapping.items()
        }
    if isinstance(value, list):
        items = cast(list[object], value)
        return [_redact_value(item) for item in items]
    if isinstance(value, tuple):
        items = cast(tuple[object, ...], value)
        return tuple(_redact_value(item) for item in items)
    return value


def _panel_host_port(panel: discovery.E27System | Mapping[str, Any]) -> tuple[str, int]:
    """
    Extract host/port from a discovery panel entry.

    Supports:
      - discovery.E27System (preferred)
      - dict-like panel entries with keys {panel_host/host/ip_address/ip} and {port}
    """
    if isinstance(panel, discovery.E27System):
        host = panel.panel_host
        port = int(panel.port)
        return host, port

    host = (
        panel.get("panel_host")
        or panel.get("host")
        or panel.get("ip_address")
        or panel.get("ip")
        or panel.get("address")
    )
    if not host or not isinstance(host, str):
        raise KernelInvalidPanelError(f"Discovered panel entry missing host/ip: {panel!r}")

    port = panel.get("port", 2101)
    if not isinstance(port, int) or port <= 0 or port > 65535:
        raise KernelInvalidPanelError(
            f"Discovered panel entry has invalid port={port!r}: {panel!r}"
        )

    return host, port


class E27Kernel:
    """
    High-level facade + kernel for E27.

    Lifecycle:
      1) panels = (await E27Kernel.discover()).panels
      2) link_keys = await elk.link(panels[0], client_identity, credentials)
      3) await elk.connect(
           link_keys, panel=panels[0], client_identity=client_identity
         )  # creates Session, HELLO, ACTIVE
      4) elk.request(...) and/or consume elk.drain_events()

    Dispatcher is synchronous and routes inbound messages to registered handlers.
    """

    _log: logging.Logger
    now: Callable[[], float]
    _session: session_mod.Session | None
    state: PanelState
    dispatcher: Dispatcher
    requests: RequestRegistry
    _events: deque[Event]
    _seq: int
    _event_lock: threading.Lock
    _subscribers: dict[int, _Subscriber]
    _next_subscriber_id: int
    _request_timeout_s: float
    _request_max_retries: int
    _request_max_backoff_s: float
    _max_pending_requests: int
    _filter_attribs_to_configured: bool
    _outbound_min_interval_s: float
    _outbound_max_burst: int
    _last_link_keys: linking.E27LinkKeys | None
    _last_client_identity: linking.E27Identity | None
    _last_session_config: session_mod.SessionConfig | None
    _feature_modules: Sequence[str]
    _features_loaded: bool
    _features_lock: threading.Lock
    _next_transfer_id: int
    _disable_retries_for_routes: set[RouteKey]
    _pending_responses: PendingResponseManager
    _closing: bool
    _closed_explicitly: bool
    _sent_events: dict[int, asyncio.Event]
    _sent_event_lock: threading.Lock
    _loop: asyncio.AbstractEventLoop | None
    _request_state: _RequestState
    _active_seq: int | None
    _active_timeout_handle: asyncio.TimerHandle | None
    _active_released: bool
    _active_request: _QueuedRequest | None
    _request_queue_high: deque[_QueuedRequest]
    _request_queue_normal: deque[_QueuedRequest]
    _keepalive_task: asyncio.Task[None] | None
    _keepalive_enabled: bool
    _keepalive_interval_s: float
    _keepalive_timeout_s: float
    _keepalive_max_missed: int
    _keepalive_missed: int
    _last_exchange_at: float
    _last_rx_at: float
    _keepalive_inflight: bool

    DEFAULT_FEATURES: Sequence[str] = (
        "elke27_lib.features.control",
        "elke27_lib.features.log",
        "elke27_lib.features.system",
        "elke27_lib.features.area",
        "elke27_lib.features.bus_ios",
        "elke27_lib.features.zone",
        "elke27_lib.features.output",
        "elke27_lib.features.tstat",
        "elke27_lib.features.network_param",
        "elke27_lib.features.rule",
        "elke27_lib.features.user",
        "elke27_lib.features.keypad",
    )

    def __init__(
        self,
        *,
        now_monotonic: Callable[[], float] = time.monotonic,
        event_queue_maxlen: int = 0,  # 0 means unbounded deque
        features: Sequence[str] | None = None,
        logger: logging.Logger | None = None,
        request_timeout_s: float = 5.0,
        request_max_retries: int = 2,
        request_max_backoff_s: float = 30.0,
        max_pending_requests: int = 64,
        outbound_min_interval_s: float = 0.05,
        outbound_max_burst: int = 1,
        filter_attribs_to_configured: bool = True,
    ) -> None:
        self._log = logger or logging.getLogger(__name__)
        self.now = now_monotonic

        # Kernel-owned components
        self._session = None
        self.state = PanelState()
        self.dispatcher = Dispatcher()
        self.requests = RequestRegistry()
        self._events = deque(maxlen=(event_queue_maxlen or None))
        self._seq = 1
        self._event_lock = threading.Lock()
        self._subscribers = {}
        self._next_subscriber_id = 1
        self._request_timeout_s = request_timeout_s
        self._request_max_retries = request_max_retries
        self._request_max_backoff_s = max(0.0, float(request_max_backoff_s))
        self._max_pending_requests = max(1, int(max_pending_requests))
        self._filter_attribs_to_configured = filter_attribs_to_configured
        self._outbound_min_interval_s = outbound_min_interval_s
        self._outbound_max_burst = outbound_max_burst
        self._last_link_keys = None
        self._last_client_identity = None
        self._last_session_config = None
        self._feature_modules = features if features is not None else self.DEFAULT_FEATURES
        self._features_loaded = False
        self._features_lock = threading.Lock()
        self._next_transfer_id = 1
        self._disable_retries_for_routes = {
            ("area", "get_attribs"),
            ("zone", "get_attribs"),
            ("output", "get_attribs"),
            ("user", "get_attribs"),
            ("keypad", "get_attribs"),
        }
        self._pending_responses = PendingResponseManager(now=self.now)
        self._closing = False
        self._closed_explicitly = False
        self._sent_events = {}
        self._sent_event_lock = threading.Lock()
        self._loop = None
        self._request_state = _RequestState.IDLE
        self._active_seq = None
        self._active_timeout_handle = None
        self._active_released = False
        self._active_request = None
        self._request_queue_high = deque()
        self._request_queue_normal = deque()
        self._keepalive_task = None
        self._keepalive_enabled = False
        self._keepalive_interval_s = 30.0
        self._keepalive_timeout_s = 10.0
        self._keepalive_max_missed = 2
        self._keepalive_missed = 0
        now = self.now()
        self._last_exchange_at = now
        self._last_rx_at = now
        self._keepalive_inflight = False

        # Always register dispatcher error envelope handler
        self.register_handler(("__error__", "__all__"), self._handle_dispatch_error_envelope)
        self.register_handler(("__error__", "panel_error"), self._handle_panel_error_envelope)

    @property
    def session(self) -> session_mod.Session:
        if self._session is None:
            raise KernelError("No active Session. Call connect() successfully first.")
        return self._session

    @property
    def ready(self) -> bool:
        return (
            self._session is not None
            and self._session.state is session_mod.SessionState.ACTIVE
            and self.state.panel.session_id is not None
            and bool(self.state.table_info_by_domain)
        )

    # -------------------------
    # Discovery / Provisioning / Connect facade
    # -------------------------

    @classmethod
    async def discover(cls, *, timeout: int = 10, address: str | None = None) -> DiscoverResult:
        """
        E27Kernel.discover uses the discovery.py module to find a list of discovered panels and
        returns the list with the data returned by discovery.AIOELKDiscovery.async_scan().
        """
        try:
            scanner = discovery.AIOELKDiscovery()
            panels_any = cast(
                object | None, await scanner.async_scan(timeout=timeout, address=address)
            )
        except Exception as e:
            LOG.warning("Discovery failed: %s", e, exc_info=True)
            raise KernelError(f"Discovery failed: {e}") from e

        if panels_any is None:
            panels_any = []
        if not isinstance(panels_any, list):
            raise KernelError(
                f"Discovery returned unexpected type {type(panels_any).__name__}; expected list."
            )

        out: list[discovery.E27System] = []
        panels_list = cast(list[object], panels_any)
        for i, p in enumerate(panels_list):
            if isinstance(p, discovery.E27System):
                out.append(p)
            else:
                raise KernelError(f"Discovery returned unexpected entry at index {i}: {p!r}")

        return DiscoverResult(panels=out)

    async def link(
        self,
        panel: discovery.E27System | dict[str, Any],
        client_identity: linking.E27Identity,
        credentials: Any,
        *,
        timeout_s: float = 10.0,
    ) -> linking.E27LinkKeys:
        """
        E27Kernel.link accepts one element of the list returned by E27Kernel.discover plus
        linking.E27Identity (client_identity) and linking.E27Credentials (accesscode + passphrase)
        and returns E27LinkKeys.

        This is provisioning-time API_LINK; it is explicitly outside Session responsibility.
        """
        host, port = _panel_host_port(panel)

        if credentials is None:
            raise KernelError("link(): credentials are required.")

        # We expect credentials.access_code and credentials.passphrase (per requirement).
        access_code = getattr(credentials, "access_code", None) or getattr(
            credentials, "accesscode", None
        )
        passphrase = getattr(credentials, "passphrase", None)

        if not isinstance(access_code, str) or not access_code:
            raise KernelError("link(): credentials.access_code (string) is required.")
        if not isinstance(passphrase, str) or not passphrase:
            raise KernelError("link(): credentials.passphrase (string) is required.")

        def _do_link_sync() -> linking.E27LinkKeys:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                sock.settimeout(float(timeout_s))
                sock.connect((host, port))

                # Wait for discovery hello/nonce (cleartext)
                nonce = linking.wait_for_discovery_nonce(sock, timeout_s=float(timeout_s))
                nonce_bytes = nonce.encode("utf-8")

                # Perform API_LINK. Existing library signature is sock-based; newer wrappers may differ.
                return linking.perform_api_link(
                    sock=sock,
                    client_identity=client_identity,
                    access_code=access_code,
                    passphrase=passphrase,
                    mn_for_hash=client_identity.mn,
                    discovery_nonce=nonce_bytes,
                    seq=110,
                    timeout_s=float(timeout_s),
                )
            finally:
                with contextlib.suppress(OSError):
                    sock.close()

        try:
            link_keys = await asyncio.to_thread(_do_link_sync)
        except (E27Error, OSError, RuntimeError, ValueError) as e:
            raise KernelError(f"Linking failed for {host}:{port}: {e}") from e

        return link_keys

    async def connect(
        self,
        link_keys: linking.E27LinkKeys,
        *,
        panel: discovery.E27System | dict[str, Any] | None = None,
        client_identity: linking.E27Identity | None = None,
        session_config: session_mod.SessionConfig | None = None,
    ) -> session_mod.SessionState:
        """
        E27Kernel.connect accepts E27LinkKeys and client_identity, creates/stores a session.Session, performs HELLO,
        and returns the session state (confirming ACTIVE).
        """
        await asyncio.to_thread(self.load_features_blocking, None)
        self._loop = asyncio.get_running_loop()
        if session_config is None and panel is None:
            raise KernelMissingContextError(
                "connect() requires panel context or session_config (HA must pass host/port)."
            )
        if client_identity is None:
            raise KernelMissingContextError(
                "connect() requires client_identity (Elke27Identity with mn/sn) for HELLO."
            )

        if session_config is None:
            if panel is None:
                raise KernelMissingContextError(
                    "connect() requires panel context or session_config (HA must pass host/port)."
                )
            host, port = _panel_host_port(panel)
        else:
            host, port = session_config.host, session_config.port

        missing_fields: list[str] = []
        for field in ("linkkey_hex", "linkhmac_hex"):
            if not getattr(link_keys, field, None):
                missing_fields.append(field)
        if missing_fields:
            raise KernelError(
                f"connect(): link_keys missing required fields: {', '.join(missing_fields)}"
            )

        link_key_hex = link_keys.linkkey_hex

        if not client_identity.mn or not client_identity.sn:
            raise KernelMissingContextError("connect() requires client_identity with mn and sn.")

        cfg = session_config or session_mod.SessionConfig(host=host, port=port)
        self._keepalive_enabled = bool(cfg.keepalive_enabled)
        self._keepalive_interval_s = float(cfg.keepalive_interval_s)
        self._keepalive_timeout_s = float(cfg.keepalive_timeout_s)
        self._keepalive_max_missed = int(cfg.keepalive_max_missed)
        cfg = replace(cfg, keepalive_enabled=False)

        self._closed_explicitly = False
        s = session_mod.Session(cfg=cfg, client_identity=client_identity, link_key_hex=link_key_hex)

        # Wire callbacks before connecting so HELLO path can report, if needed.
        s.on_message = self._on_message
        s.on_disconnected = self._on_session_disconnected
        s.on_idle = self._on_idle

        def _do_connect_sync() -> session_mod.SessionInfo:
            return s.connect()

        try:
            await asyncio.to_thread(_do_connect_sync)
        except Exception as e:
            raise KernelError(f"Session connect failed for {host}:{port}: {e}") from e

        self._session = s

        if s.state != session_mod.SessionState.ACTIVE:
            raise KernelError(
                f"Session connect completed but session.state is {s.state!r}, not ACTIVE."
            )

        s.enable_outbound_queue(
            loop=self._loop,
            min_interval_s=self._outbound_min_interval_s,
            max_burst=self._outbound_max_burst,
        )
        s.start_auto_receive()

        self.state.panel.session_id = (
            s.info.session_id if s.info is not None else self.state.panel.session_id
        )
        self.state.panel.connected = True
        self.state.table_info_by_domain.clear()
        self.state.table_info_known.clear()
        self.state.bootstrap_counts_ready = False
        self._reset_inventory_state()
        for domain in ("area", "zone", "output", "tstat"):
            self.state.table_info_by_domain.setdefault(
                domain,
                {"table_elements": None, "increment_size": None},
            )
        self._emit_connection_state(connected=True)
        self._bootstrap_requests()
        self._last_link_keys = link_keys
        self._last_client_identity = client_identity
        self._last_session_config = cfg
        self._keepalive_missed = 0
        now = self.now()
        self._last_exchange_at = now
        self._last_rx_at = now
        if self._keepalive_enabled:
            self._start_keepalive()

        return s.state

    async def reconnect(self) -> session_mod.SessionState:
        """
        Reconnect using the most recent successful connect() parameters.
        """
        if (
            self._last_link_keys is None
            or self._last_client_identity is None
            or self._last_session_config is None
        ):
            raise KernelError("No prior connect() context available for reconnect().")
        await self.close()
        return await self.connect(
            self._last_link_keys,
            client_identity=self._last_client_identity,
            session_config=self._last_session_config,
        )

    async def close(self) -> None:
        """Close any active session. Idempotent."""
        if self._session is None:
            return
        self._stop_keepalive()

        s = self._session
        self._session = None
        self._closing = True
        self._closed_explicitly = True
        self.state.panel.connected = False
        if self._log.isEnabledFor(logging.DEBUG):
            self._log.debug(
                "E27Kernel.close(): closing session (explicit) session_id=%s",
                s.info.session_id if s.info is not None else None,
            )

        def _do_close_sync() -> None:
            s.close()

        try:
            await asyncio.to_thread(_do_close_sync)
        except (OSError, RuntimeError, session_mod.SessionError) as e:
            self._log.warning("E27Kernel.close(): session close failed: %s", e, exc_info=True)
        finally:
            self._closing = False
            self._emit_connection_state(connected=False, reason="closed")

    def _start_keepalive(self) -> None:
        if self._loop is None:
            return
        if self._keepalive_task is not None and not self._keepalive_task.done():
            return
        self._keepalive_task = self._loop.create_task(self._keepalive_loop())

    def _stop_keepalive(self) -> None:
        if self._keepalive_task is None:
            return
        if not self._keepalive_task.done():
            self._keepalive_task.cancel()
        self._keepalive_task = None
        self._keepalive_missed = 0
        self._keepalive_inflight = False

    async def _keepalive_loop(self) -> None:
        while True:
            if self._closing:
                return
            if not self._keepalive_enabled:
                return
            idle_for = self.now() - self._last_exchange_at
            wait_for = self._keepalive_interval_s - idle_for
            if wait_for > 0:
                try:
                    await asyncio.sleep(wait_for)
                except asyncio.CancelledError:
                    return
                continue
            session = self._session
            if session is None or session.state is not session_mod.SessionState.ACTIVE:
                try:
                    await asyncio.sleep(0.5)
                except asyncio.CancelledError:
                    return
                continue
            if (
                self._request_state is not _RequestState.IDLE
                or self._request_queue_high
                or self._request_queue_normal
            ):
                try:
                    await asyncio.sleep(0.5)
                except asyncio.CancelledError:
                    return
                continue
            outbound = getattr(self._session, "_outbound", None)
            if outbound is not None and not outbound.is_idle():
                try:
                    await asyncio.sleep(0.5)
                except asyncio.CancelledError:
                    return
                continue
            if self._keepalive_inflight:
                try:
                    await asyncio.sleep(0.5)
                except asyncio.CancelledError:
                    return
                continue
            ok = await self._send_keepalive_request()
            if ok:
                self._keepalive_missed = 0
                continue
            self._keepalive_missed += 1
            if self._keepalive_missed >= self._keepalive_max_missed:
                session.handle_disconnect(session_mod.SessionProtocolError("Keepalive timed out"))
                return

    async def _send_keepalive_request(self) -> bool:
        self._set_loop_if_needed()
        if self._loop is None:
            return False
        if self._session is None or self._session.state is not session_mod.SessionState.ACTIVE:
            return False
        if self.now() - self._last_exchange_at < self._keepalive_interval_s:
            return True
        if (
            self._keepalive_inflight
            or self._request_state is not _RequestState.IDLE
            or self._request_queue_high
            or self._request_queue_normal
        ):
            return True
        self._keepalive_inflight = True
        try:
            builder = self.requests.require(("system", "r_u_alive"))
            payload = builder()
            seq = self._next_seq()
            future = self._pending_responses.create(
                seq,
                command_key="system_r_u_alive",
                expected_route=("system", "r_u_alive"),
                loop=self._loop,
            )
            sent_event = asyncio.Event()
            self._register_sent_event(seq, sent_event)
            try:
                self._send_request_with_seq(
                    seq,
                    "system",
                    "r_u_alive",
                    payload,
                    pending=False,
                    opaque=None,
                    expected_route=("system", "r_u_alive"),
                    priority=OutboundPriority.HIGH,
                    timeout_s=self._keepalive_timeout_s,
                )
            except Exception:
                self._pending_responses.drop(seq)
                return False
            await sent_event.wait()
            sent_at = self.now()
            try:
                await future
                return True
            except E27Timeout:
                if self._log.isEnabledFor(logging.WARNING):
                    self._log.warning(
                        "E27 keepalive response missing for seq=%s session_id=%s",
                        seq,
                        self.state.panel.session_id,
                    )
                return self._last_rx_at > sent_at
            except Exception as exc:
                self._log.warning(
                    "E27 keepalive check failed: seq=%s session_id=%s error=%s",
                    seq,
                    self.state.panel.session_id,
                    exc,
                    exc_info=True,
                )
                return self._last_rx_at > sent_at
        finally:
            self._keepalive_inflight = False

    # -------------------------
    # Feature loading
    # -------------------------

    def load_features(self, modules: Sequence[str]) -> None:
        """Backward-compatible wrapper for blocking feature loading."""
        self.load_features_blocking(modules)

    def load_features_blocking(self, modules: Sequence[str] | None = None) -> None:
        """Import each module and invoke its register(elk) function (blocking)."""
        with self._features_lock:
            if self._features_loaded:
                return
            for modname in modules or self._feature_modules:
                mod = importlib.import_module(modname)
                reg = getattr(mod, "register", None)
                if reg is None or not callable(reg):
                    raise RuntimeError(
                        f"Feature module {modname!r} has no callable register(elk) function"
                    )
                reg(self)
            self._features_loaded = True

    # -------------------------
    # Registration surface for features
    # -------------------------

    def register_handler(
        self, route: RouteKey, handler: Callable[[Mapping[str, Any], DispatchContext], bool]
    ) -> None:
        self.dispatcher.register(route, handler)

    def register_request(self, route: RouteKey, builder: RequestBuilder) -> None:
        self.requests.register(route, builder)

    def register_paged(
        self,
        route: RouteKey,
        *,
        merge_fn: Callable[[list[PagedBlock], int], Mapping[str, Any]],
        request_block: Callable[[int, PagedTransferKey], None] | None = None,
        timeout_s: float | None = None,
    ) -> None:
        self.dispatcher.register_paged(
            route,
            merge_fn=merge_fn,
            request_block=request_block,
            timeout_s=timeout_s,
        )

    def _reset_inventory_state(self) -> None:
        inv = self.state.inventory
        inv.configured_areas = set()
        inv.configured_zones = set()
        inv.configured_outputs = set()
        inv.configured_users = set()
        inv.configured_keypads = set()
        inv.configured_area_blocks_seen = set()
        inv.configured_zone_blocks_seen = set()
        inv.configured_area_blocks_requested = set()
        inv.configured_zone_blocks_requested = set()
        inv.configured_area_block_count = None
        inv.configured_zone_block_count = None
        inv.configured_area_blocks_remaining = None
        inv.configured_zone_blocks_remaining = None
        inv.configured_areas_complete = False
        inv.configured_zones_complete = False
        inv.configured_outputs_complete = False
        inv.configured_users_complete = False
        inv.configured_keypads_complete = False
        inv.area_names_logged = False
        inv.zone_names_logged = False
        inv.area_attribs_requested = set()
        inv.zone_attribs_requested = set()
        inv.output_attribs_requested = set()
        inv.user_attribs_requested = set()
        inv.keypad_attribs_requested = set()
        inv.area_invalid_streak = 0
        inv.zone_invalid_streak = 0
        inv.area_last_invalid_id = None
        inv.zone_last_invalid_id = None
        inv.area_discovery_max_id = None
        inv.zone_discovery_max_id = None

    def _bootstrap_requests(self) -> None:
        if self._session is None:
            return

        for route in (
            ("area", "get_table_info"),
            ("zone", "get_table_info"),
            ("output", "get_table_info"),
            ("tstat", "get_table_info"),
        ):
            if self.requests.get(route) is None:
                continue
            try:
                self.request(route)
            except (E27Error, KeyError, RuntimeError, TypeError, ValueError):
                continue

        for route in (
            ("area", "get_configured"),
            ("zone", "get_configured"),
            ("output", "get_configured"),
            ("user", "get_configured"),
        ):
            if self.requests.get(route) is None:
                continue
            try:
                self.request(route, block_id=1)
            except (E27Error, KeyError, RuntimeError, TypeError, ValueError):
                continue

        zone_defs_route = ("zone", "get_defs")
        if self.requests.get(zone_defs_route) is not None:
            with contextlib.suppress(E27Error, KeyError, RuntimeError, TypeError, ValueError):
                self.request(zone_defs_route, block_id=1)

    def request_csm_refresh(
        self,
        *,
        auth_pin: int | None = None,
        domains: Sequence[str] | None = None,
    ) -> None:
        """
        Request minimal CSM refresh (authenticate + table_info only).
        """
        if self._session is None:
            raise KernelError("No active Session. Call connect() successfully first.")

        if auth_pin is not None and self.requests.get(("control", "authenticate")) is not None:
            with contextlib.suppress(E27Error, KeyError, RuntimeError, TypeError, ValueError):
                self.request(("control", "authenticate"), pin=auth_pin)

        for domain in domains or ("area", "zone", "output", "tstat"):
            route = (domain, "get_table_info")
            if self.requests.get(route) is None:
                continue
            try:
                self.request(route)
            except (E27Error, KeyError, RuntimeError, TypeError, ValueError):
                continue

    # -------------------------
    # Event subscriptions
    # -------------------------

    def subscribe(
        self, callback: Callable[[Event], None], *, kinds: Iterable[str] | None = None
    ) -> int:
        kind_set = set(kinds) if kinds is not None else None
        with self._event_lock:
            token = self._next_subscriber_id
            self._next_subscriber_id += 1
            self._subscribers[token] = _Subscriber(callback=callback, kinds=kind_set)
        return token

    def unsubscribe(self, token: int) -> bool:
        with self._event_lock:
            return self._subscribers.pop(token, None) is not None

    # -------------------------
    # Session -> E27Kernel -> Dispatcher wiring
    # -------------------------

    def _on_message(self, msg: Mapping[str, Any]) -> None:
        """
        Hot path: keep this fast.

        - Update PanelState (session_id, last_message_at)
        - Dispatcher.dispatch(msg)
        - (Optional) handlers may emit events via elk.emit(...)
        """
        seq_val = msg.get("seq")
        if not isinstance(seq_val, int):
            auth_obj = _as_mapping(msg.get("authenticate"))
            if auth_obj is not None:
                auth_seq = auth_obj.get("seq")
                if isinstance(auth_seq, int) and auth_seq > 0:
                    seq_val = auth_seq
                    if "seq" not in msg:
                        msg = dict(msg)
                        msg["seq"] = auth_seq
                    if "session_id" not in msg:
                        auth_sid = auth_obj.get("session_id")
                        if isinstance(auth_sid, int):
                            if not isinstance(msg, dict):
                                msg = dict(msg)
                            msg["session_id"] = auth_sid
        if isinstance(seq_val, int) and seq_val > 0:
            self._pending_responses.resolve(seq_val, msg)

        sid = msg.get("session_id")
        if isinstance(sid, int):
            self.state.panel.session_id = sid

        self.state.panel.last_message_at = self.now()
        self._last_rx_at = self.state.panel.last_message_at
        self._last_exchange_at = self.state.panel.last_message_at

        self._log.debug("Inbound message: %s", msg)

        # Dispatcher handles routing + correlation + dispatch-error envelopes.
        result = self.dispatcher.dispatch(msg)
        if self._log.isEnabledFor(logging.DEBUG):
            self._log.debug(
                "Inbound routed: route=%s.%s seq=%s dispatched=%s",
                result.route[0],
                result.route[1],
                result.seq,
                result.handled,
            )
        seq_val = msg.get("seq")
        if isinstance(seq_val, int) and seq_val > 0:
            if self._request_state is _RequestState.IN_FLIGHT and seq_val == self._active_seq:
                self._cancel_active_timeout()
                self._complete_active(reason="reply")
            elif self._log.isEnabledFor(logging.DEBUG):
                self._log.debug(
                    "Late/unexpected reply: seq=%s active_seq=%s",
                    seq_val,
                    self._active_seq,
                )

    def _on_idle(self) -> None:
        self._try_send_next()

    def _on_session_disconnected(self, err: Exception | None) -> None:
        if self._closing:
            return
        if self._closed_explicitly and isinstance(err, session_mod.SessionIOError):
            return
        self._log.warning(
            "E27Kernel._on_session_disconnected: session reported disconnect err=%s",
            err,
        )
        self.state.panel.connected = False
        self.dispatcher.abort_paged_transfers()
        self._abort_requests(ConnectionLost("Session disconnected."))
        self._emit_connection_state(
            connected=False,
            reason=None,
            error_type=type(err).__name__ if err is not None else None,
        )

    def _is_valid_attrib_id(self, domain: str, entity_id: int) -> bool:
        if entity_id < 1:
            return False
        inv = self.state.inventory
        if (
            domain == "area"
            and inv.area_discovery_max_id is not None
            and entity_id > inv.area_discovery_max_id
        ):
            return False
        if (
            domain == "zone"
            and inv.zone_discovery_max_id is not None
            and entity_id > inv.zone_discovery_max_id
        ):
            return False
        if domain == "area" and inv.configured_areas and entity_id not in inv.configured_areas:
            return False
        if domain == "zone" and inv.configured_zones and entity_id not in inv.configured_zones:
            return False
        if (
            domain == "output"
            and inv.configured_outputs
            and entity_id not in inv.configured_outputs
        ):
            return False
        if domain == "user" and inv.configured_users and entity_id not in inv.configured_users:
            return False
        if (
            domain == "keypad"
            and inv.configured_keypads
            and entity_id not in inv.configured_keypads
        ):
            return False
        table_info = self.state.table_info_by_domain.get(domain)
        if isinstance(table_info, Mapping):
            max_id = table_info.get("table_elements")
            if isinstance(max_id, int) and max_id >= 1 and entity_id > max_id:
                return False
        return True

    def _register_sent_event(self, seq: int, event: asyncio.Event) -> asyncio.Event:
        with self._sent_event_lock:
            self._sent_events[seq] = event
        return event

    def _signal_sent_event(self, seq: int) -> None:
        with self._sent_event_lock:
            event = self._sent_events.pop(seq, None)
        if event is None:
            return
        try:
            loop = getattr(event, "_loop", None)
            if loop is not None and loop.is_running():
                loop.call_soon_threadsafe(event.set)
            else:
                event.set()
        except Exception as exc:
            self._log.warning("Event set failed: %s", exc, exc_info=True)
            event.set()

    def _set_loop_if_needed(self) -> None:
        if self._loop is not None:
            return
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            return

    def _enqueue_request(self, item: _QueuedRequest) -> None:
        if item.priority is OutboundPriority.HIGH:
            self._request_queue_high.append(item)
        else:
            self._request_queue_normal.append(item)
        self._kick_scheduler()

    def _kick_scheduler(self) -> None:
        self._set_loop_if_needed()
        if self._loop is None:
            self._try_send_next()
            return
        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None
        if running_loop is self._loop:
            self._try_send_next()
        elif self._loop.is_running():
            self._loop.call_soon_threadsafe(self._try_send_next)
        else:
            self._try_send_next()

    def _try_send_next(self) -> None:
        if self._request_state is not _RequestState.IDLE:
            return
        if not self._request_queue_high and not self._request_queue_normal:
            return
        if self._session is None:
            return
        session_state = getattr(self._session, "state", session_mod.SessionState.ACTIVE)
        if session_state is not session_mod.SessionState.ACTIVE:
            return

        item = (
            self._request_queue_high.popleft()
            if self._request_queue_high
            else self._request_queue_normal.popleft()
        )
        self._request_state = _RequestState.IN_FLIGHT
        self._active_seq = item.seq
        self._active_released = False
        self._active_request = item

        msg = self._build_request_message(item.seq, item.domain, item.name, item.payload)
        self._log_outbound(item.domain, item.name, msg)

        if item.pending:
            opaque = item.opaque
            if (
                opaque is None
                and item.expected_route is not None
                and self.dispatcher.is_paged(item.expected_route)
            ):
                payload_map = _as_mapping(item.payload)
                block_id = payload_map.get("block_id") if payload_map is not None else None
                if block_id in (None, 1):
                    opaque = PagedTransferKey(
                        session_id=self.state.panel.session_id,
                        transfer_id=self._next_transfer_id,
                        route=item.expected_route,
                    )
                    if self._log.isEnabledFor(logging.DEBUG):
                        self._log.debug(
                            "Paged transfer start: route=%s.%s seq=%s transfer_id=%s session_id=%s",
                            item.expected_route[0],
                            item.expected_route[1],
                            item.seq,
                            opaque.transfer_id,
                            opaque.session_id,
                        )
                    self._next_transfer_id += 1
                    item.opaque = opaque
            self.dispatcher.add_pending(
                PendingRequest(
                    seq=item.seq,
                    expected_route=item.expected_route,
                    created_at=self.now(),
                    opaque=item.opaque,
                )
            )

        try:
            self.session.send_json(
                msg,
                priority=item.priority,
                on_sent=lambda _: self._on_request_sent(item.seq, item.timeout_s),
                on_fail=lambda exc: self._handle_send_failure(item.seq, exc),
            )
        except Exception as exc:
            self._handle_send_failure(item.seq, exc)

    def _on_request_sent(self, seq: int, timeout_s: float) -> None:
        self._mark_request_sent(seq)
        self._arm_reply_timeout(seq, timeout_s)

    def _arm_reply_timeout(self, seq: int, timeout_s: float) -> None:
        if timeout_s <= 0:
            return
        self._set_loop_if_needed()
        if self._loop is None:
            return
        if self._active_timeout_handle is not None:
            self._active_timeout_handle.cancel()
        self._active_timeout_handle = self._loop.call_later(timeout_s, self._on_reply_timeout, seq)

    def _cancel_active_timeout(self) -> None:
        if self._active_timeout_handle is None:
            return
        self._active_timeout_handle.cancel()
        self._active_timeout_handle = None

    def _on_reply_timeout(self, seq: int) -> None:
        if self._request_state is not _RequestState.IN_FLIGHT:
            return
        if self._active_released or self._active_seq != seq:
            return
        route = None
        if self._active_request is not None:
            route = self._active_request.expected_route
        if route is not None:
            self.dispatcher.drop_pending(seq)
        self._pending_responses.fail(seq, E27Timeout(f"Response timed out for seq={seq}"))
        if self._log.isEnabledFor(logging.WARNING):
            if route is not None:
                self._log.warning(
                    "E27 reply timeout: route=%s.%s seq=%s",
                    route[0],
                    route[1],
                    seq,
                )
            else:
                self._log.warning("E27 reply timeout: seq=%s", seq)
        self._complete_active(reason="timeout")

    def _handle_send_failure(self, seq: int, exc: BaseException) -> None:
        if self._active_seq != seq:
            self._mark_send_failed(seq, exc)
            return
        self.dispatcher.drop_pending(seq)
        self._pending_responses.fail(seq, exc)
        self._signal_sent_event(seq)
        if self._log.isEnabledFor(logging.WARNING):
            self._log.warning("E27 send failed: seq=%s error=%s", seq, exc)
        self._complete_active(reason="send_failed")

    def _complete_active(self, *, reason: str) -> None:
        _ = reason
        if self._active_released:
            return
        self._active_released = True
        self._cancel_active_timeout()
        self._active_seq = None
        self._active_request = None
        self._request_state = _RequestState.IDLE
        self._kick_scheduler()

    def _abort_requests(self, exc: BaseException) -> None:
        active_seq = self._active_seq
        if (
            self._request_state is _RequestState.IN_FLIGHT
            and active_seq is not None
            and not self._active_released
        ):
            self.dispatcher.drop_pending(active_seq)
            self._pending_responses.fail(active_seq, exc)
            self._signal_sent_event(active_seq)
            if self._log.isEnabledFor(logging.WARNING):
                self._log.warning("E27 in-flight request aborted: seq=%s error=%s", active_seq, exc)
            self._complete_active(reason="abort")

        for queue in (self._request_queue_high, self._request_queue_normal):
            while queue:
                item = queue.popleft()
                self.dispatcher.drop_pending(item.seq)
                self._pending_responses.fail(item.seq, exc)
                self._signal_sent_event(item.seq)

    def _mark_request_sent(self, seq: int) -> None:
        now = self.now()
        self._last_exchange_at = now
        self._signal_sent_event(seq)

    def _mark_send_failed(self, seq: int, exc: BaseException) -> None:
        self.dispatcher.drop_pending(seq)
        self._pending_responses.fail(seq, exc)
        self._signal_sent_event(seq)

    # -------------------------
    # Outbound requests
    # -------------------------

    def request(
        self, route: RouteKey, /, *, pending: bool = True, opaque: Any = None, **kwargs: Any
    ) -> int:
        """
        Public outbound API: build payload via registry and send.

        pending=True:
          - Registers a PendingRequest with Dispatcher for seq-first correlation.
        """
        builder = self.requests.require(route)
        payload = builder(**kwargs)
        domain, name = route
        return self._send_request(
            domain, name, payload, pending=pending, opaque=opaque, expected_route=route
        )

    def _next_seq(self) -> int:
        max_seq = 2_147_483_647
        s = self._seq
        if s <= 0 or s > max_seq:
            s = 10
        next_val = s + 1
        if next_val > max_seq:
            next_val = 10
        self._seq = next_val
        return s

    def next_seq(self) -> int:
        return self._next_seq()

    @property
    def pending_responses(self) -> PendingResponseManager:
        return self._pending_responses

    def register_sent_event(self, seq: int, sent_event: asyncio.Event) -> None:
        self._register_sent_event(seq, sent_event)

    def send_request_with_seq(
        self,
        seq: int,
        domain: str,
        name: str,
        payload: Any,
        *,
        pending: bool,
        opaque: Any,
        expected_route: RouteKey | None,
        priority: OutboundPriority = OutboundPriority.NORMAL,
        timeout_s: float | None = None,
        expects_reply: bool = True,
    ) -> int:
        return self._send_request_with_seq(
            seq,
            domain,
            name,
            payload,
            pending=pending,
            opaque=opaque,
            expected_route=expected_route,
            priority=priority,
            timeout_s=timeout_s,
            expects_reply=expects_reply,
        )

    def _send_request(
        self,
        domain: str,
        name: str,
        payload: Any,
        *,
        pending: bool,
        opaque: Any,
        expected_route: RouteKey | None,
    ) -> int:
        """
        Mechanical request sender (no policy enforcement in this phase):
        - assigns seq
        - adds session_id if known
        - sends via Session.send_json()
        - optionally registers pending correlation by seq
        """
        seq = self._next_seq()
        return self._send_request_with_seq(
            seq,
            domain,
            name,
            payload,
            pending=pending,
            opaque=opaque,
            expected_route=expected_route,
        )

    def _send_request_with_seq(
        self,
        seq: int,
        domain: str,
        name: str,
        payload: Any,
        *,
        pending: bool,
        opaque: Any,
        expected_route: RouteKey | None,
        priority: OutboundPriority = OutboundPriority.NORMAL,
        timeout_s: float | None = None,
        expects_reply: bool = True,
    ) -> int:
        if self._session is None:
            raise KernelError("No active Session. Call connect() successfully first.")
        session_state = getattr(self._session, "state", session_mod.SessionState.ACTIVE)
        if session_state is not session_mod.SessionState.ACTIVE:
            raise KernelError(f"Session not active (state={session_state!r}).")
        self._set_loop_if_needed()
        if not expects_reply:
            msg = self._build_request_message(seq, domain, name, payload)
            self._log_outbound(domain, name, msg)
            try:
                self.session.send_json(
                    msg,
                    priority=priority,
                    on_sent=lambda _: self._mark_request_sent(seq),
                    on_fail=lambda exc: self._mark_send_failed(seq, exc),
                )
            except Exception as exc:
                self._mark_send_failed(seq, exc)
                raise KernelError(
                    f"Failed to send request {domain}.{name} seq={seq}: {exc}"
                ) from exc
            return seq

        timeout_value = (
            float(timeout_s) if timeout_s is not None else float(self._request_timeout_s)
        )
        queued = _QueuedRequest(
            seq=seq,
            domain=domain,
            name=name,
            payload=payload,
            pending=pending,
            opaque=opaque,
            expected_route=expected_route,
            priority=priority,
            timeout_s=timeout_value,
        )
        self._enqueue_request(queued)
        return seq

    def _build_request_message(
        self, seq: int, domain: str, name: str, payload: Any
    ) -> dict[str, Any]:
        msg: dict[str, Any] = {"seq": seq}
        if self.state.panel.session_id is not None:
            msg["session_id"] = self.state.panel.session_id

        payload_map = _as_mapping(payload)
        if name == "__root__":
            msg[domain] = dict(payload_map) if payload_map is not None else payload
        elif payload_map is not None:
            msg[domain] = {name: dict(payload_map)}
        else:
            msg[domain] = {name: payload}
        return msg

    def _log_outbound(self, domain: str, name: str, msg: Mapping[str, Any]) -> None:
        if domain == "control" and name == "r_u_alive":
            return
        if not self._log.isEnabledFor(logging.DEBUG):
            return
        redacted = _redact_value(msg)
        payload = json.dumps(redacted, sort_keys=True, separators=(",", ":"))
        if name in {"get_attribs", "get_configured"}:
            self._log.debug("Outbound request meta: route=%s.%s payload=%s", domain, name, payload)
        self._log.debug(
            "Outbound request seq=%s route=%s.%s session_id=%s msg=%s",
            msg.get("seq"),
            domain,
            name,
            msg.get("session_id"),
            payload,
        )

    # -------------------------
    # Event emission + queue
    # -------------------------

    def emit(self, evt: Event, ctx: DispatchContext) -> None:
        stamped = stamp_event(
            evt,
            at=self.now(),
            seq=ctx.seq,
            classification=ctx.classification,
            route=ctx.route,
            session_id=ctx.session_id
            if ctx.session_id is not None
            else self.state.panel.session_id,
        )
        self._log.debug("Emit event: %s", stamped)

        with self._event_lock:
            self._events.append(stamped)
            subscribers = list(self._subscribers.values())

        for sub in subscribers:
            if sub.kinds is not None and stamped.kind not in sub.kinds:
                continue
            try:
                sub.callback(stamped)
            except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
                self._log.warning("Event subscriber failed: %s", exc, exc_info=True)

    def drain_events(self) -> list[Event]:
        with self._event_lock:
            out: list[Event] = list(self._events)
            self._events.clear()
        return out

    def iter_events(self) -> Iterable[Event]:
        with self._event_lock:
            return iter(list(self._events))

    def _emit_connection_state(
        self,
        *,
        connected: bool,
        reason: str | None = None,
        error_type: str | None = None,
    ) -> None:
        ctx = DispatchContext(
            kind=MessageKind.UNKNOWN,
            seq=None,
            session_id=self.state.panel.session_id,
            route=("__local__", "connection_state"),
            classification="LOCAL",
            response_match=None,
            raw_route=None,
        )
        evt = ConnectionStateChanged(
            kind=ConnectionStateChanged.KIND,
            at=UNSET_AT,
            seq=UNSET_SEQ,
            classification=UNSET_CLASSIFICATION,
            route=UNSET_ROUTE,
            session_id=UNSET_SESSION_ID,
            connected=connected,
            reason=reason,
            error_type=error_type,
        )
        self.emit(evt, ctx=ctx)

    # -------------------------
    # Dispatcher error envelope handler
    # -------------------------

    def _handle_dispatch_error_envelope(self, msg: Mapping[str, Any], ctx: DispatchContext) -> bool:
        err_root = _as_mapping(msg.get("__error__"))
        if err_root is None or not err_root:
            return False

        code = next(iter(err_root))
        detail = _as_mapping(err_root.get(code))
        if detail is None:
            return False

        message = detail.get("message")
        keys = detail.get("keys")
        payload = detail.get("payload")
        severity = detail.get("severity")

        key_list: list[str] = []
        if isinstance(keys, list):
            keys_list = cast(list[object], keys)
            for item in keys_list:
                if isinstance(item, str):
                    key_list.append(item)
        if isinstance(payload, str):
            key_list.append(f"payload={payload}")

        evt = DispatchRoutingError(
            kind=DispatchRoutingError.KIND,
            at=UNSET_AT,
            seq=UNSET_SEQ,
            classification=UNSET_CLASSIFICATION,
            route=UNSET_ROUTE,
            session_id=UNSET_SESSION_ID,
            code=str(code),
            message=str(message) if isinstance(message, str) else "Dispatcher routing error",
            keys=tuple(key_list),
            severity=str(severity) if isinstance(severity, str) else "warning",
        )
        self.emit(evt, ctx=ctx)
        return True

    def _handle_panel_error_envelope(self, msg: Mapping[str, Any], ctx: DispatchContext) -> bool:
        error_code = msg.get("error_code")
        if isinstance(error_code, str):
            try:
                error_code = int(error_code)
            except ValueError:
                error_code = None
        if not isinstance(error_code, int):
            return False

        message = msg.get("error_message") or msg.get("error_text")
        if message is not None and not isinstance(message, str):
            message = None

        if error_code == 11008:
            evt = AuthorizationRequiredEvent(
                kind=AuthorizationRequiredEvent.KIND,
                at=UNSET_AT,
                seq=UNSET_SEQ,
                classification=UNSET_CLASSIFICATION,
                route=UNSET_ROUTE,
                session_id=UNSET_SESSION_ID,
                error_code=error_code,
                message=message,
            )
        else:
            evt = ApiError(
                kind=ApiError.KIND,
                at=UNSET_AT,
                seq=UNSET_SEQ,
                classification=UNSET_CLASSIFICATION,
                route=UNSET_ROUTE,
                session_id=UNSET_SESSION_ID,
                error_code=error_code,
                message=message,
            )
        self.emit(evt, ctx=ctx)
        return True
