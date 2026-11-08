from concurrent.futures import ThreadPoolExecutor
import unittest

from city_live.records import LiveError
from city_live.runtime import LiveCity


class CommandIdempotencyTests(unittest.TestCase):
    def setUp(self):
        self.city = LiveCity("idempotency-test")
        self.addCleanup(self.city.close)
        self.run_id = self.city.status()["run_id"]

    def execute(self, request_id, device_id="PRT-PMP-01", command="set_speed",
                params=None, *, run_id=None, expected_revision=0):
        return self.city.execute_actuator_command(
            device_id,
            command,
            {} if params is None else params,
            command_id=request_id,
            run_id=self.run_id if run_id is None else run_id,
            expected_revision=expected_revision,
        )

    def test_exact_duplicate_replays_original_receipt_without_second_effect(self):
        first = self.execute("same-request", params={"speed_pct": 30})
        duplicate = self.execute("same-request", params={"speed_pct": 30})

        self.assertEqual(duplicate, first)
        self.assertEqual(self.city.status()["revision"], 1)
        self.assertEqual(self.city._effect_counter, 1)
        self.assertEqual(
            self.city.diagnostics()["physical_state"]["pump"][
                "effective_speed_fraction"
            ],
            0.3,
        )

    def test_request_id_conflicts_on_params_command_device_and_context(self):
        cases = (
            ("params", "PRT-PMP-01", "set_speed", {"speed_pct": 70}, self.run_id, 0),
            ("command", "PRT-PMP-01", "stop", {}, self.run_id, 0),
            ("device", "PRT-PMP-02", "set_speed", {"speed_pct": 30}, self.run_id, 0),
            ("run", "PRT-PMP-01", "set_speed", {"speed_pct": 30}, "other-run", 0),
            ("revision", "PRT-PMP-01", "set_speed", {"speed_pct": 30}, self.run_id, 1),
        )
        for label, device_id, command, params, run_id, revision in cases:
            with self.subTest(label=label):
                city = LiveCity(f"conflict-{label}")
                self.addCleanup(city.close)
                self.run_id = city.status()["run_id"]
                first = city.execute_actuator_command(
                    "PRT-PMP-01",
                    "set_speed",
                    {"speed_pct": 30},
                    command_id="shared-request",
                    run_id=self.run_id,
                    expected_revision=0,
                )
                self.assertEqual(first["effective_revision"], 1)
                with self.assertRaisesRegex(LiveError, "IDEMPOTENCY_CONFLICT"):
                    city.execute_actuator_command(
                        device_id,
                        command,
                        params,
                        command_id="shared-request",
                        run_id=run_id,
                        expected_revision=revision,
                    )
                self.assertEqual(city.status()["revision"], 1)
                self.assertEqual(city._effect_counter, 1)

    def test_parameter_key_order_does_not_change_fingerprint(self):
        first = self.execute(
            "ordered-params",
            "AMB-01",
            "dispatch",
            {
                "destination_node_id": "J-D1",
                "route_road_ids": ["R-LOW"],
                "mission_ref": "mission-1",
            },
        )
        duplicate = self.execute(
            "ordered-params",
            "AMB-01",
            "dispatch",
            {
                "mission_ref": "mission-1",
                "route_road_ids": ["R-LOW"],
                "destination_node_id": "J-D1",
            },
        )
        self.assertEqual(duplicate, first)
        self.assertEqual(self.city.status()["revision"], 1)
        self.assertEqual(self.city._effect_counter, 1)

    def test_legacy_adapter_replays_before_stale_revision_validation(self):
        first = self.city.command(
            "A-PUMP-01",
            True,
            command_id="legacy-replay",
            run_id=self.run_id,
            expected_revision=0,
        )
        self.city.step()
        revision_after_step = self.city.status()["revision"]
        effect_counter_after_step = self.city._effect_counter
        duplicate = self.city.command(
            "A-PUMP-01",
            True,
            command_id="legacy-replay",
            run_id=self.run_id,
            expected_revision=0,
        )
        self.assertEqual(duplicate, first)
        self.assertEqual(self.city.status()["revision"], revision_after_step)
        self.assertEqual(self.city._effect_counter, effect_counter_after_step)
        with self.assertRaisesRegex(LiveError, "IDEMPOTENCY_CONFLICT"):
            self.city.command(
                "A-PUMP-01",
                True,
                command_id="legacy-replay",
                run_id=self.run_id,
                expected_revision=1,
            )

    def test_concurrent_exact_duplicates_apply_one_effect(self):
        with ThreadPoolExecutor(max_workers=10) as pool:
            results = list(pool.map(
                lambda _: self.execute(
                    "concurrent-request", params={"speed_pct": 70}
                ),
                range(10),
            ))

        self.assertTrue(all(result == results[0] for result in results))
        self.assertEqual(self.city.status()["revision"], 1)
        self.assertEqual(self.city._effect_counter, 1)
        self.assertEqual(
            self.city.diagnostics()["physical_state"]["pump"][
                "effective_speed_fraction"
            ],
            0.7,
        )

    def test_aliases_share_canonical_quarantine_and_restore(self):
        self.city.quarantine_device("P-03", "test isolation")
        initial_revision = self.city.status()["revision"]
        for index, device_id in enumerate(
            ("P-03", "PRT-PMP-01", "PRT-PMP-02", "A-PUMP-01")
        ):
            self.assertTrue(self.city.is_device_isolated(device_id))
            with self.assertRaisesRegex(LiveError, "GUARDIAN_RESTRICTED"):
                self.city.execute_actuator_command(
                    device_id,
                    "set_speed",
                    {"speed_pct": 30},
                    command_id=f"quarantined-{index}",
                    run_id=self.run_id,
                    expected_revision=initial_revision,
                )

        self.assertEqual(self.city.status()["revision"], initial_revision)
        self.assertEqual(self.city._effect_counter, 0)
        self.city.restore_quarantined_device("PRT-PMP-02")
        self.assertFalse(self.city.is_device_isolated("P-03"))

        result = self.city.execute_actuator_command(
            "PRT-PMP-01",
            "set_speed",
            {"speed_pct": 30},
            command_id="quarantined-1",
            run_id=self.run_id,
            expected_revision=initial_revision,
        )
        self.assertEqual(result["status"], "APPLIED")
        self.assertEqual(self.city._effect_counter, 1)

    def test_successful_receipt_replays_after_later_quarantine(self):
        first = self.execute("replay-after-isolation", params={"speed_pct": 30})
        self.city.quarantine_device("A-PUMP-01", "later isolation")
        duplicate = self.execute(
            "replay-after-isolation", params={"speed_pct": 30}
        )
        self.assertEqual(duplicate, first)
        self.assertEqual(self.city.status()["revision"], 1)
        self.assertEqual(self.city._effect_counter, 1)

        with self.assertRaisesRegex(LiveError, "GUARDIAN_RESTRICTED"):
            self.execute(
                "new-command-during-isolation",
                params={"speed_pct": 70},
                expected_revision=1,
            )
        self.assertEqual(self.city._effect_counter, 1)


if __name__ == "__main__":
    unittest.main()
