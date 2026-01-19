# elkm1/test/e27_test_framing.py
#
# Minimal but high-value tests for E27 link-layer framing/deframing.
#
# These tests assume elke27_lib.framing exports:
#   - DeframeState
#   - deframe_feed(state, chunk) -> list[DeframeResult]
#   - frame_build(protocol_byte: int, data_frame: bytes) -> bytes
#
# And that DeframeResult has:
#   - ok: bool
#   - frame_no_crc: bytes | None
#
# NOTE: These tests do NOT cover api_link/hello (which are unframed). These tests
# validate the link-layer framing used for schema-0 encrypted traffic.

from __future__ import annotations

import logging
import random

import pytest

from elke27_lib.framing import DeframeState, RxErrorType, STARTCHAR, deframe_feed, frame_build

STARTCHAR = 0x7E


def _collect_ok_frames(results):
    return [r.frame_no_crc for r in results if getattr(r, "ok", False) and getattr(r, "frame_no_crc", None)]


def test_frame_round_trip_single_chunk():
    state = DeframeState()
    protocol = 0x80
    data_frame = b"\x01\x02\x03\x04\x05\x06\x07\x08"

    framed = frame_build(protocol_byte=protocol, data_frame=data_frame)

    results = deframe_feed(state, framed)
    frames = _collect_ok_frames(results)

    assert len(frames) == 1
    frame_no_crc = frames[0]

    # frame_no_crc keeps protocol + length + data (CRC removed)
    assert frame_no_crc[0] == protocol
    assert frame_no_crc[3:] == data_frame


def test_frame_split_across_chunks():
    state = DeframeState()
    protocol = 0x80
    data_frame = b"0123456789ABCDEF"

    framed = frame_build(protocol_byte=protocol, data_frame=data_frame)

    # Split mid-stream (arbitrary)
    a = framed[:7]
    b = framed[7:]

    r1 = deframe_feed(state, a)
    assert _collect_ok_frames(r1) == []

    r2 = deframe_feed(state, b)
    frames = _collect_ok_frames(r2)

    assert len(frames) == 1
    frame_no_crc = frames[0]
    assert frame_no_crc[0] == protocol
    assert frame_no_crc[3:] == data_frame


def test_frame_split_across_three_chunks():
    state = DeframeState()
    protocol = 0x80
    data_frame = b"ABCDEFGHIJKLMNOPQRSTUVWXYZ"

    framed = frame_build(protocol_byte=protocol, data_frame=data_frame)

    a = framed[:3]
    b = framed[3:11]
    c = framed[11:]

    assert _collect_ok_frames(deframe_feed(state, a)) == []
    assert _collect_ok_frames(deframe_feed(state, b)) == []
    frames = _collect_ok_frames(deframe_feed(state, c))

    assert len(frames) == 1
    frame_no_crc = frames[0]
    assert frame_no_crc[0] == protocol
    assert frame_no_crc[3:] == data_frame


def test_multiple_frames_in_one_chunk():
    state = DeframeState()

    framed1 = frame_build(protocol_byte=0x80, data_frame=b"AAA")
    framed2 = frame_build(protocol_byte=0x81, data_frame=b"BBBBBBBB")
    framed3 = frame_build(protocol_byte=0x82, data_frame=b"")

    combined = framed1 + framed2 + framed3
    results = deframe_feed(state, combined)
    frames = _collect_ok_frames(results)

    assert len(frames) == 3
    assert frames[0][0] == 0x80 and frames[0][3:] == b"AAA"
    assert frames[1][0] == 0x81 and frames[1][3:] == b"BBBBBBBB"
    assert frames[2][0] == 0x82 and frames[2][3:] == b""


