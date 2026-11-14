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
STATUSES = {"proposed", "adopted", "deferred", "rejected", "already-present"}


class RegistryError(ValueError):
    pass


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
    require(isinstance(value, str) and SHA.fullmatch(value), label + " must be a full lowercase commit SHA")


def relative_path(value, label):
    text(value, label)
    path = PurePosixPath(value)
    require(not path.is_absolute() and ".." not in path.parts and "\\" not in value
            and str(path) == value and value != ".", label + " must be a relative path without traversal")


def ref(value, label, pattern=False):
    text(value, label)
    require(value.startswith(("refs/tags/", "refs/heads/")), label + " must name a full tag or branch ref")
    require(not any(c.isspace() or ord(c) < 32 for c in value)
            and not any(c in value for c in ("..", "@{", "\\", ":", "?", "[", "^", "~", "//"))
            and not value.endswith(("/", ".", ".lock"))
            and all(part and not part.startswith(".") for part in value.split("/")), label + " is not a valid ref")
    require(pattern or "*" not in value, label + " must be an exact ref")


def validate_ledger(data, source):
    label = source["id"] + " ledger"
    keys(data, {"schema", "source", "reviewed_head", "reviewed_date", "scope", "features"}, set(), label)
    require(type(data["schema"]) is int and data["schema"] == 1, label + " schema must be 1")
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
             {"integration", "application", "verification"}, label + " feature")
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
        selected = feature["status"] in {"proposed", "adopted"}
        require(selected == ("application" in feature), name + " application must be present only for proposed/adopted features")
        require(selected == ("integration" in feature), name + " integration must be present only for proposed/adopted features")
        if selected:
            require(isinstance(feature["integration"], str) and feature["integration"] in {"direct", "adapted", "inspired"}, name + " integration is invalid")
            application = feature["application"]
            keys(application, {"layer"}, {"pr", "commit"}, name + " application")
            identifier(application["layer"], name + " layer")
            require(application["layer"] not in layers, name + " layer is duplicated")
            layers.add(application["layer"])
            if "pr" in application:
                require(isinstance(application["pr"], str) and APP_PR.fullmatch(application["pr"]), name + " PR must be a Collie app PR URL")
            if feature["status"] == "adopted":
                require("pr" in application and "commit" in application, name + " adopted feature needs its merged app PR and commit")
                sha(application["commit"], name + " app commit")
            else:
                require("commit" not in application, name + " proposed feature cannot claim an adopted commit")


def validate(root=ROOT):
    directory = Path(root).resolve() / "upstreams"
    data = read_json(directory / "sources.json")
    keys(data, {"schema", "sources"}, set(), "registry")
    require(type(data["schema"]) is int and data["schema"] == 1, "registry schema must be 1")
    require(isinstance(data["sources"], list) and data["sources"], "sources must be a nonempty list")
    ids = set()
    repositories = set()
    ledgers = set()
    for source in data["sources"]:
        keys(source, {"id", "repository", "status", "strategy", "tracking", "baseline"},
             {"snapshot", "ledger"}, "source")
        identifier(source["id"], "source id")
        label = "source " + source["id"]
        require(source["id"] not in ids, label + " is duplicated")
        ids.add(source["id"])
        repository = source["repository"]
        require(isinstance(repository, str) and REPOSITORY.fullmatch(repository), label + " repository must be owner/name")
        require(repository.lower() not in repositories, label + " repository is duplicated")
        repositories.add(repository.lower())
        text(source["baseline"], label + " baseline")
        require(isinstance(source["status"], str) and source["status"] in {"active", "historical"}, label + " status is invalid")
        if source["status"] == "historical":
            require(source["strategy"] == "historical" and source["tracking"] is None,
                    label + " historical sources cannot actively track updates")
        else:
            require(isinstance(source["strategy"], str) and source["strategy"] in {"tag-diff", "selected-commits"}, label + " active strategy is invalid")
            ref(source["tracking"], label + " tracking", pattern=True)
            if source["strategy"] == "tag-diff":
                require(source["tracking"].startswith("refs/tags/"), label + " tag-diff must track tags")
        if "snapshot" in source:
            snapshot = source["snapshot"]
            keys(snapshot, {"ref", "commit", "ref_origin"}, {"signal_base"}, label + " snapshot")
            ref(snapshot["ref"], label + " snapshot ref")
            sha(snapshot["commit"], label + " snapshot commit")
            require(isinstance(snapshot["ref_origin"], str) and snapshot["ref_origin"] in {"upstream", "archived-bundle"}, label + " ref origin is invalid")
            if source["status"] == "active":
                require(snapshot["ref_origin"] == "upstream", label + " active snapshot must be upstream")
                require(fnmatch.fnmatchcase(snapshot["ref"], source["tracking"]), label + " snapshot does not match tracked refs")
            if "signal_base" in snapshot:
                base = snapshot["signal_base"]
                keys(base, {"ref", "commit"}, set(), label + " Signal base")
                ref(base["ref"], label + " Signal ref")
                require(base["ref"].startswith("refs/tags/"), label + " Signal base must be a tag")
                sha(base["commit"], label + " Signal commit")
                require(base["commit"] != snapshot["commit"], label + " fork snapshot cannot equal its Signal base")
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
