#!/usr/bin/env python3
"""Report what is waiting upstream, from the app's merge history. Read-only.

For each upstream-merge source in upstreams/sources.json (Signal, Molly) this finds
the last upstream commit the app merged (the `Upstream:` trailer of its two-parent
`Layer: upgrade` merges, as written by tooling/upstream/merge-upstream.py), resolves
the candidate upstream ref, counts the commits the merge would bring and prints the
merge-upstream.py command to run. Selective sources (Johan's fork) only list new
tags since the pinned review snapshot: a person reviews them.

Nothing in the app checkout, this repository or the registry is modified and nothing
is merged. Counting commits needs the candidate's commit objects, so they are fetched
(commits only, no file contents) into a small cache repository that borrows the app
checkout's objects; use --no-fetch to skip even that and use what the cache has.
"""

import argparse
import fnmatch
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_APP = Path("~/Dev/collieim-android")
DEFAULT_REVISION = "origin/main"
MERGE_TOOL = "tooling/upstream/merge-upstream.py"
SIGNAL_TAG = re.compile(r"v(\d+(?:\.\d+)*)\Z")
ANY_VERSION = re.compile(r"(\d+(?:\.\d+)*)")
MOLLY_RELEASE = re.compile(r"molly-(\d+(?:\.\d+)*)\Z")
TRAILER = re.compile(r"(?P<repository>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+) (?P<name>[^\s@]+)(?:@(?P<commit>[0-9a-f]{40}))?\Z")
FIELD, RECORD = "\x1f", "\x1e"


