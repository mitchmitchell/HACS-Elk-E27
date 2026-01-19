"""
E27 Session (DDR-0034 / Option A)

Responsibilities:
- Own the TCP socket lifecycle.
- Perform HELLO after connect to obtain session keys.
- Provide a robust framed receive pump using framing.DeframeState + framing.deframe_feed(state, chunk).
- Encrypt+frame outbound schema-0 payloads; deframe+decrypt inbound schema-0 payloads.
- Surface inbound decrypted JSON objects as events (callbacks) or via recv_json().

Non-responsibilities (explicit):
- API_LINK / linking (belongs to provisioning/installer flow).
- Deciding whether/when to AUTHENTICATE (privilege escalation is application-driven).
- Driving application workflows/sequences beyond send/recv primitives.
"""

from __future__ import annotations

import asyncio
import json
import logging
import socket
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Optional

from .framing import DeframeState, deframe_feed, frame_build
from .hello import perform_hello
from .presentation import decrypt_schema0_envelope, encrypt_schema0_envelope
from . import linking
from .errors import E27Error
from .outbound import OutboundItem, OutboundPriority, OutboundQueue

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SessionConfig:
    host: str
    port: int = 2101  # Mitch preference: non-TLS port 2101
    connect_timeout_s: float = 5.0
    io_timeout_s: float = 0.5          # socket read timeout (pump cadence)
    hello_timeout_s: float = 5.0       # overall HELLO timeout
    recv_max_bytes: int = 4096         # per socket recv() call
    protocol_default: int = 0x80       # default protocol byte for schema-0 encrypted frames
    wire_log: bool = False            # enable raw RX/TX hex dump logging
    keepalive_enabled: bool = True
    keepalive_interval_s: float = 30.0
    keepalive_timeout_s: float = 10.0
    keepalive_max_missed: int = 2
    auto_receive: bool = True         # start background receive loop when on_message is set
    auto_receive_thread_fallback: bool = False  # allow dedicated thread when no event loop exists


@dataclass(frozen=True)
class SessionInfo:
    session_id: int
    session_key_hex: str
    session_hmac_hex: str


class SessionState(str, Enum):
    """Internal connection lifecycle states.

    This is intentionally mechanical and policy-free.
    """

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    HELLO = "hello"
    ACTIVE = "active"


class SessionError(RuntimeError):
    """Base exception for Session failures."""


class SessionNotReadyError(SessionError):
    """Raised when an operation requires an ACTIVE session."""


class SessionIOError(SessionError):
    """Raised when the underlying transport fails."""


class SessionProtocolError(SessionError):
    """Raised when framing/crypto/JSON decoding fails."""


