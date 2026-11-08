"""Autonomous Guardian mock client.

The mock behaves like an external partner watchdog: it only uses the public
Façade tools (``get_sensor_readings`` and ``isolate_device``) and never reads
private ground truth. When a water-level sensor disagrees with its declared
backup by more than the configured threshold, the sensor that moved furthest
since the previous sample is quarantined.

Run the full standalone proof with::

    python tools/run_standalone.py
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from twin_interface.auth import Role, get_token_for_role

DEFAULT_DISCREPANCY_THRESHOLD_CM = 50.0


@dataclass(frozen=True, slots=True)
class GuardianAction:
    """One quarantine decision taken by the mock."""

    device_id: str
    backup_id: str
    discrepancy_cm: float
    reason: str
    response: dict[str, Any]


@dataclass
class GuardianMock:
    """Compare redundant water-level sensors and isolate the outlier."""

    facade: Any
    token: str | None = None
    discrepancy_threshold_cm: float = DEFAULT_DISCREPANCY_THRESHOLD_CM
    _last_values: dict[str, float] = field(default_factory=dict)
    _isolated: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        if self.token is None:
            self.token = get_token_for_role(Role.GUARDIAN)
        if not self.token:
            raise ValueError(
                "No Guardian token configured. Set TWIN_TOKEN_GUARDIAN or "
                "TWIN_ALLOW_INSECURE_DEV_TOKENS=1 for local development."
            )

    def _readings(self) -> dict[str, dict[str, Any]]:
        response = self.facade.get_sensor_readings(
            token=self.token, sensor_type="water_level"
        )
        if not response.get("ok"):
            return {}
        return {row["sensor_id"]: row for row in response["data"]["readings"]}

    @staticmethod
    def _usable(reading: dict[str, Any] | None) -> bool:
        return bool(
            reading
            and reading.get("available")
            and reading.get("status") == "OK"
            and isinstance(reading.get("reported_value"), (int, float))
        )

    def _movement(self, sensor_id: str, value: float) -> float:
        previous = self._last_values.get(sensor_id)
        return abs(value - previous) if previous is not None else 0.0

    def scan(self) -> list[GuardianAction]:
        """Run one watchdog cycle and return every quarantine action taken."""
        readings = self._readings()
        actions: list[GuardianAction] = []

        for sensor_id in sorted(readings):
            reading = readings[sensor_id]
            if not self._usable(reading) or sensor_id in self._isolated:
                continue

            value = float(reading["reported_value"])
            for backup_id in reading.get("backup_ids") or ():
                if backup_id in self._isolated:
                    continue
                backup = readings.get(backup_id)
                if not self._usable(backup):
                    continue

                backup_value = float(backup["reported_value"])
                discrepancy = abs(value - backup_value)
                if discrepancy <= self.discrepancy_threshold_cm:
                    continue

                sensor_move = self._movement(sensor_id, value)
                backup_move = self._movement(backup_id, backup_value)
                if sensor_move > backup_move:
                    culprit = sensor_id
                elif backup_move > sensor_move:
                    culprit = backup_id
                else:
                    # No temporal evidence yet: a false-high reading is the
                    # usual failure mode for flood telemetry.
                    culprit = sensor_id if value > backup_value else backup_id

                if culprit in self._isolated:
                    continue

                other = backup_id if culprit == sensor_id else sensor_id
                reason = (
                    f"Discrepancy {discrepancy:.1f} cm against backup {other} "
                    f"exceeds {self.discrepancy_threshold_cm:.0f} cm threshold."
                )
                response = self.facade.isolate_device(
                    token=self.token, device_id=culprit, reason=reason
                )
                if response.get("ok"):
                    self._isolated.add(culprit)
                    actions.append(
                        GuardianAction(
                            device_id=culprit,
                            backup_id=other,
                            discrepancy_cm=round(discrepancy, 2),
                            reason=reason,
                            response=response,
                        )
                    )

        for sensor_id, reading in readings.items():
            if self._usable(reading):
                self._last_values[sensor_id] = float(reading["reported_value"])

        return actions

    def restore(self, device_id: str) -> dict[str, Any]:
        """Lift a quarantine previously applied by this mock."""
        response = self.facade.restore_device(token=self.token, device_id=device_id)
        if response.get("ok"):
            self._isolated.discard(device_id)
        return response
