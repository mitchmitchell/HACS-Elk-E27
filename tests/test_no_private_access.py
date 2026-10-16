# ruff: noqa: S101
"""No elke27 private attribute is referenced from the integration (D4/D5)."""

from __future__ import annotations

from pathlib import Path
import re

SOURCE = Path(__file__).parent.parent / "custom_components" / "elke27"
PRIVATE = re.compile(r"""client\._|["']_raise_v2|getattr\([^)]*["']_""")


def test_no_client_private_access() -> None:
    """Nothing under custom_components reaches into client._ attributes."""
    hits = [
        f"{path.name}:{lineno}: {line.strip()}"
        for path in sorted(SOURCE.rglob("*.py"))
        for lineno, line in enumerate(path.read_text().splitlines(), 1)
        if PRIVATE.search(line)
    ]
    assert hits == []
