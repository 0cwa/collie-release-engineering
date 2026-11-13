#!/usr/bin/env python3
"""Audit CodeQL SARIF without discarding findings or mistaking metrics for alerts."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import html
import json
import math
from pathlib import Path
import re
from typing import Any


LANGUAGES = {"actions": "actions", "python": "py", "javascript-typescript": "js", "c-cpp": "cpp", "java-kotlin": "java"}
# CLI baseline descriptors use extractor names, not query ID prefixes.
EXTRACTORS = {"actions": "actions", "python": "python", "javascript-typescript": "javascript", "c-cpp": "cpp", "java-kotlin": "java"}
LEVELS = {"none", "note", "warning", "error"}


class SarifError(ValueError):
    """The input cannot establish a successful, complete CodeQL analysis."""


def objects(value: Any, label: str) -> list[dict]:
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise SarifError(f"{label} must be an array of objects")
    return value


def indexed(values: list, index: Any, label: str) -> dict:
    if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(values):
        raise SarifError(f"Invalid {label} index: {index!r}")
    return values[index]


def descriptor(run: dict, reference: dict, collection: str) -> dict:
    """Resolve both driver rules and --sarif-group-rules-by-pack references."""
    tool = run["tool"]
    component = tool["driver"]
    component_ref = reference.get("toolComponent")
    if component_ref:
        extensions = objects(tool.get("extensions", []), "tool.extensions")
        if "index" in component_ref:
            component = indexed(extensions, component_ref["index"], "tool component")
        else:
            matches = [item for item in [component, *extensions]
                       if any(component_ref.get(key) and component_ref[key] == item.get(key)
                              for key in ("name", "guid"))]
            if len(matches) != 1:
                raise SarifError("Unresolved tool component")
            component = matches[0]
    candidates = objects(component.get(collection, []), collection)
    if "index" in reference:
        item = indexed(candidates, reference["index"], collection)
        if reference.get("id", item.get("id")) != item.get("id"):
            raise SarifError(f"Mismatched {collection} ID and index")
        return item
    if reference.get("id"):
        return next((item for item in candidates if item.get("id") == reference["id"]),
                    {"id": reference["id"]})
    raise SarifError(f"Missing {collection} reference")


def rule(run: dict, result: dict) -> dict:
    reference = dict(result.get("rule", {}))
    for source, target in (("ruleId", "id"), ("ruleIndex", "index")):
        if source in result:
            if target in reference and reference[target] != result[source]:
                raise SarifError(f"Mismatched {source}")
            reference[target] = result[source]
    return descriptor(run, reference, "rules")


def message(value: dict, metadata: dict) -> str:
    if not isinstance(value, dict):
        raise SarifError("A SARIF message must be an object")
    template = value.get("text", value.get("markdown"))
    if template is None:
        entry = metadata.get("messageStrings", {}).get(value.get("id"), {})
        template = entry.get("text", entry.get("markdown", value.get("id", "")))
    if not isinstance(template, str):
        raise SarifError("A SARIF message must contain text")
    arguments = value.get("arguments", [])
    return re.sub(r"\{([0-9]+)\}",
                  lambda match: str(arguments[int(match[1])]) if int(match[1]) < len(arguments) else match[0],
                  template)


def locations(run: dict, entries: list) -> list[dict]:
    result = []
    for entry in objects(entries, "locations"):
        physical = entry.get("physicalLocation", {})
        artifact = physical.get("artifactLocation", {})
        if "uri" not in artifact and "index" in artifact:
            artifact = {**indexed(objects(run.get("artifacts", []), "artifacts"),
                                   artifact["index"], "artifact").get("location", {}), **artifact}
        region = physical.get("region", {})
        result.append({
            "uri": artifact.get("uri"), "uri_base_id": artifact.get("uriBaseId"),
            "start_line": region.get("startLine"), "start_column": region.get("startColumn"),
            "end_line": region.get("endLine"), "end_column": region.get("endColumn"),
        })
    return result


def level(item: dict, metadata: dict) -> str:
    value = item.get("level", metadata.get("defaultConfiguration", {}).get("level", "warning"))
    if value not in LEVELS:
        raise SarifError(f"Unknown SARIF level: {value!r}")
    return value


def audit_run(run: dict, language: str, source: str) -> dict:
    driver = run.get("tool", {}).get("driver", {})
    if driver.get("name") != "CodeQL":
        raise SarifError("Expected a CodeQL SARIF run")
    prefix = LANGUAGES[language] + "/"
    components = [driver, *objects(run.get("tool", {}).get("extensions", []), "tool.extensions")]
    rules = [item for component in components for item in objects(component.get("rules", []), "rules")]
    language_rules = [item for item in rules if item.get("id", "").startswith(prefix)]
    errors = []
    properties = run.get("properties", {})
    incremental_mode = properties.get("incrementalMode")
    query_scope = "full"
    if "incrementalMode" in properties:
        # The pinned action adds this property for diff-informed and overlay
        # runs. Such a run can report zero findings after restricting queries
        # to changed ranges; it cannot establish this gate's full-scan claim.
        # Do not accept a future or malformed mode as an implicit full scan.
        query_scope = "incremental"
        if not isinstance(incremental_mode, str) or not incremental_mode.strip():
            query_scope = "invalid"
            errors.append("CodeQL reported an invalid incremental analysis mode")
        else:
            errors.append(f"Incremental analysis mode {incremental_mode!r} cannot establish a full repository scan")
    if not language_rules:
        errors.append(f"No {language} rules were reported")
    if not any(item.get("properties", {}).get("kind") in {"problem", "path-problem"}
               for item in language_rules):
        errors.append(f"No {language} alert queries were reported")
    findings = []
    if "results" not in run:
        errors.append("No results array establishes that alert queries completed")
    for result in objects(run.get("results", []), "results"):
        metadata = rule(run, result)
        flow = []
        for code_flow in objects(result.get("codeFlows", []), "codeFlows"):
            for thread in objects(code_flow.get("threadFlows", []), "threadFlows"):
                flow.append(locations(run, [item.get("location", {}) for item in
                                            objects(thread.get("locations", []), "threadFlow.locations")]))
        # Every result is retained, including note/none levels, suppressed results,
        # absent baselines and unrecognized query kinds. Metrics have their own
        # CodeQL SARIF property and are never identified by a severity threshold.
        findings.append({
            "rule": metadata["id"], "level": level(result, metadata),
            "security_severity": metadata.get("properties", {}).get("security-severity"),
            "query_kind": metadata.get("properties", {}).get("kind"),
            "query_precision": metadata.get("properties", {}).get("precision"),
            "result_kind": result.get("kind", "fail"),
            "message": message(result.get("message", {}), metadata),
            "locations": locations(run, result.get("locations", [])),
            "related_locations": locations(run, result.get("relatedLocations", [])),
            "code_flows": flow,
            "baseline_state": result.get("baselineState"),
            "suppressions": result.get("suppressions", []),
        })
    metrics = []
    for metric in objects(run.get("properties", {}).get("metricResults", []), "metricResults"):
        metadata = rule(run, metric)
        if metadata.get("properties", {}).get("kind") != "metric":
            raise SarifError("metricResults contains a rule not declared as a metric")
        for field in ("value", "baseline"):
            if field in metric and (isinstance(metric[field], bool) or
                                    not isinstance(metric[field], (int, float)) or not math.isfinite(metric[field])):
                raise SarifError(f"Metric {field} must be numeric")
        if "value" not in metric:
            raise SarifError("Metric value is missing")
        metrics.append({"rule": metadata["id"], "value": metric["value"],
                        "baseline": metric.get("baseline"),
                        "name": metadata.get("shortDescription", {}).get("text", metadata["id"]),
                        "message": message(metric.get("message", {}), metadata),
                        "locations": locations(run, metric.get("locations", []))})
        # C/C++ puts these extraction failures in metricResults, even when all
        # files were nominally extracted and no warning notification was emitted.
        if metadata["id"] in {"cpp/telemetry/failed-includes", "cpp/telemetry/compiler-errors"} and metric["value"] > 0:
            errors.append(f"{metadata['id']}: {metric['value']} — {metrics[-1]['message']}")

    diagnostics = []
    diagnostic_counts = Counter()
    extracted, expected = set(), set()
    invocations = objects(run.get("invocations", []), "invocations")
    if not invocations:
        errors.append("No invocation records establish analysis success")
    for invocation in invocations:
        if invocation.get("executionSuccessful") is not True:
            errors.append("CodeQL invocation did not report executionSuccessful=true")
        for collection in ("toolExecutionNotifications", "toolConfigurationNotifications"):
            for notification in objects(invocation.get(collection, []), collection):
                metadata = (descriptor(run, notification["descriptor"], "notifications")
                            if notification.get("descriptor") else {"id": "unidentified-notification"})
                severity = level(notification, metadata)
                identifier = metadata["id"]
                diagnostic_counts[(identifier, severity)] += 1
                if identifier == "cpp/extractor/summary":
                    # This is a note even when one compilation failed. Other
                    # ABIs can still cover every file, so file counts alone do
                    # not establish a complete traced multi-architecture build.
                    failures = notification.get("properties", {}).get("attributes", {}).get("extractor-failures")
                    if isinstance(failures, bool) or not isinstance(failures, int) or failures < 0:
                        errors.append("C/C++ extractor summary has no valid failure count")
                    elif failures:
                        errors.append(f"C/C++ extractor reported {failures} failed compilation(s)")
                tags = metadata.get("properties", {}).get("tags", [])
                where = locations(run, notification.get("locations", []))
                file_set = None
                # The CLI emits baselines outside the query language namespace
                # (for example cli/expected-extracted-files/actions). Match the
                # exact extractor suffix so another language cannot supply the
                # baseline, and never treat CLI baseline locations as extracted.
                if (identifier == f"cli/expected-extracted-files/{EXTRACTORS[language]}"
                        and "expected-extracted-files" in tags):
                    file_set = expected
                elif identifier.startswith(prefix):
                    if "successfully-extracted-files" in tags:
                        file_set = extracted
                    elif "expected-extracted-files" in tags:
                        file_set = expected
                if file_set is not None:
                    file_set.update((item["uri_base_id"] or "", item["uri"]) for item in where if item["uri"])
                # Per-file coverage can contain thousands of entries for OTHER
                # languages. Summarize them by descriptor, without claiming that
                # their presence in the baseline means those files were scanned.
                is_coverage = any(tag in tags for tag in ("successfully-extracted-files", "expected-extracted-files"))
                if not is_coverage or severity in {"warning", "error"}:
                    diagnostics.append({
                        "id": identifier, "level": severity,
                        "message": message(notification.get("message", {}), metadata),
                        "locations": where, "properties": notification.get("properties", {}),
                    })
    if not extracted:
        errors.append(f"No successfully extracted {language} files were reported")
    if not expected and language != "java-kotlin":
        errors.append(f"No matching expected-file baseline establishes {language} coverage")
    # CLI's repository-wide baseline includes tests and variants not built by
    # the selected Android task. Java/Kotlin uses the separate compiler scope.
    missing = expected - extracted if language != "java-kotlin" else set()
    if missing:
        errors.append(f"{len(missing)} expected {language} file(s) were not extracted")
    return {
        "source": source, "category": run.get("automationDetails", {}).get("id"),
        "tool_version": driver.get("semanticVersion", driver.get("version")),
        "query_scope": query_scope, "incremental_mode": incremental_mode,
        "rule_count": len(language_rules), "findings": findings, "metrics": metrics,
        "diagnostics": diagnostics,
        "diagnostic_counts": [{"id": key[0], "level": key[1], "count": count}
                              for key, count in sorted(diagnostic_counts.items())],
        "coverage": {
            "language": language, "extracted_file_count": len(extracted),
            "expected_file_count": len(expected) if expected else None,
            "matched_expected_file_count": len(expected & extracted) if expected else None,
            "missing_files": [uri for _, uri in sorted(missing)],
            "extracted_files": [uri for _, uri in sorted(extracted)],
        },
        "errors": errors,
    }


def audit(paths: list[Path], language: str) -> dict:
    report = {"schema": 1, "language": language, "runs": [], "errors": [], "retired_reviews": []}
    if language == "java-kotlin":
        report["errors"].append("Java/Kotlin compiler scope has not been verified")
    if not paths:
        report["errors"].append("No SARIF files were found")
    for path in paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or data.get("version") != "2.1.0":
                raise SarifError("Expected SARIF version 2.1.0")
            runs = objects(data.get("runs"), "runs")
            if not runs:
                raise SarifError("SARIF contains no runs")
            for run in runs:
                report["runs"].append(audit_run(run, language, str(path)))
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
            report["errors"].append(f"{path}: {error}")
    update_outcome(report)
    return report


def update_outcome(report: dict) -> None:
    report["finding_count"] = sum(len(run["findings"]) for run in report["runs"])
    report["reviewed_finding_count"] = sum(bool(finding.get("review")) for run in report["runs"]
                                            for finding in run["findings"])
    report["unreviewed_finding_count"] = report["finding_count"] - report["reviewed_finding_count"]
    report["diagnostic_warning_count"] = sum(item["count"] for run in report["runs"]
                                             for item in run["diagnostic_counts"] if item["level"] == "warning")
    report["diagnostic_error_count"] = sum(item["count"] for run in report["runs"]
                                           for item in run["diagnostic_counts"] if item["level"] == "error")
    report["passed"] = not (report["errors"] or report["unreviewed_finding_count"] or
                            report["diagnostic_warning_count"] or report["diagnostic_error_count"] or
                            any(run["errors"] for run in report["runs"]))


def apply_reviews(report: dict, review_file: Path, source_root: Path) -> None:
    """Recognize exact reviewed false positives without editing or dropping SARIF."""
    try:
        document = json.loads(review_file.read_text(encoding="utf-8"))
        if document.get("schema") != 1:
            raise SarifError("Unsupported reviewed-finding schema")
        reviews = objects(document.get("reviews"), "reviews")
        identifiers = set()
        for review in reviews:
            identifier = review.get("id")
            if not isinstance(identifier, str) or not identifier or identifier in identifiers:
                raise SarifError("Reviewed-finding IDs must be unique nonempty strings")
            identifiers.add(identifier)
            if review.get("disposition") != "false-positive" or not review.get("reason"):
                raise SarifError(f"Review {identifier} needs a false-positive explanation")
            if review.get("language") != report["language"]:
                continue
            where = review.get("location")
            if not isinstance(where, dict) or not isinstance(where.get("uri"), str):
                raise SarifError(f"Review {identifier} needs an exact source location")
            relative = Path(where["uri"])
            if relative.is_absolute() or ".." in relative.parts:
                raise SarifError(f"Review {identifier} has an unsafe source path")
            source = source_root / relative
            if not source.resolve().is_relative_to(source_root.resolve()) or source.is_symlink():
                raise SarifError(f"Review {identifier} source escapes its checkout")
            if not re.fullmatch(r"[0-9a-f]{64}", review.get("source_sha256", "")):
                raise SarifError(f"Review {identifier} needs a SHA-256 source fingerprint")
            fields = ("rule", "level", "security_severity", "query_kind", "query_precision", "message")
            if any(field not in review for field in fields) or not review.get("tool_version") or not review.get("category"):
                raise SarifError(f"Review {identifier} is missing an exact finding identity")
            try:
                source_bytes = source.read_bytes()
            except FileNotFoundError:
                # A base-bound review for a source file removed from this revision
                # cannot match or suppress a current SARIF result. Retain its exact
                # old digest in the audit for migration visibility; findings remain
                # unreviewed unless an active source-bound review matches them.
                report["retired_reviews"].append({
                    "id": identifier,
                    "path": where["uri"],
                    "source_sha256": review["source_sha256"],
                    "reason": "source path absent from analyzed revision",
                })
                continue
            if hashlib.sha256(source_bytes).hexdigest() != review["source_sha256"]:
                raise SarifError(f"Review {identifier} is stale: source bytes changed; re-review required")
            matches = []
            for run in report["runs"]:
                if (run["category"], run["tool_version"]) != (review["category"], review["tool_version"]):
                    continue
                for finding in run["findings"]:
                    if (all(finding.get(field) == review[field] for field in fields) and
                            finding["locations"] == [where] and not finding.get("review")):
                        matches.append(finding)
            if len(matches) > 1:
                raise SarifError(f"Review {identifier} ambiguously matches multiple findings")
            if matches:
                matches[0]["review"] = {"id": identifier, "disposition": review["disposition"],
                                         "reason": review["reason"], "source_sha256": review["source_sha256"]}
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        report["errors"].append(f"{review_file}: {error}")
    update_outcome(report)


def cell(value: Any) -> str:
    return html.escape(str(value)).replace("|", "\\|").replace("`", "\\`").replace("\n", " ").replace("\r", " ")


def location_text(where: list[dict]) -> str:
    return ", ".join((item["uri"] or "location unavailable") +
                     (f":{item['start_line']}" if item["start_line"] is not None else "") for item in where) or "location unavailable"


def markdown(report: dict) -> str:
    lines = [f"## CodeQL audit: {cell(report['language'])}", "",
             f"**{'PASS' if report['passed'] else 'FAIL'}** — {report['finding_count']} raw findings; "
             f"{report['reviewed_finding_count']} reviewed false positives; "
             f"{report['unreviewed_finding_count']} unreviewed findings; "
             f"{report['diagnostic_error_count']} diagnostic errors; "
             f"{report['diagnostic_warning_count']} diagnostic warnings.", "",
             "Unreviewed findings fail this gate regardless of severity, suppression or baseline state. "
             "Reviewed false positives must match the exact rule, location, message, tool version and source-file SHA-256; all remain in SARIF. "
             "Extraction warnings, errors and missing expected files also fail. This result covers only the selected language.", ""]
    if report.get("retired_reviews"):
        lines += ["", "### Retired source-bound reviews", ""]
        for retired in report["retired_reviews"]:
            lines.append(
                f"- {cell(retired['id'])}: {cell(retired['path'])} is absent from the analyzed revision; "
                f"recorded source SHA-256 `{cell(retired['source_sha256'])}` retained. "
                "This record does not suppress SARIF findings."
            )
    for error in report["errors"]:
        lines.append(f"- {cell(error)}")
    if "compiler_scope" in report:
        for language in ("java", "kotlin"):
            scope = report["compiler_scope"].get(language, {})
            lines.append(f"- {language}: {len(scope.get('expected', []))} tracked compiler inputs; "
                         f"{len(scope.get('extracted', []))} successfully extracted; "
                         f"{len(scope.get('missing', []))} missing.")
    for run in report["runs"]:
        coverage = run["coverage"]
        lines += ["", f"### {cell(run['source'])}", "",
                  f"CodeQL {cell(run['tool_version'])}; category: {cell(run['category'] or 'not recorded')}; "
                  f"{run['rule_count']} rules.", "",
                  f"Query scope: {cell(run['query_scope'])}. "
                  f"Incremental mode: {cell(run['incremental_mode']) if run['query_scope'] != 'full' else 'not enabled'}. "
                  "Query scope does not establish extraction coverage; see the file evidence below.", "",
                  f"Extracted files: {coverage['extracted_file_count']}. "
                  f"Expected baseline: {coverage['expected_file_count'] if coverage['expected_file_count'] is not None else 'not recorded'}. "
                  f"Missing expected files: {len(coverage['missing_files'])}.", ""]
        for error in run["errors"]:
            lines.append(f"- {cell(error)}")
        for path in coverage["missing_files"]:
            lines.append(f"- Missing: {cell(path)}")
        if run["findings"]:
            lines += ["", "| Rule | Level | Location | Message | Review |", "| --- | --- | --- | --- | --- |"]
            for finding in run["findings"]:
                lines.append("| " + " | ".join(cell(value) for value in (
                    finding["rule"], finding["level"], location_text(finding["locations"]), finding["message"],
                    finding.get("review", {}).get("id", "unreviewed"))) + " |")
            for finding in run["findings"]:
                if finding.get("review"):
                    lines += ["", f"Review {cell(finding['review']['id'])}: {cell(finding['review']['reason'])}", ""]
        if run["metrics"]:
            lines += ["", "Metrics are reported values, not findings or file coverage percentages.", "",
                      "| Metric | Value | Baseline | Detail |", "| --- | --- | --- | --- |"]
            for metric in run["metrics"]:
                lines.append(f"| {cell(metric['rule'])} | {cell(metric['value'])} | {cell(metric['baseline'])} | {cell(metric['message'])} |")
        if run["diagnostic_counts"]:
            lines += ["", "| Diagnostic | Level | Count |", "| --- | --- | --- |"]
            for diagnostic in run["diagnostic_counts"]:
                lines.append(f"| {cell(diagnostic['id'])} | {cell(diagnostic['level'])} | {diagnostic['count']} |")
        for diagnostic in run["diagnostics"]:
            if diagnostic["level"] in {"warning", "error"}:
                lines += ["", f"- {cell(diagnostic['id'])}: {cell(diagnostic['message'])} "
                          f"({cell(location_text(diagnostic['locations']))})"]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sarif", nargs="*", type=Path, help="SARIF files or result directories")
    parser.add_argument("--sarif", dest="sarif_options", action="append", default=[], type=Path,
                        help="SARIF file or result directory (may be repeated)")
    parser.add_argument("--language", required=True, choices=sorted(LANGUAGES))
    parser.add_argument("--output", required=True, type=Path, help="JSON audit report")
    parser.add_argument("--summary", required=True, type=Path, help="Markdown audit report")
    parser.add_argument("--reviews", type=Path, help="Exact source-bound false-positive reviews; SARIF remains unchanged")
    parser.add_argument("--source-root", type=Path, default=Path("."))
    parser.add_argument("--compiler-inputs", type=Path)
    parser.add_argument("--extracted-inputs", type=Path)
    args = parser.parse_args(argv)
    if not args.sarif and not args.sarif_options:
        parser.error("provide a SARIF file or directory")
    paths = sorted({file for path in [*args.sarif, *args.sarif_options] for file in
                    (path.rglob("*.sarif") if path.is_dir() else [path])})
    report = audit(paths, args.language)
    if args.reviews:
        apply_reviews(report, args.reviews, args.source_root)
    if args.language == "java-kotlin" and args.compiler_inputs and args.extracted_inputs:
        import compile_scope
        try:
            scope = compile_scope.check(args.source_root, args.compiler_inputs, args.extracted_inputs)
            report["compiler_scope"] = scope
            if scope["passed"]:
                report["errors"].remove("Java/Kotlin compiler scope has not been verified")
        except (OSError, ValueError, KeyError, TypeError) as error:
            report["errors"].append(f"Compiler scope: {error}")
        update_outcome(report)
    for path, content in ((args.output, json.dumps(report, indent=2) + "\n"), (args.summary, markdown(report))):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    print(f"CodeQL audit: {'PASS' if report['passed'] else 'FAIL'}; {report['finding_count']} findings")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
