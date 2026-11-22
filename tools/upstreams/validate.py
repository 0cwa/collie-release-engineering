#!/usr/bin/env python3
"""Check the curated source registry and review ledgers; never fetch or apply code."""

import argparse
from datetime import date
import fnmatch
import json
from pathlib import Path, PurePosixPath
import re


ROOT = Path(__file__).resolve().parents[2]
SHA = re.compile(r"[0-9a-f]{40}\Z")
ID = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
APP_PR = re.compile(r"https://github\.com/0cwa/collieim-android/pull/[1-9][0-9]*\Z")
STATUSES = {"selected", "deferred", "rejected", "already-present"}
INTEGRATIONS = {"direct", "adapted", "inspired"}
ROUTES = {"upstream-merge", "selective-feature", "historical-only"}
APP_MERGE_PROVENANCE = (
    "https://github.com/0cwa/collieim-android/blob/main/"
    "docs/VERIFY.md#verify-an-upstream-merge"
)
# These are the repositories supported by the app's upstream-merge verifier.
MERGE_DISCOVERY_REFS = {
    "signalapp/signal-android": "refs/tags/v*",
    "mollyim/mollyim-android": "refs/heads/*",
}
JOHAN_REPOSITORY = "johanw666/signal-android"


class RegistryError(ValueError):
    pass


def repository_identity(value):
    """GitHub owner/name is case-insensitive: compare repositories only through this form."""
    return value.lower()


def require(condition, message):
    if not condition:
        raise RegistryError(message)


def unique_keys(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate JSON key: " + key)
        result[key] = value
    return result


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_keys)


def keys(value, required, optional, label):
    require(isinstance(value, dict), label + " must be an object")
    require(required <= value.keys(), label + " is missing required fields")
    require(value.keys() <= required | optional, label + " has unknown fields")


def text(value, label):
    require(isinstance(value, str) and bool(value.strip()), label + " must be nonempty text")


def identifier(value, label):
    require(isinstance(value, str) and ID.fullmatch(value), label + " must be a lowercase identifier")


def sha(value, label):
    require(isinstance(value, str) and SHA.fullmatch(value) and value != "0" * 40,
            label + " must be a full lowercase commit SHA")


def relative_path(value, label):
    text(value, label)
    path = PurePosixPath(value)
    require(not path.is_absolute() and ".." not in path.parts and "\\" not in value
            and str(path) == value and value != "."
            and not any(ord(c) < 32 or ord(c) == 127 for c in value),
            label + " must be a relative path without traversal or control characters")


def ref(value, label, pattern=False):
    text(value, label)
    require(value.startswith(("refs/tags/", "refs/heads/")), label + " must name a full tag or branch ref")
    require(not any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value)
            and not any(c in value for c in ("..", "@{", "\\", ":", "?", "[", "^", "~", "//"))
            and not value.endswith(("/", "."))
            and all(part and not part.startswith(".") and not part.endswith(".lock")
                    for part in value.split("/")), label + " is not a valid ref")
    require(pattern or "*" not in value, label + " must be an exact ref")
    require(value.count("*") <= 1, label + " must contain at most one discovery wildcard")


def app_pr(value, label):
    require(isinstance(value, str) and APP_PR.fullmatch(value), label + " must be a Collie app PR URL")


def target_branch(value, label):
    text(value, label)
    require(not value.startswith(("refs/", "-")) and value not in {"HEAD", "@"}
            and not SHA.fullmatch(value.lower()),
            label + " must name a target branch, not a full ref or commit")
    ref("refs/heads/" + value, label)


def integration(value, label):
    require(isinstance(value, str) and value in INTEGRATIONS, label + " integration is invalid")


