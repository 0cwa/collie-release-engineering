import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("upstream_validation", ROOT / "tools/upstreams/validate.py")
registry = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(registry)


class UpstreamSourcesTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.upstreams = self.root / "upstreams"
        (self.upstreams / "johanw666").mkdir(parents=True)
        self.sources = registry.read_json(ROOT / "upstreams/sources.json")
        self.ledger = registry.read_json(ROOT / "upstreams/johanw666/review.json")
        self.ledger["features"] = [{
            "id": "example-feature",
            "title": "Example selected feature",
            "status": "proposed",
            "upstream_commits": ["a" * 40],
            "paths": ["app/src/main/example.kt"],
            "reason": "A fixture with an explicit selected feature.",
            "integration": "adapted",
            "application": {"layer": "example-feature"},
        }]

    def write(self):
        (self.upstreams / "sources.json").write_text(json.dumps(self.sources), encoding="utf-8")
        (self.upstreams / "johanw666/review.json").write_text(json.dumps(self.ledger), encoding="utf-8")

    def validate(self):
        self.write()
        return registry.validate(self.root)

    def test_checked_in_registry_and_ledger(self):
        self.assertEqual(registry.validate(ROOT), (4, 1))

    def test_proposed_and_adopted_feature_records(self):
        self.assertEqual(self.validate(), (4, 1))
        feature = self.ledger["features"][0]
        feature["status"] = "adopted"
        feature["application"].update(pr="https://github.com/0cwa/collieim-android/pull/123", commit="b" * 40)
        self.assertEqual(self.validate(), (4, 1))

    def test_duplicate_json_keys_are_rejected(self):
        self.write()
        path = self.upstreams / "sources.json"
        path.write_text('{"schema":1,"schema":1,"sources":[]}', encoding="utf-8")
        with self.assertRaisesRegex(registry.RegistryError, "duplicate JSON key"):
            registry.validate(self.root)

    def test_duplicate_source_ids_and_repositories_are_rejected(self):
        for field in ("id", "repository"):
            with self.subTest(field=field):
                original = self.sources["sources"][1][field]
                self.sources["sources"][1][field] = self.sources["sources"][0][field]
                with self.assertRaisesRegex(registry.RegistryError, "duplicated"):
                    self.validate()
                self.sources["sources"][1][field] = original

    def test_historical_source_cannot_track_new_updates(self):
        self.sources["sources"][2]["tracking"] = "refs/heads/main"
        with self.assertRaisesRegex(registry.RegistryError, "historical sources cannot actively track"):
            self.validate()

    def test_clean_branch_cannot_replace_custom_release_pin(self):
        self.sources["sources"][3]["snapshot"]["ref"] = "refs/heads/main"
        with self.assertRaisesRegex(registry.RegistryError, "snapshot does not match tracked refs"):
            self.validate()

    def test_snapshot_requires_full_sha(self):
        self.sources["sources"][3]["snapshot"]["commit"] = "8f4ad85"
        with self.assertRaisesRegex(registry.RegistryError, "full lowercase commit SHA"):
            self.validate()

    def test_ledger_head_cannot_drift_from_snapshot(self):
        self.ledger["reviewed_head"] = "c" * 40
        with self.assertRaisesRegex(registry.RegistryError, "reviewed head differs"):
            self.validate()

    def test_duplicate_feature_and_layer_ids_are_rejected(self):
        original = copy.deepcopy(self.ledger["features"][0])
        self.ledger["features"].append(copy.deepcopy(original))
        with self.assertRaisesRegex(registry.RegistryError, "is duplicated"):
            self.validate()
        self.ledger["features"][1]["id"] = "another-feature"
        with self.assertRaisesRegex(registry.RegistryError, "layer is duplicated"):
            self.validate()

    def test_proposal_cannot_claim_merged_commit(self):
        self.ledger["features"][0]["application"]["commit"] = "d" * 40
        with self.assertRaisesRegex(registry.RegistryError, "proposed feature cannot claim"):
            self.validate()

    def test_adoption_requires_app_pr_and_commit(self):
        feature = self.ledger["features"][0]
        feature["status"] = "adopted"
        with self.assertRaisesRegex(registry.RegistryError, "adopted feature needs"):
            self.validate()
        feature["application"].update(pr="https://github.com/johanw666/Signal-Android/pull/1", commit="d" * 40)
        with self.assertRaisesRegex(registry.RegistryError, "PR must be a Collie app PR"):
            self.validate()

    def test_deferred_feature_cannot_claim_application(self):
        self.ledger["features"][0]["status"] = "deferred"
        with self.assertRaisesRegex(registry.RegistryError, "application must be present only"):
            self.validate()

    def test_source_paths_and_ledger_paths_stay_relative(self):
        self.ledger["features"][0]["paths"] = ["../outside"]
        with self.assertRaisesRegex(registry.RegistryError, "relative path without traversal"):
            self.validate()
        self.ledger["features"][0]["paths"] = ["app/file.kt"]
        self.sources["sources"][3]["ledger"] = "../outside.json"
        with self.assertRaisesRegex(registry.RegistryError, "relative path without traversal"):
            self.validate()

    def test_misspelled_fields_are_not_silently_ignored(self):
        self.ledger["features"][0]["upstream_commit"] = "e" * 40
        with self.assertRaisesRegex(registry.RegistryError, "unknown fields"):
            self.validate()


if __name__ == "__main__":
    unittest.main()
