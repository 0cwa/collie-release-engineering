#!/usr/bin/env python3
"""Transfer screenshot data across the unprivileged build/publisher boundary.

The publisher never checks out or runs the candidate tree. It reads one bounded
JSON member from an artifact ZIP, permits only PNGs in existing screenshot source
sets, and creates Git LFS pointers directly in a fresh bare repository's index.
No artifact is extracted, and no candidate hook, filter, or Git configuration is
used to upload objects or update the branch.
"""

import argparse
import base64
import binascii
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import struct
import subprocess
import tempfile
import urllib.parse
import zipfile
import zlib


MANIFEST_NAME = "screenshot-changes.json"
MAX_FILES = 1000
MAX_IMAGE_BYTES = 16 * 1024 * 1024
MAX_TOTAL_BYTES = 128 * 1024 * 1024
MAX_MANIFEST_BYTES = MAX_TOTAL_BYTES * 4 // 3 + 1024 * 1024
MAX_ARCHIVE_BYTES = MAX_MANIFEST_BYTES + 1024 * 1024
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
SCREENSHOT_PATHSPEC = ":(glob)**/src/screenshotTest*/reference/**/*.png"
SHA1 = re.compile(r"[0-9a-f]{40}")


class InvalidScreenshot(ValueError):
    """Input cannot cross the screenshot publication boundary."""


def require_sha(value):
    if not isinstance(value, str) or SHA1.fullmatch(value) is None:
        raise InvalidScreenshot("Expected an immutable 40-character commit SHA")
    return value


def reference_source_set(path):
    """Return the screenshot source set, rejecting aliases and unsafe paths."""
    if not isinstance(path, str):
        raise InvalidScreenshot("Screenshot path must be a string")
    try:
        encoded = path.encode("utf-8")
    except UnicodeEncodeError as error:
        raise InvalidScreenshot("Screenshot path must be valid UTF-8") from error
    parts = path.split("/")
    if (
        not encoded or len(encoded) > 1024
        or PurePosixPath(path).is_absolute()
        or any(part in ("", ".", "..") for part in parts)
        or any(part.lower().startswith(".git") for part in parts)
        or any(part.endswith((" ", ".")) for part in parts)
        or "\\" in path or ":" in path
        or any(ord(character) < 32 or ord(character) == 127 for character in path)
        or not path.endswith(".png")
    ):
        raise InvalidScreenshot("Only canonical relative screenshot PNG paths are allowed")
    for index in range(len(parts) - 3):
        if (
            parts[index] == "src"
            and re.fullmatch(r"screenshotTest[A-Za-z0-9_]*", parts[index + 1])
            and parts[index + 2] == "reference"
        ):
            return "/".join(parts[:index + 2])
    raise InvalidScreenshot("Path is outside a screenshot reference directory")


def validate_png(data):
    """Check the PNG container without invoking an image decoder or executing it."""
    if not data or len(data) > MAX_IMAGE_BYTES or not data.startswith(PNG_SIGNATURE):
        raise InvalidScreenshot("Screenshot must be a bounded PNG image")
    offset, chunks, has_data = len(PNG_SIGNATURE), 0, False
    while offset + 12 <= len(data):
        length = struct.unpack_from(">I", data, offset)[0]
        kind = data[offset + 4:offset + 8]
        end = offset + 12 + length
        if end > len(data) or re.fullmatch(rb"[A-Za-z]{4}", kind) is None:
            raise InvalidScreenshot("Malformed PNG chunk")
        payload = data[offset + 8:offset + 8 + length]
        checksum = struct.unpack_from(">I", data, offset + 8 + length)[0]
        if zlib.crc32(kind + payload) & 0xFFFFFFFF != checksum:
            raise InvalidScreenshot("PNG chunk checksum mismatch")
        if chunks == 0:
            if kind != b"IHDR" or length != 13:
                raise InvalidScreenshot("PNG must start with an IHDR chunk")
            width, height, depth, color, compression, filtering, interlace = struct.unpack(
                ">IIBBBBB", payload
            )
            depths = {0: (1, 2, 4, 8, 16), 2: (8, 16), 3: (1, 2, 4, 8), 4: (8, 16), 6: (8, 16)}
            if (
                not 0 < width <= 16384 or not 0 < height <= 16384
                or width * height > 64 * 1024 * 1024
                or depth not in depths.get(color, ())
                or compression != 0 or filtering != 0 or interlace not in (0, 1)
            ):
                raise InvalidScreenshot("Unsupported or oversized PNG header")
        elif kind == b"IHDR":
            raise InvalidScreenshot("Duplicate PNG header")
        if kind == b"IDAT":
            has_data = has_data or length > 0
        if kind == b"IEND":
            if length != 0 or not has_data or end != len(data):
                raise InvalidScreenshot("Malformed PNG end or trailing data")
            return
        if kind[0] & 32 == 0 and kind not in (b"IHDR", b"PLTE", b"IDAT"):
            raise InvalidScreenshot("Unknown critical PNG chunk")
        chunks += 1
        offset = end
    raise InvalidScreenshot("PNG is incomplete")


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InvalidScreenshot("Duplicate JSON field")
        result[key] = value
    return result