class Session:
    """
    Minimal E27 session connection.

    Typical usage:
        s = Session(cfg, client_identity=client_identity, link_key_hex="...")
        s.connect()          # performs HELLO and becomes ready
        s.send_json({...})   # application sends requests (including authenticate if desired)
        obj = s.recv_json()  # or call s.pump_once() to dispatch via callback
    """

    def __init__(
        self,
        cfg: SessionConfig,
        *,
        client_identity: linking.E27Identity,
        link_key_hex: str,
    ) -> None:
        self.cfg = cfg
        self.client_identity = client_identity
        self.link_key_hex = link_key_hex

        self.sock: Optional[socket.socket] = None
        self._deframe_state: Optional[DeframeState] = None

        self.info: Optional[SessionInfo] = None

        self.state: SessionState = SessionState.DISCONNECTED
        self.last_error: Exception | None = None

        self._tx_envelope_seq = 1
        self._last_rx_envelope_seq: Optional[int] = None
        now = time.monotonic()
        self._last_rx_at = now
        self._last_tx_at = now
        self._last_exchange_at = now
        self._rx_count = 0
        self._recv_thread: Optional[threading.Thread] = None
        self._recv_stop: Optional[threading.Event] = None
        self._recv_lock = threading.Lock()
        self._recv_task: Optional[asyncio.Task[None]] = None
        self._recv_loop_ref: Optional[asyncio.AbstractEventLoop] = None
        self._outbound: Optional[OutboundQueue] = None

        # Event hooks (optional)
        self.on_connected: Optional[Callable[[SessionInfo], None]] = None
        self.on_message: Optional[Callable[[dict[str, Any]], None]] = None
        self.on_disconnected: Optional[Callable[[Exception | None], None]] = None
        self.on_idle: Optional[Callable[[], None]] = None

    # --------------------------
    # Connection lifecycle
    # --------------------------

    def connect(self) -> SessionInfo:
        """
        Connect TCP and perform HELLO to obtain session keys.
        """
        if self.state is not SessionState.DISCONNECTED:
            # Mechanical safety: connect() is intended to establish a new session.
            self.close()

        self.last_error = None
        self.state = SessionState.CONNECTING

        logger.info("E27 Session connecting to %s:%s", self.cfg.host, self.cfg.port)
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.settimeout(self.cfg.connect_timeout_s)
            s.connect((self.cfg.host, self.cfg.port))
        except OSError as e:
            self.last_error = e
            self.state = SessionState.DISCONNECTED
            try:
                s.close()
            except OSError:
                pass
            raise SessionIOError(
                f"Failed to connect to {self.cfg.host}:{self.cfg.port}: {e}"
            ) from e

        # After connect, switch to pump cadence timeout.
        s.settimeout(self.cfg.io_timeout_s)
        self.sock = s
        self._deframe_state = DeframeState()

        self.state = SessionState.HELLO
        try:
            keys = perform_hello(
                sock=s,
                client_identity=self.client_identity,
                linkkey_hex=self.link_key_hex,
                timeout_s=self.cfg.hello_timeout_s,
            )
        except E27Error as e:
            self.last_error = e
            # HELLO failure is a session setup failure; close and surface clearly.
            self._handle_disconnect(e)
            raise SessionProtocolError(
                f"HELLO failed for {self.cfg.host}:{self.cfg.port}: {e}"
            ) from e

        self.info = SessionInfo(
            session_id=keys.session_id,
            session_key_hex=keys.session_key_hex,
            session_hmac_hex=keys.hmac_key_hex,
        )
        self.state = SessionState.ACTIVE
        self._tx_envelope_seq = 1
        self._last_rx_at = time.monotonic()
        self._last_tx_at = self._last_rx_at
        self._last_exchange_at = self._last_rx_at

        logger.info("E27 HELLO complete; session_id=%s", self.info.session_id)

        if self.on_connected:
            self.on_connected(self.info)

        if self.cfg.auto_receive and self.on_message is not None:
            self._start_receiver()

        return self.info

    def close(self) -> None:
        """
        Close the socket. Safe to call multiple times.
        """
        self._stop_receiver()
        if self._outbound is not None:
            self._outbound.stop(fail_exc=SessionIOError("Session closed."))
            self._outbound = None
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
        self.sock = None
        self._deframe_state = None
        self.info = None
        self.state = SessionState.DISCONNECTED

    # --------------------------
    # Transport helpers
    # --------------------------

    def _require_ready(self) -> None:
        if (
            self.state is not SessionState.ACTIVE
            or self.sock is None
            or self.info is None
            or self._deframe_state is None
        ):
            raise SessionNotReadyError(
                "Session is not ACTIVE/ready (call connect() successfully first)."
            )

    def _recv_some(self, *, max_bytes: int) -> bytes:
        """
        Read from socket; may raise TimeoutError or ConnectionError.
        Kept as a method so tests can monkeypatch it.
        """
        self._require_ready()
        assert self.sock is not None

        try:
            data = self.sock.recv(max_bytes)
        except socket.timeout as e:
            raise TimeoutError("Timed out waiting for data from the panel.") from e
        except OSError as e:
            raise SessionIOError(
                f"Socket read failed from {self.cfg.host}:{self.cfg.port}: {e}"
            ) from e

        if not data:
            raise SessionIOError(
                f"Connection closed by the panel ({self.cfg.host}:{self.cfg.port})."
            )

        return data

    def _send_all(self, data: bytes) -> None:
        self._require_ready()
        assert self.sock is not None
        try:
            self.sock.sendall(data)
            now = time.monotonic()
            self._last_tx_at = now
            self._last_exchange_at = now
        except OSError as e:
            raise SessionIOError(
                f"Socket write failed to {self.cfg.host}:{self.cfg.port}: {e}"
            ) from e

    # --------------------------
    # Framed receive pump
    # --------------------------

    def _recv_one_frame_no_crc(self, *, timeout_s: float) -> bytes:
        """
        Return the first valid frame_no_crc from the stream.

        frame_no_crc layout (per framing.deframe_feed):
            [protocol_byte][len_lo][len_hi][ciphertext...]
        """
        self._require_ready()
        assert self._deframe_state is not None

        deadline = time.monotonic() + timeout_s
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(
                    "Timed out waiting for a framed message from the panel."
                )

            try:
                chunk = self._recv_some(max_bytes=self.cfg.recv_max_bytes)
            except TimeoutError:
                # keep pumping until overall deadline; allow idle hooks for retries
                if self.on_idle:
                    try:
                        self.on_idle()
                    except Exception:
                        pass
                continue

            if self.cfg.wire_log and logger.isEnabledFor(logging.DEBUG):
                logger.debug("RX raw chunk (%d bytes): %s", len(chunk), chunk.hex())

            results = deframe_feed(self._deframe_state, chunk)
            for r in results:
                if getattr(r, "ok", False):
                    if r.frame_no_crc is None:
                        continue
                    if self.cfg.wire_log and logger.isEnabledFor(logging.DEBUG):
                        logger.debug(
                            "RX frame_no_crc (%d bytes): %s",
                            len(r.frame_no_crc or b""),
                            (r.frame_no_crc or b"").hex(),
                        )
                    return r.frame_no_crc
                # CRC-bad or malformed frames: ignore and keep scanning.
                # If the framing layer provides details, emit at debug level.
                err = getattr(r, "error", None)
                if err:
                    logger.debug("Ignoring invalid frame while resyncing: %s", err)

    # --------------------------
    # Public send/recv API
    # --------------------------

    def send_json(
        self,
        obj: dict[str, Any],
        *,
        protocol_byte: Optional[int] = None,
        priority: OutboundPriority = OutboundPriority.NORMAL,
        on_sent: Optional[Callable[[float], None]] = None,
        on_fail: Optional[Callable[[BaseException], None]] = None,
    ) -> None:
        """
        Encrypt schema-0 payload (JSON UTF-8 bytes) and send as a framed message.
        """
        framed = self._encode_json(obj, protocol_byte=protocol_byte)
        if self._outbound is None:
            self._send_all(framed)
            if on_sent is not None:
                on_sent(time.monotonic())
            return

        item = OutboundItem(
            payload=framed,
            seq=obj.get("seq") if isinstance(obj.get("seq"), int) else None,
            kind="request",
            priority=priority,
            enqueued_at=time.monotonic(),
            on_sent=on_sent,
            on_fail=on_fail,
        )
        self._outbound.enqueue(item)

    def enable_outbound_queue(
        self,
        *,
        loop: asyncio.AbstractEventLoop,
        min_interval_s: float,
        max_burst: int,
    ) -> None:
        self._outbound = OutboundQueue(
            loop=loop,
            send_fn=self._send_all,
            min_interval_s=min_interval_s,
            max_burst=max_burst,
            logger=logger,
        )
        self._outbound.start()

    def _encode_json(self, obj: dict[str, Any], *, protocol_byte: Optional[int] = None) -> bytes:
        self._require_ready()
        assert self.info is not None

        payload = json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8")

        envelope_seq = self._tx_envelope_seq
        self._tx_envelope_seq = self._next_envelope_seq(envelope_seq)
        proto, ciphertext = encrypt_schema0_envelope(
            payload=payload,
            session_key=bytes.fromhex(self.info.session_key_hex),
            src=1,
            dest=0,
            head=0,
            envelope_seq=envelope_seq,
        )

        framed = frame_build(protocol_byte=proto, data_frame=ciphertext)
        if self.cfg.wire_log and logger.isEnabledFor(logging.DEBUG):
            logger.debug("TX framed (%d bytes): %s", len(framed), framed.hex())
        return framed

    def recv_json(self, *, timeout_s: float = 5.0) -> dict[str, Any]:
        """
        Receive one framed message, decrypt schema-0, parse JSON, return dict.
        """
        with self._recv_lock:
            self._require_ready()
            assert self.info is not None
            idle_check_at = time.monotonic() + timeout_s
            while True:
                if self.on_idle and time.monotonic() >= idle_check_at:
                    try:
                        self.on_idle()
                    except Exception:
                        pass
                    idle_check_at = time.monotonic() + timeout_s
                frame_no_crc = self._recv_one_frame_no_crc(timeout_s=timeout_s)
                if len(frame_no_crc) < 3:
                    raise SessionProtocolError(
                        f"Received an invalid frame (too short) from {self.cfg.host}:{self.cfg.port}."
                    )

                protocol_byte = frame_no_crc[0]
                ciphertext = frame_no_crc[3:]  # skip protocol + 2-byte length

                try:
                    env = decrypt_schema0_envelope(
                        protocol_byte=protocol_byte,
                        ciphertext=ciphertext,
                        session_key=bytes.fromhex(self.info.session_key_hex),
                    )
                except Exception as e:
                    raise SessionProtocolError(
                        f"Failed to decrypt schema-0 envelope from {self.cfg.host}:{self.cfg.port}: {e}"
                    ) from e
                seq_val = getattr(env, "seq", None)
                if isinstance(seq_val, int):
                    self._last_rx_envelope_seq = seq_val

                try:
                    obj = json.loads(env.payload.decode("utf-8"))
                except Exception as e:
                    raise SessionProtocolError(
                        f"Received invalid JSON payload from {self.cfg.host}:{self.cfg.port}: {e}"
                    ) from e

                if not isinstance(obj, dict):
                    raise SessionProtocolError(
                        f"Expected a JSON object (dict) but received {type(obj).__name__} from {self.cfg.host}:{self.cfg.port}."
                    )

                if logger.isEnabledFor(logging.DEBUG):
                    logger.debug(
                        "RX json: session_id=%s seq=%s keys=%s",
                        obj.get("session_id"),
                        obj.get("seq"),
                        tuple(obj.keys()),
                    )
                self._rx_count += 1
                self._last_rx_at = time.monotonic()
                self._last_exchange_at = self._last_rx_at
                return obj

    def pump_once(self, *, timeout_s: float = 0.5) -> Optional[dict[str, Any]]:
        """
        One pump iteration: receive and dispatch exactly one message if available.

        Returns:
            The decoded JSON dict if one was received, else None on timeout.
        """
        try:
            obj = self.recv_json(timeout_s=timeout_s)
        except TimeoutError:
            if self.on_idle:
                self.on_idle()
            return None
        except SessionNotReadyError:
            # Caller attempted to pump without a connected session.
            raise
        except (SessionIOError, SessionProtocolError) as e:
            # A transport/protocol failure means the session is no longer healthy.
            logger.warning(
                "Session pump failed (%s) in state=%s for %s:%s: %s",
                type(e).__name__,
                self.state.value,
                self.cfg.host,
                self.cfg.port,
                e,
            )
            self._handle_disconnect(e)
            raise
        except Exception as e:
            # Unexpected error: still treat as disconnect-worthy at the Session layer.
            logger.warning(
                "Unexpected session pump error (%s) in state=%s for %s:%s: %s",
                type(e).__name__,
                self.state.value,
                self.cfg.host,
                self.cfg.port,
                e,
            )
            self._handle_disconnect(e)
            raise

        if self.on_message:
            self.on_message(obj)

        return obj

    def _start_receiver(self) -> None:
        if self._recv_thread is not None or self._recv_task is not None:
            return
        self._recv_stop = threading.Event()
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop is not None:
            # Prefer asyncio.to_thread when a loop is running (HA async contexts).
            self._recv_loop_ref = loop
            self._recv_task = loop.create_task(asyncio.to_thread(self._recv_loop, self._recv_stop))
            return

        if not self.cfg.auto_receive_thread_fallback:
            return

        self._recv_thread = threading.Thread(
            target=self._recv_loop,
            args=(self._recv_stop,),
            name="e27-recv",
            daemon=True,
        )
        self._recv_thread.start()

    def start_auto_receive(self) -> None:
        """Start auto-receive if enabled and a message handler is configured."""
        if (
            not self.cfg.auto_receive
            or self.on_message is None
            or self.state is not SessionState.ACTIVE
        ):
            return
        self._start_receiver()

    def _stop_receiver(self) -> None:
        if self._recv_stop is not None:
            self._recv_stop.set()
        if self._recv_thread is not None and self._recv_thread is not threading.current_thread():
            self._recv_thread.join(timeout=1.0)
        if self._recv_task is not None:
            # Let the to_thread worker exit via stop_event; no hard cancel needed.
            self._recv_task = None
            self._recv_loop_ref = None
        self._recv_thread = None
        self._recv_stop = None

    def _recv_loop(self, stop_event: threading.Event) -> None:
        while not stop_event.is_set():
            if self.state is not SessionState.ACTIVE:
                stop_event.wait(0.1)
                continue
            try:
                obj = self.recv_json(timeout_s=self.cfg.io_timeout_s)
            except TimeoutError:
                if self.on_idle:
                    try:
                        self.on_idle()
                    except Exception:
                        pass
                continue
            except SessionNotReadyError:
                break
            except (SessionIOError, SessionProtocolError) as e:
                self._handle_disconnect(e)
                break
            except Exception as e:
                self._handle_disconnect(e)
                logger.warning("Session receive loop error: %s", e, exc_info=True)
                break

            if self.on_message:
                self.on_message(obj)

    def _handle_disconnect(self, err: Exception | None) -> None:
        self.last_error = err
        try:
            self.close()
        finally:
            if self.on_disconnected:
                self.on_disconnected(err)

    def _next_envelope_seq(self, current: int) -> int:
        return self._wrap_seq(current + 1, wrap_to=1)

    @staticmethod
    def _wrap_seq(value: int, *, wrap_to: int) -> int:
        max_seq = 2_147_483_647
        if value > max_seq:
            return wrap_to
        if value <= 0:
            return wrap_to
        return value

    def reconnect(self) -> SessionInfo:
        """Mechanical reconnect helper (no backoff/policy).

        This is intentionally a convenience wrapper around close() + connect().
        Any retry/backoff strategy belongs above the Session layer.
        """
        self.close()
        return self.connect()
