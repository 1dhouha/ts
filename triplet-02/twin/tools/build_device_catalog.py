"""Build the reconciled B-device/C-runtime catalog.

The runtime reads the generated JSON only; B's YAML remains the public source.
"""
from pathlib import Path
import json

import yaml


ROOT = Path(__file__).resolve().parents[1]
SEED = ROOT / "config" / "devices.seed.yaml"
OVERLAY = ROOT / "config" / "device_bindings.json"
PROPOSAL = ROOT / "src" / "city_live" / "data" / "sensors.json"
TARGET = ROOT / "src" / "city_live" / "data" / "device_catalog.json"

UNIT = {
    "water_level": "cm",
    "flow": "L/s",
    "pressure": "bar",
    "temperature": "°C",
    "traffic": "veh/min",
}
TYPE = {
    "water_level": "water_level",
    "flow": "flow",
    "pressure": "pressure",
    "temperature": "temperature",
    "traffic": "traffic_count",
}
COMMANDS = {
    "pump": ["start", "stop", "set_speed"],
    "valve": ["set_opening"],
    "signal": ["set_phase", "set_priority_corridor", "set_mode"],
    "feeder": ["open", "close", "connect", "disconnect"],
    "responder": ["dispatch", "release"],
}


def load_inputs():
    seed = yaml.safe_load(SEED.read_text(encoding="utf-8"))
    overlay = json.loads(OVERLAY.read_text(encoding="utf-8"))
    proposal = {
        row["sensor_id"]: row
        for row in json.loads(PROPOSAL.read_text(encoding="utf-8"))["sensors"]
    }
    return seed, overlay, proposal


def sensor_rows(seed, overlay, proposal, zones):
    rows, seen = [], set()
    for device in seed["devices"]:
        if device["kind"] != "sensor":
            continue
        sensor_id = device["device_id"]
        binding = overlay["sensors"].get(sensor_id)
        if binding is None:
            raise ValueError(f"Missing sensor binding: {sensor_id}")
        base = proposal[sensor_id]
        family = device["type"]
        if family not in UNIT:
            raise ValueError(f"Unsupported B sensor type: {family}")
        if base["type"] != TYPE[family] or base["unit"] != UNIT[family]:
            raise ValueError(f"Canonical sensor definition conflict: {sensor_id}")
        if device.get("unit") != base["unit"]:
            raise ValueError(f"Sensor unit conflict: {sensor_id}")
        if (list(device["normal_range"]) != base["normal_limits"]
                or [device["hard_min"], device["hard_max"]] != base["hard_limits"]):
            raise ValueError(f"Sensor limits conflict: {sensor_id}")
        source = base["source"]
        supported = source is not None
        if binding.get("source") != source:
            raise ValueError(f"Sensor source conflict: {sensor_id}")
        if bool(binding.get("supported", supported)) != supported:
            raise ValueError(f"Sensor supported conflict: {sensor_id}")
        if bool(binding.get("scenario_only", not supported)) != (not supported):
            raise ValueError(f"Sensor scenario_only conflict: {sensor_id}")
        rows.append({
            "sensor_id": sensor_id,
            "zone_id": device["zone_id"],
            "core_zone_id": zones[device["zone_id"]],
            "type": base["type"],
            "unit": base["unit"],
            "description": binding.get("description", base["description"]),
            "normal_limits": list(base["normal_limits"]),
            "hard_limits": list(base["hard_limits"]),
            "interval": binding.get("interval", base["interval"]),
            "linked_device_ids": list(device.get("linked_device_ids", [])),
            "backup_ids": list(device.get("backup_ids", [])),
            "source": source,
            "supported": supported,
            "scenario_only": not supported,
            "physical_id": binding.get("physical_id"),
            "aliases": list(binding.get("aliases", [])),
        })
        seen.add(sensor_id)
    extra = set(overlay["sensors"]) - seen
    if extra:
        raise ValueError(f"Binding for unknown sensor: {sorted(extra)}")
    return rows


