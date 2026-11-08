"""Sensor transduction from supplied ground truth to reported telemetry."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import math
from typing import Any

from .faults import SensorFaultPipeline
from .registry import DeviceRegistry


SENSOR_FIELDS = (
    "sensor_id",
    "zone_id",
    "type",
    "unit",
    "timestamp",
    "sim_tick",
    "seq",
    "actual_value",
    "reported_value",
    "delta",
    "status",
    "fault",
    "available",
    "backup_ids",
    "linked_device_ids",
)


class SensorEngine:
    """Build the official 15-field reading without mutating city_core."""

    def __init__(self, registry: DeviceRegistry, seed: int = 0) -> None:
        self.registry = registry
        self._seed = seed
        self._pipelines: dict[str, SensorFaultPipeline] = {}
        for device in registry.list_devices():
            if device.get("kind") == "sensor":
                self._pipelines[device["device_id"]] = self._new_pipeline(
                    device["device_id"]
                )

    def inject_fault(
        self, sensor_id: str, fault_type: str, params: dict[str, Any]
    ) -> None:
        self._pipeline(sensor_id).set_fault(fault_type, params)

    def clear_fault(self, sensor_id: str) -> None:
        self._pipeline(sensor_id).clear_fault()

    def sample_sensor(
        self,
        sensor_id: str,
        ground_truth_value: float | None,
        sim_tick: int,
        seq_num: int,
    ) -> dict[str, Any]:
        if isinstance(sim_tick, bool) or not isinstance(sim_tick, int):
            raise TypeError("sim_tick must be an integer")
        if isinstance(seq_num, bool) or not isinstance(seq_num, int):
            raise TypeError("seq_num must be an integer")
        device = self.registry.get_device(sensor_id)
        if device.get("kind") != "sensor":
            raise ValueError(f"Device is not a sensor: {sensor_id}")

        actual = self._finite_or_none(ground_truth_value, "ground_truth_value")
        pipeline = self._pipeline(sensor_id)
        if device["available"]:
            reported, status = pipeline.apply(actual, sim_tick)
        else:
            reported, status = None, "OFFLINE"
        reported = self._clamp_and_round(reported, device)
        delta = (
            round(reported - actual, 2)
            if reported is not None and actual is not None
            else None
        )

        reading = {
            "sensor_id": sensor_id,
            "zone_id": device.get("zone_id"),
            "type": device.get("type"),
            "unit": device.get("unit"),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "sim_tick": sim_tick,
            "seq": seq_num,
            "actual_value": actual,
            "reported_value": reported,
            "delta": delta,
            "status": status,
            "fault": pipeline.fault_type,
            "available": bool(device["available"]),
            "backup_ids": list(device.get("backup_ids", [])),
            "linked_device_ids": list(device.get("linked_device_ids", [])),
        }
        if tuple(reading) != SENSOR_FIELDS:
            raise RuntimeError("Sensor reading fields do not match the contract")
        return reading

    def _pipeline(self, sensor_id: str) -> SensorFaultPipeline:
        pipeline = self._pipelines.get(sensor_id)
        if pipeline is None:
            device = self.registry.get_device(sensor_id)
            if device.get("kind") != "sensor":
                raise ValueError(f"Device is not a sensor: {sensor_id}")
            pipeline = self._new_pipeline(sensor_id)
            self._pipelines[sensor_id] = pipeline
        return pipeline

    def _new_pipeline(self, sensor_id: str) -> SensorFaultPipeline:
        digest = hashlib.sha256(sensor_id.encode("utf-8")).digest()
        sensor_seed = self._seed ^ int.from_bytes(digest[:8], "big")
        return SensorFaultPipeline(seed=sensor_seed)

    @staticmethod
    def _finite_or_none(value: float | None, name: str) -> float | None:
        if value is None:
            return None
        converted = float(value)
        if not math.isfinite(converted):
            raise ValueError(f"{name} must be finite or None")
        return converted

    @classmethod
    def _clamp_and_round(
        cls, value: float | None, device: dict[str, Any]
    ) -> float | None:
        converted = cls._finite_or_none(value, "reported_value")
        if converted is None:
            return None
        lower = float(device["hard_min"])
        upper = float(device["hard_max"])
        if lower > upper:
            raise ValueError(
                f"Invalid hard limits for sensor {device['device_id']}"
            )
        return round(max(lower, min(upper, converted)), 2)
