# test/test_e27_session.py
#
# Unit tests for session.py (DDR-0034 / Option A)
#
# These tests validate Session as a transport + crypto + framing boundary:
# - TCP connect lifecycle + HELLO handshake + key storage
# - framing pump uses DeframeState + deframe_feed(state, chunk)
# - send_json encrypts schema-0 and frames via frame_build, then sendall
# - recv_json deframes + decrypts schema-0 + parses JSON dict
# - pump_once dispatches on_message, handles timeout, and disconnects on errors
#
# IMPORTANT:
# - Session is NOT expected to perform API_LINK, authenticate, or workflows.
# - These tests use fakes/mocks for socket, perform_hello, framing, and crypto helpers.

from __future__ import annotations

import socket
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, cast

import pytest

# Adjust import path if your session module lives elsewhere.
# The tests are written to be resilient by monkeypatching module-level dependencies.
from elke27_lib import linking
from elke27_lib import session as session_mod
from elke27_lib.framing import DeframeState
from test.helpers.internal import get_private, set_private


@dataclass
class _HelloKeys:
    session_id: int
    session_key_hex: str
    hmac_key_hex: str


class _FakeSocket:
    """
    Minimal socket fake used by Session.connect() / _recv_some() / _send_all().
    """

    def __init__(self) -> None:
        self.connected_to: tuple[str, int] | None = None
        self.timeouts: list[float] = []
        self.closed: bool = False
        self.sent: list[bytes] = []
        self._recv_queue: list[bytes] = []

    def settimeout(self, t: float) -> None:
        self.timeouts.append(t)

    def connect(self, addr: tuple[str, int]) -> None:
        self.connected_to = addr

    def close(self) -> None:
        self.closed = True

    def sendall(self, data: bytes) -> None:
        self.sent.append(data)

    def recv(self, _max_bytes: int) -> bytes:
        # Session._recv_some treats b"" as socket closed.
        if not self._recv_queue:
            return b""
        return self._recv_queue.pop(0)

    def queue_recv(self, *chunks: bytes) -> None:
        self._recv_queue.extend(chunks)


@dataclass
class _DeframeResult:
    ok: bool
    frame_no_crc: bytes = b""


@dataclass
class _DecryptEnvelope:
    payload: bytes


def _make_session_ready(monkeypatch: pytest.MonkeyPatch) -> tuple[session_mod.Session, _FakeSocket]:
    """
    Create a Session instance that is already connected/ready without running connect().
    """
    _ = monkeypatch
    cfg = session_mod.SessionConfig(host="127.0.0.1", port=2101, keepalive_enabled=False)
    identity = linking.E27Identity(
        mn="0222", sn="001122334455", fwver="1.0", hwver="1.0", osver="1.0"
    )
    s = session_mod.Session(cfg, client_identity=identity, link_key_hex="00" * 16)

    fake_sock = _FakeSocket()

    # Mark session as "ready"
    s.sock = cast(socket.socket, cast(object, fake_sock))
    set_private(s, "_deframe_state", DeframeState())
    s.info = session_mod.SessionInfo(
        session_id=123, session_key_hex="11" * 16, session_hmac_hex="22" * 20
    )
    # Session methods under test require the ACTIVE state. In production this is set by connect().
    # For these unit tests we intentionally bypass connect() and set state directly.
    s.state = session_mod.SessionState.ACTIVE

    return s, fake_sock


