"""Seedable sensor telemetry fault behaviors."""

from __future__ import annotations

import math
import random
from typing import Any


class SensorFaultPipeline:
    """Apply one configured fault to a sensor's actual values."""

    _FAULTS = frozenset(
        {
            "FALSE_READING",
            "STUCK",
            "DRIFT",
            "NOISE",
            "DELAYED",
            "DROPOUT",
            "DEAD",
        }
    )

    def __init__(self, seed: int | None = None) -> None:
        self._rng = random.Random(seed)
        self._fault_type: str | None = None
        self._params: dict[str, Any] = {}
        self._last_reported: float | None = None
        self._stuck_value: float | None = None
        self._drift_offset = 0.0
        self._delayed_values: dict[int, float] = {}

    @property
    def fault_type(self) -> str | None:
        return self._fault_type

    def set_fault(self, fault_type: str, params: dict[str, Any]) -> None:
        if not isinstance(fault_type, str):
            raise TypeError("fault_type must be a string")
        if not isinstance(params, dict):
            raise TypeError("fault parameters must be a mapping")
        normalized = fault_type.upper()
        if normalized not in self._FAULTS:
            raise ValueError(f"Unsupported sensor fault: {fault_type}")

        if normalized == "FALSE_READING":
            if "fixed_value" in params and params["fixed_value"] is not None:
                value = float(params["fixed_value"])
                if not math.isfinite(value):
                    raise ValueError("FALSE_READING fixed_value must be finite")
            if "offset" in params:
                value = float(params["offset"])
                if not math.isfinite(value):
                    raise ValueError("FALSE_READING offset must be finite")
        elif normalized == "DRIFT":
            rate = float(params.get("rate_per_tick", 0.0))
            if not math.isfinite(rate):
                raise ValueError("DRIFT rate_per_tick must be finite")
        elif normalized == "NOISE":
            amplitude = float(params.get("amplitude", 0.0))
            if not math.isfinite(amplitude) or amplitude < 0:
                raise ValueError("NOISE amplitude must be non-negative")
        elif normalized == "DELAYED":
            lag_ticks = int(params.get("lag_ticks", 1))
            if lag_ticks < 0:
                raise ValueError("DELAYED lag_ticks must be non-negative")

        self._fault_type = normalized
        self._params = dict(params)
        self._stuck_value = self._last_reported
        self._drift_offset = 0.0

    def clear_fault(self) -> None:
        self._fault_type = None
        self._params = {}
        self._stuck_value = None
        self._drift_offset = 0.0

    def apply(
        self, actual_value: float | None, sim_tick: int
    ) -> tuple[float | None, str]:
        if isinstance(sim_tick, bool) or not isinstance(sim_tick, int):
            raise TypeError("sim_tick must be an integer")

        actual = float(actual_value) if actual_value is not None else None
        if actual is not None:
            self._delayed_values[sim_tick] = actual

        fault_type = self._fault_type
        status = "OK"
        if fault_type is None:
            reported = actual
        elif fault_type == "FALSE_READING":
            fixed_value = self._params.get("fixed_value")
            if fixed_value is not None:
                reported = float(fixed_value)
            elif actual is not None:
                reported = actual + float(self._params.get("offset", 0.0))
            else:
                reported = None
        elif fault_type == "STUCK":
            reported = self._stuck_value
        elif fault_type == "DRIFT":
            self._drift_offset += float(
                self._params.get("rate_per_tick", 0.0)
            )
            reported = (
                actual + self._drift_offset if actual is not None else None
            )
        elif fault_type == "NOISE":
            amplitude = abs(float(self._params.get("amplitude", 0.0)))
            reported = (
                actual + self._rng.gauss(0.0, amplitude)
                if actual is not None
                else None
            )
        elif fault_type == "DELAYED":
            lag_ticks = int(self._params.get("lag_ticks", 1))
            if lag_ticks < 0:
                raise ValueError("DELAYED lag_ticks must be non-negative")
            reported = self._delayed_values.get(sim_tick - lag_ticks)
            status = "DEGRADED"
        elif fault_type == "DROPOUT":
            reported = None
            status = "OFFLINE"
        else:
            reported = None
            status = "FAILED"

        if reported is not None:
            self._last_reported = float(reported)
        return reported, status
