"""Tests for Elke27 temperature conversion helpers."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any
import unittest


def _load_temperature_module() -> Any:
    module_path = (
        Path(__file__).parents[1] / "custom_components" / "elke27" / "temperature.py"
    )
    spec = importlib.util.spec_from_file_location("elke27_temperature", module_path)
    if spec is None or spec.loader is None:
        msg = f"Could not load temperature module from {module_path}"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


temperature = _load_temperature_module()


class TemperatureTest(unittest.TestCase):
    """Test thermostat temperature conversions."""

    def test_setpoint_encoder_removed(self) -> None:
        """Setpoints are passed as degrees; elke27 sends whole degrees."""
        assert not hasattr(temperature, "encode_temperature_setpoint")  # noqa: S101

    def test_normalize_temperature_preserves_existing_read_behavior(self) -> None:
        """Normalize raw panel values without changing existing whole-degree reads."""
        cases = (
            (704, 70.4),
            (700, 70.0),
            (680, 68.0),
            (68, 68.0),
            (71.5, 71.5),
            (80, 80.0),
        )
        for raw_value, degrees in cases:
            with self.subTest(raw_value=raw_value):
                assert temperature.normalize_temperature(raw_value) == degrees  # noqa: S101
