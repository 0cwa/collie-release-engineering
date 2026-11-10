"""Exercise the audit against reduced CodeQL 2.27.4 SARIF and hostile inputs."""

from contextlib import redirect_stdout
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest

import report


def location(uri=None, index=None, line=1):
    artifact = {"uriBaseId": "%SRCROOT%"}
    if uri is not None:
        artifact["uri"] = uri
    if index is not None:
        artifact["index"] = index
    return {"physicalLocation": {"artifactLocation": artifact, "region": {"startLine": line}}}


def fixture():
    # These metricResults and diagnostic descriptor shapes come from CodeQL
    # 2.27.4's actual analysis of main f6f68cdee08fb8d9acb6a866b1673deb10525f20
    # with codeql/python-queries 1.9.0. The values intentionally exceed the LOC
    # baseline: they are NOT file-coverage percentages and are NOT findings.
    return {
        "version": "2.1.0", "runs": [{
            "tool": {"driver": {
                "name": "CodeQL", "semanticVersion": "2.27.4",
                "rules": [
                    {"id": "py/overly-permissive-file", "defaultConfiguration": {"level": "warning"},
                     "properties": {"kind": "problem", "security-severity": "7.8"}},
                    {"id": "py/summary/lines-of-user-code", "properties": {"kind": "metric"}},
                    {"id": "py/summary/lines-of-code", "properties": {"kind": "metric"}},
                ],
                "notifications": [
                    {"id": "py/diagnostics/successfully-extracted-files",
                     "properties": {"kind": "diagnostic", "tags": ["successfully-extracted-files"]}},
                    {"id": "py/baseline/expected-extracted-files",
                     "properties": {"tags": ["expected-extracted-files", "telemetry"]}},
                    {"id": "java/baseline/expected-extracted-files",
                     "properties": {"tags": ["expected-extracted-files", "telemetry"]}},
                ],
            }},
            "automationDetails": {"id": "/language:python/"},
            "artifacts": [{"location": {"uri": "tools/patches/test_generated_stages.py", "uriBaseId": "%SRCROOT%"}}],
            "results": [],
            "properties": {"metricResults": [
                {"rule": {"id": "py/summary/lines-of-user-code", "index": 1},
                 "ruleId": "py/summary/lines-of-user-code", "ruleIndex": 1, "value": 16203, "baseline": 16171},
                {"rule": {"id": "py/summary/lines-of-code", "index": 2},
                 "ruleId": "py/summary/lines-of-code", "ruleIndex": 2, "value": 16203},
            ]},
            "invocations": [{"executionSuccessful": True, "toolExecutionNotifications": [
                {"descriptor": {"id": "py/diagnostics/successfully-extracted-files", "index": 0},
                 "level": "none", "message": {"text": ""}, "locations": [location(index=0)]},
                {"descriptor": {"id": "py/baseline/expected-extracted-files", "index": 1},
                 "level": "none", "message": {"text": ""}, "locations": [location(index=0)]},
                {"descriptor": {"id": "java/baseline/expected-extracted-files", "index": 2},
                 "level": "none", "message": {"text": ""}, "locations": [location("app/NotScanned.kt")]},
                {"descriptor": {"id": "py/extractor/summary"}, "level": "note",
                 "message": {"text": "Internal telemetry for the Python extractor.\n\nNo action needed."}},
            ]}],
        }],
    }


