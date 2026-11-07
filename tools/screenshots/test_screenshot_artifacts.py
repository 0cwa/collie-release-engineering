import base64
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import struct
import tempfile
import unittest
from unittest import mock
import zipfile
import zlib

import screenshot_artifacts as screenshots


BASE = "a" * 40
PATH = "feature/example/src/screenshotTestDebug/reference/example.png"
SOURCE = "feature/example/src/screenshotTestDebug/kotlin/Example.kt"
ATTRIBUTES = b"**/src/screenshotTest*/reference/**/*.png filter=lfs diff=lfs merge=lfs -text\n"


def png(pixel=b"\x12\x34\x56\xff"):
    def chunk(kind, content):
        return struct.pack(">I", len(content)) + kind + content + struct.pack(">I", zlib.crc32(kind + content))
    return (
        screenshots.PNG_SIGNATURE
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"\0" + pixel))
        + chunk(b"IEND", b"")
    )


def manifest(changes=None, base=BASE):
    if changes is None:
        changes = [{"path": PATH, "content": base64.b64encode(png()).decode()}]
    return json.dumps({"schema": 1, "base_commit": base, "changes": changes}).encode()


def archive(raw=None, filename=screenshots.MANIFEST_NAME, mode=stat.S_IFREG | 0o644, extra=None):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as result:
        entry = zipfile.ZipInfo(filename)
        entry.create_system = 3
        entry.external_attr = mode << 16
        result.writestr(entry, manifest() if raw is None else raw)
        if extra:
            result.writestr(extra, b"malicious content")
    return output.getvalue()