def test_multiple_frames_across_chunks_with_boundary_split():
    state = DeframeState()

    framed1 = frame_build(protocol_byte=0x80, data_frame=b"FIRST")
    framed2 = frame_build(protocol_byte=0x81, data_frame=b"SECOND")

    combined = framed1 + framed2
    a = combined[:9]
    b = combined[9:25]
    c = combined[25:]

    assert _collect_ok_frames(deframe_feed(state, a)) == []
    frames_b = _collect_ok_frames(deframe_feed(state, b))
    frames_c = _collect_ok_frames(deframe_feed(state, c))
    frames = frames_b + frames_c

    assert any(f[0] == 0x80 and f[3:] == b"FIRST" for f in frames)
    assert any(f[0] == 0x81 and f[3:] == b"SECOND" for f in frames)


def test_frame_with_escaped_startchar_split_across_chunks():
    state = DeframeState()
    payload = bytes([0x10, STARTCHAR, 0x20, 0x30])
    framed = frame_build(protocol_byte=0x81, data_frame=payload)

    escape_idx = framed.find(bytes([STARTCHAR, 0x00]))
    assert escape_idx != -1
    a = framed[: escape_idx + 1]
    b = framed[escape_idx + 1 :]

    assert _collect_ok_frames(deframe_feed(state, a)) == []
    frames = _collect_ok_frames(deframe_feed(state, b))
    assert len(frames) == 1
    assert frames[0][0] == 0x81
    assert frames[0][3:] == payload


def test_escape_sequence_round_trip_contains_startchar_in_payload():
    """
    If the payload contains 0x7E, the framer must escape it as 0x7E 0x00.
    The deframer must restore it.
    """
    state = DeframeState()
    protocol = 0x80

    # Put STARTCHAR in the data_frame so it must be escaped
    data_frame = bytes([0x11, 0x22, STARTCHAR, 0x33, 0x44])

    framed = frame_build(protocol_byte=protocol, data_frame=data_frame)

    # Ensure the wire representation includes an escape sequence (heuristic)
    assert bytes([STARTCHAR, 0x00]) in framed

    results = deframe_feed(state, framed)
    frames = _collect_ok_frames(results)

    assert len(frames) == 1
    frame_no_crc = frames[0]
    assert frame_no_crc[0] == protocol
    assert frame_no_crc[3:] == data_frame


def test_resync_on_startchar_followed_by_nonzero_starts_new_frame():
    """
    Node-RED rule:
      STARTCHAR followed by non-zero is ALWAYS a new message, and that byte is the protocol byte.
    This test injects garbage, then STARTCHAR + protocol byte for a new valid frame.
    """
    state = DeframeState()

    good = frame_build(protocol_byte=0x80, data_frame=b"OK")
    assert good[0] == STARTCHAR

    # Create a stream where we start a frame, then inject a resync trigger mid-way:
    #   STARTCHAR, then a partial header, then another STARTCHAR and a non-zero protocol byte.
    # We simulate this by chopping a valid frame early and gluing a new valid frame.
    partial = good[:4]  # START + protocol + 2 bytes of length maybe incomplete
    # Now inject a resync trigger. We want STARTCHAR followed by non-zero protocol (0x81).
    # Then append a complete valid frame with protocol 0x81.
    new_good = frame_build(protocol_byte=0x81, data_frame=b"NEW")

    stream = partial + bytes([STARTCHAR, 0x81]) + new_good[1:]  # omit STARTCHAR from new_good (already injected)
    results = deframe_feed(state, stream)
    frames = _collect_ok_frames(results)

    # We should get at least the second frame; depending on implementation we may or may not get the first.
    assert any(f[0] == 0x81 and f[3:] == b"NEW" for f in frames)


def test_bad_crc_does_not_prevent_parsing_following_frame():
    """
    If a frame has a bad CRC, deframer should report an error but continue scanning for the next frame.
    """
    state = DeframeState()

    good1 = frame_build(protocol_byte=0x80, data_frame=b"FIRST")
    good2 = frame_build(protocol_byte=0x81, data_frame=b"SECOND")

    # Corrupt one byte in the framed bytes of good1 AFTER the STARTCHAR to break CRC.
    bad1 = bytearray(good1)
    assert bad1[0] == STARTCHAR
    if len(bad1) < 6:
        pytest.skip("Frame unexpectedly too short to corrupt safely")
    bad1[5] ^= 0xFF  # flip bits

    stream = bytes(bad1) + good2
    results = deframe_feed(state, stream)
    frames = _collect_ok_frames(results)

    assert any(f[0] == 0x81 and f[3:] == b"SECOND" for f in frames)


