from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, fields
import json
from pathlib import Path
from threading import Barrier
from unittest.mock import patch
import unittest

from city_core import build_city


class LifecycleConsumerTests(unittest.TestCase):
    def test_standalone_lifecycle_flow_and_stale_consumer_work(self):
        core = build_city("r-one").value
        b, c = core.observations, core.metadata
        topology = c.read_topology().value
        held = b.read_private("r-one").value
        stale_work = lambda: core.lifecycle.advance_one("r-one", 1)
        for _ in range(3):
            b.read_private("r-one")
            c.read_metadata()
        self.assertEqual(c.read_metadata().value.context.tick, 0)
        self.assertTrue(core.lifecycle.advance_one("r-one", 0).ok)
        self.assertTrue(core.lifecycle.reset("r-one", 1, "r-two").ok)
        self.assertEqual(stale_work().error.code, "OLD_RUN")
        self.assertEqual(c.read_topology().value, topology)
        self.assertEqual(c.read_metadata().value.context, b.read_private("r-two").value.context)
        self.assertEqual(held.context.run_id, "r-one")
        self.assertEqual(c.read_metadata().value.context.tick, 0)

    def test_deterministic_ordered_trajectory_excludes_run_labels(self):
        def trajectory(run):
            core = build_city(run).value
            for n in range(5):
                self.assertTrue(core.lifecycle.advance_one(run, n).ok)
            page = core.lifecycle.read_private_transitions(run, -1, 100).value
            records = [asdict(r) for r in page.records]
            for record in records:
                record["context"].pop("run_id")
                record["input"].pop("run_id")
            return core.observations.read_private(run).value.physical_state, records
        self.assertEqual(trajectory("r-one"), trajectory("r-two"))

    def test_same_revision_concurrent_requests_commit_exactly_once(self):
        core = build_city("r-one").value
        barrier = Barrier(2)
        def advance():
            barrier.wait(timeout=5)
            return core.lifecycle.advance_one("r-one", 0)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: advance(), range(2)))
        self.assertEqual(sum(r.ok for r in results), 1)
        self.assertEqual([r.error.code for r in results if not r.ok], ["STATE_CONFLICT"])
        self.assertEqual(core.metadata.read_metadata().value.context.revision, 1)
        self.assertEqual(len(core.lifecycle.read_private_transitions("r-one", -1, 10).value.records), 1)

    def test_public_token_allowlist_and_private_free_live_store(self):
        core = build_city("r-one").value
        handle = core.metadata
        token = handle.make_evidence_token("r-one", "request:17").value
        data = asdict(token)
        profile = json.loads((Path(__file__).parents[2] / "fixtures/lifecycle_contract.json").read_text())
        self.assertEqual(set(data), set(profile["evidence_token_fields"]))
        self.assertEqual(data["correlation_id"], "request:17")
        self.assertEqual(data["token_id"], "ev-0000000001")
        self.assertEqual(handle.make_evidence_token("r-one").value.token_id, "ev-0000000002")
        self.assertEqual(handle.read_metadata().value.context.revision, 0)
        self.assertEqual(core.lifecycle.read_private_transitions("r-one", -1, 10).value.records, ())
        public_store = handle._metadata
        self.assertEqual({f.name for f in fields(public_store)}, {"lock", "current", "token_count"})
        self.assertEqual({f.name for f in fields(public_store.current)}, {"metadata", "topology"})
        for private in ("physical_state", "config_digest", "before_digest", "basin_volume", "5800"):
            self.assertNotIn(private, json.dumps(data))
        for run, correlation, code in (("r-old", None, "OLD_RUN"), (None, None, "INVALID_ARGUMENT"),
                                      ("r-one", "", "INVALID_ARGUMENT"), ("r-one", True, "INVALID_ARGUMENT"),
                                      ("r-one", "x" * 129, "INVALID_ARGUMENT"),
                                      ("r-one", "secret\n", "INVALID_ARGUMENT")):
            result = handle.make_evidence_token(run, correlation)
            self.assertEqual(result.error.code, code)
            self.assertNotIn("secret", result.error.safe_message)
        with patch("city_core.snapshots.MAX_TOKENS", 2):
            self.assertEqual(handle.make_evidence_token("r-one").error.code, "MODEL_LIMIT")
        core.lifecycle.advance_one("r-one", 0)
        third = handle.make_evidence_token("r-one").value
        self.assertEqual((third.token_id, third.context.revision), ("ev-0000000003", 1))
        core.lifecycle.reset("r-one", 1, "r-two")
        self.assertEqual(handle.make_evidence_token("r-two").value.token_id, "ev-0000000001")
        self.assertEqual(handle.make_evidence_token("r-one").error.code, "OLD_RUN")


if __name__ == "__main__":
    unittest.main()