def validate_history(history, label):
    """Validate past events independently of the latest review or application plan."""
    require(isinstance(history, list) and history, label + " integration_history must be a nonempty list")
    prs = set()
    merges = set()
    for event in history:
        name = label + " integration history"
        keys(event, {"pr", "target_branch", "merge_commit", "source_head", "layer", "integration"}, set(), name)
        app_pr(event["pr"], name + " PR")
        target_branch(event["target_branch"], name + " target branch")
        sha(event["merge_commit"], name + " merge commit")
        sha(event["source_head"], name + " source head")
        identifier(event["layer"], name + " layer")
        integration(event["integration"], name)
        merge = (event["target_branch"], event["merge_commit"])
        require(event["pr"] not in prs and merge not in merges, name + " is duplicated")
        prs.add(event["pr"])
        merges.add(merge)


def validate_ledger(data, source):
    label = source["id"] + " ledger"
    keys(data, {"schema", "source", "reviewed_head", "reviewed_date", "scope", "features"}, set(), label)
    require(type(data["schema"]) is int and data["schema"] == 2, label + " schema must be 2")
    require(data["source"] == source["id"], label + " source differs")
    sha(data["reviewed_head"], label + " reviewed_head")
    require(data["reviewed_head"] == source["snapshot"]["commit"], label + " reviewed head differs from source snapshot")
    try:
        require(isinstance(data["reviewed_date"], str)
                and date.fromisoformat(data["reviewed_date"]).isoformat() == data["reviewed_date"], label + " date must use YYYY-MM-DD")
    except ValueError as error:
        raise RegistryError(label + " date must use YYYY-MM-DD") from error
    text(data["scope"], label + " scope")
    require(isinstance(data["features"], list), label + " features must be a list")
    seen = set()
    layers = set()
    for feature in data["features"]:
        keys(feature, {"id", "title", "status", "upstream_commits", "paths", "reason"},
             {"integration", "application", "verification", "integration_history"}, label + " feature")
        identifier(feature["id"], label + " feature id")
        name = label + " feature " + feature["id"]
        require(feature["id"] not in seen, name + " is duplicated")
        seen.add(feature["id"])
        text(feature["title"], name + " title")
        text(feature["reason"], name + " reason")
        require(isinstance(feature["status"], str) and feature["status"] in STATUSES, name + " status is invalid")
        for field, check in (("upstream_commits", sha), ("paths", relative_path)):
            values = feature[field]
            require(isinstance(values, list) and values, name + " " + field + " must be a nonempty list")
            for value in values:
                check(value, name + " " + field)
            require(len(set(values)) == len(values), name + " " + field + " contains duplicates")
        if "verification" in feature:
            text(feature["verification"], name + " verification")
        selected = feature["status"] == "selected"
        require(selected == ("application" in feature), name + " application must be present only for selected features")
        require(selected == ("integration" in feature), name + " integration must be present only for selected features")
        if selected:
            integration(feature["integration"], name)
            application = feature["application"]
            keys(application, {"layer"}, {"pr"}, name + " application")
            identifier(application["layer"], name + " layer")
            require(application["layer"] not in layers, name + " layer is duplicated")
            layers.add(application["layer"])
            if "pr" in application:
                app_pr(application["pr"], name + " PR")
        if "integration_history" in feature:
            validate_history(feature["integration_history"], name)