def test_wait_start_logs_once_per_sequence(caplog):
    state = DeframeState()
    caplog.set_level(logging.ERROR, logger="elke27_lib.framing")

    deframe_feed(state, b"no-start")
    deframe_feed(state, b"still-no-start")
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1

    framed = frame_build(protocol_byte=0x80, data_frame=b"OK")
    deframe_feed(state, framed)

    deframe_feed(state, b"again-no-start")
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 2


def test_resync_logs_once_per_sequence(caplog):
    state = DeframeState()
    caplog.set_level(logging.ERROR, logger="elke27_lib.framing")

    good = frame_build(protocol_byte=0x80, data_frame=b"OK")
    partial = good[:6]
    stream = partial + bytes([STARTCHAR, 0x81]) + good[1:]

    deframe_feed(state, stream)
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert any("deframe resync" in r.message for r in errors)

    caplog.clear()
    deframe_feed(state, bytes([STARTCHAR, 0x81]) + good[1:])
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) <= 1


def test_random_valid_chunks_no_framing_errors():
    rng = random.Random(123)
    state = DeframeState()

    frames = []
    for _ in range(5):
        protocol = rng.randint(0x80, 0x83)
        payload = bytes(rng.randint(0, 255) for _ in range(rng.randint(0, 40)))
        frames.append(frame_build(protocol_byte=protocol, data_frame=payload))

    stream = b"".join(frames)
    chunks = []
    idx = 0
    while idx < len(stream):
        step = rng.randint(1, 11)
        chunks.append(stream[idx : idx + step])
        idx += step

    results = []
    for chunk in chunks:
        results.extend(deframe_feed(state, chunk))

    framing_errors = [r for r in results if r.error == RxErrorType.FRAMING_ERROR]
    assert framing_errors == []
    assert len(_collect_ok_frames(results)) == len(frames)


def test_random_garbage_chunks_emit_framing_errors():
    rng = random.Random(456)
    state = DeframeState()

    garbage = bytes(rng.choice([b for b in range(0x01, 0x7F) if b != STARTCHAR]) for _ in range(50))
    chunks = []
    idx = 0
    while idx < len(garbage):
        step = rng.randint(1, 9)
        chunks.append(garbage[idx : idx + step])
        idx += step

    results = []
    for chunk in chunks:
        results.extend(deframe_feed(state, chunk))

    framing_errors = [r for r in results if r.error == RxErrorType.FRAMING_ERROR]
    assert framing_errors


def _chunk_bytes_random(rng, data: bytes, *, min_size: int, max_size: int) -> list[bytes]:
    chunks: list[bytes] = []
    idx = 0
    while idx < len(data):
        step = rng.randint(min_size, max_size)
        chunks.append(data[idx : idx + step])
        idx += step
    return chunks


def test_randomized_stream_with_garbage_gaps_recovers_frames():
    rng = random.Random(9001)
    state = DeframeState()

    frames = []
    for _ in range(6):
        protocol = rng.randint(0x80, 0x83)
        payload = bytes(rng.randint(0, 255) for _ in range(rng.randint(1, 30)))
        frames.append(frame_build(protocol_byte=protocol, data_frame=payload))

    # Insert garbage (no STARTCHAR) between frames to force resync.
    garbage = bytes(rng.choice([b for b in range(0x01, 0x7F) if b != STARTCHAR]) for _ in range(20))
    stream = b""
    for i, framed in enumerate(frames):
        stream += framed
        if i < len(frames) - 1:
            stream += garbage[: rng.randint(3, len(garbage))]

    results: list[object] = []
    for chunk in _chunk_bytes_random(rng, stream, min_size=1, max_size=9):
        results.extend(deframe_feed(state, chunk))

    ok_frames = _collect_ok_frames(results)
    framing_errors = [r for r in results if r.error == RxErrorType.FRAMING_ERROR]

    assert len(ok_frames) == len(frames)
    assert framing_errors


