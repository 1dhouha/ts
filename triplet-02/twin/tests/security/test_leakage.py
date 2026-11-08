"""Security and anti-leakage test suite.

Mathematically verifies that private Ground Truth variables (actual_value, delta,
fault models) can NEVER escape through public projections to the Brain or Guardian.
"""
import os
import unittest

os.environ.setdefault("TWIN_ALLOW_INSECURE_DEV_TOKENS", "1")

from twin_interface.auth import Role, resolve_token, DEFAULT_TOKENS
from twin_interface.envelope import ErrorCode, error_envelope, success_envelope
from twin_interface.permissions import check_tool_permission, is_tool_permitted
from twin_interface.projections import (
    FORBIDDEN_TRUTH_FIELDS,
    project_event,
    project_reading,
    project_readings,
)


def contains_forbidden_keys(obj, forbidden_set):
    """Recursively search any dictionary or iterable for forbidden keys or strings."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in forbidden_set:
                return True, f"Forbidden key '{k}' found in dictionary"
            has_forbidden, reason = contains_forbidden_keys(v, forbidden_set)
            if has_forbidden:
                return True, reason
    elif isinstance(obj, (list, tuple, set)):
        for item in obj:
            has_forbidden, reason = contains_forbidden_keys(item, forbidden_set)
            if has_forbidden:
                return True, reason
    return False, None


class SecurityLeakageTests(unittest.TestCase):
    def setUp(self):
        # A simulated raw sensor reading that contains private Ground Truth facts
        self.raw_reading = {
            "sensor_id": "PRT-WLV-01",
            "zone_id": "PRT",
            "type": "water_level",
            "unit": "cm",
            "sim_tick": 45,
            "seq": 1024,
            "timestamp": "2026-10-23T12:00:00Z",
            "reported_value": 180.0,
            "status": "OK",
            "available": True,
            "backup_ids": ["PRT-WLV-02"],
            "linked_device_ids": ["PRT-PMP-01"],
            # --- FORBIDDEN GROUND TRUTH SECRETS ---
            "actual_value": 40.0,
            "delta": 140.0,
            "fault": "FALSE_READING",
            "drift_rate": 0.5,
            "noise": 0.05,
            "bias": 140.0,
            "ground_truth": {"actual_storage_m3": 12000.0},
            "unmodeled_secret": "classified",
        }

    def test_brain_projection_never_leaks_ground_truth(self):
        """Verify that Brain role projection completely strips all forbidden secrets."""
        projected = project_reading(self.raw_reading, Role.BRAIN)

        # 1. Assert public fields are preserved
        self.assertEqual(projected["sensor_id"], "PRT-WLV-01")
        self.assertEqual(projected["reported_value"], 180.0)
        self.assertEqual(projected["status"], "OK")

        # 2. Assert zero forbidden fields exist in the output
        has_leak, reason = contains_forbidden_keys(projected, FORBIDDEN_TRUTH_FIELDS)
        self.assertFalse(has_leak, f"Leak detected for Brain: {reason}")

        # 3. Explicit check for common forbidden keys
        for key in ("actual_value", "delta", "fault", "noise", "ground_truth", "unmodeled_secret"):
            self.assertNotIn(key, projected)

    def test_guardian_projection_never_leaks_ground_truth(self):
        """Verify that Guardian role projection completely strips all forbidden secrets."""
        projected = project_reading(self.raw_reading, Role.GUARDIAN)

        self.assertEqual(projected["sensor_id"], "PRT-WLV-01")
        self.assertEqual(projected["reported_value"], 180.0)

        has_leak, reason = contains_forbidden_keys(projected, FORBIDDEN_TRUTH_FIELDS)
        self.assertFalse(has_leak, f"Leak detected for Guardian: {reason}")

    def test_evaluator_role_preserves_full_ground_truth(self):
        """Verify that the Evaluator / Jury role receives the full ground truth for verification."""
        projected = project_reading(self.raw_reading, Role.EVALUATOR)

        self.assertEqual(projected["actual_value"], 40.0)
        self.assertEqual(projected["delta"], 140.0)
        self.assertEqual(projected["fault"], "FALSE_READING")

    def test_project_readings_list(self):
        """Verify batch projection across a list of readings."""
        batch = [self.raw_reading, {**self.raw_reading, "sensor_id": "PRT-WLV-02", "reported_value": 78.0}]
        projected_batch = project_readings(batch, Role.BRAIN)

        self.assertEqual(len(projected_batch), 2)
        for reading in projected_batch:
            has_leak, reason = contains_forbidden_keys(reading, FORBIDDEN_TRUTH_FIELDS)
            self.assertFalse(has_leak, reason)

    def test_project_event_sanitization(self):
        """Verify that public event logs strip ground truth details."""
        event = {
            "seq": 50,
            "type": "FAULT_ACTIVATED",
            "details": {
                "sensor_id": "PRT-WLV-01",
                "fault": "DRIFT",
                "drift_rate": 0.5,
            },
        }
        event["payload"] = {
            "reading": {"sensor_id": "PRT-WLV-01", "actual_value": 140.0},
            "nested": [{"fault": "DRIFT", "safe": True}],
        }
        sanitized = project_event(event, Role.BRAIN)
        self.assertNotIn("fault", sanitized["details"])
        self.assertNotIn("drift_rate", sanitized["details"])
        self.assertEqual(sanitized["details"]["sensor_id"], "PRT-WLV-01")
        serialized = str(sanitized)
        for forbidden in FORBIDDEN_TRUTH_FIELDS:
            self.assertNotIn(f'"{forbidden}"', serialized)
        self.assertTrue(sanitized["payload"]["nested"][0]["safe"])


class AuthAndPermissionsTests(unittest.TestCase):
    def test_token_resolution(self):
        """Verify bearer token resolution across all roles."""
        self.assertEqual(resolve_token(DEFAULT_TOKENS[Role.BRAIN]), Role.BRAIN)
        self.assertEqual(resolve_token(f"Bearer {DEFAULT_TOKENS[Role.GUARDIAN]}"), Role.GUARDIAN)
        self.assertEqual(resolve_token(DEFAULT_TOKENS[Role.OPERATOR]), Role.OPERATOR)
        self.assertEqual(resolve_token(f"Bearer {DEFAULT_TOKENS[Role.EVALUATOR]}"), Role.EVALUATOR)

        # Invalid tokens
        self.assertIsNone(resolve_token("invalid-token"))
        self.assertIsNone(resolve_token(None))
        self.assertIsNone(resolve_token(""))

    def test_permission_matrix(self):
        """Verify that permissions strictly enforce actor separation."""
        # Brain can actuate but cannot isolate
        self.assertTrue(is_tool_permitted("send_actuator_command", Role.BRAIN))
        self.assertFalse(is_tool_permitted("isolate_device", Role.BRAIN))
        self.assertFalse(is_tool_permitted("get_ground_truth", Role.BRAIN))

        # Guardian can isolate but cannot actuate
        self.assertTrue(is_tool_permitted("isolate_device", Role.GUARDIAN))
        self.assertFalse(is_tool_permitted("send_actuator_command", Role.GUARDIAN))
        self.assertFalse(is_tool_permitted("get_ground_truth", Role.GUARDIAN))

        # Both can read telemetry
        self.assertTrue(is_tool_permitted("get_sensor_readings", Role.BRAIN))
        self.assertTrue(is_tool_permitted("get_sensor_readings", Role.GUARDIAN))
        self.assertTrue(is_tool_permitted("get_city_state", Role.BRAIN))

        # Evaluator can see ground truth
        self.assertTrue(is_tool_permitted("get_ground_truth", Role.EVALUATOR))

    def test_check_tool_permission_diagnostics(self):
        """Verify informative error messages when unauthorized."""
        ok, msg = check_tool_permission("isolate_device", Role.BRAIN)
        self.assertFalse(ok)
        self.assertIn("not authorized", msg)

        ok, msg = check_tool_permission("send_actuator_command", None)
        self.assertFalse(ok)
        self.assertIn("Missing or invalid", msg)


class EnvelopeTests(unittest.TestCase):
    def test_success_envelope_structure(self):
        """Verify success envelope conforms to standard format."""
        envelope = success_envelope("run-001", {"reading": 10.5})
        self.assertTrue(envelope["ok"])
        self.assertEqual(envelope["run_id"], "run-001")
        self.assertEqual(envelope["data"], {"reading": 10.5})
        self.assertIsNone(envelope["error"])
        self.assertIn("timestamp", envelope)

    def test_error_envelope_structure(self):
        """Verify error envelope conforms to standard format."""
        envelope = error_envelope("run-001", ErrorCode.GUARDIAN_RESTRICTED, "Device quarantined", details={"device": "V-12"})
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["run_id"], "run-001")
        self.assertIsNone(envelope["data"])
        self.assertEqual(envelope["error"]["code"], "GUARDIAN_RESTRICTED")
        self.assertEqual(envelope["error"]["details"]["device"], "V-12")


if __name__ == "__main__":
    unittest.main()