def decode_manifest(raw, expected_sha):
    require_sha(expected_sha)
    if len(raw) > MAX_MANIFEST_BYTES:
        raise InvalidScreenshot("Screenshot manifest is too large")
    try:
        manifest = json.loads(raw, object_pairs_hook=unique_object)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise InvalidScreenshot("Malformed screenshot manifest") from error
    if (
        not isinstance(manifest, dict)
        or set(manifest) != {"schema", "base_commit", "changes"}
        or type(manifest["schema"]) is not int or manifest["schema"] != 1
        or manifest["base_commit"] != expected_sha
        or not isinstance(manifest["changes"], list)
        or len(manifest["changes"]) > MAX_FILES
    ):
        raise InvalidScreenshot("Screenshot manifest identity or schema mismatch")
    changes, total = {}, 0
    for change in manifest["changes"]:
        if not isinstance(change, dict) or set(change) != {"path", "content"}:
            raise InvalidScreenshot("Unexpected screenshot change fields")
        path, content = change["path"], change["content"]
        reference_source_set(path)
        if path in changes:
            raise InvalidScreenshot("Duplicate screenshot path")
        if content is not None:
            if not isinstance(content, str) or len(content) > (MAX_IMAGE_BYTES + 2) // 3 * 4:
                raise InvalidScreenshot("Screenshot content is too large or is not base64")
            try:
                content = base64.b64decode(content, validate=True)
            except (binascii.Error, ValueError) as error:
                raise InvalidScreenshot("Invalid base64 screenshot content") from error
            validate_png(content)
            total += len(content)
            if total > MAX_TOTAL_BYTES:
                raise InvalidScreenshot("Screenshot changes exceed the total size limit")
        changes[path] = content
    return changes


def read_artifact(archive, expected_sha):
    if len(archive) > MAX_ARCHIVE_BYTES:
        raise InvalidScreenshot("Screenshot artifact is too large")
    try:
        with zipfile.ZipFile(io.BytesIO(archive)) as source:
            members = source.infolist()
            if len(members) != 1:
                raise InvalidScreenshot("Artifact must contain exactly one manifest")
            member = members[0]
            mode = member.external_attr >> 16
            if (
                member.filename != MANIFEST_NAME or member.is_dir()
                or stat.S_IFMT(mode) not in (0, stat.S_IFREG)
                or mode & 0o111 or member.flag_bits & 1
                or member.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
                or member.file_size > MAX_MANIFEST_BYTES
            ):
                raise InvalidScreenshot("Artifact member is not a regular bounded manifest")
            # Never extract an archive supplied by the candidate build.
            with source.open(member) as stream:
                raw = stream.read(MAX_MANIFEST_BYTES + 1)
            return decode_manifest(raw, expected_sha)
    except (zipfile.BadZipFile, RuntimeError, OSError, EOFError) as error:
        raise InvalidScreenshot("Malformed screenshot artifact ZIP") from error


def git_environment():
    # Inherited Git overrides must not select a different repository, index,
    # hook directory, credential helper, executable, or LFS upload endpoint.
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    environment.update({
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_ATTR_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_LFS_SKIP_SMUDGE": "1",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_AUTHOR_NAME": "github-actions[bot]",
        "GIT_AUTHOR_EMAIL": "41898282+github-actions[bot]@users.noreply.github.com",
        "GIT_COMMITTER_NAME": "github-actions[bot]",
        "GIT_COMMITTER_EMAIL": "41898282+github-actions[bot]@users.noreply.github.com",
    })
    return environment