def test_randomized_chunks_with_escaped_payloads():
    rng = random.Random(4242)
    state = DeframeState()

    frames = []
    for i in range(5):
        protocol = 0x80 + i
        payload = bytearray(rng.randint(0, 255) for _ in range(rng.randint(5, 30)))
        payload[rng.randint(0, len(payload) - 1)] = STARTCHAR
        frames.append(frame_build(protocol_byte=protocol, data_frame=bytes(payload)))

    stream = b"".join(frames)
    results: list[object] = []
    for chunk in _chunk_bytes_random(rng, stream, min_size=1, max_size=7):
        results.extend(deframe_feed(state, chunk))

    ok_frames = _collect_ok_frames(results)
    assert len(ok_frames) == len(frames)


def test_randomized_split_escape_sequences_across_boundaries():
    rng = random.Random(777)
    state = DeframeState()

    payload = bytes([0x10, STARTCHAR, 0x20, STARTCHAR, 0x30, 0x40])
    framed = frame_build(protocol_byte=0x81, data_frame=payload)

    # Ensure escape sequences exist.
    assert bytes([STARTCHAR, 0x00]) in framed

    results = []
    for chunk in _chunk_bytes_random(rng, framed, min_size=1, max_size=2):
        results.extend(deframe_feed(state, chunk))

    ok_frames = _collect_ok_frames(results)
    assert any(f[0] == 0x81 and f[3:] == payload for f in ok_frames)


def test_wait_start_throttle_with_random_garbage_bursts(caplog):
    rng = random.Random(2222)
    state = DeframeState()
    caplog.set_level(logging.ERROR, logger="elke27_lib.framing")

    bursts = 6
    for _ in range(bursts):
        garbage = bytes(rng.choice([b for b in range(0x01, 0x7F) if b != STARTCHAR]) for _ in range(20))
        deframe_feed(state, garbage)
        deframe_feed(state, frame_build(protocol_byte=0x80, data_frame=b"OK"))

    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    # Each burst should log at most once due to throttling.
    assert len(errors) <= bursts


def test_randomized_interleaved_bad_crc_recovery():
    rng = random.Random(1337)
    state = DeframeState()

    frames = []
    for i in range(6):
        protocol = 0x80 + i
        payload = bytes(rng.randint(0, 255) for _ in range(rng.randint(1, 25)))
        frames.append(frame_build(protocol_byte=protocol, data_frame=payload))

    stream = b""
    for framed in frames:
        stream += framed
        # Inject a corrupted copy half the time.
        if rng.random() < 0.5:
            bad = bytearray(framed)
            if len(bad) > 6:
                bad[6] ^= 0xFF
                stream += bytes(bad)

    results = []
    for chunk in _chunk_bytes_random(rng, stream, min_size=1, max_size=8):
        results.extend(deframe_feed(state, chunk))

    ok_frames = _collect_ok_frames(results)
    bad_crc = [r for r in results if r.error == RxErrorType.BAD_CRC]

    assert len(ok_frames) >= len(frames)
    assert bad_crc


def test_truncated_frames_resync_and_throttle(caplog):
    rng = random.Random(31337)
    state = DeframeState()
    caplog.set_level(logging.ERROR, logger="elke27_lib.framing")

    good = frame_build(protocol_byte=0x80, data_frame=b"GOOD")
    stream = b""
    for _ in range(6):
        # Append a truncated frame (drop tail) then a valid one.
        cut = rng.randint(2, max(3, len(good) - 2))
        stream += good[:cut]
        stream += good

    results = []
    for chunk in _chunk_bytes_random(rng, stream, min_size=1, max_size=7):
        results.extend(deframe_feed(state, chunk))

    ok_frames = _collect_ok_frames(results)

    assert ok_frames
    # Resync path may log errors without emitting FRAMING_ERROR results.
    assert any("deframe resync" in r.message for r in caplog.records)
    # Throttle should keep error logs bounded across repeated truncations.
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) <= 6
