from __future__ import annotations

from elke27_lib.client import Elke27Client


def test_kernel_next_seq_wraps() -> None:
    client = Elke27Client()
    kernel = client._kernel
    kernel._seq = 2_147_483_647
    assert kernel._next_seq() == 2_147_483_647
    assert kernel._next_seq() == 10
    assert kernel._next_seq() == 11
