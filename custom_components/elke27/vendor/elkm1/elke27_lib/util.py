"""Utility functions"""

from __future__ import annotations

from typing import Union
from urllib.parse import urlparse


def pretty_const(value: str) -> str:
    """Make a constant pretty for printing in GUI"""
    words = value.split("_")
    pretty = words[0].capitalize()
    for word in words[1:]:
        pretty += " " + word.lower()
    return pretty


def url_scheme_is_secure(url_or_scheme: str) -> bool:
    scheme = url_or_scheme.split("://", 1)[0]
    return scheme == "elks"


def parse_url(url: str) -> tuple[str, str, int, None]:
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    if scheme not in {"elk", "elks"}:
        raise ValueError(f"Invalid scheme: {scheme}")
    if not parsed.hostname:
        raise ValueError("Invalid host")
    normalized_scheme = "elks" if scheme == "elks" else "elk"
    default_port = 2601 if normalized_scheme == "elks" else 2101
    port = parsed.port or default_port
    return normalized_scheme, parsed.hostname, port, None


def calculate_crc16_checksum(w_sum: int, data_bytes: bytes | bytearray, start: int, numb: int) -> int:
    """
    CRC-16 (polynomial 0xA001, standard reflected CRC-16)

    :param w_sum: Initial CRC value
    :param data_bytes: Byte buffer (bytes or bytearray)
    :param start: Starting index
    :param numb: Number of bytes to process
    :return: 16-bit CRC value
    """
    w_sum &= 0xFFFF

    for i in range(start, start + numb):
        data = data_bytes[i] & 0xFF

        for _ in range(8):
            xor_flag = (w_sum & 1) ^ (data & 1)
            w_sum >>= 1
            if xor_flag:
                w_sum ^= 0xA001
            data >>= 1

    return w_sum & 0xFFFF

#def swap_endianness(src: Union[bytes, bytearray, list[int]]) -> bytearray:
def swap_endianness(src: Union[bytes, bytearray, list[int]]) -> bytes:
    """
    Swaps the endianness of 32-bit words in a byte array.
    Processes the input in 4-byte chunks, reversing the order of bytes within each chunk.

    Raises:
        ValueError:
            - if src is None
            - if src is empty
            - if length of src is not evenly divisible by 4
    """
    if src is None:
        raise ValueError("swap_endianness: src is None")

    length = len(src)
    if length == 0:
        raise ValueError("swap_endianness: src is empty")

    if length % 4 != 0:
        raise ValueError(
            f"swap_endianness: length {length} is not divisible by 4"
        )

    # Normalize input to bytes-like
    data = src if isinstance(src, (bytes, bytearray)) else bytes(src)

    result = bytearray(length)

    for index in range(0, length, 4):
        result[index + 0] = data[index + 3]
        result[index + 1] = data[index + 2]
        result[index + 2] = data[index + 1]
        result[index + 3] = data[index + 0]

    return bytes(result)

def calculate_block_padding(length: int, block_size: int = 16) -> int:
    """
    Equivalent to JS:
        (16 - (length % 16)) & 15

    Returns the number of padding bytes needed to reach the next block boundary,
    in the range 0..block_size-1.

    Hard-fails if length is negative.
    """
    if length < 0:
        raise ValueError(f"calculate_block_padding: length must be >= 0 (got {length})")
    if block_size <= 0:
        raise ValueError(f"calculate_block_padding: block_size must be > 0 (got {block_size})")

    # For block_size=16, this matches the JS exactly:
    return (block_size - (length % block_size)) % block_size
