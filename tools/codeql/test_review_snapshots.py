"""Exercise trusted snapshot selection against real tracked source trees."""

import copy
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout

import report
import review_snapshots
from test_report import fixture, location, problem


class TrustedReviewSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "analyzed-source"
        self.source.mkdir()
        self.write_source(".github/workflows/codeql.yml", "name: CodeQL\n")
        self.write_source("settings.gradle.kts", "rootProject.name = \"sample\"\n")
        self.write_source("build.gradle.kts", "plugins {}\n")
        self.write_source("app/src/main/java/Sample.kt", "class Sample\n")
        subprocess.run(["git", "init", "-q", str(self.source)], check=True)
        subprocess.run(["git", "-C", str(self.source), "add", "."], check=True)
        subprocess.run(["git", "-C", str(self.source), "-c", "user.name=Test", "-c",
                        "user.email=test@example.invalid", "commit", "-qm", "fixture"], check=True)

        self.policy = self.root / "trusted-base"
        config = self.policy / ".github/codeql/codeql-config.yml"
        config.parent.mkdir(parents=True)
        config.write_text("name: trusted\nqueries: security-extended\n", encoding="utf-8")
        self.config_hash = hashlib.sha256(config.read_bytes()).hexdigest()
        self.default = self.policy / ".github/codeql/reviewed-findings.json"
        self.default.write_text(json.dumps({"schema": 1, "reviews": []}), encoding="utf-8")
        self.catalog_path = self.policy / ".github/codeql/review-snapshots.json"

        self.sarif = self.root / "analysis.sarif"
        self.data = fixture()
        run = self.data["runs"][0]
        run["artifacts"][0]["location"]["uri"] = ".github/workflows/codeql.yml"
        finding = problem()
        finding["codeFlows"] = [{"threadFlows": [{"locations": [
            {"location": location("app/src/main/java/Sample.kt", line=8)},
        ]}]}]
        finding["relatedLocations"] = [location("app/src/main/java/Sample.kt", line=9)]
        run["results"] = [finding]
        self.sarif.write_text(json.dumps(self.data), encoding="utf-8")
        self.review = self.make_review(finding)

    def write_source(self, relative, content):
        path = self.source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def make_review(self, finding, identifier="fixture-review"):
        parsed = report.audit([self.sarif], "python")
        audited_run = parsed["runs"][0]
        audited = parsed["runs"][0]["findings"][0]
        source = self.source / ".github/workflows/codeql.yml"
        dependency = self.source / "app/src/main/java/Sample.kt"
        return {
            "id": identifier,
            "disposition": "false-positive",
            "language": "python",
            "category": audited_run["category"],
            "tool_version": audited_run["tool_version"],
            "location": audited["locations"][0],
            "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "reason": "Fixture for checking that the existing exact guards still run.",
            **{key: audited[key] for key in (
                "rule", "level", "security_severity", "query_kind", "query_precision", "message"
            )},
            "reviewed_sources": {
                "app/src/main/java/Sample.kt": hashlib.sha256(dependency.read_bytes()).hexdigest(),
            },
            "context_sha256": report.context_sha256(audited, self.source),
        }

    def write_manifest(self, name, reviews):
        relative = f".github/codeql/snapshots/{name}.json"
        path = self.policy / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps({"schema": 1, "reviews": reviews}, sort_keys=True).encode("utf-8")
        path.write_bytes(content)
        return relative, hashlib.sha256(content).hexdigest(), path

    def catalog_entry(self, manifest_relative, manifest_hash, source_hash=None, config_hash=None,
                      language="python"):
        return {
            "language": language,
            "source_sha256": source_hash or review_snapshots.source_fingerprint(self.source, language),
            "analysis_config_sha256": config_hash or self.config_hash,
            "manifest": manifest_relative,
            "manifest_sha256": manifest_hash,
        }

    def write_catalog(self, entries):
        self.catalog_path.parent.mkdir(parents=True, exist_ok=True)
        self.catalog_path.write_text(json.dumps({
            "schema": 1, "profile": review_snapshots.PROFILE, "snapshots": entries,
        }, sort_keys=True), encoding="utf-8")

    def resolve(self):
        return review_snapshots.resolve_review_snapshot(
            source_root=self.source,
            trusted_policy_root=self.policy,
            catalog_relative_path=".github/codeql/review-snapshots.json",
            default_reviews_relative_path=".github/codeql/reviewed-findings.json",
            language="python",
            analysis_config_relative_path=".github/codeql/codeql-config.yml",
            analysis_config_sha256=self.config_hash,
        )

    def test_matching_catalog_selects_one_manifest_and_runs_existing_guards(self):
        relative, digest, manifest = self.write_manifest("complete", [self.review])
        self.write_catalog([self.catalog_entry(relative, digest)])

        selected, identity = self.resolve()
        self.assertEqual(selected, manifest)
        self.assertEqual(identity["selection"], "matched-snapshot")
        self.assertEqual(identity["selected_review_sha256"], digest)
        self.assertEqual(identity["language"], "python")
        self.assertEqual(identity["source_sha256"], review_snapshots.source_fingerprint(self.source, "python"))

        result = report.audit([self.sarif], "python")
        report.apply_reviews(result, selected, self.source)
        result["review_snapshot_selection"] = identity
        self.assertTrue(result["passed"])
        self.assertEqual(result["reviewed_finding_count"], 1)
        summary = report.markdown(result)
        self.assertIn("Trusted review snapshot", summary)
        self.assertIn(relative, summary)
        self.assertIn(digest, summary)

        dependency = self.source / "app/src/main/java/Sample.kt"
        original = dependency.read_text(encoding="utf-8")
        dependency.write_text(original + "changed\n", encoding="utf-8")
        stale = report.audit([self.sarif], "python")
        report.apply_reviews(stale, selected, self.source)
        self.assertFalse(stale["passed"])
        self.assertEqual(stale["reviewed_finding_count"], 0)
        self.assertTrue(any("reviewed_sources source bytes changed" in error for error in stale["errors"]))
        dependency.write_text(original, encoding="utf-8")

        changed_context = copy.deepcopy(self.data)
        flow_location = changed_context["runs"][0]["results"][0]["codeFlows"][0]["threadFlows"][0]
        flow_location["locations"][0]["location"]["physicalLocation"]["region"]["startLine"] += 1
        self.sarif.write_text(json.dumps(changed_context), encoding="utf-8")
        context_result = report.audit([self.sarif], "python")
        report.apply_reviews(context_result, selected, self.source)
        self.assertFalse(context_result["passed"])
        self.assertEqual(context_result["reviewed_finding_count"], 0)

        self.sarif.write_text(json.dumps(self.data), encoding="utf-8")
        primary = self.source / ".github/workflows/codeql.yml"
        original_primary = primary.read_text(encoding="utf-8")
        primary.write_text(original_primary + "# changed\n", encoding="utf-8")
        primary_result = report.audit([self.sarif], "python")
        report.apply_reviews(primary_result, selected, self.source)
        self.assertFalse(primary_result["passed"])
        self.assertEqual(primary_result["reviewed_finding_count"], 0)
        self.assertTrue(any("source bytes changed" in error for error in primary_result["errors"]))
        primary.write_text(original_primary, encoding="utf-8")

        duplicate_relative, duplicate_hash, duplicate_manifest = self.write_manifest(
            "duplicate-review", [self.review, {**self.review, "id": "second-id"}])
        self.write_catalog([self.catalog_entry(duplicate_relative, duplicate_hash)])
        duplicate_selected, _ = self.resolve()
        duplicate_result = report.audit([self.sarif], "python")
        report.apply_reviews(duplicate_result, duplicate_selected, self.source)
        self.assertEqual(duplicate_selected, duplicate_manifest)
        self.assertFalse(duplicate_result["passed"])
        self.assertEqual(duplicate_result["reviewed_finding_count"], 1)
        self.assertTrue(any("duplicates another review" in error for error in duplicate_result["errors"]))

    def test_policy_and_document_changes_do_not_change_key_but_recipe_and_config_do(self):
        original = review_snapshots.source_fingerprint(self.source, "python")
        self.write_source("docs/security.md", "ordinary documentation\n")
        self.write_source(".github/codeql/snapshots/policy-only.json", "{}\n")
        subprocess.run(["git", "-C", str(self.source), "add", "."], check=True)
        self.assertEqual(review_snapshots.source_fingerprint(self.source, "python"), original)

        old_relative, old_digest, _ = self.write_manifest("pre-source-change", [self.review])
        self.write_catalog([self.catalog_entry(old_relative, old_digest, source_hash=original)])

        self.write_source("app/src/main/java/Sample.kt", "class Sample { val changed = true }\n")
        subprocess.run(["git", "-C", str(self.source), "add", "."], check=True)
        self.assertNotEqual(review_snapshots.source_fingerprint(self.source, "python"), original)
        selected, identity = self.resolve()
        self.assertEqual(selected, self.default)
        self.assertEqual(identity["selection"], "default-no-match")
        self.write_source(".github/workflows/codeql.yml", "name: CodeQL\n# changed recipe\n")
        subprocess.run(["git", "-C", str(self.source), "add", "."], check=True)
        recipe_key = review_snapshots.source_fingerprint(self.source, "python")
        self.assertNotEqual(recipe_key, original)
        subprocess.run(["git", "-C", str(self.source), "update-index", "--chmod=+x",
                        "settings.gradle.kts"], check=True)
        self.assertNotEqual(review_snapshots.source_fingerprint(self.source, "python"), recipe_key)

        relative, digest, _ = self.write_manifest("old-config", [self.review])
        original_source = review_snapshots.source_fingerprint(self.source, "python")
        self.write_catalog([self.catalog_entry(relative, digest, source_hash=original_source)])
        config = self.policy / ".github/codeql/codeql-config.yml"
        config.write_text("name: changed trusted config\n", encoding="utf-8")
        new_config_hash = hashlib.sha256(config.read_bytes()).hexdigest()
        with self.assertRaisesRegex(review_snapshots.SnapshotSelectionError, "do not match"):
            review_snapshots.resolve_review_snapshot(
                source_root=self.source, trusted_policy_root=self.policy,
                catalog_relative_path=".github/codeql/review-snapshots.json",
                default_reviews_relative_path=".github/codeql/reviewed-findings.json",
                language="python", analysis_config_relative_path=".github/codeql/codeql-config.yml",
                analysis_config_sha256=self.config_hash,
            )
        selected, identity = review_snapshots.resolve_review_snapshot(
            source_root=self.source, trusted_policy_root=self.policy,
            catalog_relative_path=".github/codeql/review-snapshots.json",
            default_reviews_relative_path=".github/codeql/reviewed-findings.json",
            language="python", analysis_config_relative_path=".github/codeql/codeql-config.yml",
            analysis_config_sha256=new_config_hash,
        )
        self.assertEqual(selected, self.default)
        self.assertEqual(identity["selection"], "default-no-match")

    def test_untracked_and_index_removed_profile_sources_fail_selection(self):
        relative, digest, _ = self.write_manifest("approved", [self.review])
        self.write_catalog([self.catalog_entry(relative, digest)])
        injected = self.write_source("app/src/main/java/Injected.kt", "class Injected\n")
        # Ignore rules must not hide an additional compiler input from inventory validation.
        self.write_source(".gitignore", "Injected.kt\n")
        with self.assertRaisesRegex(review_snapshots.SnapshotSelectionError, "untracked profile input"):
            self.resolve()
        injected.unlink()
        subprocess.run(["git", "-C", str(self.source), "rm", "--cached", "-q",
                        "app/src/main/java/Sample.kt"], check=True)
        with self.assertRaisesRegex(review_snapshots.SnapshotSelectionError, "untracked profile input"):
            self.resolve()

    def test_profile_root_symlinks_and_gitlinks_are_rejected(self):
        target = self.root / "external-module"
        target.mkdir()
        (target / "Extra.kt").write_text("class Extra\n", encoding="utf-8")
        commit = subprocess.check_output(["git", "-C", str(self.source), "rev-parse", "HEAD"], text=True).strip()
        for name in ("core", "lib", "buildSrc"):
            with self.subTest(root=name, kind="symlink"):
                path = self.source / name
                path.symlink_to(target, target_is_directory=True)
                subprocess.run(["git", "-C", str(self.source), "add", name], check=True)
                with self.assertRaisesRegex(review_snapshots.SnapshotSelectionError, "unsafe or non-file"):
                    review_snapshots.source_fingerprint(self.source, "python")
                subprocess.run(["git", "-C", str(self.source), "rm", "--cached", "-q", name], check=True)
                path.unlink()
            with self.subTest(root=name, kind="gitlink"):
                subprocess.run(["git", "-C", str(self.source), "update-index", "--add", "--cacheinfo",
                                f"160000,{commit},{name}"], check=True)
                with self.assertRaisesRegex(review_snapshots.SnapshotSelectionError, "unsafe or non-file"):
                    review_snapshots.source_fingerprint(self.source, "python")
                subprocess.run(["git", "-C", str(self.source), "update-index", "--force-remove", name], check=True)

    def test_generated_module_outputs_do_not_change_key_or_hide_source_directories(self):
        original = review_snapshots.source_fingerprint(self.source, "python")
        self.write_source("app/build.gradle.kts", "plugins {}\n")
        subprocess.run(["git", "-C", str(self.source), "add", "app/build.gradle.kts"], check=True)
        original = review_snapshots.source_fingerprint(self.source, "python")
        self.write_source("app/build/generated/Generated.java", "class Generated {}\n")
        self.write_source("app/.gradle/cache.bin", "generated cache\n")
        self.assertEqual(review_snapshots.source_fingerprint(self.source, "python"), original)
        self.write_source("app/src/main/java/build/Injected.kt", "class Injected\n")
        with self.assertRaisesRegex(review_snapshots.SnapshotSelectionError, "untracked profile input"):
            review_snapshots.source_fingerprint(self.source, "python")

    def test_no_match_uses_default_and_does_not_review_findings(self):
        relative, digest, _ = self.write_manifest("other-source", [self.review])
        self.write_catalog([self.catalog_entry(relative, digest, source_hash="a" * 64)])
        selected, identity = self.resolve()
        self.assertEqual(selected, self.default)
        self.assertEqual(identity["selection"], "default-no-match")

        result = report.audit([self.sarif], "python")
        report.apply_reviews(result, selected, self.source)
        self.assertFalse(result["passed"])
        self.assertEqual(result["reviewed_finding_count"], 0)
        self.assertEqual(result["unreviewed_finding_count"], 1)

    def test_partial_snapshots_are_never_combined(self):
        first = self.review
        second_finding = copy.deepcopy(self.data["runs"][0]["results"][0])
        second_finding["locations"][0]["physicalLocation"]["region"]["startLine"] = 106
        self.data["runs"][0]["results"].append(second_finding)
        self.sarif.write_text(json.dumps(self.data), encoding="utf-8")
        parsed = report.audit([self.sarif], "python")["runs"][0]["findings"][1]
        second = {**self.review, "id": "second-review", "location": parsed["locations"][0],
                  "context_sha256": report.context_sha256(parsed, self.source)}
        relative_a, hash_a, manifest_a = self.write_manifest("partial-a", [first])
        relative_b, hash_b, _ = self.write_manifest("partial-b", [second])
        source_key = review_snapshots.source_fingerprint(self.source, "python")
        self.write_catalog([
            self.catalog_entry(relative_a, hash_a, source_hash=source_key),
            self.catalog_entry(relative_b, hash_b, source_hash="b" * 64),
        ])

        selected, identity = self.resolve()
        self.assertEqual(selected, manifest_a)
        result = report.audit([self.sarif], "python")
        report.apply_reviews(result, selected, self.source)
        self.assertFalse(result["passed"])
        self.assertEqual(result["reviewed_finding_count"], 1)
        self.assertEqual(result["unreviewed_finding_count"], 1)
        self.assertEqual(identity["selection"], "matched-snapshot")

    def test_analysis_failures_and_incremental_scope_survive_valid_selection(self):
        relative, digest, selected_manifest = self.write_manifest("valid", [self.review])
        self.write_catalog([self.catalog_entry(relative, digest)])
        selected, _ = self.resolve()
        self.assertEqual(selected, selected_manifest)

        failed_data = copy.deepcopy(self.data)
        failed_data["runs"][0]["invocations"][0]["executionSuccessful"] = False
        self.sarif.write_text(json.dumps(failed_data), encoding="utf-8")
        result = report.audit([self.sarif], "python")
        report.apply_reviews(result, selected, self.source)
        self.assertFalse(result["passed"])
        self.assertTrue(any("executionSuccessful=true" in error for error in result["runs"][0]["errors"]))

        incremental_data = copy.deepcopy(self.data)
        incremental_data["runs"][0]["properties"] = {"incrementalMode": "changed-files"}
        self.sarif.write_text(json.dumps(incremental_data), encoding="utf-8")
        result = report.audit([self.sarif], "python")
        report.apply_reviews(result, selected, self.source)
        self.assertFalse(result["passed"])
        self.assertEqual(result["runs"][0]["query_scope"], "incremental")
        self.assertTrue(any("cannot establish a full repository scan" in error for error in result["runs"][0]["errors"]))

        incomplete_data = copy.deepcopy(self.data)
        notifications = incomplete_data["runs"][0]["invocations"][0]["toolExecutionNotifications"]
        incomplete_data["runs"][0]["invocations"][0]["toolExecutionNotifications"] = [
            item for item in notifications
            if item.get("descriptor", {}).get("id") != "py/diagnostics/successfully-extracted-files"
        ]
        self.sarif.write_text(json.dumps(incomplete_data), encoding="utf-8")
        result = report.audit([self.sarif], "python")
        report.apply_reviews(result, selected, self.source)
        self.assertFalse(result["passed"])
        self.assertTrue(any("No successfully extracted python files" in error for error in result["runs"][0]["errors"]))

    def test_catalog_malformed_duplicate_hash_and_unsafe_paths_fail_closed(self):
        relative, digest, manifest = self.write_manifest("one", [self.review])
        entry = self.catalog_entry(relative, digest)

        self.catalog_path.write_text(
            '{"schema":1,"schema":1,"profile":"collie-android-v1","snapshots":[]}', encoding="utf-8")
        with self.assertRaisesRegex(review_snapshots.SnapshotSelectionError, "malformed JSON"):
            self.resolve()

        self.catalog_path.write_text(json.dumps({
            "schema": 1.0, "profile": review_snapshots.PROFILE, "snapshots": [],
        }), encoding="utf-8")
        with self.assertRaisesRegex(review_snapshots.SnapshotSelectionError, "unsupported review catalog schema"):
            self.resolve()

        self.write_catalog([{"language": "python"}])
        with self.assertRaisesRegex(review_snapshots.SnapshotSelectionError, "malformed snapshot entry"):
            self.resolve()

        self.write_catalog([entry, entry])
        with self.assertRaisesRegex(review_snapshots.SnapshotSelectionError, "duplicate snapshot identities"):
            self.resolve()

        self.write_catalog([entry])
        manifest.write_text('{"schema":1,"reviews":[]}', encoding="utf-8")
        with self.assertRaisesRegex(review_snapshots.SnapshotSelectionError, "SHA-256 does not match"):
            self.resolve()

        unsafe = dict(entry, manifest="../outside.json")
        self.write_catalog([unsafe])
        with self.assertRaisesRegex(review_snapshots.SnapshotSelectionError, "canonical relative path"):
            self.resolve()

        link = self.policy / ".github/codeql/snapshots/link.json"
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(manifest)
        linked = dict(entry, manifest=".github/codeql/snapshots/link.json")
        self.write_catalog([linked])
        with self.assertRaisesRegex(review_snapshots.SnapshotSelectionError, "symlinks"):
            self.resolve()

    def test_duplicate_catalog_identity_is_rejected_even_if_only_one_is_relevant(self):
        relative_a, hash_a, _ = self.write_manifest("duplicate-a", [self.review])
        relative_b, hash_b, _ = self.write_manifest("duplicate-b", [])
        entry_a = self.catalog_entry(relative_a, hash_a)
        entry_b = self.catalog_entry(relative_b, hash_b)
        self.write_catalog([entry_a, entry_b])
        with self.assertRaisesRegex(review_snapshots.SnapshotSelectionError, "duplicate snapshot identities"):
            self.resolve()

    def test_pr_checkout_cannot_supply_catalog_and_policy_only_merge_keeps_key(self):
        key_before = review_snapshots.source_fingerprint(self.source, "python")
        untrusted = self.source / ".github/codeql/review-snapshots.json"
        untrusted.parent.mkdir(parents=True, exist_ok=True)
        untrusted.write_text('{"schema":1,"profile":"evil","snapshots":[]}', encoding="utf-8")
        subprocess.run(["git", "-C", str(self.source), "add", ".github/codeql/review-snapshots.json"], check=True)
        key_after_policy_addition = review_snapshots.source_fingerprint(self.source, "python")
        self.assertEqual(key_before, key_after_policy_addition)

        relative, digest, trusted_manifest = self.write_manifest("trusted", [self.review])
        self.write_catalog([self.catalog_entry(relative, digest)])
        selected, identity = self.resolve()
        self.assertEqual(selected, trusted_manifest)
        self.assertEqual(identity["catalog_path"], ".github/codeql/review-snapshots.json")
        self.assertNotEqual(selected.resolve(), untrusted.resolve())

    def test_cli_audit_records_selection_and_malformed_opt_in_fails_without_applying_default(self):
        self.default.write_text(json.dumps({"schema": 1, "reviews": [self.review]}), encoding="utf-8")
        legacy_output = self.root / "legacy-audit.json"
        legacy_summary = self.root / "legacy-audit.md"
        legacy_args = [
            "--sarif", str(self.sarif), "--language", "python", "--output", str(legacy_output),
            "--summary", str(legacy_summary), "--reviews", str(self.default), "--source-root", str(self.source),
        ]
        with redirect_stdout(io.StringIO()):
            self.assertEqual(report.main(legacy_args), 0)
        legacy = json.loads(legacy_output.read_text(encoding="utf-8"))
        self.assertEqual(legacy["reviewed_finding_count"], 1)
        self.assertNotIn("review_snapshot_selection", legacy)
        self.assertNotIn("Trusted review snapshot", legacy_summary.read_text(encoding="utf-8"))

        self.default.write_text(json.dumps({"schema": 1, "reviews": []}), encoding="utf-8")
        relative, digest, _manifest = self.write_manifest("cli", [self.review])
        self.write_catalog([self.catalog_entry(relative, digest)])
        output = self.root / "audit.json"
        summary = self.root / "audit.md"
        args = [
            "--sarif", str(self.sarif), "--language", "python", "--output", str(output),
            "--summary", str(summary), "--reviews", ".github/codeql/reviewed-findings.json",
            "--source-root", str(self.source), "--review-catalog", ".github/codeql/review-snapshots.json",
            "--trusted-policy-root", str(self.policy), "--analysis-config", ".github/codeql/codeql-config.yml",
            "--analysis-config-sha256", self.config_hash,
        ]
        with redirect_stdout(io.StringIO()):
            self.assertEqual(report.main(args), 0)
        result = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(result["review_snapshot_selection"]["selection"], "matched-snapshot")
        self.assertEqual(result["review_snapshot_selection"]["selected_review_path"], relative)
        self.assertIn(digest, summary.read_text(encoding="utf-8"))

        self.catalog_path.write_text("{broken", encoding="utf-8")
        with redirect_stdout(io.StringIO()):
            self.assertEqual(report.main(args), 1)
        failed = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(failed["reviewed_finding_count"], 0)
        self.assertEqual(failed["review_snapshot_selection"]["selection"], "error")
        self.assertIn("Selection failed", summary.read_text(encoding="utf-8"))

        self.write_catalog([self.catalog_entry(relative, digest)])
        with redirect_stdout(io.StringIO()):
            self.assertEqual(report.main(args[:-2]), 1)
        incomplete = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(incomplete["reviewed_finding_count"], 0)
        self.assertEqual(incomplete["review_snapshot_selection"]["selection"], "error")
        self.assertTrue(any("requires catalog" in error for error in incomplete["errors"]))


if __name__ == "__main__":
    unittest.main()