def git(repository, *arguments, data=None):
    command = [
        "git", "-C", str(repository),
        "-c", "core.hooksPath=" + os.devnull,
        "-c", "core.fsmonitor=false",
        "-c", "protocol.file.allow=never",
        "-c", "protocol.ext.allow=never",
        "-c", "credential.helper=",
        "-c", "credential.helper=!gh auth git-credential",
        "-c", "credential.useHttpPath=true",
        *arguments,
    ]
    result = subprocess.run(command, input=data, capture_output=True, env=git_environment(), timeout=600)
    if result.returncode:
        # Do not echo transport diagnostics: LFS responses can include signed URLs.
        raise InvalidScreenshot("Git operation failed; no screenshot success is claimed")
    return result.stdout


def collect(repository, base_sha, output):
    require_sha(base_sha)
    if git(repository, "rev-parse", "HEAD").decode().strip() != base_sha:
        raise InvalidScreenshot("Candidate HEAD changed during screenshot generation")
    tracked = git(repository, "diff", "--no-ext-diff", "--no-textconv", "--name-only", "-z",
                  base_sha, "--", SCREENSHOT_PATHSPEC)
    untracked = git(repository, "ls-files", "--others", "--exclude-standard", "-z",
                    "--", SCREENSHOT_PATHSPEC)
    paths = sorted({name.decode("utf-8") for name in (tracked + untracked).split(b"\0") if name})
    changes, total = [], 0
    for path in paths:
        reference_source_set(path)
        current = repository
        content = None
        for part in PurePosixPath(path).parts:
            current = current / part
            try:
                mode = current.lstat().st_mode
            except FileNotFoundError:
                break
            if stat.S_ISLNK(mode):
                raise InvalidScreenshot("Screenshot path contains a symlink")
            if current != repository / path and not stat.S_ISDIR(mode):
                raise InvalidScreenshot("Screenshot parent is not a directory")
        else:
            if not stat.S_ISREG(mode) or current.stat().st_size > MAX_IMAGE_BYTES:
                raise InvalidScreenshot("Screenshot is not a bounded regular file")
            content = current.read_bytes()
            validate_png(content)
            total += len(content)
            if total > MAX_TOTAL_BYTES:
                raise InvalidScreenshot("Screenshot changes exceed the total size limit")
        changes.append({"path": path, "content": None if content is None else base64.b64encode(content).decode("ascii")})
    manifest = json.dumps({"schema": 1, "base_commit": base_sha, "changes": changes}).encode()
    decode_manifest(manifest, base_sha)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(manifest)
    print(f"Collected {len(changes)} screenshot changes")


def validate_target(repository, branch, base_sha, protected_branch):
    require_sha(base_sha)
    if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository) is None:
        raise InvalidScreenshot("Invalid GitHub repository")
    if (
        not branch or branch in ("main", protected_branch) or branch.startswith("8.")
        or branch.startswith(("/", "-")) or branch.endswith(("/", "."))
        or ".." in branch or "@{" in branch or "//" in branch
        or any(character in branch for character in " ~^:?*[\\")
        or any(ord(character) < 32 or ord(character) == 127 for character in branch)
        or any(part.startswith(".") or part.endswith(".lock") for part in branch.split("/"))
    ):
        raise InvalidScreenshot("Refusing to update an invalid or protected branch")


def api(endpoint, *, binary=False):
    result = subprocess.run(
        ["gh", "api", "--hostname", "github.com", "--method", "GET", endpoint],
        capture_output=True, timeout=120,
    )
    if result.returncode:
        raise InvalidScreenshot("GitHub read failed; no screenshot success is claimed")
    if len(result.stdout) > MAX_ARCHIVE_BYTES:
        raise InvalidScreenshot("GitHub response exceeds the screenshot size limit")
    return result.stdout if binary else json.loads(result.stdout)


