from dataclasses import asdict, FrozenInstanceError, fields
import json
from pathlib import Path
import unittest

from city_core import baseline_config, build_city
from tests.support import city


class ReadContractTests(unittest.TestCase):
    def test_public_fixture_matches_real_read_shapes_and_all_bindings(self):
        profile = json.loads((Path(__file__).parents[2] / "fixtures/read_contract.json").read_text())
        reader = city().metadata
        self.assertEqual(set(asdict(reader.read_metadata().value.context)),
                         set(profile["context_fields"]))
        topology = reader.read_topology().value
        for family, ids in profile["sensor_families"].items():
            self.assertEqual({d.id for d in topology.devices if d.family == family}, set(ids))
        self.assertEqual(profile["capabilities"], ["read_topology", "read_metadata", "read_private"])
        for private in ("5800", "config_digest", "fault_profile", "physical_state"):
            self.assertNotIn(private, json.dumps(profile))

    def test_metadata_and_topology_nested_records_cannot_change(self):
        reader = city().metadata
        metadata = reader.read_metadata().value
        topology = reader.read_topology().value
        with self.assertRaises(FrozenInstanceError):
            metadata.context.revision = 5
        with self.assertRaises(FrozenInstanceError):
            topology.devices[0].target_id = "B-01"
        with self.assertRaises(FrozenInstanceError):
            topology.relationships[0].target_id = "B-01"
        self.assertEqual(metadata, reader.read_metadata().value)
        self.assertEqual(topology, reader.read_topology().value)

    def test_same_real_core_consumers_and_read_only_local_flow(self):
        core = city()
        b, c = core.observations, core.metadata
        first = b.read_private("r-test").value
        for _ in range(10):
            self.assertEqual(first, b.read_private("r-test").value)
            self.assertEqual(first.context, c.read_metadata().value.context)
            self.assertEqual((first.context.tick, first.context.sim_time_ms,
                              first.context.revision), (0, 0, 0))
        self.assertFalse(hasattr(core, "advance_one"))
        self.assertFalse(hasattr(core, "reset"))
        self.assertFalse(hasattr(core, "apply_effect"))

    def test_supported_nested_maps_and_items_are_immutable(self):
        core = city()
        old = core.observations.read_private("r-test").value
        for name in ("roads", "feeders", "signals", "responders", "air"):
            mapping = getattr(old.physical_state, name)
            key = next(iter(mapping))
            with self.subTest(domain=name):
                with self.assertRaises(TypeError):
                    mapping[key] = mapping[key]
                item = mapping[key]
                attr = fields(item)[0].name
                with self.assertRaises(FrozenInstanceError):
                    setattr(item, attr, None)
        route = old.physical_state.responders["AMB-01"].route_road_ids
        self.assertIsInstance(route, tuple)
        with self.assertRaises(AttributeError):
            route.append("R-LOW")
        self.assertEqual(old, core.observations.read_private("r-test").value)

    def test_c_handle_contains_no_private_state_and_exports_only_allowlist(self):
        core = city()
        handle = core.metadata
        self.assertFalse(hasattr(handle, "read_private"))
        self.assertEqual({f.name for f in fields(handle)}, {"_metadata", "_topology"})
        metadata = asdict(handle.read_metadata().value)
        self.assertEqual(set(metadata), {"context"})
        self.assertEqual(set(metadata["context"]), {"run_id", "tick", "sim_time_ms",
                         "revision", "model_version", "catalog_version", "contract_version"})
        topology = asdict(handle.read_topology().value)
        self.assertEqual(set(topology), {"catalog_version", "entities", "relationships", "devices"})
        serialized = json.dumps({"metadata": metadata, "topology": topology})
        for private in ("physical_state", "config_digest", "basin_volume", "fault_profile",
                        "5800", "private_truth", "effective_opening"):
            self.assertNotIn(private, serialized)

    def test_bad_run_identity_has_safe_stable_failure_and_no_mutation(self):
        core = city()
        first = core.observations.read_private("r-test").value
        for run, code in (("r-old", "OLD_RUN"), (None, "INVALID_ARGUMENT"),
                          (True, "INVALID_ARGUMENT"), ("", "INVALID_ARGUMENT"),
                          ("bad secret\n", "INVALID_ARGUMENT")):
            result = core.observations.read_private(run)
            self.assertFalse(result.ok)
            self.assertEqual(result.error.code, code)
            self.assertFalse(result.error.retryable)
            self.assertNotIn("secret", result.error.safe_message)
        self.assertEqual(first, core.observations.read_private("r-test").value)

    def test_invalid_builder_run_ids_and_fresh_default_helpers(self):
        for run in (None, True, 1, "", "x" * 97, "hidden label", "é"):
            with self.subTest(run=run):
                result = build_city(run)
                self.assertFalse(result.ok)
                self.assertIsNone(result.error.context)
        a = baseline_config()
        a["initial_physical"]["water"]["basin_volume_m3"] = 0
        self.assertEqual(baseline_config()["initial_physical"]["water"]["basin_volume_m3"], 5800)


if __name__ == "__main__":
    unittest.main()
