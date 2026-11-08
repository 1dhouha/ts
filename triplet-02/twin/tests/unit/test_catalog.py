from copy import deepcopy
from dataclasses import FrozenInstanceError
import unittest

from city_core import baseline_catalog, build_city
from tests.support import city


class CatalogTests(unittest.TestCase):
    def test_equivalent_catalog_permutations_return_identical_topology(self):
        reference = city().metadata.read_topology().value
        for name in ("entities", "relationships", "devices"):
            with self.subTest(collection=name):
                catalog = baseline_catalog()
                catalog[name].reverse()
                self.assertEqual(city(catalog=catalog).metadata.read_topology().value,
                                 reference)

    def test_complete_connected_accepted_catalog(self):
        topology = city().metadata.read_topology().value
        self.assertEqual((len(topology.entities), len(topology.devices),
                          len(topology.relationships)), (35, 21, 36))
        zones = {e.id for e in topology.entities if e.kind == "zone"}
        self.assertEqual(zones, {"Z-DOWNTOWN", "Z-RESIDENTIAL", "Z-INDUSTRIAL",
                                 "Z-RIVER", "Z-HEALTH"})
        edges = {(e.source_id, e.relation, e.target_id) for e in topology.relationships}
        self.assertIn(("F-ESS", "powers", "P-03"), edges)
        self.assertIn(("R-LOW", "endpoint_b", "J-H1"), edges)
        self.assertIn(("R-HIGH", "endpoint_b", "J-H1"), edges)
        self.assertEqual({t for s, r, t in edges if s == "F-IND" and r == "powers"},
                         {"PLANT-01"})

    def test_invalid_catalogs_never_return_a_core(self):
        mutations = [
            lambda c: c["entities"].append(deepcopy(c["entities"][0])),
            lambda c: c["entities"].pop(),
            lambda c: c["devices"].pop(),
            lambda c: c["relationships"].pop(),
            lambda c: c["relationships"].append(deepcopy(c["relationships"][0])),
            lambda c: c["entities"][0].update(kind="nonsense"),
            lambda c: c["entities"][0].update(location_id="missing"),
            lambda c: c["relationships"][0].update(target_id="missing"),
            lambda c: c["relationships"][0].update(target_id="PLANT-01"),
            lambda c: c["devices"][0].update(target_id="B-01"),
            lambda c: c["devices"][0].update(binding_relation="controls"),
            lambda c: c.update(extra=True),
            lambda c: c["devices"][0].update(private_fault="spoof"),
            lambda c: c.update(entities=None),
        ]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                catalog = baseline_catalog()
                mutate(catalog)
                result = build_city("r-test", catalog=catalog)
                self.assertFalse(result.ok)
                self.assertFalse(hasattr(result, "value"))

    def test_non_mapping_shapes_and_versions(self):
        for value in (None, [], "catalog", 1, True):
            with self.subTest(value=value):
                self.assertEqual(build_city("r-test", catalog=value).error.code,
                                 "INVALID_ARGUMENT")
        config = baseline_catalog()
        config["catalog_version"] = "unsupported"
        self.assertEqual(build_city("r-test", catalog=config).error.code,
                         "UNSUPPORTED_VERSION")

    def test_catalog_inputs_cannot_mutate_retained_topology(self):
        source = baseline_catalog()
        core = city(catalog=source)
        old = core.metadata.read_topology().value
        source["entities"].clear()
        self.assertEqual(len(old.entities), 35)
        self.assertEqual(old, core.metadata.read_topology().value)
        with self.assertRaises(FrozenInstanceError):
            old.entities[0].kind = "changed"


if __name__ == "__main__":
    unittest.main()