def download_changes(repository, artifact_id, run_id, attempt, base_sha):
    if any(re.fullmatch(r"[1-9][0-9]*", value) is None for value in (artifact_id, run_id, attempt)):
        raise InvalidScreenshot("Invalid artifact or workflow run identity")
    endpoint = f"repos/{repository}/actions/artifacts/{artifact_id}"
    metadata = api(endpoint)
    if (
        metadata.get("id") != int(artifact_id)
        or metadata.get("name") != f"screenshot-changes-{run_id}-{attempt}"
        or metadata.get("workflow_run", {}).get("id") != int(run_id)
        or metadata.get("expired") is not False
        or type(metadata.get("size_in_bytes")) is not int
        or not 0 < metadata["size_in_bytes"] <= MAX_ARCHIVE_BYTES
    ):
        raise InvalidScreenshot("Artifact does not belong to this workflow run and attempt")
    return read_artifact(api(endpoint + "/zip", binary=True), base_sha)


def check_run(repository, run_id, attempt, branch, base_sha):
    """Bind data to the exact branch-scoped, successful generation attempt."""
    if any(re.fullmatch(r"[1-9][0-9]*", value) is None for value in (run_id, attempt)):
        raise InvalidScreenshot("Invalid workflow run identity")
    run = api(f"repos/{repository}/actions/runs/{run_id}/attempts/{attempt}")
    if (
        run.get("id") != int(run_id) or run.get("run_attempt") != int(attempt)
        or run.get("head_sha") != base_sha or run.get("head_branch") != branch
        or run.get("head_repository", {}).get("full_name") != repository
        or run.get("event") != "workflow_dispatch" or run.get("status") != "completed"
        or run.get("conclusion") != "success"
        or run.get("path") != ".github/workflows/update-screenshots.yml"
    ):
        raise InvalidScreenshot("Screenshot producer workflow, source or attempt differs")

def check_head(repository, branch, base_sha, pr_number):
    reference = api(f"repos/{repository}/git/ref/heads/{urllib.parse.quote(branch, safe='')}")
    if (
        reference.get("ref") != "refs/heads/" + branch
        or reference.get("object", {}).get("type") != "commit"
        or reference["object"].get("sha") != base_sha
    ):
        raise InvalidScreenshot("Branch moved; request screenshots again for its new head")
    if pr_number:
        if re.fullmatch(r"[1-9][0-9]*", pr_number) is None:
            raise InvalidScreenshot("Invalid pull request number")
        pull = api(f"repos/{repository}/pulls/{pr_number}")
        head = pull.get("head", {})
        if (
            pull.get("state") != "open" or head.get("sha") != base_sha
            or head.get("ref") != branch or head.get("repo", {}).get("full_name") != repository
        ):
            raise InvalidScreenshot("Pull request closed, moved, or changed repositories")


def prepare_commit(repository, base_sha, changes, lfs_storage):
    """Validate the base tree, then write only canonical pointers to its index."""
    require_sha(base_sha)
    entries = {}
    for row in git(repository, "ls-tree", "-r", "-t", "-z", base_sha).split(b"\0"):
        if row:
            metadata, name = row.split(b"\t", 1)
            entries[name.decode("utf-8")] = metadata.decode("ascii").split()
    for path, content in changes.items():
        source_set = reference_source_set(path)
        if entries.get(source_set, [None, None])[1] != "tree":
            raise InvalidScreenshot("Screenshot source set does not exist in the approved tree")
        for parent in PurePosixPath(path).parents:
            entry = entries.get(str(parent))
            if entry is not None and entry[1] != "tree":
                raise InvalidScreenshot("Screenshot path has a non-directory ancestor")
        existing = entries.get(path)
        if existing is not None and existing[:2] != ["100644", "blob"]:
            raise InvalidScreenshot("Screenshot target is a symlink, executable, or non-file")
        if existing is None and content is None:
            raise InvalidScreenshot("Cannot delete a screenshot missing from the approved tree")
    git(repository, "read-tree", base_sha)
    if not changes:
        return None, []
    paths = b"".join(path.encode("utf-8") + b"\0" for path in changes)
    attributes = git(repository, "check-attr", "--cached", "-z", "--stdin", "filter", data=paths).split(b"\0")
    expected = [[path.encode("utf-8"), b"filter", b"lfs"] for path in changes]
    if attributes[-1:] != [b""] or [attributes[index:index + 3] for index in range(0, len(attributes) - 1, 3)] != expected:
        raise InvalidScreenshot("Screenshot paths must use the existing Git LFS filter")
    updates, objects = [], []
    for path, content in changes.items():
        if content is None:
            updates.append(b"0 " + b"0" * 40 + b"\t" + path.encode("utf-8") + b"\0")
            continue
        oid = hashlib.sha256(content).hexdigest()
        pointer = f"version https://git-lfs.github.com/spec/v1\noid sha256:{oid}\nsize {len(content)}\n".encode()
        blob = git(repository, "hash-object", "-w", "--stdin", data=pointer).strip()
        if entries.get(path, [None, None, None])[2] == blob.decode("ascii"):
            continue
        target = lfs_storage / "objects" / oid[:2] / oid[2:4] / oid
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        objects.append(oid)
        updates.append(b"100644 " + blob + b"\t" + path.encode("utf-8") + b"\0")
    if not updates:
        return None, []
    git(repository, "update-index", "-z", "--index-info", data=b"".join(updates))
    tree = git(repository, "write-tree").decode().strip()
    commit = git(repository, "commit-tree", tree, "-p", base_sha,
                 data=b"Update screenshot reference images.\n").decode().strip()
    return require_sha(commit), sorted(set(objects))


