"""Load declarative JSON scenarios and apply triggers at their simulation tick."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class ScenarioController:
    """Drive Twin sensor and actuator test actions from JSON trigger lists.

    A scenario has a top-level ``triggers`` array. Each trigger includes
    ``sim_tick``, ``action``, ``target_device_id``, and, as applicable,
    ``fault_type`` and ``parameters``. Supported actions are INJECT_FAULT,
    CLEAR_FAULT, SET_AVAILABLE, SET_RESTRICTED/QUARANTINE, and
    DISPATCH_COMMAND.
    """

    _ACTIONS = {
        "INJECT_FAULT",
        "CLEAR_FAULT",
        "SET_AVAILABLE",
        "SET_RESTRICTED",
        "QUARANTINE",
        "DISPATCH_COMMAND",
    }

    def __init__(
        self,
        sensor_engine: Any,
        actuator_engine: Any | None = None,
        registry: Any | None = None,
    ) -> None:
        self.sensor_engine = sensor_engine
        self.actuator_engine = actuator_engine
        self.registry = registry or getattr(sensor_engine, "registry", None)
        self.scenario: dict[str, Any] | None = None
        self._triggers: list[dict[str, Any]] = []
        self._executed: set[int] = set()

    def load_scenario(self, scenario_path: str | Path) -> dict[str, Any]:
        path = Path(scenario_path)
        with path.open("r", encoding="utf-8") as scenario_file:
            scenario = json.load(scenario_file)
        if not isinstance(scenario, dict):
            raise ValueError("Scenario JSON must contain an object")
        triggers = scenario.get("triggers")
        if not isinstance(triggers, list):
            raise ValueError("Scenario must contain a triggers array")

        validated: list[dict[str, Any]] = []
        for index, trigger in enumerate(triggers):
            if not isinstance(trigger, dict):
                raise ValueError(f"Trigger {index} must be an object")
            tick = trigger.get("sim_tick")
            if isinstance(tick, bool) or not isinstance(tick, int) or tick < 0:
                raise ValueError(
                    f"Trigger {index} sim_tick must be a non-negative integer"
                )
            action = trigger.get("action")
            if not isinstance(action, str) or action.upper() not in self._ACTIONS:
                raise ValueError(
                    f"Trigger {index} has unsupported action: {action!r}"
                )
            target = trigger.get("target_device_id")
            if not isinstance(target, str) or not target:
                raise ValueError(
                    f"Trigger {index} requires target_device_id"
                )
            parameters = trigger.get("parameters", {})
            if not isinstance(parameters, dict):
                raise ValueError(
                    f"Trigger {index} parameters must be an object"
                )
            if action.upper() == "INJECT_FAULT":
                if not isinstance(trigger.get("fault_type"), str):
                    raise ValueError(
                        f"Trigger {index} INJECT_FAULT requires fault_type"
                    )
            validated.append(dict(trigger))

        self.scenario = scenario
        self._triggers = validated
        self._executed.clear()
        return dict(scenario)

    def on_tick(self, sim_tick: int) -> list[dict[str, Any]]:
        """Execute each ordinary trigger exactly once when its tick is reached."""
        if self.scenario is None:
            raise RuntimeError("Load a scenario before processing ticks")
        if isinstance(sim_tick, bool) or not isinstance(sim_tick, int):
            raise TypeError("sim_tick must be an integer")
        if sim_tick < 0:
            raise ValueError("sim_tick must be non-negative")

        results = []
        for index, trigger in enumerate(self._triggers):
            if index in self._executed:
                continue
            if trigger.get("after_scenario", False):
                continue
            if trigger["sim_tick"] != sim_tick:
                continue
            results.append(self._execute(index, trigger))
        return results

    def trigger(self, sim_tick: int) -> list[dict[str, Any]]:
        """Alias for :meth:`on_tick`."""
        return self.on_tick(sim_tick)

    def complete(self) -> list[dict[str, Any]]:
        """Apply triggers explicitly marked ``after_scenario``."""
        if self.scenario is None:
            raise RuntimeError("Load a scenario before completing it")
        results = []
        for index, trigger in enumerate(self._triggers):
            if index in self._executed or not trigger.get("after_scenario", False):
                continue
            results.append(self._execute(index, trigger))
        return results

    def _execute(
        self, index: int, trigger: dict[str, Any]
    ) -> dict[str, Any]:
        action = trigger["action"].upper()
        device_id = trigger["target_device_id"]
        parameters = trigger.get("parameters", {})

        if action == "INJECT_FAULT":
            engine = self._engine_for(device_id)
            engine.inject_fault(device_id, trigger["fault_type"], parameters)
            result: Any = {
                "device_id": device_id,
                "fault_type": trigger["fault_type"].upper(),
                "parameters": dict(parameters),
            }
        elif action == "CLEAR_FAULT":
            engine = self._engine_for(device_id)
            engine.clear_fault(device_id)
            result = {"device_id": device_id, "fault_cleared": True}
        elif action in {"SET_RESTRICTED", "QUARANTINE"}:
            if self.registry is None:
                raise RuntimeError(
                    "SET_RESTRICTED requires a device registry"
                )
            restricted_value = (
                True
                if action == "QUARANTINE"
                else trigger.get(
                    "restricted", parameters.get("restricted", True)
                )
            )
            if not isinstance(restricted_value, bool):
                raise TypeError("restricted must be a boolean")
            restricted = restricted_value
            reason = str(
                trigger.get("reason", parameters.get("reason", ""))
            )
            self.registry.set_restricted(device_id, restricted, reason)
            result = {
                "device_id": device_id,
                "restricted": restricted,
                "reason": reason if restricted else "",
            }
        elif action == "SET_AVAILABLE":
            if self.registry is None:
                raise RuntimeError("SET_AVAILABLE requires a device registry")
            available_value = trigger.get("available", parameters.get("available"))
            if available_value is None:
                raise ValueError("SET_AVAILABLE requires an available boolean")
            available = available_value
            if not isinstance(available, bool):
                raise TypeError("available must be a boolean")
            self.registry.set_available(device_id, available)
            result = {"device_id": device_id, "available": available}
        else:
            if self.actuator_engine is None:
                raise RuntimeError(
                    "DISPATCH_COMMAND requires an actuator engine"
                )
            command = trigger.get("command")
            request_id = trigger.get(
                "request_id",
                f"{self.scenario.get('scenario_id', self.scenario.get('ID', 'scenario'))}-{index}",
            )
            if not isinstance(command, str) or not command:
                raise ValueError(
                    f"Trigger {index} DISPATCH_COMMAND requires command"
                )
            result = self.actuator_engine.dispatch_command(
                device_id,
                command,
                parameters,
                str(request_id),
            )

        self._executed.add(index)
        return {
            "sim_tick": trigger["sim_tick"],
            "action": action,
            "target_device_id": device_id,
            "result": result,
        }

    def _engine_for(self, device_id: str) -> Any:
        if self.registry is None:
            raise RuntimeError("Fault actions require a device registry")
        device = self.registry.get_device(device_id)
        kind = device.get("kind")
        if kind == "sensor":
            return self.sensor_engine
        if kind == "actuator" and self.actuator_engine is not None:
            return self.actuator_engine
        raise ValueError(f"No fault engine available for device: {device_id}")
