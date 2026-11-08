#!/usr/bin/env python3
"""Record CodeQL's open alerts and current analysis configuration without writes."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def request(repository: str, endpoint: str, token: str, **parameters):
    url = f"https://api.github.com/repos/{repository}/code-scanning/{endpoint}"
    if parameters:
        url += "?" + urlencode(parameters)
    req = Request(url, headers={
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "collie-codeql-audit",
    })
    with urlopen(req, timeout=60) as response:
        return json.load(response)


def pages(repository: str, endpoint: str, token: str, **parameters):
    page = 1
    while True:
        batch = request(repository, endpoint, token, per_page=100, page=page, **parameters)
        if not isinstance(batch, list):
            raise ValueError(f"Expected a list from {endpoint}")
        yield from batch
        if len(batch) < 100:
            return
        page += 1


def summarize_alert(alert):
    instance = alert["most_recent_instance"]
    return {
        "number": alert["number"],
        "url": alert["html_url"],
        "state": alert["state"],
        "rule": alert["rule"]["id"],
        "severity": alert["rule"].get("security_severity_level") or alert["rule"]["severity"],
        "description": alert["rule"]["description"],
        "ref": instance["ref"],
        "commit_sha": instance["commit_sha"],
        "category": instance["category"],
        "location": instance["location"],
        "message": instance["message"]["text"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY"))
    parser.add_argument("--ref", default="refs/heads/main")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.repository or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", args.repository):
        parser.error("a valid owner/repository is required")
    token = os.environ.get("GH_TOKEN")
    if not token:
        parser.error("GH_TOKEN with security-events: read is required")
    alerts = [summarize_alert(alert) for alert in pages(
        args.repository, "alerts", token, state="open", tool_name="CodeQL", ref=args.ref)]
    analyses = list(pages(args.repository, "analyses", token, tool_name="CodeQL", ref=args.ref))
    latest = {}
    for analysis in analyses:
        latest.setdefault(analysis["category"], {
            key: analysis.get(key) for key in (
                "id", "ref", "commit_sha", "category", "created_at", "error",
                "warning", "results_count", "rules_count", "tool", "analysis_key")
        })
    try:
        default_setup = request(args.repository, "default-setup", token)
    except HTTPError as error:
        if error.code not in (403, 404):
            raise
        # GITHUB_TOKEN can read alerts but not repository administration settings.
        # Unknown configuration must never be interpreted as disabled scanning.
        default_setup = {"state": "unknown", "http_status": error.code}
    snapshot = {
        "schema": 1,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "repository": args.repository,
        "ref": args.ref,
        "default_setup": default_setup,
        "open_alerts": alerts,
        "latest_analyses": list(latest.values()),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(snapshot, indent=2) + "\n", encoding="utf-8")
    # Keep the audit reviewable through both Actions logs and its retained artifact.
    print(json.dumps(snapshot, indent=2))


if __name__ == "__main__":
    main()