def publish(repository, branch, base_sha, protected_branch, artifact_id, run_id, attempt, pr_number):
    validate_target(repository, branch, base_sha, protected_branch)
    check_run(repository, run_id, attempt, branch, base_sha)
    changes = download_changes(repository, artifact_id, run_id, attempt, base_sha)
    check_head(repository, branch, base_sha, pr_number)
    if not changes:
        print("Screenshots are already up to date.")
        return
    remote = f"https://github.com/{repository}.git"
    with tempfile.TemporaryDirectory(prefix="screenshot-publish-") as temporary:
        directory = Path(temporary)
        bare, storage = directory / "repository.git", directory / "lfs"
        git(directory, "init", "--bare", "--template=", str(bare))
        git(bare, "remote", "add", "origin", remote)
        # Fetch objects only. Candidate files and .lfsconfig are never checked out.
        git(bare, "fetch", "--no-tags", "--depth=1", "origin", base_sha)
        if git(bare, "rev-parse", "FETCH_HEAD").decode().strip() != base_sha:
            raise InvalidScreenshot("Fetched source does not match the approved head")
        commit, objects = prepare_commit(bare, base_sha, changes, storage)
        if commit is None:
            print("Screenshots are already up to date.")
            return
        check_head(repository, branch, base_sha, pr_number)
        if objects:
            endpoint = remote + "/info/lfs"
            git(bare,
                "-c", "lfs.storage=" + str(storage),
                "-c", "lfs.url=" + endpoint,
                "-c", "lfs.pushurl=" + endpoint,
                "-c", "remote.origin.lfsurl=" + endpoint,
                "-c", "remote.origin.lfspushurl=" + endpoint,
                "-c", "lfs.basictransfersonly=true",
                "-c", "lfs.allowincompletepush=false",
                "-c", "lfs.locksverify=false",
                "lfs", "push", "--object-id", "origin", "--stdin",
                data=("\n".join(objects) + "\n").encode("ascii"))
        # The new commit has exactly base_sha as its parent. The explicit lease
        # also rejects a branch movement during upload, including a force push.
        git(bare, "push", "--porcelain", f"--force-with-lease=refs/heads/{branch}:{base_sha}",
            "origin", f"{commit}:refs/heads/{branch}")
        print(f"Published {len(changes)} screenshot changes in {commit}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    collector = commands.add_parser("collect")
    collector.add_argument("--repository", type=Path, required=True)
    collector.add_argument("--base-sha", required=True)
    collector.add_argument("--output", type=Path, required=True)
    publisher = commands.add_parser("publish")
    publisher.add_argument("--repository", required=True)
    publisher.add_argument("--branch", required=True)
    publisher.add_argument("--base-sha", required=True)
    publisher.add_argument("--protected-branch", required=True)
    publisher.add_argument("--artifact-id", required=True)
    publisher.add_argument("--run-id", required=True)
    publisher.add_argument("--attempt", required=True)
    publisher.add_argument("--pr-number", default="")
    arguments = vars(parser.parse_args())
    command = arguments.pop("command")
    try:
        (collect if command == "collect" else publish)(**arguments)
    except (InvalidScreenshot, OSError, subprocess.TimeoutExpired) as error:
        parser.exit(1, f"Screenshot update rejected: {error}\n")


if __name__ == "__main__":
    main()
