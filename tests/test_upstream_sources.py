import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock


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
            "status": "selected",
            "upstream_commits": ["a" * 40],
            "paths": ["app/src/main/example.kt"],
            "reason": "A fixture with an explicit selected feature.",
            "integration": "adapted",
            "application": {"layer": "example-feature"},
        }]

    def source(self, source_id):
        return next(source for source in self.sources["sources"] if source["id"] == source_id)

    def integration_record(self, target="main", pr=123):
        return {
            "pr": f"https://github.com/0cwa/collieim-android/pull/{pr}",
            "target_branch": target,
            "merge_commit": "b" * 40,
            "source_head": self.ledger["reviewed_head"],
            "layer": "example-feature",
            "integration": "adapted",
        }

    def write(self):
        (self.upstreams / "sources.json").write_text(json.dumps(self.sources), encoding="utf-8")
        (self.upstreams / "johanw666/review.json").write_text(json.dumps(self.ledger), encoding="utf-8")

    def validate(self):
        self.write()
        return registry.validate(self.root)

    def test_checked_in_routes_preserve_source_identity_and_historical_pins(self):
        self.assertEqual(registry.validate(ROOT), (4, 1))
        self.assertEqual({source["id"]: source["route"] for source in self.sources["sources"]}, {
            "signal": "upstream-merge",
            "molly": "upstream-merge",
            "johanw666": "selective-feature",
            "signal-foss": "historical-only",
        })
        self.assertEqual(self.source("johanw666")["discovery_ref"], "refs/tags/*-JW")
        self.assertEqual(self.source("johanw666")["snapshot"], {
            "ref": "refs/tags/v8.36.0.0-JW",
            "commit": "8f4ad8577344e844945ee8e73d95fd1fcc7208bc",
            "ref_origin": "upstream",
            "signal_base": {
                "ref": "refs/tags/v8.36.0",
                "commit": "b8544a813786e2442f145735e500e78df6a33014",
            },
        })
        self.assertEqual(self.source("signal-foss")["snapshot"], {
            "ref": "refs/heads/upstream-fork/signal-foss-8.33.2.0-FOSS",
            "commit": "b13bff463547475bbe1ae55335add689f52532e0",
            "ref_origin": "archived-bundle",
        })
        self.assertEqual(self.source("signal-foss")["provenance"], "archive/patch-forest/forks/signal-foss/provenance.json")
        self.assertTrue((ROOT / self.source("signal-foss")["provenance"]).is_file())

    def test_checked_in_integrations_record_distinct_historical_targets(self):
        ledger = registry.read_json(ROOT / "upstreams/johanw666/review.json")
        self.assertEqual(ledger["reviewed_head"], "8f4ad8577344e844945ee8e73d95fd1fcc7208bc")
        self.assertEqual(ledger["reviewed_date"], "2026-11-14")
        features = {feature["id"]: feature for feature in ledger["features"]}
        expected = {
            "group-profile-sharing": (142, "main", "4772439eefd3b24ecc5a69441d80f291707add48", "adapted"),
            "text-only-web-previews": (143, "trailblazer-beta", "171f05376614b6480b77404067fb5a612f5e60b9", "inspired"),
        }
        for feature_id, (pr, target, commit, integration) in expected.items():
            feature = features[feature_id]
            self.assertEqual(feature["status"], "selected")
            self.assertEqual(feature["integration_history"], [{
                "pr": f"https://github.com/0cwa/collieim-android/pull/{pr}",
                "target_branch": target,
                "merge_commit": commit,
                "source_head": ledger["reviewed_head"],
                "layer": feature["application"]["layer"],
                "integration": integration,
            }])

    def test_validation_needs_no_network_or_git_process(self):
        with mock.patch("socket.create_connection", side_effect=AssertionError("network access")), \
                mock.patch("subprocess.Popen", side_effect=AssertionError("external process")):
            self.assertEqual(registry.validate(ROOT), (4, 1))

    def test_duplicate_json_keys_are_rejected(self):
        self.write()
        path = self.upstreams / "sources.json"
        path.write_text('{"schema":2,"schema":2,"sources":[]}', encoding="utf-8")
        with self.assertRaisesRegex(registry.RegistryError, "duplicate JSON key"):
            registry.validate(self.root)

    def test_duplicate_source_ids_and_repositories_are_rejected(self):
        source = self.source("molly")
        for field in ("id", "repository"):
            with self.subTest(field=field):
                original = source[field]
                source[field] = self.source("signal")[field]
                with self.assertRaisesRegex(registry.RegistryError, "duplicated"):
                    self.validate()
                source[field] = original

    def test_legacy_registry_fields_cannot_restore_old_routes(self):
        source = self.source("signal")
        for field, value in (("status", "active"), ("strategy", "tag-diff"),
                             ("tracking", "refs/tags/v*"), ("baseline", "canonicalVersionName")):
            with self.subTest(field=field):
                source[field] = value
                with self.assertRaisesRegex(registry.RegistryError, "unknown fields"):
                    self.validate()
                del source[field]

    def test_merge_route_rejects_snapshots_and_feature_ledgers(self):
        for source_id in ("signal", "molly"):
            source = self.source(source_id)
            for field, value in (("snapshot", self.source("johanw666")["snapshot"]),
                                 ("ledger", "johanw666/review.json")):
                with self.subTest(source=source_id, field=field):
                    source[field] = value
                    with self.assertRaises(registry.RegistryError):
                        self.validate()
                    del source[field]

    def test_merge_route_requires_app_provenance_and_supported_repository(self):
        source = self.source("signal")
        original = copy.deepcopy(source)
        for field, value in (("provenance", "app/build.gradle.kts canonicalVersionName"),
                             ("repository", "unreviewed/Signal-Android"),
                             ("discovery_ref", "refs/heads/main")):
            with self.subTest(field=field):
                source[field] = value
                with self.assertRaises(registry.RegistryError):
                    self.validate()
                source[field] = original[field]

    def test_molly_discovery_does_not_choose_a_permanent_branch(self):
        molly = self.source("molly")
        self.assertEqual(molly["discovery_ref"], "refs/heads/*")
        for branch in ("main", "molly-8.34"):
            with self.subTest(branch=branch):
                molly["discovery_ref"] = "refs/heads/" + branch
                with self.assertRaises(registry.RegistryError):
                    self.validate()

    def test_routes_require_their_own_evidence(self):
        for source_id, field in (("signal", "provenance"), ("johanw666", "snapshot"),
                                 ("johanw666", "ledger"), ("signal-foss", "snapshot"),
                                 ("signal-foss", "provenance")):
            with self.subTest(source=source_id, field=field):
                source = self.source(source_id)
                value = source.pop(field)
                with self.assertRaises(registry.RegistryError):
                    self.validate()
                source[field] = value
        self.source("signal")["route"] = "tag-diff"
        with self.assertRaisesRegex(registry.RegistryError, "route"):
            self.validate()

    def test_historical_source_cannot_discover_or_use_live_upstream_refs(self):
        historical = self.source("signal-foss")
        historical["discovery_ref"] = "refs/heads/main"
        with self.assertRaisesRegex(registry.RegistryError, "historical|discovery"):
            self.validate()
        historical["discovery_ref"] = None
        historical["snapshot"]["ref_origin"] = "upstream"
        with self.assertRaisesRegex(registry.RegistryError, "historical|archived"):
            self.validate()

    def test_historical_source_cannot_become_a_feature_import(self):
        historical = self.source("signal-foss")
        historical["ledger"] = "johanw666/review.json"
        with self.assertRaises(registry.RegistryError):
            self.validate()
        del historical["ledger"]
        historical["snapshot"]["signal_base"] = self.source("johanw666")["snapshot"]["signal_base"]
        with self.assertRaises(registry.RegistryError):
            self.validate()

    def test_selective_source_requires_upstream_origin_and_own_signal_base(self):
        snapshot = self.source("johanw666")["snapshot"]
        snapshot["ref_origin"] = "archived-bundle"
        with self.assertRaises(registry.RegistryError):
            self.validate()
        snapshot["ref_origin"] = "upstream"
        base = snapshot.pop("signal_base")
        with self.assertRaises(registry.RegistryError):
            self.validate()
        snapshot["signal_base"] = base
        base["commit"] = snapshot["commit"]
        with self.assertRaisesRegex(registry.RegistryError, "Signal base"):
            self.validate()

    def test_selective_snapshot_must_be_a_concrete_ref_matching_discovery(self):
        source = self.source("johanw666")
        source["discovery_ref"] = "refs/tags/custom-*"
        for ref in ("refs/heads/custom-8.36", "refs/tags/v8.36.0", "refs/tags/Custom-8.36"):
            with self.subTest(ref=ref):
                source["snapshot"]["ref"] = ref
                with self.assertRaisesRegex(registry.RegistryError, "snapshot does not match discovery refs"):
                    self.validate()
        source["snapshot"]["ref"] = "refs/tags/custom-*"
        with self.assertRaisesRegex(registry.RegistryError, "snapshot ref must be an exact ref"):
            self.validate()

    def test_selective_signal_base_must_be_a_tag(self):
        snapshot = self.source("johanw666")["snapshot"]
        snapshot["signal_base"]["ref"] = "refs/heads/main"
        with self.assertRaisesRegex(registry.RegistryError, "Signal base must be a tag"):
            self.validate()

    def test_selective_discovery_uses_configured_tag_and_branch_patterns(self):
        source = self.source("johanw666")
        for pattern, ref in (("refs/tags/custom-*", "refs/tags/custom-8.36"),
                             ("refs/heads/releases/*", "refs/heads/releases/8.36"),
                             ("refs/heads/release", "refs/heads/release")):
            with self.subTest(pattern=pattern):
                source["discovery_ref"] = pattern
                source["snapshot"]["ref"] = ref
                self.assertEqual(self.validate(), (4, 1))

    def test_selective_discovery_rejects_invalid_ref_patterns(self):
        source = self.source("johanw666")
        for pattern in (None, "tags/custom-*", "refs/pull/*", "refs/tags/", "refs/tags/bad..*",
                        "refs/tags/custom-?", "refs/tags/custom-[12]", "refs/heads/*/release-*"):
            with self.subTest(pattern=pattern):
                source["discovery_ref"] = pattern
                with self.assertRaisesRegex(registry.RegistryError, "discovery ref"):
                    self.validate()

    def test_repository_identity_ignores_case_in_every_comparison(self):
        for cased in ("Johanw666/Signal-Android", "johanw666/signal-android", "JOHANW666/SIGNAL-ANDROID"):
            with self.subTest(repository=cased, accepted=True):
                self.source("johanw666")["repository"] = cased
                self.assertEqual(self.validate(), (4, 1))
                self.source("johanw666")["repository"] = "johanw666/Signal-Android"
        for cased in ("SignalApp/signal-android", "MOLLYIM/Mollyim-Android"):
            with self.subTest(repository=cased, merge=True):
                source = self.source("signal" if cased.lower().startswith("signal") else "molly")
                original = source["repository"]
                source["repository"] = cased
                self.assertEqual(self.validate(), (4, 1))
                source["repository"] = original
        for cased in ("SIGNALAPP/Signal-android", "signalapp/signal-android"):
            with self.subTest(repository=cased, duplicate=True):
                source = self.source("molly")
                source["repository"] = cased
                with self.assertRaisesRegex(registry.RegistryError, "supported|duplicated"):
                    self.validate()
                source["repository"] = "mollyim/mollyim-android"

    def test_all_commit_evidence_requires_full_lowercase_sha(self):
        feature = self.ledger["features"][0]
        history = self.integration_record()
        feature["integration_history"] = [history]
        fields = [
            (self.source("johanw666")["snapshot"], "commit"),
            (self.source("johanw666")["snapshot"]["signal_base"], "commit"),
            (self.source("signal-foss")["snapshot"], "commit"),
            (self.ledger, "reviewed_head"),
            (feature["upstream_commits"], 0),
            (history, "merge_commit"),
            (history, "source_head"),
        ]
        for index, (record, field) in enumerate(fields):
            original = record[field]
            for invalid in ("8f4ad85", "A" * 40, "g" * 40, "0" * 40, "a" * 40 + "\n", None):
                with self.subTest(field=index, value=invalid):
                    record[field] = invalid
                    with self.assertRaisesRegex(registry.RegistryError, "full lowercase commit SHA"):
                        self.validate()
            record[field] = original

    def test_ledger_review_head_cannot_drift_from_review_snapshot(self):
        self.ledger["reviewed_head"] = "c" * 40
        with self.assertRaisesRegex(registry.RegistryError, "reviewed head differs"):
            self.validate()

    def test_selected_review_does_not_require_an_integration(self):
        self.assertEqual(self.validate(), (4, 1))
        self.ledger["features"][0]["application"]["pr"] = "https://github.com/0cwa/collieim-android/pull/123"
        self.assertEqual(self.validate(), (4, 1))

    def test_history_is_separate_from_latest_proposal_and_attribution(self):
        feature = self.ledger["features"][0]
        history = self.integration_record()
        feature["integration_history"] = [history]
        feature["application"] = {"layer": "replacement-feature", "pr": "https://github.com/0cwa/collieim-android/pull/456"}
        feature["integration"] = "inspired"
        self.assertEqual(self.validate(), (4, 1))
        self.assertEqual(history["layer"], "example-feature")
        self.assertEqual(history["integration"], "adapted")

    def test_review_progress_does_not_rewrite_historical_source_head(self):
        feature = self.ledger["features"][0]
        history = self.integration_record()
        old_source = history["source_head"]
        feature["integration_history"] = [history]
        self.source("johanw666")["snapshot"]["commit"] = "f" * 40
        self.ledger["reviewed_head"] = "f" * 40
        self.ledger["reviewed_date"] = "2026-11-22"
        feature["upstream_commits"] = ["e" * 40]
        self.assertEqual(self.validate(), (4, 1))
        self.assertEqual(history["source_head"], old_source)

    def test_later_review_decisions_can_retain_historical_integrations(self):
        feature = self.ledger["features"][0]
        feature["integration_history"] = [self.integration_record()]
        del feature["application"], feature["integration"]
        for status in ("deferred", "rejected", "already-present"):
            with self.subTest(status=status):
                feature["status"] = status
                self.assertEqual(self.validate(), (4, 1))

    def test_legacy_adoption_and_global_app_commit_are_rejected(self):
        feature = self.ledger["features"][0]
        feature["status"] = "adopted"
        with self.assertRaisesRegex(registry.RegistryError, "status"):
            self.validate()
        feature["status"] = "selected"
        feature["application"]["commit"] = "d" * 40
        with self.assertRaisesRegex(registry.RegistryError, "unknown fields"):
            self.validate()

    def test_history_requires_each_evidence_field(self):
        record = self.integration_record()
        self.ledger["features"][0]["integration_history"] = [record]
        for field in tuple(record):
            with self.subTest(field=field):
                value = record.pop(field)
                with self.assertRaisesRegex(registry.RegistryError, "missing|required"):
                    self.validate()
                record[field] = value

    def test_history_requires_concrete_target_branch(self):
        record = self.integration_record()
        self.ledger["features"][0]["integration_history"] = [record]
        for target in ("main", "trailblazer-beta", "release/next"):
            with self.subTest(valid=target):
                record["target_branch"] = target
                self.assertEqual(self.validate(), (4, 1))
        for target in ("", None, "refs/heads/main", "refs/tags/v8.36.0", "*",
                       "main beta", "../main", "HEAD", "@", "-main", "a" * 40, "A" * 40):
            with self.subTest(invalid=target):
                record["target_branch"] = target
                with self.assertRaises(registry.RegistryError):
                    self.validate()

    def test_history_can_record_multiple_targets_without_claiming_current_state(self):
        feature = self.ledger["features"][0]
        feature["integration_history"] = [self.integration_record(), self.integration_record("trailblazer-beta", 456)]
        feature["integration_history"][1]["merge_commit"] = "c" * 40
        self.assertEqual(self.validate(), (4, 1))
        feature["integration_history"][0]["currently_included"] = True
        with self.assertRaisesRegex(registry.RegistryError, "unknown fields"):
            self.validate()

    def test_duplicate_or_malformed_history_is_rejected(self):
        feature = self.ledger["features"][0]
        record = self.integration_record()
        for history in ([], {}, [None], [record, copy.deepcopy(record)]):
            with self.subTest(history=history):
                feature["integration_history"] = history
                with self.assertRaises(registry.RegistryError):
                    self.validate()

    def test_proposal_and_history_pr_links_must_point_to_collie(self):
        feature = self.ledger["features"][0]
        history = self.integration_record()
        feature["integration_history"] = [history]
        for record in (feature["application"], history):
            with self.subTest(record=record):
                original = record.get("pr")
                record["pr"] = "https://github.com/johanw666/Signal-Android/pull/1"
                with self.assertRaisesRegex(registry.RegistryError, "Collie app PR"):
                    self.validate()
                if original is None:
                    del record["pr"]
                else:
                    record["pr"] = original

    def test_direct_adapted_and_inspired_are_explicit_in_plan_and_history(self):
        feature = self.ledger["features"][0]
        history = self.integration_record()
        feature["integration_history"] = [history]
        for integration in ("direct", "adapted", "inspired"):
            feature["integration"] = history["integration"] = integration
            self.assertEqual(self.validate(), (4, 1))
        for record in (feature, history):
            with self.subTest(record=record):
                record["integration"] = "imported"
                with self.assertRaisesRegex(registry.RegistryError, "integration"):
                    self.validate()
                record["integration"] = "inspired"

    def test_duplicate_feature_and_layer_ids_are_rejected(self):
        original = copy.deepcopy(self.ledger["features"][0])
        self.ledger["features"].append(copy.deepcopy(original))
        with self.assertRaisesRegex(registry.RegistryError, "is duplicated"):
            self.validate()
        self.ledger["features"][1]["id"] = "another-feature"
        with self.assertRaisesRegex(registry.RegistryError, "layer is duplicated"):
            self.validate()

    def test_deferred_feature_cannot_claim_a_current_application_plan(self):
        self.ledger["features"][0]["status"] = "deferred"
        with self.assertRaisesRegex(registry.RegistryError, "application.*selected"):
            self.validate()

    def test_source_paths_and_ledger_paths_stay_relative(self):
        self.ledger["features"][0]["paths"] = ["../outside"]
        with self.assertRaisesRegex(registry.RegistryError, "relative path without traversal"):
            self.validate()
        self.ledger["features"][0]["paths"] = ["app/file.kt"]
        self.source("johanw666")["ledger"] = "../outside.json"
        with self.assertRaisesRegex(registry.RegistryError, "relative path without traversal"):
            self.validate()

    def test_paths_reject_control_characters_without_filesystem_errors(self):
        source = self.source("johanw666")
        feature = self.ledger["features"][0]
        for record, field, original in ((source, "ledger", source["ledger"]),
                                        (feature["paths"], 0, feature["paths"][0])):
            for control in ("\x00", "\x1f", "\x7f", "\n"):
                with self.subTest(field=field, control=repr(control)):
                    record[field] = "app/invalid" + control + "path"
                    with self.assertRaisesRegex(registry.RegistryError, "path|control"):
                        self.validate()
            record[field] = original

    def test_misspelled_fields_are_not_silently_ignored(self):
        self.ledger["features"][0]["upstream_commit"] = "e" * 40
        with self.assertRaisesRegex(registry.RegistryError, "unknown fields"):
            self.validate()


if __name__ == "__main__":
    unittest.main()
