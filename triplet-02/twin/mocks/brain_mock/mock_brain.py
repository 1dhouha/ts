"""Autonomous Brain mock client.

The mock behaves like an external decision engine: it polls public water-level
telemetry through the Façade and dispatches the linked pump when a sensor
crosses the flood threshold. Commands are idempotent per sensor/device episode
and are re-armed only after the level falls below a hysteresis band.

Run the full standalone proof with::

    python tools/run_standalone.py
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from twin_interface.auth import Role, get_token_for_role

DEFAULT_FLOOD_THRESHOLD_CM = 80.0
DEFAULT_HYSTERESIS_CM = 5.0


@dataclass(frozen=True, slots=True)
class BrainAction:
    """One pump dispatch issued by the mock."""

    sensor_id: str
    device_id: str
    water_level_cm: float
    request_id: str
    response: dict[str, Any]


@dataclass
class BrainMock:
    """Dispatch a linked pump whenever water level exceeds the flood threshold."""

    facade: Any
    token: str | None = None
    flood_threshold_cm: float = DEFAULT_FLOOD_THRESHOLD_CM
    hysteresis_cm: float = DEFAULT_HYSTERESIS_CM
    _dispatched: set[tuple[str, str]] = field(default_factory=set)
    _attempted: set[tuple[str, str]] = field(default_factory=set)

    def __post_init__(self) -> None:
        if self.token is None:
            self.token = get_token_for_role(Role.BRAIN)
        if not self.token:
            raise ValueError(
                "No Brain token configured. Set TWIN_TOKEN_BRAIN or "
                "TWIN_ALLOW_INSECURE_DEV_TOKENS=1 for local development."
            )

    @staticmethod
    def _usable(reading: dict[str, Any] | None) -> bool:
        return bool(
            reading
            and reading.get("available")
            and reading.get("status") == "OK"
            and isinstance(reading.get("reported_value"), (int, float))
        )

    def tick(self) -> list[BrainAction]:
        """Run one decision cycle and return every command sent."""
        response = self.facade.get_sensor_readings(
            token=self.token, sensor_type="water_level"
        )
        if not response.get("ok"):
            return []

        actions: list[BrainAction] = []
        for reading in sorted(response["data"]["readings"], key=lambda row: row["sensor_id"]):
            if not self._usable(reading):
                continue

            level = float(reading["reported_value"])
            sensor_id = reading["sensor_id"]

            if level < self.flood_threshold_cm - self.hysteresis_cm:
                for key in [key for key in self._attempted if key[0] == sensor_id]:
                    self._attempted.discard(key)
                    self._dispatched.discard(key)
                continue

            if level <= self.flood_threshold_cm:
                continue

            for device_id in reading.get("linked_device_ids") or ():
                key = (sensor_id, device_id)
                if key in self._attempted:
                    continue

                request_id = f"brain-flood-{sensor_id}-{device_id}"
                command = self.facade.send_actuator_command(
                    token=self.token,
                    device_id=device_id,
                    command="start",
                    params={"speed_pct": 100},
                    request_id=request_id,
                    context={
                        "decision": "FLOOD_MITIGATION",
                        "sensor_id": sensor_id,
                        "water_level_cm": round(level, 2),
                    },
                )
                self._attempted.add(key)
                if command.get("ok"):
                    self._dispatched.add(key)
                    actions.append(
                        BrainAction(
                            sensor_id=sensor_id,
                            device_id=device_id,
                            water_level_cm=round(level, 2),
                            request_id=request_id,
                            response=command,
                        )
                    )

        return actions
