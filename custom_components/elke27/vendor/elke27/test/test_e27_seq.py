from __future__ import annotations

from elke27_lib.client import Elke27Client
from test.helpers.internal import get_kernel, set_private


def test_kernel_next_seq_wraps() -> None:
    client = Elke27Client()
    kernel = get_kernel(client)
    set_private(kernel, "_seq", 2147483647)
    assert kernel.next_seq() == 2_147_483_647
    assert kernel.next_seq() == 10
    assert kernel.next_seq() == 11
