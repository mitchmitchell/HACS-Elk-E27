"""Temperature helpers for the Elke27 integration."""

from __future__ import annotations

from typing import Any

_IMPLIED_DECIMAL_TEMP_THRESHOLD = 200


def normalize_temperature(value: Any) -> float | None:
    """Normalize thermostat temperatures to display units."""
    # Temporary until elke27_lib ships py.typed (strict mypy treats fields as Any).
    if not isinstance(value, int | float):
        return None
    # elke27 0.3.8+ already scales values by the panel "prec" mask. Values this
    # large are setpoints left as tenths by HACS 0.1.4 (e.g. 680 for 68 F); show
    # them as degrees until the next setpoint write stores whole degrees again.
    if abs(value) >= _IMPLIED_DECIMAL_TEMP_THRESHOLD:
        return float(value) / 10.0
    return float(value)