class ArtifactBoundaryTests(unittest.TestCase):
    def test_valid_images_deletions_and_no_changes(self):
        changes = [{"path": PATH, "content": base64.b64encode(png()).decode()},
                   {"path": PATH.replace("example.png", "removed.png"), "content": None}]
        result = screenshots.read_artifact(archive(manifest(changes)), BASE)
        self.assertEqual(result[PATH], png())
        self.assertIsNone(result[changes[1]["path"]])
        self.assertEqual(screenshots.read_artifact(archive(manifest([])), BASE), {})

    def test_archive_members_cannot_escape_or_execute(self):
        for name in ("../screenshot-changes.json", "/tmp/screenshot-changes.json",
                     "nested/screenshot-changes.json", ".github/workflows/injected.yml"):
            with self.subTest(name=name), self.assertRaises(screenshots.InvalidScreenshot):
                screenshots.read_artifact(archive(filename=name), BASE)
        for mode in (stat.S_IFLNK | 0o777, stat.S_IFREG | 0o755, stat.S_IFDIR | 0o755):
            with self.subTest(mode=mode), self.assertRaises(screenshots.InvalidScreenshot):
                screenshots.read_artifact(archive(mode=mode), BASE)
        with self.assertRaises(screenshots.InvalidScreenshot):
            screenshots.read_artifact(archive(extra="../workflow/helper.py"), BASE)
        with self.assertRaises(screenshots.InvalidScreenshot):
            screenshots.read_artifact(archive(extra=screenshots.MANIFEST_NAME), BASE)

    def test_archive_and_uncompressed_manifest_sizes_are_bounded(self):
        payload = archive()
        with mock.patch.object(screenshots, "MAX_ARCHIVE_BYTES", len(payload) - 1):
            with self.assertRaises(screenshots.InvalidScreenshot):
                screenshots.read_artifact(payload, BASE)
        with mock.patch.object(screenshots, "MAX_MANIFEST_BYTES", 10):
            with self.assertRaises(screenshots.InvalidScreenshot):
                screenshots.read_artifact(payload, BASE)
        with self.assertRaises(screenshots.InvalidScreenshot):
            screenshots.read_artifact(b"not a zip", BASE)

    def test_duplicate_fields_schema_changes_and_stale_identity_fail(self):
        bad = [
            b'{"schema":1,"schema":1,"base_commit":"' + BASE.encode() + b'","changes":[]}',
            manifest().replace(b'"schema": 1', b'"schema": true'),
            manifest().replace(b'"schema": 1', b'"schema": 2'),
            manifest().replace(b'"changes": [', b'"extra": true, "changes": ['),
            manifest(base="b" * 40),
            b"[]", b"null", b"not json",
        ]
        for raw in bad:
            with self.subTest(raw=raw), self.assertRaises(screenshots.InvalidScreenshot):
                screenshots.decode_manifest(raw, BASE)
        change = {"path": PATH, "content": None}
        with self.assertRaises(screenshots.InvalidScreenshot):
            screenshots.decode_manifest(manifest([change, change]), BASE)

    def test_only_canonical_screenshot_png_paths_cross_the_boundary(self):
        paths = [
            "../" + PATH, "/" + PATH, PATH.replace("/example.png", "/../example.png"),
            PATH.replace("/reference/", "//reference/"), PATH.replace("/reference/", "/./reference/"),
            PATH.replace("/reference/", "/reference/.git/"), PATH.replace("/reference/", "/reference/.GiT/"),
            PATH.replace("example.png", "bad\\name.png"), PATH.replace("example.png", "bad:stream.png"),
            PATH.replace("example.png", "evil\n.png"), PATH.replace("example.png", "bad\0.png"),
            PATH.replace("example.png", "evil.svg"), ".github/workflows/release.yml",
            "app/src/main/assets/screenshot.png", PATH.replace("/reference/", "/reference /"),
        ]
        for path in paths:
            with self.subTest(path=path), self.assertRaises(screenshots.InvalidScreenshot):
                screenshots.decode_manifest(manifest([{"path": path, "content": None}]), BASE)
        screens = PATH.replace("example.png", "Example_phone landscape (night)_1234.png")
        self.assertIn(screens, screenshots.decode_manifest(manifest([{"path": screens, "content": None}]), BASE))

    def test_corrupted_or_disguised_images_and_large_collections_fail(self):
        for data in (b"#!/bin/sh\nexit 0", screenshots.PNG_SIGNATURE + b"shell script", png()[:-1], png() + b"trailing", png()[:30] + b"broken" + png()[36:]):
            with self.subTest(data=data), self.assertRaises(screenshots.InvalidScreenshot):
                screenshots.decode_manifest(manifest([{"path": PATH, "content": base64.b64encode(data).decode()}]), BASE)
        with self.assertRaises(screenshots.InvalidScreenshot):
            screenshots.decode_manifest(manifest([{"path": PATH, "content": "not base64!"}]), BASE)
        for constant, value in (("MAX_FILES", 0), ("MAX_IMAGE_BYTES", 10), ("MAX_TOTAL_BYTES", 10)):
            with mock.patch.object(screenshots, constant, value), self.assertRaises(screenshots.InvalidScreenshot):
                screenshots.decode_manifest(manifest(), BASE)

    def test_artifact_identity_is_checked_before_download(self):
        metadata = {"id": 19, "name": "screenshot-changes-42-2", "workflow_run": {"id": 42},
                    "expired": False, "size_in_bytes": 512}
        for mutation in ({"id": 20}, {"name": "screenshot-changes-42-1"},
                         {"workflow_run": {"id": 41}}, {"expired": True},
                         {"size_in_bytes": screenshots.MAX_ARCHIVE_BYTES + 1}, {"size_in_bytes": True}):
            with self.subTest(mutation=mutation), mock.patch.object(screenshots, "api", return_value=metadata | mutation) as api:
                with self.assertRaises(screenshots.InvalidScreenshot):
                    screenshots.download_changes("owner/repo", "19", "42", "2", BASE)
                self.assertEqual(api.call_count, 1)
        with mock.patch.object(screenshots, "api", side_effect=[metadata, archive()]):
            self.assertEqual(screenshots.download_changes("owner/repo", "19", "42", "2", BASE)[PATH], png())

    def test_producer_run_source_attempt_event_and_workflow_are_bound(self):
        run = {"id":42,"run_attempt":2,"head_sha":BASE,"head_branch":"topic",
               "head_repository":{"full_name":"owner/repo"},"event":"workflow_dispatch",
               "status":"completed","conclusion":"success","path":".github/workflows/update-screenshots.yml"}
        with mock.patch.object(screenshots,"api",return_value=run):screenshots.check_run("owner/repo","42","2","topic",BASE)
        for mutation in ({"head_sha":"b"*40},{"head_branch":"other"},{"run_attempt":3},{"event":"pull_request"},
                         {"head_repository":{"full_name":"other/repo"}},{"conclusion":"failure"},{"path":".github/workflows/other.yml"}):
            with self.subTest(mutation=mutation),mock.patch.object(screenshots,"api",return_value=run|mutation):
                with self.assertRaises(screenshots.InvalidScreenshot):screenshots.check_run("owner/repo","42","2","topic",BASE)

    def test_changed_branch_or_pull_request_is_rejected(self):
        reference = {"ref": "refs/heads/topic", "object": {"type": "commit", "sha": BASE}}
        pull = {"state": "open", "head": {"sha": BASE, "ref": "topic", "repo": {"full_name": "owner/repo"}}}
        with mock.patch.object(screenshots, "api", return_value=reference | {"object": {"type": "commit", "sha": "b" * 40}}):
            with self.assertRaises(screenshots.InvalidScreenshot):
                screenshots.check_head("owner/repo", "topic", BASE, "3")
        for mutation in ({"state": "closed"}, {"head": pull["head"] | {"sha": "b" * 40}},
                         {"head": pull["head"] | {"ref": "another"}},
                         {"head": pull["head"] | {"repo": {"full_name": "attacker/repo"}}}):
            with self.subTest(mutation=mutation), mock.patch.object(screenshots, "api", side_effect=[reference, pull | mutation]):
                with self.assertRaises(screenshots.InvalidScreenshot):
                    screenshots.check_head("owner/repo", "topic", BASE, "3")

    def test_protected_refs_and_command_injection_inputs_are_rejected(self):
        for branch in ("main", "release", "8.35", "../topic", "topic\nother", "-bad", "topic.lock", "topic?", "refs//topic"):
            with self.subTest(branch=branch), self.assertRaises(screenshots.InvalidScreenshot):
                screenshots.validate_target("owner/repo", branch, BASE, "release")
        for repository in ("owner/repo/extra", "https://attacker.invalid/repo", "owner/repo?query"):
            with self.subTest(repository=repository), self.assertRaises(screenshots.InvalidScreenshot):
                screenshots.validate_target(repository, "topic", BASE, "main")
        with self.assertRaises(screenshots.InvalidScreenshot):
            screenshots.validate_target("owner/repo", "topic", "--upload-pack=evil", "main")


class BareRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.repository = self.directory / "candidate.git"
        self.storage = self.directory / "lfs"
        screenshots.git(self.directory, "init", "--bare", "--template=", str(self.repository))

    def base(self, extra=None, attributes=ATTRIBUTES):
        files = {
            ".gitattributes": ("100644", attributes),
            SOURCE: ("100644", b"candidate test source must never run"),
            PATH: ("100644", b"version https://git-lfs.github.com/spec/v1\noid sha256:" + b"0" * 64 + b"\nsize 1\n"),
            "gradlew": ("100755", b"#!/bin/sh\nexit 99\n"),
        }
        files.update(extra or {})
        rows = []
        for path, (mode, content) in files.items():
            blob = screenshots.git(self.repository, "hash-object", "-w", "--stdin", data=content).strip()
            rows.append(mode.encode() + b" " + blob + b"\t" + path.encode() + b"\0")
        screenshots.git(self.repository, "update-index", "-z", "--index-info", data=b"".join(rows))
        tree = screenshots.git(self.repository, "write-tree").decode().strip()
        commit = screenshots.git(self.repository, "commit-tree", tree, data=b"base\n").decode().strip()
        screenshots.git(self.repository, "update-ref", "HEAD", commit)
        return commit

    def test_only_lfs_image_changes_enter_commit_without_checkout_or_hooks(self):
        sentinel = self.directory / "executed"
        malicious = b"#!/bin/sh\ntouch " + str(sentinel).encode() + b"\n"
        base = self.base({
            ".lfsconfig": ("100644", b"[lfs]\n url = https://attacker.invalid/\n"),
            "candidate.sh": ("100755", malicious),
        })
        hooks = self.repository / "hooks"
        hooks.mkdir()
        for name in ("pre-commit", "post-checkout", "post-commit"):
            script = hooks / name
            script.write_bytes(malicious)
            script.chmod(0o700)
        screenshots.git(self.repository, "config", "filter.lfs.clean", str(self.directory / "candidate.sh"))
        new_path = PATH.replace("example.png", "new image (night).png")
        commit, objects = screenshots.prepare_commit(self.repository, base, {PATH: None, new_path: png()}, self.storage)
        self.assertFalse(sentinel.exists())
        self.assertFalse((self.repository / "candidate.sh").exists())
        self.assertEqual(screenshots.git(self.repository, "rev-parse", "HEAD").decode().strip(), base)
        changed = screenshots.git(self.repository, "diff", "--name-only", base, commit).decode().splitlines()
        self.assertEqual(set(changed), {PATH, new_path})
        oid = hashlib.sha256(png()).hexdigest()
        pointer = screenshots.git(self.repository, "show", commit + ":" + new_path)
        self.assertEqual(pointer, f"version https://git-lfs.github.com/spec/v1\noid sha256:{oid}\nsize {len(png())}\n".encode())
        self.assertEqual((self.storage / "objects" / oid[:2] / oid[2:4] / oid).read_bytes(), png())
        self.assertEqual(objects, [oid])
        self.assertEqual(screenshots.git(self.repository, "rev-list", "--parents", "-n", "1", commit).decode().split(), [commit, base])

    def test_rejects_symlinks_executables_and_non_directory_ancestors(self):
        for mode, path in (("120000", PATH), ("100755", PATH), ("120000", PATH.rsplit("/", 1)[0] + "/escape")):
            with self.subTest(mode=mode, path=path):
                base = self.base({path: (mode, b"../../../../.github")})
                target = path + "/nested.png" if path.endswith("escape") else path
                with self.assertRaises(screenshots.InvalidScreenshot):
                    screenshots.prepare_commit(self.repository, base, {target: png()}, self.storage)

    def test_rejects_new_source_sets_missing_deletions_and_attribute_overrides(self):
        base = self.base()
        for path, content in ((PATH.replace("feature/example", "feature/new"), png()),
                              (PATH.replace("example.png", "missing.png"), None)):
            with self.subTest(path=path), self.assertRaises(screenshots.InvalidScreenshot):
                screenshots.prepare_commit(self.repository, base, {path: content}, self.storage)
        base = self.base(attributes=ATTRIBUTES + b"*.png filter=malicious\n")
        with self.assertRaises(screenshots.InvalidScreenshot):
            screenshots.prepare_commit(self.repository, base, {PATH: png()}, self.storage)

    def test_unchanged_lfs_content_creates_no_commit(self):
        base = self.base()
        commit, _ = screenshots.prepare_commit(self.repository, base, {PATH: png()}, self.storage)
        self.assertEqual(screenshots.prepare_commit(self.repository, commit, {PATH: png()}, self.storage), (None, []))

    def test_explicit_branch_lease_rejects_a_concurrent_update(self):
        base = self.base()
        commit, _ = screenshots.prepare_commit(self.repository, base, {PATH: png()}, self.storage)
        tree = screenshots.git(self.repository, "rev-parse", base + "^{tree}").decode().strip()
        other = screenshots.git(self.repository, "commit-tree", tree, "-p", base, data=b"concurrent update\n").decode().strip()
        remote = self.directory / "remote.git"
        screenshots.git(self.directory, "init", "--bare", "--template=", str(remote))
        # Only this offline test enables file transport between its own fixtures.
        screenshots.git(remote, "-c", "protocol.file.allow=always", "fetch", str(self.repository), other)
        screenshots.git(remote, "update-ref", "refs/heads/topic", other)
        with self.assertRaises(screenshots.InvalidScreenshot):
            screenshots.git(self.repository, "-c", "protocol.file.allow=always", "push",
                            "--force-with-lease=refs/heads/topic:" + base, str(remote), commit + ":refs/heads/topic")
        self.assertEqual(screenshots.git(remote, "rev-parse", "refs/heads/topic").decode().strip(), other)

    def test_inherited_git_overrides_cannot_change_publisher_behavior(self):
        hostile = {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.hooksPath",
                   "GIT_CONFIG_VALUE_0": "/attacker", "GIT_INDEX_FILE": "/attacker/index",
                   "GIT_LFS_SKIP_PUSH": "1", "GIT_DIR": "/attacker/repo"}
        with mock.patch.dict(os.environ, hostile):
            environment = screenshots.git_environment()
        for key in hostile:
            self.assertNotIn(key, environment)
        self.assertEqual(environment["GIT_CONFIG_GLOBAL"], os.devnull)
        self.assertEqual(environment["GIT_CONFIG_SYSTEM"], os.devnull)
        self.assertEqual(environment["GIT_NO_REPLACE_OBJECTS"], "1")


if __name__ == "__main__":
    unittest.main()
