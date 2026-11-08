"""Runtime registry for the simulated city's devices."""

from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml


class DeviceRegistry:
    def __init__(self, seed_path: str | None = None) -> None:
        self._seed_path: str | None = None
        self._seed_records: dict[str, dict[str, Any]] = {}
        self._records: dict[str, dict[str, Any]] = {}
        if seed_path is not None:
            self.load_seed(seed_path)

    def load_seed(self, seed_path: str) -> None:
        path = Path(seed_path)
        with path.open("r", encoding="utf-8") as seed_file:
            seed = yaml.safe_load(seed_file)
        if not isinstance(seed, dict) or not isinstance(seed.get("devices"), list):
            raise ValueError("Device seed must contain a devices list")

        records: dict[str, dict[str, Any]] = {}
        for raw_record in seed["devices"]:
            if not isinstance(raw_record, dict):
                raise ValueError("Every seeded device must be a mapping")
            device_id = raw_record.get("device_id")
            if not isinstance(device_id, str) or not device_id:
                raise ValueError("Every seeded device must have a device_id")
            if device_id in records:
                raise ValueError(f"Duplicate device_id in seed: {device_id}")
            record = deepcopy(raw_record)
            record.update(
                status="AVAILABLE",
                available=True,
                restricted=False,
                restriction_reason="",
                replaces=None,
            )
            records[device_id] = record

        self._seed_path = str(path)
        self._seed_records = deepcopy(records)
        self._records = records

    def get_device(self, device_id: str) -> dict[str, Any]:
        record = self._records.get(device_id)
        if record is None:
            raise KeyError(f"Unknown device: {device_id}")
        return self._with_status(record)

    def list_devices(self) -> list[dict[str, Any]]:
        return [self._with_status(record) for record in self._records.values()]

    def set_restricted(
        self, device_id: str, restricted: bool, reason: str = ""
    ) -> None:
        record = self._require_device(device_id)
        record["restricted"] = bool(restricted)
        record["restriction_reason"] = reason if restricted else ""

    def set_available(self, device_id: str, available: bool) -> None:
        self._require_device(device_id)["available"] = bool(available)

    def get_compatible_devices(self, device_id: str) -> list[dict[str, Any]]:
        source = self._require_device(device_id)
        backup_ids = source.get("backup_ids", [])
        compatible = []
        for backup_id in backup_ids:
            candidate = self._records.get(backup_id)
            if (
                candidate is not None
                and candidate.get("type") == source.get("type")
                and candidate.get("kind") == source.get("kind")
                and candidate["available"]
                and not candidate["restricted"]
            ):
                compatible.append(self._with_status(candidate))
        return compatible

    def replace_device(
        self, old_id: str, new_record: dict[str, Any]
    ) -> dict[str, Any]:
        old = self._require_device(old_id)
        replacement = deepcopy(new_record)
        new_id = replacement.get("device_id")
        if not isinstance(new_id, str) or not new_id:
            raise ValueError("Replacement must have a device_id")
        if new_id in self._records:
            raise ValueError(f"Device already registered: {new_id}")
        if replacement.get("type") != old.get("type"):
            raise ValueError("Replacement device must have the same type")
        replacement.update(
            status="AVAILABLE",
            available=True,
            restricted=False,
            restriction_reason="",
            replaces=old_id,
        )
        old["available"] = False
        self._records[new_id] = replacement
        return self._with_status(replacement)

    def reset(self) -> None:
        if self._seed_path is None:
            raise RuntimeError("Cannot reset registry before loading a seed")
        self._records = deepcopy(self._seed_records)

    def _require_device(self, device_id: str) -> dict[str, Any]:
        record = self._records.get(device_id)
        if record is None:
            raise KeyError(f"Unknown device: {device_id}")
        return record

    @staticmethod
    def _with_status(record: dict[str, Any]) -> dict[str, Any]:
        result = deepcopy(record)
        if result["restricted"]:
            result["status"] = "RESTRICTED"
        elif not result["available"]:
            result["status"] = "OFFLINE"
        else:
            result["status"] = "AVAILABLE"
        return result