def load_validator():
    spec = importlib.util.spec_from_file_location("upstream_validation", Path(__file__).with_name("validate.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


validator = load_validator()


class StatusError(Exception):
    pass


def git(*args, cwd=None, git_dir=None, check=True):
    command = ["git"] + (["--git-dir", str(git_dir)] if git_dir else []) + [str(a) for a in args]
    result = subprocess.run(command, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            env={**os.environ, "LC_ALL": "C", "GIT_TERMINAL_PROMPT": "0"})
    if check and result.returncode:
        raise StatusError(f"git {' '.join(str(a) for a in args[:2])} failed: {result.stderr.strip()}")
    return result


def version(text, pattern):
    match = pattern.search(text)
    return tuple(int(part) for part in match.group(1).split(".")) if match else None


def short(commit):
    return commit[:12]


def remote_refs(url, pattern):
    """Map each ref matching `pattern` to its commit; annotated tags resolve to their target."""
    refs, peeled = {}, {}
    for line in git("ls-remote", "--", url, pattern).stdout.splitlines():
        commit, _, name = line.partition("\t")
        if name.endswith("^{}"):
            peeled[name[:-3]] = commit
        elif fnmatch.fnmatchcase(name, pattern):
            refs[name] = commit
    return {name: peeled.get(name, commit) for name, commit in refs.items()}


def merge_history(app, commit):
    """Newest-first two-parent `Layer: upgrade` merges that carry an `Upstream:` trailer."""
    log = git("log", "--topo-order", "--merges", "--min-parents=2", "--max-parents=2",
              f"--format=%H{FIELD}%P{FIELD}%(trailers:key=Layer,valueonly,unfold,separator=%x1f){FIELD}"
              f"%(trailers:key=Upstream,valueonly,unfold,separator=%x1f){RECORD}", commit, cwd=app).stdout
    merges = []
    for record in log.split(RECORD):
        record = record.strip("\n")
        if not record:
            continue
        merge, parents, layers, upstreams = record.split(FIELD)
        if "upgrade" not in layers.split(FIELD):
            continue
        for value in filter(None, upstreams.split(FIELD)):
            match = TRAILER.fullmatch(value.strip())
            if match:
                merges.append({"merge": merge, "upstream_commit": parents.split()[1], **match.groupdict()})
    return merges


def last_merge(merges, repository):
    identity = validator.repository_identity(repository)
    for merge in merges:
        if validator.repository_identity(merge["repository"]) == identity:
            if merge["commit"] and merge["commit"] != merge["upstream_commit"]:
                raise StatusError(f"merge {short(merge['merge'])} names {merge['name']}@{short(merge['commit'])} "
                                  f"but its second parent is {short(merge['upstream_commit'])}")
            return merge
    return None


def cache_repository(cache, source_id, app):
    """A bare repository borrowing the app's objects, so only new upstream commits are fetched."""
    directory = Path(cache) / f"{source_id}.git"
    if not directory.exists():
        directory.parent.mkdir(parents=True, exist_ok=True)
        git("init", "--bare", "-q", str(directory))
    objects = git("rev-parse", "--path-format=absolute", "--git-path", "objects", cwd=app).stdout.strip()
    alternates = directory / "objects/info/alternates"
    alternates.parent.mkdir(parents=True, exist_ok=True)
    alternates.write_text(objects + "\n", encoding="utf-8")
    return directory


def count_waiting(cache, source_id, app, url, ref, base, app_commit, candidate, fetch):
    directory = cache_repository(cache, source_id, app)
    if fetch:
        # The base ref lets fetch negotiate from commits the app already has.
        git("update-ref", "refs/status/base", base, git_dir=directory)
        # A named remote lets the filtered fetch treat the source as a promisor remote.
        git("config", "remote.upstream.url", url, git_dir=directory)
        git("fetch", "--no-tags", "--filter=tree:0", "upstream", f"+{ref}:refs/status/candidate", git_dir=directory)
    if git("cat-file", "-e", f"{candidate}^{{commit}}", git_dir=directory, check=False).returncode:
        raise StatusError(f"commits for {ref} are not in {directory}; run without --no-fetch")
    count = int(git("rev-list", "--count", candidate, f"^{app_commit}", git_dir=directory).stdout)
    log = git("log", "--format=%cs", candidate, f"^{app_commit}", git_dir=directory).stdout.split()
    descends = git("merge-base", "--is-ancestor", base, candidate, git_dir=directory, check=False).returncode == 0
    return {"waiting": count, "oldest_date": log[-1] if log else None, "newest_date": log[0] if log else None,
            "last_merged_is_ancestor": descends}


def command_for(upstream, name, commit):
    return f"python3 {MERGE_TOOL} {upstream} {name} --expected-commit {commit}"


def merge_source(source, merges, context):
    repository = source["repository"]
    upstream = source["id"]
    result = {"id": upstream, "repository": repository, "route": source["route"]}
    merged = last_merge(merges, repository)
    if merged is None:
        raise StatusError(f"no Layer: upgrade merge with an Upstream: {repository} trailer in {context['revision']}")
    url = context["urls"].get(upstream) or f"https://github.com/{repository}.git"
    result["last_merged"] = {"name": merged["name"], "commit": merged["upstream_commit"], "merge": merged["merge"]}
    if upstream == "signal":
        tags = {name[len("refs/tags/"):]: commit for name, commit in remote_refs(url, source["discovery_ref"]).items()}
        releases = sorted(((version(name, SIGNAL_TAG), name) for name in tags if SIGNAL_TAG.fullmatch(name)))
        if not releases:
            raise StatusError("no release tags (v<number>) found upstream")
        name = releases[-1][1]
        ref, commit = f"refs/tags/{name}", tags[name]
        merged_version = version(merged["name"], SIGNAL_TAG)
        result["newer_tags_included"] = [tag for number, tag in releases if merged_version and merged_version < number
                                  and tag != name]
    else:
        branch = context["molly_branch"] or merged["name"]
        heads = {name[len("refs/heads/"):]: commit for name, commit in remote_refs(url, source["discovery_ref"]).items()}
        if branch not in heads:
            raise StatusError(f"branch {branch} is not in {repository}")
        name, ref, commit = branch, f"refs/heads/{branch}", heads[branch]
        current = version(branch, MOLLY_RELEASE)
        result["branch_selection"] = "--molly-branch" if context["molly_branch"] else "the last merge"
        result["newer_release_branches"] = sorted(
            (other for other in heads if current and (version(other, MOLLY_RELEASE) or ()) > current),
            key=lambda other: version(other, MOLLY_RELEASE))
    result["candidate"] = {"name": name, "ref": ref, "commit": commit}
    result.update(count_waiting(context["cache"], upstream, context["app"], url, ref, merged["upstream_commit"],
                                context["commit"], commit, context["fetch"]))
    result["command"] = command_for(upstream, name, commit) if result["waiting"] else None
    return result


def selective_source(source, context):
    result = {"id": source["id"], "repository": source["repository"], "route": source["route"],
              "action": "manual review"}
    url = context["urls"].get(source["id"]) or f"https://github.com/{source['repository']}.git"
    snapshot = source["snapshot"]
    tags = remote_refs(url, source["discovery_ref"])
    pinned = tags.get(snapshot["ref"])
    result["reviewed"] = {"ref": snapshot["ref"], "commit": snapshot["commit"],
                          "pinned_tag_moved": pinned is not None and pinned != snapshot["commit"],
                          "pinned_tag_missing": pinned is None}
    base = version(snapshot["ref"], ANY_VERSION)
    versions = {name: version(name, ANY_VERSION) for name in tags}
    newer = sorted((versions[name], name) for name in tags if versions[name] and versions[name] > base)
    result["new_tags"] = [{"ref": name, "commit": tags[name]} for _, name in newer]
    return result


def collect(root=ROOT, app=DEFAULT_APP, revision=DEFAULT_REVISION, cache=None, fetch=True, urls=None,
            molly_branch=None):
    """Return the whole report as plain data; errors are recorded per source."""
    root, app = Path(root), Path(app).expanduser()
    validator.validate(root)
    sources = validator.read_json(root / "upstreams/sources.json")["sources"]
    if cache is None:
        cache = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "collie-upstream-status"
    commit = git("rev-parse", "--verify", "--end-of-options", f"{revision}^{{commit}}", cwd=app).stdout.strip()
    tool = git("show", f"{commit}:{MERGE_TOOL}", cwd=app, check=False)
    supported = tool.returncode == 0 and "--expected-commit" in tool.stdout
    merges = merge_history(app, commit)
    context = {"app": app, "revision": revision, "commit": commit, "cache": cache, "fetch": fetch,
               "urls": urls or {}, "molly_branch": molly_branch, "expected_commit_supported": supported}
    report = {"app": {"path": str(app), "revision": revision, "commit": commit,
                      "expected_commit_option": supported}, "sources": []}
    for source in sources:
        try:
            if source["route"] == "upstream-merge":
                report["sources"].append(merge_source(source, merges, context))
            elif source["route"] == "selective-feature":
                report["sources"].append(selective_source(source, context))
        except StatusError as error:
            report["sources"].append({"id": source["id"], "repository": source["repository"],
                                      "route": source["route"], "error": str(error)})
    return report


def render(report):
    app = report["app"]
    lines = [f"App {app['path']} at {app['revision']} ({short(app['commit'])})"]
    for source in report["sources"]:
        lines += ["", f"{source['id']} ({source['repository']}, {source['route']})"]
        if "error" in source:
            lines.append(f"  error: {source['error']}")
        elif source["route"] == "selective-feature":
            reviewed = source["reviewed"]
            lines.append(f"  last review: {reviewed['ref']} at {short(reviewed['commit'])}")
            if reviewed["pinned_tag_moved"]:
                lines.append("  warning: that tag now points at a different commit; review the change explicitly")
            if reviewed["pinned_tag_missing"]:
                lines.append("  warning: that tag no longer exists upstream")
            if source["new_tags"]:
                lines.append(f"  new tags since the review ({len(source['new_tags'])}), manual review:")
                lines += [f"    {tag['ref']} {short(tag['commit'])}" for tag in source["new_tags"]]
            else:
                lines.append("  no new tags since the review; manual review has nothing to start from")
        else:
            merged, candidate = source["last_merged"], source["candidate"]
            lines.append(f"  last merged: {merged['name']} at {short(merged['commit'])} (merge {short(merged['merge'])})")
            lines.append(f"  candidate:   {candidate['ref']} at {short(candidate['commit'])}"
                         + (f" (branch from {source['branch_selection']})" if "branch_selection" in source else ""))
            if source.get("newer_tags_included"):
                lines.append(f"  other tags newer than the last merge (the candidate includes them): {', '.join(source['newer_tags_included'])}")
            if "newer_release_branches" in source:
                newer = source["newer_release_branches"]
                lines.append("  newer Molly release branch: " + (", ".join(newer) + " (choose explicitly, "
                             "see --molly-branch)" if newer else "none"))
            if not source["last_merged_is_ancestor"]:
                lines.append("  warning: the last merged commit is not in the candidate's history "
                             "(the upstream ref was rewritten); inspect before merging")
            if source["waiting"]:
                dates = source["oldest_date"] if source["oldest_date"] == source["newest_date"] else \
                    f"{source['oldest_date']} to {source['newest_date']}"
                lines.append(f"  waiting: {source['waiting']} commits not in the app ({dates})")
                lines.append(f"  range:   {short(merged['commit'])}..{short(candidate['commit'])}")
                lines.append(f"  run in the app checkout: {source['command']}")
                if not app["expected_commit_option"]:
                    lines.append("  note: --expected-commit is not in this app revision's merge-upstream.py yet "
                                 "(app PR #227); drop it until that merges")
            else:
                lines.append("  waiting: nothing; the app already contains the candidate")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, default=ROOT, help="release-engineering checkout")
    parser.add_argument("--app", type=Path, default=DEFAULT_APP, help="app checkout (default: %(default)s)")
    parser.add_argument("--revision", default=DEFAULT_REVISION, help="app revision to read (default: %(default)s)")
    parser.add_argument("--molly-branch", help="Molly branch to report instead of the branch of the last merge")
    parser.add_argument("--cache", type=Path, help="cache repositories (default: ~/.cache/collie-upstream-status)")
    parser.add_argument("--no-fetch", dest="fetch", action="store_false",
                        help="count with the commits already in the cache")
    parser.add_argument("--url", action="append", default=[], metavar="ID=URL",
                        help="read this source from another repository or path (for tests)")
    parser.add_argument("--json", action="store_true", help="print the same data as JSON")
    args = parser.parse_args()
    urls = dict(item.split("=", 1) for item in args.url)
    try:
        report = collect(args.root, args.app, args.revision, args.cache, args.fetch, urls, args.molly_branch)
    except (validator.RegistryError, StatusError, OSError, ValueError, json.JSONDecodeError) as error:
        parser.exit(1, f"{error}\n")
    print(json.dumps(report, indent=2) if args.json else render(report))
    sys.exit(1 if any("error" in source for source in report["sources"]) else 0)


if __name__ == "__main__":
    main()