def validate_route(source, label):
    route = source["route"]
    require(isinstance(route, str) and route in ROUTES, label + " route is invalid")
    if route == "upstream-merge":
        require("snapshot" not in source and "ledger" not in source,
                label + " upstream-merge uses app provenance, not a snapshot or ledger")
        require(source.get("provenance") == APP_MERGE_PROVENANCE,
                label + " upstream-merge must refer to the app's verified upstream merge provenance")
        identity = repository_identity(source["repository"])
        require(identity in MERGE_DISCOVERY_REFS,
                label + " repository is not supported by the app upstream-merge verifier")
        require(source["discovery_ref"] == MERGE_DISCOVERY_REFS[identity],
                label + " upstream-merge discovery must cover Signal tags or Molly branches; select an exact ref per operation")
        return

    require("snapshot" in source, label + " route requires a pinned snapshot")
    snapshot = source["snapshot"]
    if route == "historical-only":
        require(source["discovery_ref"] is None, label + " historical sources cannot actively track discovery refs")
        require("ledger" not in source, label + " historical sources cannot have a selective-feature ledger")
        require("provenance" in source, label + " historical sources require original provenance")
        relative_path(source["provenance"], label + " historical provenance")
        keys(snapshot, {"ref", "commit", "ref_origin"}, set(), label + " historical snapshot")
        require(snapshot["ref_origin"] == "archived-bundle", label + " historical snapshot must retain its archived-bundle origin")
    else:
        require("provenance" not in source, label + " selective-feature provenance is its review ledger")
        require("ledger" in source, label + " selective-feature requires a review ledger")
        ref(source["discovery_ref"], label + " discovery ref", pattern=True)
        if repository_identity(source["repository"]) == JOHAN_REPOSITORY:
            require(source["discovery_ref"] == "refs/tags/*-JW",
                    label + " Johan discovery must select custom *-JW tags, not the clean Signal branch")
        keys(snapshot, {"ref", "commit", "ref_origin", "signal_base"}, set(), label + " selective-feature snapshot")
        require(snapshot["ref_origin"] == "upstream", label + " selective-feature snapshot must be upstream")
    ref(snapshot["ref"], label + " snapshot ref")
    sha(snapshot["commit"], label + " snapshot commit")
    if route == "selective-feature":
        require(fnmatch.fnmatchcase(snapshot["ref"], source["discovery_ref"]), label + " snapshot does not match discovery refs")
        base = snapshot["signal_base"]
        keys(base, {"ref", "commit"}, set(), label + " Signal base")
        ref(base["ref"], label + " Signal ref")
        require(base["ref"].startswith("refs/tags/"), label + " Signal base must be a tag")
        sha(base["commit"], label + " Signal commit")
        require(base["commit"] != snapshot["commit"], label + " fork snapshot cannot equal its Signal base")


def validate(root=ROOT):
    directory = Path(root).resolve() / "upstreams"
    data = read_json(directory / "sources.json")
    keys(data, {"schema", "sources"}, set(), "registry")
    require(type(data["schema"]) is int and data["schema"] == 2, "registry schema must be 2")
    require(isinstance(data["sources"], list) and data["sources"], "sources must be a nonempty list")
    ids = set()
    repositories = set()
    ledgers = set()
    for source in data["sources"]:
        keys(source, {"id", "repository", "route", "discovery_ref"},
             {"provenance", "snapshot", "ledger"}, "source")
        identifier(source["id"], "source id")
        label = "source " + source["id"]
        require(source["id"] not in ids, label + " is duplicated")
        ids.add(source["id"])
        repository = source["repository"]
        require(isinstance(repository, str) and REPOSITORY.fullmatch(repository), label + " repository must be owner/name")
        require(repository_identity(repository) not in repositories, label + " repository is duplicated")
        repositories.add(repository_identity(repository))
        validate_route(source, label)
        if "ledger" in source:
            require("snapshot" in source, label + " ledger requires a pinned snapshot")
            relative_path(source["ledger"], label + " ledger path")
            path = (directory / source["ledger"]).resolve()
            require(path.is_relative_to(directory), label + " ledger escapes upstreams directory")
            require(path not in ledgers, label + " ledger is shared by multiple sources")
            ledgers.add(path)
            validate_ledger(read_json(path), source)
    return len(ids), len(ledgers)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT, help="release-engineering checkout")
    args = parser.parse_args()
    try:
        sources, ledgers = validate(args.root)
    except (RegistryError, OSError, json.JSONDecodeError) as error:
        parser.exit(1, str(error) + "\n")
    print(f"Validated {sources} sources and {ledgers} review ledgers (offline).")


if __name__ == "__main__":
    main()
