#!/usr/bin/env python3
"""Resolve one trusted, complete source-specific review manifest.

The versioned ``collie-android-v1`` profile hashes tracked application and
analysis-recipe files, excluding policy payloads and documentation. Its roots
are deliberately fixed in this pinned helper rather than read from analyzed
source or from the catalog being selected.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
from typing import Any


PROFILE = "collie-android-v1"
PROFILE_ROOTS = (
    "app/", "core/", "feature/", "lib/", "build-inputs/", "gradle/",
    "buildSrc/", "tooling/",
)
PROFILE_FILES = frozenset({
    ".github/workflows/codeql.yml",
    "build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts",
    "gradle.properties", "gradlew", "gradlew.bat",
})
LANGUAGES = frozenset({"actions", "python", "javascript-typescript", "c-cpp", "java-kotlin"})
HASH = re.compile(r"[0-9a-f]{64}\Z")
MAX_CATALOG_BYTES = 1_000_000
MAX_SNAPSHOTS = 256
MAX_MANIFEST_BYTES = 8_000_000


class SnapshotSelectionError(ValueError):
    """Opt-in selection could not be verified; no review file may be applied."""


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise SnapshotSelectionError("JSON contains a duplicate object key")
        result[key] = value
    return result


def _read_json(path: Path, limit: int, label: str) -> tuple[dict, bytes]:
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise SnapshotSelectionError(f"{label} cannot be read") from error
    if len(raw) > limit:
        raise SnapshotSelectionError(f"{label} exceeds its size limit")
    try:
        document = json.loads(raw.decode("utf-8"), object_pairs_hook=_no_duplicate_keys)
    except (UnicodeError, ValueError) as error:
        raise SnapshotSelectionError(f"{label} is malformed JSON") from error
    if not isinstance(document, dict):
        raise SnapshotSelectionError(f"{label} must be a JSON object")
    return document, raw


def _canonical_relative(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise SnapshotSelectionError(f"{label} must be a canonical relative path")
    path = PurePosixPath(value)
    if (path.is_absolute() or path.as_posix() != value or
            any(part in ("", ".", "..") for part in value.split("/"))):
        raise SnapshotSelectionError(f"{label} must be a canonical relative path")
    return value


def _trusted_file(root: Path, relative: str, label: str) -> Path:
    relative = _canonical_relative(relative, label)
    current = root
    for part in relative.split("/"):
        current = current / part
        try:
            if current.is_symlink():
                raise SnapshotSelectionError(f"{label} must not use symlinks")
        except OSError as error:
            raise SnapshotSelectionError(f"{label} is unavailable") from error
    try:
        resolved = current.resolve(strict=True)
        if not resolved.is_relative_to(root) or not resolved.is_file():
            raise SnapshotSelectionError(f"{label} is outside the trusted policy root")
    except OSError as error:
        raise SnapshotSelectionError(f"{label} is unavailable") from error
    return resolved


def _profile_path(path: str) -> bool:
    return path in PROFILE_FILES or any(path == root.rstrip("/") or path.startswith(root) for root in PROFILE_ROOTS)



def _validate_profile_inventory(root: Path, tracked_paths: set[str]) -> None:
    """Reject disk inputs omitted from the index, including Git-ignored source.

    Only Gradle's fixed build/cache directories directly beneath an indexed
    module build script are pruned. An arbitrary source directory named build
    remains part of the inventory. Generated output is not a source snapshot.
    """
    def visit(path: Path, optional: bool = False) -> None:
        relative = path.relative_to(root).as_posix()
        try:
            info = path.lstat()
        except FileNotFoundError as error:
            if optional:
                return
            raise SnapshotSelectionError("analyzed profile input disappeared") from error
        except OSError as error:
            raise SnapshotSelectionError("analyzed profile inventory is unavailable") from error
        if stat.S_ISLNK(info.st_mode):
            raise SnapshotSelectionError("analyzed profile contains a symlink")
        if stat.S_ISDIR(info.st_mode):
            parent = path.parent.relative_to(root).as_posix()
            if path.name in {"build", ".gradle"} and any(
                f"{parent}/{script}" in tracked_paths for script in ("build.gradle", "build.gradle.kts")
            ):
                return
            try:
                children = sorted(path.iterdir())
            except OSError as error:
                raise SnapshotSelectionError("analyzed profile inventory is unavailable") from error
            for child in children:
                visit(child)
        elif not stat.S_ISREG(info.st_mode):
            raise SnapshotSelectionError("analyzed profile contains a non-regular input")
        elif relative not in tracked_paths:
            raise SnapshotSelectionError("analyzed source contains an untracked profile input")

    for prefix in PROFILE_ROOTS:
        visit(root / prefix.rstrip("/"), optional=True)
    for relative in sorted(PROFILE_FILES):
        visit(root / relative, optional=True)


def source_fingerprint(source_root: Path, language: str) -> str:
    """Hash actual tracked file bytes, paths, and Git modes for the fixed profile."""
    if language not in LANGUAGES:
        raise SnapshotSelectionError("unsupported analysis language")
    try:
        root = source_root.resolve(strict=True)
        if not root.is_dir():
            raise SnapshotSelectionError("analyzed source root is unavailable")
        top = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        if Path(top).resolve(strict=True) != root:
            raise SnapshotSelectionError("analyzed source root must be a Git worktree root")
        listing = subprocess.run(
            ["git", "-C", str(root), "ls-files", "--stage", "-z"],
            check=True, capture_output=True,
        ).stdout
    except SnapshotSelectionError:
        raise
    except (OSError, subprocess.CalledProcessError) as error:
        raise SnapshotSelectionError("tracked analyzed source files cannot be enumerated") from error

    entries: list[tuple[str, str, str]] = []
    anchors: set[str] = set()
    for record in listing.split(b"\0"):
        if not record:
            continue
        try:
            metadata, raw_path = record.split(b"\t", 1)
            mode, _object_id, stage = metadata.decode("ascii").split(" ")
            path = raw_path.decode("utf-8")
        except (ValueError, UnicodeError) as error:
            raise SnapshotSelectionError("Git tracked-file listing is malformed") from error
        if not _profile_path(path):
            continue
        if stage != "0":
            raise SnapshotSelectionError("analyzed source has an unmerged profile input")
        if path in ("settings.gradle", "settings.gradle.kts", ".github/workflows/codeql.yml"):
            anchors.add(path)
        if path == "app/" or path.startswith("app/"):
            anchors.add("app/")
        parts = PurePosixPath(path).parts
        if (PurePosixPath(path).is_absolute() or "\\" in path or ".." in parts or
                not path or mode not in {"100644", "100755"}):
            raise SnapshotSelectionError("analyzed profile contains an unsafe or non-file input")
        file_path = root.joinpath(*parts)
        current = root
        for part in parts:
            current = current / part
            try:
                if current.is_symlink():
                    raise SnapshotSelectionError("analyzed profile contains a symlink")
            except OSError as error:
                raise SnapshotSelectionError("analyzed profile input is unavailable") from error
        try:
            info = file_path.stat(follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode):
                raise SnapshotSelectionError("analyzed profile contains a non-regular input")
            content = file_path.read_bytes()
        except OSError as error:
            raise SnapshotSelectionError("tracked analyzed profile input is unavailable") from error
        entries.append((path, mode, hashlib.sha256(content).hexdigest()))

    _validate_profile_inventory(root, {path for path, _mode, _digest in entries})

    if "app/" not in anchors or not any(path in anchors for path in ("settings.gradle", "settings.gradle.kts")):
        raise SnapshotSelectionError("analyzed source does not satisfy collie-android-v1 anchors")
    if ".github/workflows/codeql.yml" not in anchors:
        raise SnapshotSelectionError("tracked CodeQL workflow is missing from collie-android-v1")
    identity = {
        "schema": 1,
        "profile": PROFILE,
        "language": language,
        "files": [{"path": path, "mode": mode, "sha256": digest}
                  for path, mode, digest in sorted(entries)],
    }
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def resolve_review_snapshot(
    *,
    source_root: Path,
    trusted_policy_root: Path,
    catalog_relative_path: str,
    default_reviews_relative_path: str,
    language: str,
    analysis_config_relative_path: str,
    analysis_config_sha256: str,
) -> tuple[Path, dict[str, Any]]:
    """Select one complete manifest or the unchanged default on a catalog miss."""
    try:
        policy_root = trusted_policy_root.resolve(strict=True)
        if trusted_policy_root.is_symlink() or not policy_root.is_dir():
            raise SnapshotSelectionError("trusted policy root is unavailable")
    except OSError as error:
        raise SnapshotSelectionError("trusted policy root is unavailable") from error
    if language not in LANGUAGES:
        raise SnapshotSelectionError("unsupported analysis language")
    if not isinstance(analysis_config_sha256, str) or not HASH.fullmatch(analysis_config_sha256):
        raise SnapshotSelectionError("trusted analysis config requires a lowercase SHA-256")

    catalog_path = _trusted_file(policy_root, catalog_relative_path, "review catalog")
    default_path = _trusted_file(policy_root, default_reviews_relative_path, "default review manifest")
    config_path = _trusted_file(policy_root, analysis_config_relative_path, "trusted analysis config")
    catalog, catalog_bytes = _read_json(catalog_path, MAX_CATALOG_BYTES, "review catalog")
    if (set(catalog) != {"schema", "profile", "snapshots"} or
            type(catalog.get("schema")) is not int or catalog.get("schema") != 1):
        raise SnapshotSelectionError("unsupported review catalog schema")
    if catalog.get("profile") != PROFILE:
        raise SnapshotSelectionError("review catalog uses an unsupported source profile")
    snapshots = catalog.get("snapshots")
    if not isinstance(snapshots, list) or len(snapshots) > MAX_SNAPSHOTS:
        raise SnapshotSelectionError("review catalog snapshots must be a bounded array")

    try:
        config_bytes = config_path.read_bytes()
    except OSError as error:
        raise SnapshotSelectionError("trusted analysis config cannot be read") from error
    actual_config_hash = hashlib.sha256(config_bytes).hexdigest()
    if actual_config_hash != analysis_config_sha256:
        raise SnapshotSelectionError("trusted analysis config bytes do not match the supplied SHA-256")
    source_hash = source_fingerprint(source_root, language)

    identity_keys: set[tuple[str, str, str]] = set()
    validated: list[tuple[dict[str, Any], str, Path, str]] = []
    for entry in snapshots:
        if not isinstance(entry, dict) or set(entry) != {
            "language", "source_sha256", "analysis_config_sha256", "manifest", "manifest_sha256"
        }:
            raise SnapshotSelectionError("review catalog contains a malformed snapshot entry")
        entry_language = entry.get("language")
        entry_source = entry.get("source_sha256")
        entry_config = entry.get("analysis_config_sha256")
        manifest_relative = _canonical_relative(entry.get("manifest"), "snapshot manifest path")
        manifest_hash = entry.get("manifest_sha256")
        if (not isinstance(entry_language, str) or entry_language not in LANGUAGES
                or not isinstance(entry_source, str) or not HASH.fullmatch(entry_source)
                or not isinstance(entry_config, str) or not HASH.fullmatch(entry_config)
                or not isinstance(manifest_hash, str) or not HASH.fullmatch(manifest_hash)):
            raise SnapshotSelectionError("review catalog contains an invalid snapshot identity or digest")
        key = (entry_language, entry_source, entry_config)
        if key in identity_keys:
            raise SnapshotSelectionError("review catalog contains duplicate snapshot identities")
        identity_keys.add(key)
        manifest_path = _trusted_file(policy_root, manifest_relative, "snapshot manifest")
        try:
            manifest_bytes = manifest_path.read_bytes()
        except OSError as error:
            raise SnapshotSelectionError("snapshot manifest cannot be read") from error
        if len(manifest_bytes) > MAX_MANIFEST_BYTES:
            raise SnapshotSelectionError("snapshot manifest exceeds its size limit")
        actual_manifest_hash = hashlib.sha256(manifest_bytes).hexdigest()
        if actual_manifest_hash != manifest_hash:
            raise SnapshotSelectionError("snapshot manifest SHA-256 does not match the catalog")
        validated.append((entry, manifest_relative, manifest_path, actual_manifest_hash))

    matches = [row for row in validated if row[0]["language"] == language
               and row[0]["source_sha256"] == source_hash
               and row[0]["analysis_config_sha256"] == actual_config_hash]
    if len(matches) > 1:
        raise SnapshotSelectionError("review catalog selection is ambiguous")
    if matches:
        _entry, manifest_relative, manifest_path, manifest_hash = matches[0]
        selected_kind = "matched-snapshot"
    else:
        manifest_relative = _canonical_relative(default_reviews_relative_path, "default review manifest path")
        manifest_path = default_path
        try:
            manifest_bytes = default_path.read_bytes()
        except OSError as error:
            raise SnapshotSelectionError("default review manifest cannot be read") from error
        manifest_hash = hashlib.sha256(manifest_bytes).hexdigest()
        selected_kind = "default-no-match"

    return manifest_path, {
        "selection": selected_kind,
        "catalog_path": _canonical_relative(catalog_relative_path, "review catalog path"),
        "catalog_sha256": hashlib.sha256(catalog_bytes).hexdigest(),
        "selected_review_path": manifest_relative,
        "selected_review_sha256": manifest_hash,
        "language": language,
        "profile": PROFILE,
        "source_sha256": source_hash,
        "analysis_config_path": _canonical_relative(analysis_config_relative_path, "trusted analysis config path"),
        "analysis_config_sha256": actual_config_hash,
    }
