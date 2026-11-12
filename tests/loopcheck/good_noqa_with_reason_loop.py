"""Loop module with a reasoned suppression."""

import time


def legacy() -> None:
    time.sleep(0)  # noqa: ELK101 -- zero sleep yields only in tests