def actuator_rows(seed, overlay, zones, core=False):
    rows = []
    if core:
        for item in overlay["core_actuators"]:
            family = item["family"]
            rows.append({
                "device_id": item["device_id"],
                "zone_id": item["zone_id"],
                "core_zone_id": zones[item["zone_id"]],
                "type": item["type"],
                "family": family,
                "effect_tag": item["effect_tag"],
                "target_id": item["target_id"],
                "supported": bool(item.get("supported", True)),
                "physical_id": item.get("physical_id"),
                "aliases": list(item.get("aliases", [])),
                "sensor_ids": list(item.get("sensor_ids", [])),
                "backup_ids": [],
                "commands": COMMANDS[family],
            })
        return rows
    sensors_by_target = {
        item["target_id"]: item.get("sensor_ids", [])
        for item in overlay["core_actuators"]
    }
    for device in seed["devices"]:
        if device["kind"] != "actuator":
            continue
        device_id = device["device_id"]
        binding = overlay["actuators"].get(device_id)
        if binding is None:
            raise ValueError(f"Missing actuator binding: {device_id}")
        family = binding["family"]
        rows.append({
            "device_id": device_id,
            "zone_id": device["zone_id"],
            "core_zone_id": zones[device["zone_id"]],
            "type": device["type"],
            "family": family,
            "effect_tag": binding["effect_tag"],
            "target_id": binding["target_id"],
            "supported": bool(binding["supported"]),
            "physical_id": binding.get("physical_id"),
            "aliases": list(binding.get("aliases", [])),
            "sensor_ids": list(
                sensors_by_target.get(binding.get("target_id"), [])
                if binding["supported"] else ()
            ),
            "backup_ids": list(device.get("backup_ids", [])),
            "commands": COMMANDS[family],
        })
    extra = set(overlay["actuators"]) - {row["device_id"] for row in rows}
    if extra:
        raise ValueError(f"Binding for unknown actuator: {sorted(extra)}")
    return rows


def validate(sensors, actuators):
    ids = [row["sensor_id"] for row in sensors] + [row["device_id"] for row in actuators]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate device IDs")
    for row in sensors:
        if row["unit"] != UNIT.get("traffic" if row["type"] == "traffic_count" else row["type"]):
            raise ValueError(f"Invalid unit: {row['sensor_id']}")
        if row["source"] is not None and row["source"] not in ("basin", "river", "surface"):
            if ":" not in row["source"]:
                raise ValueError(f"Invalid source: {row['sensor_id']}")
    for row in actuators:
        if row["supported"] and row["target_id"] is None:
            raise ValueError(f"Supported actuator has no target: {row['device_id']}")
        if len(row["sensor_ids"]) != len(set(row["sensor_ids"])):
            raise ValueError(f"Duplicate causal sensor: {row['device_id']}")
        if set(row["sensor_ids"]) - {sensor["sensor_id"] for sensor in sensors}:
            raise ValueError(f"Unknown causal sensor: {row['device_id']}")


def main():
    seed, overlay, proposal = load_inputs()
    zones = overlay["zones"]
    sensors = sensor_rows(seed, overlay, proposal, zones)
    actuators = actuator_rows(seed, overlay, zones) + actuator_rows(seed, overlay, zones, core=True)
    validate(sensors, actuators)
    payload = {
        "version": "b-city-device-catalog-1",
        "source": "config/devices.seed.yaml",
        "bindings": "config/device_bindings.json",
        "zones": zones,
        "sensors": sorted(sensors, key=lambda row: row["sensor_id"]),
        "actuators": sorted(actuators, key=lambda row: row["device_id"]),
    }
    TARGET.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {TARGET} ({len(sensors)} sensors, {len(actuators)} actuators)")


if __name__ == "__main__":
    main()