def problem():
    return {"ruleId": "py/overly-permissive-file", "ruleIndex": 0,
            "message": {"text": "Overly permissive mask in chmod sets file to world readable."},
            "locations": [location(index=0, line=105)]}


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def audit(self, data, language="python"):
        path = self.root / "analysis.sarif"
        path.write_text(json.dumps(data), encoding="utf-8")
        return report.audit([path], language)

    def test_actual_codeql_metric_shape_is_reported_separately(self):
        result = self.audit(fixture())
        self.assertTrue(result["passed"])
        self.assertEqual(result["finding_count"], 0)
        run = result["runs"][0]
        self.assertEqual([(m["rule"], m["value"], m["baseline"]) for m in run["metrics"]], [
            ("py/summary/lines-of-user-code", 16203, 16171), ("py/summary/lines-of-code", 16203, None)])
        self.assertEqual(run["coverage"]["expected_file_count"], 1)
        self.assertEqual(run["coverage"]["extracted_file_count"], 1)
        self.assertEqual(run["coverage"]["missing_files"], [])
        self.assertNotIn("app/NotScanned.kt", run["coverage"]["extracted_files"])
        self.assertIn("py/extractor/summary", report.markdown(result))

    def test_problem_uses_rule_level_and_indexed_file_location(self):
        data = fixture()
        data["runs"][0]["results"] = [problem()]
        result = self.audit(data)
        self.assertFalse(result["passed"])
        finding = result["runs"][0]["findings"][0]
        self.assertEqual(finding["rule"], "py/overly-permissive-file")
        self.assertEqual(finding["level"], "warning")
        self.assertEqual(finding["query_kind"], "problem")
        self.assertEqual(finding["locations"][0]["uri"], "tools/patches/test_generated_stages.py")
        self.assertEqual(finding["locations"][0]["start_line"], 105)

    def test_path_problem_in_extension_preserves_source_sink_and_related_location(self):
        data = fixture()
        run = data["runs"][0]
        run["tool"]["extensions"] = [{"name": "codeql/python-queries", "rules": [
            {"id": "py/path-injection", "properties": {"kind": "path-problem"},
             "defaultConfiguration": {"level": "error"},
             "messageStrings": {"message": {"text": "Untrusted data reaches {0}"}}}]}]
        run["results"] = [{
            "rule": {"id": "py/path-injection", "index": 0, "toolComponent": {"index": 0}},
            "message": {"id": "message", "arguments": ["sink"]},
            "locations": [location("sink.py", line=8)],
            "relatedLocations": [location("source.py", line=2)],
            "codeFlows": [{"threadFlows": [{"locations": [
                {"location": location("source.py", line=2)}, {"location": location("sink.py", line=8)}]}]}],
        }]
        result = self.audit(data)
        self.assertFalse(result["passed"])
        finding = result["runs"][0]["findings"][0]
        self.assertEqual(finding["query_kind"], "path-problem")
        self.assertEqual(finding["message"], "Untrusted data reaches sink")
        self.assertEqual(finding["code_flows"][0][0]["uri"], "source.py")
        self.assertEqual(finding["code_flows"][0][1]["start_line"], 8)
        self.assertEqual(finding["related_locations"][0]["start_line"], 2)

    def test_every_result_level_and_suppression_still_fails(self):
        for severity in ("none", "note", "warning", "error"):
            with self.subTest(level=severity):
                data = fixture()
                finding = problem()
                finding.update(level=severity, baselineState="absent", suppressions=[{"kind": "external", "status": "accepted"}])
                data["runs"][0]["results"] = [finding]
                result = self.audit(data)
                self.assertFalse(result["passed"])
                self.assertEqual(result["finding_count"], 1)

    def test_results_are_never_discarded_by_query_kind(self):
        for query_kind in ("metric", "diagnostic", "unknown", None):
            with self.subTest(query_kind=query_kind):
                data = fixture()
                data["runs"][0]["tool"]["driver"]["rules"][0]["properties"]["kind"] = query_kind
                data["runs"][0]["results"] = [problem()]
                result = self.audit(data)
                self.assertFalse(result["passed"])
                self.assertEqual(result["finding_count"], 1)

    def test_nonmetric_cannot_hide_in_metric_results(self):
        data = fixture()
        data["runs"][0]["properties"]["metricResults"] = [{"ruleId": "py/overly-permissive-file", "value": 1}]
        result = self.audit(data)
        self.assertFalse(result["passed"])
        self.assertIn("not declared as a metric", result["errors"][0])

    def test_failed_invocation_and_error_diagnostic_remain_visible_without_results(self):
        data = fixture()
        run = data["runs"][0]
        del run["results"]
        run["invocations"][0]["executionSuccessful"] = False
        run["invocations"][0]["toolExecutionNotifications"].append({
            "descriptor": {"id": "py/extractor/failure"}, "level": "error",
            "message": {"text": "Parser failed"}, "locations": [location("broken.py", line=4)]})
        result = self.audit(data)
        self.assertFalse(result["passed"])
        self.assertEqual(result["diagnostic_error_count"], 1)
        self.assertIn("Parser failed", report.markdown(result))
        self.assertIn("broken.py:4", report.markdown(result))

    def test_warning_diagnostic_and_configuration_error_fail(self):
        for collection, severity in (("toolExecutionNotifications", "warning"), ("toolConfigurationNotifications", "error")):
            with self.subTest(collection=collection):
                data = fixture()
                data["runs"][0]["invocations"][0].setdefault(collection, []).append({
                    "level": severity, "message": {"text": "Incomplete analysis"}})
                result = self.audit(data)
                self.assertFalse(result["passed"])
                self.assertIn("Incomplete analysis", report.markdown(result))

    def test_missing_expected_file_fails_without_counting_other_languages(self):
        data = fixture()
        data["runs"][0]["invocations"][0]["toolExecutionNotifications"].append({
            "descriptor": {"id": "py/baseline/expected-extracted-files", "index": 1},
            "level": "none", "message": {"text": ""}, "locations": [location("missing.py")]})
        result = self.audit(data)
        self.assertFalse(result["passed"])
        self.assertEqual(result["runs"][0]["coverage"]["missing_files"], ["missing.py"])

    def test_cli_baseline_detects_partial_actions_extraction(self):
        # Reduced shape from the custom app audit: one composite action was
        # extracted, but the CLI baseline also includes four workflows.
        data = fixture()
        run = data["runs"][0]
        run["tool"]["driver"]["rules"] = [
            {"id": "actions/code-injection", "properties": {"kind": "problem"}}]
        run["properties"] = {}
        paths = [".github/actions/build/action.yml", *[
            f".github/workflows/workflow-{i}.yml" for i in range(4)]]
        run["tool"]["driver"]["notifications"] = [
            {"id": "actions/diagnostics/successfully-extracted-files",
             "properties": {"tags": ["successfully-extracted-files"]}},
            {"id": "cli/expected-extracted-files/actions",
             "properties": {"tags": ["expected-extracted-files", "telemetry"]}}]
        notifications = [
            {"descriptor": {"index": index}, "level": "none", "message": {"text": ""},
             "locations": [location(path) for path in files]}
            for index, files in ((0, paths[:1]), (1, paths))]
        run["invocations"][0]["toolExecutionNotifications"] = notifications
        result = self.audit(data, "actions")
        self.assertFalse(result["passed"])
        coverage = result["runs"][0]["coverage"]
        self.assertEqual(coverage["extracted_file_count"], 1)
        self.assertEqual(coverage["expected_file_count"], 5)
        self.assertEqual(coverage["matched_expected_file_count"], 1)
        self.assertEqual(coverage["missing_files"], paths[1:])
        self.assertIn("4 expected actions file(s) were not extracted", report.markdown(result))
        notifications[0]["locations"] = [location(path) for path in paths]
        complete = self.audit(data, "actions")
        self.assertTrue(complete["passed"])
        self.assertEqual(complete["runs"][0]["coverage"]["matched_expected_file_count"], 5)

    def test_cli_baseline_matches_exact_extractor_for_each_language(self):
        for language, prefix, extractor in (
                ("actions", "actions", "actions"), ("python", "py", "python"),
                ("javascript-typescript", "js", "javascript"), ("c-cpp", "cpp", "cpp")):
            for collection in ("toolExecutionNotifications", "toolConfigurationNotifications"):
                with self.subTest(language=language, collection=collection):
                    data = fixture()
                    run = data["runs"][0]
                    run["properties"] = {}
                    run["tool"]["driver"]["rules"] = [
                        {"id": f"{prefix}/problem", "properties": {"kind": "problem"}}]
                    descriptors = [
                        {"id": f"{prefix}/diagnostics/successfully-extracted-files",
                         "properties": {"tags": ["successfully-extracted-files"]}},
                        {"id": f"cli/expected-extracted-files/{extractor}",
                         "properties": {"tags": ["expected-extracted-files"]}},
                        {"id": "cli/expected-extracted-files/java",
                         "properties": {"tags": ["expected-extracted-files"]}},
                        {"id": f"cli/expected-extracted-files/{extractor}-other",
                         "properties": {"tags": ["expected-extracted-files"]}},
                    ]
                    # Exercise extension component resolution and index-only
                    # references, indexed locations and duplicate baseline rows.
                    run["tool"]["extensions"] = [{"name": "coverage", "notifications": descriptors}]
                    run["invocations"] = [{"executionSuccessful": True, collection: [
                        {"descriptor": {"index": index, "toolComponent": {"index": 0}},
                         "level": "none", "message": {"text": ""}, "locations": where}
                        for index, where in ((0, [location(index=0)]),
                                             (1, [location(index=0), location("missing"), location("missing")]),
                                             (2, [location("other-language")]),
                                             (3, [location("other-suffix")]))]}]
                    result = self.audit(data, language)
                    self.assertFalse(result["passed"])
                    coverage = result["runs"][0]["coverage"]
                    self.assertEqual(coverage["extracted_file_count"], 1)
                    self.assertEqual(coverage["expected_file_count"], 2)
                    self.assertEqual(coverage["missing_files"], ["missing"])
                    # Expected evidence must not manufacture successful extraction.
                    del run["invocations"][0][collection][0]
                    result = self.audit(data, language)
                    self.assertEqual(result["runs"][0]["coverage"]["extracted_file_count"], 0)
                    self.assertFalse(result["passed"])

    def test_missing_baseline_is_unknown_not_zero_or_full_coverage(self):
        data = fixture()
        del data["runs"][0]["invocations"][0]["toolExecutionNotifications"][1]
        result = self.audit(data)
        self.assertFalse(result["passed"])
        self.assertIsNone(result["runs"][0]["coverage"]["expected_file_count"])
        self.assertIn("Expected baseline: not recorded", report.markdown(result))

    def test_wrong_extractor_baseline_cannot_disable_coverage_gate(self):
        data = fixture()
        data["runs"][0]["tool"]["driver"]["notifications"][1]["id"] = "cli/expected-extracted-files/actions"
        data["runs"][0]["invocations"][0]["toolExecutionNotifications"][1]["descriptor"]["id"] = "cli/expected-extracted-files/actions"
        result = self.audit(data)
        self.assertFalse(result["passed"])
        self.assertIsNone(result["runs"][0]["coverage"]["expected_file_count"])
        self.assertIn("No matching expected-file baseline", result["runs"][0]["errors"][0])

    def test_action_incremental_modes_cannot_pass_a_full_repository_audit(self):
        # codeql-action v4.39.3 src/analyze.ts emits this run property for PR
        # range-limited queries and overlay databases. All files may still
        # appear extracted while the alert queries only evaluate changed ranges.
        for mode in ("diff-informed", "overlay", "diff-informed,overlay", "future-mode"):
            with self.subTest(mode=mode):
                data = fixture()
                data["runs"][0]["properties"]["incrementalMode"] = mode
                result = self.audit(data)
                self.assertFalse(result["passed"])
                self.assertEqual(result["finding_count"], 0)
                self.assertEqual(result["runs"][0]["incremental_mode"], mode)
                self.assertEqual(result["runs"][0]["query_scope"], "incremental")
                self.assertEqual(result["runs"][0]["coverage"]["matched_expected_file_count"], 1)
                self.assertIn("cannot establish a full repository scan", report.markdown(result))
                self.assertIn(f"Incremental mode: {mode}", report.markdown(result))
        full = self.audit(fixture())
        self.assertTrue(full["passed"])
        self.assertIsNone(full["runs"][0]["incremental_mode"])
        self.assertEqual(full["runs"][0]["query_scope"], "full")

    def test_malformed_incremental_mode_does_not_mean_full_scan(self):
        for mode in (None, "", False, [], {}):
            with self.subTest(mode=mode):
                data = fixture()
                data["runs"][0]["properties"]["incrementalMode"] = mode
                result = self.audit(data)
                self.assertFalse(result["passed"])
                self.assertEqual(result["runs"][0]["query_scope"], "invalid")
                self.assertIn("invalid incremental analysis mode", report.markdown(result))

    def test_missing_extraction_or_invocation_cannot_pass(self):
        for mutation in ("extraction", "invocation", "success", "results", "rules"):
            with self.subTest(mutation=mutation):
                data = fixture()
                run = data["runs"][0]
                if mutation == "extraction":
                    del run["invocations"][0]["toolExecutionNotifications"][0]
                elif mutation == "invocation":
                    run["invocations"] = []
                elif mutation == "success":
                    del run["invocations"][0]["executionSuccessful"]
                elif mutation == "results":
                    del run["results"]
                else:
                    run["tool"]["driver"]["rules"][0]["properties"]["kind"] = "metric"
                self.assertFalse(self.audit(data)["passed"])

    def test_wrong_language_empty_runs_invalid_json_and_missing_file_fail(self):
        self.assertFalse(self.audit(fixture(), "actions")["passed"])
        self.assertFalse(self.audit({"version": "2.1.0", "runs": []})["passed"])
        self.assertFalse(report.audit([], "python")["passed"])
        path = self.root / "bad.sarif"
        self.assertFalse(report.audit([path], "python")["passed"])
        path.write_text("{broken", encoding="utf-8")
        self.assertFalse(report.audit([path], "python")["passed"])

    def test_mismatched_rule_index_and_nonfinite_metric_fail(self):
        data = fixture()
        data["runs"][0]["results"] = [{**problem(), "ruleIndex": 1}]
        self.assertFalse(self.audit(data)["passed"])

    def test_cpp_compiler_errors_are_failures_even_when_only_in_metrics(self):
        data = fixture()
        run = data["runs"][0]
        run["tool"]["driver"]["rules"].append({
            "id": "cpp/telemetry/compiler-errors", "properties": {"kind": "metric"}})
        # Actual CodeQL 2.27.4 C++ build-mode:none metric for this repository.
        run["properties"]["metricResults"].append({
            "ruleId": "cpp/telemetry/compiler-errors", "value": 1,
            "message": {"text": "cannot open source file 'jni.h'"}})
        result = self.audit(data)
        self.assertFalse(result["passed"])
        self.assertEqual(result["finding_count"], 0)
        self.assertIn("jni.h", report.markdown(result))
        data = fixture()
        data["runs"][0]["properties"]["metricResults"][0]["value"] = float("nan")
        self.assertFalse(self.audit(data)["passed"])

    def test_all_runs_contribute_to_gate(self):
        data = fixture()
        other = copy.deepcopy(data["runs"][0])
        other["results"] = [problem()]
        data["runs"].append(other)
        result = self.audit(data)
        self.assertEqual(len(result["runs"]), 2)
        self.assertEqual(result["finding_count"], 1)

    def test_cpp_note_cannot_hide_partial_architecture_extraction_failure(self):
        data = json.loads(json.dumps(fixture()).replace('py/', 'cpp/'))
        summary = data['runs'][0]['invocations'][0]['toolExecutionNotifications'][-1]
        # Actual CodeQL 2.27.4 manual-build summary shape. All expected files
        # can be extracted by successful ABIs despite another ABI failing.
        summary['properties'] = {'attributes': {
            'cache-hits': 0, 'cache-misses': 4,
            'extractor-successes': 4, 'extractor-failures': 0,
        }}
        self.assertTrue(self.audit(data, 'c-cpp')['passed'])
        for failures in (1, -1, None, True, '0'):
            with self.subTest(failures=failures):
                summary['properties']['attributes']['extractor-failures'] = failures
                result = self.audit(data, 'c-cpp')
                self.assertFalse(result['passed'])
                self.assertEqual(result['diagnostic_error_count'], 0)
                self.assertEqual(result['runs'][0]['coverage']['missing_files'], [])
                self.assertTrue(result['runs'][0]['errors'])
        self.assertFalse(result["passed"])

    def test_cli_writes_reports_before_nonzero_exit_and_escapes_markdown(self):
        data = fixture()
        finding = problem()
        finding["message"]["text"] = "<script>alert(1)</script>|new\nrow"
        data["runs"][0]["results"] = [finding]
        sarif = self.root / "analysis.sarif"
        sarif.write_text(json.dumps(data), encoding="utf-8")
        output, summary = self.root / "audit.json", self.root / "audit.md"
        with redirect_stdout(io.StringIO()):
            code = report.main(["--sarif", str(sarif), "--language", "python", "--output", str(output), "--summary", str(summary)])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(output.read_text())["finding_count"], 1)
        self.assertIn("&lt;script&gt;", summary.read_text())
        self.assertNotIn("<script>", summary.read_text())
        self.assertIn("\\|new row", summary.read_text())


if __name__ == "__main__":
    unittest.main()