def test_connect_establishes_tcp_and_performs_hello(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_sock = _FakeSocket()

    # Patch socket.socket(...) to return our fake.
    def _fake_socket_ctor(*_args: Any, **_kwargs: Any) -> _FakeSocket:
        return fake_sock

    monkeypatch.setattr(socket, "socket", _fake_socket_ctor)

    # Patch HELLO to return deterministic keys.
    keys = _HelloKeys(session_id=7, session_key_hex="aa" * 16, hmac_key_hex="bb" * 20)

    def _fake_perform_hello(
        *,
        sock: Any,
        client_identity: linking.E27Identity,
        linkkey_hex: str,
        timeout_s: float,
    ) -> _HelloKeys:
        _ = client_identity
        assert sock is fake_sock
        assert identity.mn == "0222"
        assert linkkey_hex == "cc" * 16
        assert timeout_s == 9.0
        return keys

    monkeypatch.setattr(session_mod, "perform_hello", _fake_perform_hello)

    cfg = session_mod.SessionConfig(
        host="10.0.0.5",
        port=2101,
        connect_timeout_s=3.0,
        io_timeout_s=0.25,
        hello_timeout_s=9.0,
        keepalive_enabled=False,
    )
    identity = linking.E27Identity(
        mn="0222", sn="001122334455", fwver="1.0", hwver="1.0", osver="1.0"
    )
    s = session_mod.Session(cfg, client_identity=identity, link_key_hex="cc" * 16)

    captured: dict[str, Any] = {}

    def _on_connected(info: session_mod.SessionInfo) -> None:
        captured["info"] = info

    s.on_connected = _on_connected

    info = s.connect()

    # TCP connect and timeouts were set correctly.
    assert fake_sock.connected_to == ("10.0.0.5", 2101)
    assert fake_sock.timeouts[0] == 3.0  # connect timeout
    assert fake_sock.timeouts[1] == 0.25  # io/pump cadence timeout

    # HELLO result stored as SessionInfo and returned.
    assert isinstance(info, session_mod.SessionInfo)
    assert info.session_id == 7
    assert info.session_key_hex == "aa" * 16
    assert info.session_hmac_hex == "bb" * 20


def test_close_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    _ = monkeypatch
    s, fake_sock = _make_session_ready(monkeypatch)

    s.close()
    assert fake_sock.closed is True
    assert s.sock is None
    assert get_private(s, "_deframe_state") is None
    assert s.info is None

    # Calling again should not raise.
    s.close()
    assert s.sock is None


def test_send_json_encrypts_frames_and_sendall(monkeypatch: pytest.MonkeyPatch) -> None:
    s, fake_sock = _make_session_ready(monkeypatch)

    # Patch encrypt_schema0_envelope to return a protocol byte + ciphertext.
    seen_seq: list[int] = []

    def _fake_encrypt_schema0_envelope(
        *,
        payload: bytes,
        session_key: bytes,
        src: int,
        dest: int,
        head: int,
        envelope_seq: int,
        _iv: bytes = b"",
    ) -> tuple[int, bytes]:
        assert s.info is not None
        assert session_key == bytes.fromhex(s.info.session_key_hex)
        # Ensure JSON is compact separators (",", ":") and utf-8.
        decoded = payload.decode("utf-8")
        assert decoded == '{"a":1,"b":"x"}'
        assert src == 1
        assert dest == 0
        assert head == 0
        seen_seq.append(envelope_seq)
        return 0x83, b"CIPHERTEXT"

    monkeypatch.setattr(session_mod, "encrypt_schema0_envelope", _fake_encrypt_schema0_envelope)

    # Patch frame_build to create deterministic framed bytes.
    def _fake_frame_build(*, protocol_byte: int, data_frame: bytes) -> bytes:
        assert protocol_byte == 0x83
        assert data_frame == b"CIPHERTEXT"
        return b"FRAMED:" + bytes([protocol_byte]) + data_frame

    monkeypatch.setattr(session_mod, "frame_build", _fake_frame_build)

    s.send_json({"a": 1, "b": "x"})
    s.send_json({"a": 1, "b": "x"})
    assert fake_sock.sent == [
        b"FRAMED:" + b"\x83" + b"CIPHERTEXT",
        b"FRAMED:" + b"\x83" + b"CIPHERTEXT",
    ]
    assert seen_seq == [1, 2]


def test_envelope_seq_wraps(monkeypatch: pytest.MonkeyPatch) -> None:
    s, _ = _make_session_ready(monkeypatch)
    set_private(s, "_tx_envelope_seq", 2147483647)
    captured: list[int] = []

    def _fake_encrypt_schema0_envelope(
        *,
        payload: bytes,
        session_key: bytes,
        src: int,
        dest: int,
        head: int,
        envelope_seq: int,
        iv: bytes = b"",
    ) -> tuple[int, bytes]:
        _ = payload, session_key, src, dest, head, iv
        captured.append(envelope_seq)
        return 0x80, b"X"

    monkeypatch.setattr(session_mod, "encrypt_schema0_envelope", _fake_encrypt_schema0_envelope)

    def _fake_frame_build(*, protocol_byte: int, data_frame: bytes) -> bytes:
        _ = protocol_byte, data_frame
        return b"X"

    monkeypatch.setattr(session_mod, "frame_build", _fake_frame_build)

    s.send_json({"x": 1})
    s.send_json({"x": 1})
    assert captured == [2_147_483_647, 1]


def test_recv_json_deframes_decrypts_and_parses(monkeypatch: pytest.MonkeyPatch) -> None:
    s, _ = _make_session_ready(monkeypatch)

    # Provide a valid frame_no_crc: [proto][len_lo][len_hi][ciphertext...]
    def _fake_recv_one_frame_no_crc(*, timeout_s: float) -> bytes:
        _ = timeout_s
        return b"\x84\x05\x00" + b"ABCDE"

    monkeypatch.setattr(s, "_recv_one_frame_no_crc", _fake_recv_one_frame_no_crc)

    def _fake_decrypt_schema0_envelope(
        *,
        session_key: bytes,
        protocol_byte: int,
        ciphertext: bytes,
    ) -> _DecryptEnvelope:
        assert s.info is not None
        assert session_key == bytes.fromhex(s.info.session_key_hex)
        assert protocol_byte == 0x84
        assert ciphertext == b"ABCDE"
        return _DecryptEnvelope(payload=b'{"ok":true,"n":2}')

    monkeypatch.setattr(session_mod, "decrypt_schema0_envelope", _fake_decrypt_schema0_envelope)

    obj = s.recv_json(timeout_s=1.0)
    assert obj == {"ok": True, "n": 2}


def test_recv_json_rejects_short_frame(monkeypatch: pytest.MonkeyPatch) -> None:
    s, _ = _make_session_ready(monkeypatch)

    def _fake_recv_one_frame_no_crc(*, timeout_s: float) -> bytes:
        _ = timeout_s
        return b"\x80\x00"

    monkeypatch.setattr(s, "_recv_one_frame_no_crc", _fake_recv_one_frame_no_crc)

    # Session now raises SessionProtocolError with a contextual message.
    with pytest.raises(session_mod.SessionProtocolError, match=r"invalid frame \(too short\)"):
        s.recv_json(timeout_s=0.1)


def test_recv_json_rejects_non_object_json(monkeypatch: pytest.MonkeyPatch) -> None:
    s, _ = _make_session_ready(monkeypatch)

    def _fake_recv_one_frame_no_crc(*, timeout_s: float) -> bytes:
        _ = timeout_s
        return b"\x81\x03\x00" + b"XYZ"

    monkeypatch.setattr(s, "_recv_one_frame_no_crc", _fake_recv_one_frame_no_crc)

    def _fake_decrypt_schema0_envelope(
        *, session_key: bytes, protocol_byte: int, ciphertext: bytes
    ) -> _DecryptEnvelope:
        _ = session_key, protocol_byte, ciphertext
        return _DecryptEnvelope(payload=b"[1,2,3]")

    monkeypatch.setattr(session_mod, "decrypt_schema0_envelope", _fake_decrypt_schema0_envelope)

    with pytest.raises(session_mod.SessionProtocolError, match=r"Expected a JSON object \(dict\)"):
        s.recv_json(timeout_s=0.2)


def test_recv_json_rejects_invalid_json(monkeypatch: pytest.MonkeyPatch) -> None:
    s, _ = _make_session_ready(monkeypatch)

    def _fake_recv_one_frame_no_crc(*, timeout_s: float) -> bytes:
        _ = timeout_s
        return b"\x81\x03\x00" + b"XYZ"

    monkeypatch.setattr(s, "_recv_one_frame_no_crc", _fake_recv_one_frame_no_crc)

    def _fake_decrypt_schema0_envelope(
        *, session_key: bytes, protocol_byte: int, ciphertext: bytes
    ) -> _DecryptEnvelope:
        _ = session_key, protocol_byte, ciphertext
        return _DecryptEnvelope(payload=b'{"unterminated":')

    monkeypatch.setattr(session_mod, "decrypt_schema0_envelope", _fake_decrypt_schema0_envelope)

    with pytest.raises(session_mod.SessionProtocolError, match=r"Received invalid JSON payload"):
        s.recv_json(timeout_s=0.2)


def test_recv_one_frame_no_crc_uses_deframe_state_and_feed(monkeypatch: pytest.MonkeyPatch) -> None:
    s, _ = _make_session_ready(monkeypatch)

    # Make _recv_some return two chunks. The first yields no OK frames, second yields an OK frame.
    chunks = [b"chunk1", b"chunk2"]

    def _fake_recv_some(*, max_bytes: int) -> bytes:
        assert max_bytes == s.cfg.recv_max_bytes
        return chunks.pop(0)

    monkeypatch.setattr(s, "_recv_some", _fake_recv_some)

    feed_calls: list[tuple[Any, bytes]] = []

    def _fake_deframe_feed(state: Any, chunk: bytes) -> list[_DeframeResult]:
        # Must be called with canonical state object and the chunk.
        feed_calls.append((state, chunk))
        if chunk == b"chunk1":
            # Simulate CRC-bad frame and/or incomplete parse.
            return [_DeframeResult(ok=False)]
        # Second chunk returns a valid frame.
        return [_DeframeResult(ok=True, frame_no_crc=b"\x80\x01\x00" + b"Z")]

    monkeypatch.setattr(session_mod, "deframe_feed", _fake_deframe_feed)

    recv_one_frame_no_crc = cast(Callable[..., bytes], get_private(s, "_recv_one_frame_no_crc"))
    frame = recv_one_frame_no_crc(timeout_s=0.5)
    assert frame == b"\x80\x01\x00Z"

    # Ensure DeframeState instance was used and deframe_feed called twice.
    assert len(feed_calls) == 2
    deframe_state = cast(DeframeState, get_private(s, "_deframe_state"))
    assert feed_calls[0][0] is deframe_state
    assert feed_calls[0][1] == b"chunk1"
    assert feed_calls[1][0] is deframe_state
    assert feed_calls[1][1] == b"chunk2"


def test_recv_one_frame_no_crc_queues_multiple_frames(monkeypatch: pytest.MonkeyPatch) -> None:
    s, _ = _make_session_ready(monkeypatch)

    recv_calls: list[bytes] = []
    chunks = [b"chunk1"]

    def _fake_recv_some(*, max_bytes: int) -> bytes:
        assert max_bytes == s.cfg.recv_max_bytes
        recv_calls.append(b"call")
        return chunks.pop(0)

    monkeypatch.setattr(s, "_recv_some", _fake_recv_some)

    def _fake_deframe_feed(state: Any, chunk: bytes) -> list[_DeframeResult]:
        deframe_state = cast(DeframeState, get_private(s, "_deframe_state"))
        assert state is deframe_state
        assert chunk == b"chunk1"
        return [
            _DeframeResult(ok=True, frame_no_crc=b"\x80\x01\x00A"),
            _DeframeResult(ok=True, frame_no_crc=b"\x81\x01\x00B"),
        ]

    monkeypatch.setattr(session_mod, "deframe_feed", _fake_deframe_feed)

    recv_one_frame_no_crc = cast(Callable[..., bytes], get_private(s, "_recv_one_frame_no_crc"))
    frame1 = recv_one_frame_no_crc(timeout_s=0.5)
    frame2 = recv_one_frame_no_crc(timeout_s=0.5)

    assert frame1 == b"\x80\x01\x00A"
    assert frame2 == b"\x81\x01\x00B"
    assert len(recv_calls) == 1


def test_recv_json_drains_multi_frame_chunk(monkeypatch: pytest.MonkeyPatch) -> None:
    s, _ = _make_session_ready(monkeypatch)

    chunks = [b"chunk1"]

    def _fake_recv_some(*, max_bytes: int) -> bytes:
        assert max_bytes == s.cfg.recv_max_bytes
        return chunks.pop(0)

    monkeypatch.setattr(s, "_recv_some", _fake_recv_some)

    def _fake_deframe_feed(state: Any, chunk: bytes) -> list[_DeframeResult]:
        deframe_state = cast(DeframeState, get_private(s, "_deframe_state"))
        assert state is deframe_state
        assert chunk == b"chunk1"
        return [
            _DeframeResult(ok=True, frame_no_crc=b"\x84\x05\x00AAAAA"),
            _DeframeResult(ok=True, frame_no_crc=b"\x84\x05\x00BBBBB"),
        ]

    monkeypatch.setattr(session_mod, "deframe_feed", _fake_deframe_feed)

    payloads = [
        b'{"zone":1}',
        b'{"zone":2}',
    ]

    def _fake_decrypt_schema0_envelope(
        *, session_key: bytes, protocol_byte: int, ciphertext: bytes
    ) -> _DecryptEnvelope:
        _ = session_key, ciphertext
        assert protocol_byte == 0x84
        return _DecryptEnvelope(payload=payloads.pop(0))

    monkeypatch.setattr(session_mod, "decrypt_schema0_envelope", _fake_decrypt_schema0_envelope)

    first = s.recv_json(timeout_s=0.5)
    second = s.recv_json(timeout_s=0.5)

    assert first == {"zone": 1}
    assert second == {"zone": 2}


def test_recv_json_skips_invalid_then_reads_next_frame(monkeypatch: pytest.MonkeyPatch) -> None:
    s, _ = _make_session_ready(monkeypatch)

    chunks = [b"chunk1"]

    def _fake_recv_some(*, max_bytes: int) -> bytes:
        assert max_bytes == s.cfg.recv_max_bytes
        return chunks.pop(0)

    monkeypatch.setattr(s, "_recv_some", _fake_recv_some)

    def _fake_deframe_feed(state: Any, chunk: bytes) -> list[_DeframeResult]:
        deframe_state = cast(DeframeState, get_private(s, "_deframe_state"))
        assert state is deframe_state
        assert chunk == b"chunk1"
        return [
            _DeframeResult(ok=True, frame_no_crc=b"\x84\x05\x00AAAAA"),
            _DeframeResult(ok=False),
            _DeframeResult(ok=True, frame_no_crc=b"\x84\x05\x00BBBBB"),
        ]

    monkeypatch.setattr(session_mod, "deframe_feed", _fake_deframe_feed)

    payloads = [
        b'{"zone":3}',
        b'{"zone":4}',
    ]

    def _fake_decrypt_schema0_envelope(
        *, session_key: bytes, protocol_byte: int, ciphertext: bytes
    ) -> _DecryptEnvelope:
        _ = session_key, ciphertext
        assert protocol_byte == 0x84
        return _DecryptEnvelope(payload=payloads.pop(0))

    monkeypatch.setattr(session_mod, "decrypt_schema0_envelope", _fake_decrypt_schema0_envelope)

    first = s.recv_json(timeout_s=0.5)
    second = s.recv_json(timeout_s=0.5)

    assert first == {"zone": 3}
    assert second == {"zone": 4}


def test_recv_json_handles_partial_frame_then_next_chunk(monkeypatch: pytest.MonkeyPatch) -> None:
    s, _ = _make_session_ready(monkeypatch)

    chunks = [b"chunk1", b"chunk2"]

    def _fake_recv_some(*, max_bytes: int) -> bytes:
        assert max_bytes == s.cfg.recv_max_bytes
        return chunks.pop(0)

    monkeypatch.setattr(s, "_recv_some", _fake_recv_some)

    def _fake_deframe_feed(state: Any, chunk: bytes) -> list[_DeframeResult]:
        deframe_state = cast(DeframeState, get_private(s, "_deframe_state"))
        assert state is deframe_state
        if chunk == b"chunk1":
            return []
        return [_DeframeResult(ok=True, frame_no_crc=b"\x84\x05\x00HELLO")]

    monkeypatch.setattr(session_mod, "deframe_feed", _fake_deframe_feed)

    def _fake_decrypt_schema0_envelope(
        *, session_key: bytes, protocol_byte: int, ciphertext: bytes
    ) -> _DecryptEnvelope:
        _ = session_key, protocol_byte, ciphertext
        return _DecryptEnvelope(payload=b'{"zone":5}')

    monkeypatch.setattr(session_mod, "decrypt_schema0_envelope", _fake_decrypt_schema0_envelope)

    obj = s.recv_json(timeout_s=0.5)
    assert obj == {"zone": 5}


def test_recv_json_delivers_after_decrypt_error(monkeypatch: pytest.MonkeyPatch) -> None:
    s, _ = _make_session_ready(monkeypatch)

    chunks = [b"chunk1"]

    def _fake_recv_some(*, max_bytes: int) -> bytes:
        assert max_bytes == s.cfg.recv_max_bytes
        return chunks.pop(0)

    monkeypatch.setattr(s, "_recv_some", _fake_recv_some)

    def _fake_deframe_feed(state: Any, chunk: bytes) -> list[_DeframeResult]:
        deframe_state = cast(DeframeState, get_private(s, "_deframe_state"))
        assert state is deframe_state
        assert chunk == b"chunk1"
        return [
            _DeframeResult(ok=True, frame_no_crc=b"\x84\x05\x00AAAAA"),
            _DeframeResult(ok=True, frame_no_crc=b"\x84\x05\x00BBBBB"),
        ]

    monkeypatch.setattr(session_mod, "deframe_feed", _fake_deframe_feed)

    def _fake_decrypt_schema0_envelope(
        *, session_key: bytes, protocol_byte: int, ciphertext: bytes
    ) -> _DecryptEnvelope:
        _ = session_key, protocol_byte
        if ciphertext == b"AAAAA":
            raise session_mod.SessionProtocolError("decrypt failed")
        return _DecryptEnvelope(payload=b'{"zone":6}')

    monkeypatch.setattr(session_mod, "decrypt_schema0_envelope", _fake_decrypt_schema0_envelope)

    with pytest.raises(session_mod.SessionProtocolError, match=r"decrypt failed"):
        s.recv_json(timeout_s=0.5)

    obj = s.recv_json(timeout_s=0.5)
    assert obj == {"zone": 6}


def test_pump_once_timeout_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    s, _ = _make_session_ready(monkeypatch)

    def _fake_recv_json(*, timeout_s: float) -> dict[str, Any]:
        _ = timeout_s
        raise TimeoutError("socket timeout")

    monkeypatch.setattr(s, "recv_json", _fake_recv_json)

    assert s.pump_once(timeout_s=0.01) is None


def test_pump_once_dispatches_on_message(monkeypatch: pytest.MonkeyPatch) -> None:
    s, _ = _make_session_ready(monkeypatch)

    def _fake_recv_json(*, timeout_s: float) -> dict[str, Any]:
        _ = timeout_s
        return {"hello": "world"}

    monkeypatch.setattr(s, "recv_json", _fake_recv_json)

    captured: list[dict[str, Any]] = []

    def _on_message(obj: dict[str, Any]) -> None:
        captured.append(obj)

    s.on_message = _on_message

    out = s.pump_once(timeout_s=0.2)
    assert out == {"hello": "world"}
    assert captured == [{"hello": "world"}]


def test_pump_once_disconnects_and_emits_on_disconnected(monkeypatch: pytest.MonkeyPatch) -> None:
    s, fake_sock = _make_session_ready(monkeypatch)

    class Boom(Exception):
        pass

    def _fake_recv_json(*, timeout_s: float) -> dict[str, Any]:
        _ = timeout_s
        raise Boom("decrypt failed")

    monkeypatch.setattr(s, "recv_json", _fake_recv_json)

    disconnected: dict[str, Any] = {"called": False, "err": None}

    def _on_disconnected(err: Exception | None) -> None:
        disconnected["called"] = True
        disconnected["err"] = err

    s.on_disconnected = _on_disconnected

    with pytest.raises(Boom):
        s.pump_once(timeout_s=0.2)

    # Session should have been closed and callback fired.
    assert s.sock is None
    assert s.info is None
    assert get_private(s, "_deframe_state") is None
    assert fake_sock.closed is True

    assert disconnected["called"] is True
    assert isinstance(disconnected["err"], Boom)
