"""Fake-GitHub coverage for metadata preflight and final byte readback."""
import contextlib
import hashlib
import json
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))
import migration_receipts as receipts
import release_artifacts as a
import release_publish as publisher
import runtime_receipts as runtime


def write_json(path, value):
    path.write_text(json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n')


class FakeGitHub:
    def __init__(self, root, *, seed_existing, fault=None):
        self.root = root
        self.seed_existing = seed_existing
        self.fault = fault
        self.remote = None
        self.remote_bytes = {}
        self.calls = []
        self.downloaded_names = []
        self.downloaded_bytes = []

    def inputs(self):
        return next(self.root.glob('release-publish-*/inputs'))

    def seed(self, paths, prerelease=False):
        self.remote_bytes = {path.name: path.read_bytes() for path in paths}
        self.remote = {'isDraft': True, 'isPrerelease': prerelease}

    def assets(self):
        result = []
        for index, (name, data) in enumerate(sorted(self.remote_bytes.items())):
            remote_id = 'node-' + name
            digest = 'sha256:' + hashlib.sha256(data).hexdigest()
            size = len(data)
            if self.fault == 'digest-mismatch' and name == 'release-notes.md':
                digest = 'sha256:' + ('0' * 64)
            elif self.fault in ('missing-digest', 'missing-digest-and-fallback-mismatch') and name == 'release-notes.md':
                digest = None
            elif self.fault == 'unsupported-digest' and name == 'release-notes.md':
                digest = 'sha512:' + ('0' * 128)
            elif self.fault == 'malformed-digest' and name == 'release-notes.md':
                digest = 'sha256:malformed'
            elif self.fault == 'wrong-size' and name == 'release-notes.md':
                size += 1
            if self.fault == 'duplicate-id' and index == 1:
                remote_id = result[0]['id']
            if self.fault == 'changed-id-after-publish' and self.remote and not self.remote['isDraft']:
                remote_id += '-published'
            result.append({'id': remote_id, 'name': name, 'size': size, 'digest': digest})
        if self.fault == 'missing-source':
            result = [asset for asset in result if asset['name'] != 'source.json']
        elif self.fault == 'missing-notes':
            result = [asset for asset in result if asset['name'] != 'release-notes.md']
        elif self.fault == 'duplicate-asset' and result:
            duplicate = dict(result[0])
            duplicate['id'] = 'node-duplicate'
            result.append(duplicate)
        return result

    def __call__(self, cmd, *unused, **kwargs):
        self.calls.append(cmd)
        action = cmd[2]
        if action == 'view':
            if self.remote is None and self.seed_existing:
                self.seed(list(self.inputs().iterdir()), '--prerelease' in cmd)
            if self.remote is None:
                raise subprocess.CalledProcessError(1, cmd, stderr='release not found')
            return types.SimpleNamespace(stdout=json.dumps({**self.remote, 'assets': self.assets()}))
        if action == 'create':
            paths = [value for value in cmd if isinstance(value, Path) and value.is_file()]
            self.seed(paths, '--prerelease' in cmd)
            if self.fault == 'create-response-lost':
                raise subprocess.CalledProcessError(1, cmd, stderr='lost create response')
        elif action == 'upload':
            paths = [value for value in cmd if isinstance(value, Path) and value.is_file()]
            for path in paths:
                self.remote_bytes[path.name] = path.read_bytes()
        elif action == 'edit':
            self.remote['isDraft'] = False
        elif action == 'download':
            if not self.remote['isDraft'] and self.fault == 'final-readback-interrupted':
                raise subprocess.CalledProcessError(1, cmd, stderr='interrupted final readback')
            destination = Path(cmd[cmd.index('--dir') + 1])
            self.downloaded_names.append(set(self.remote_bytes))
            self.downloaded_bytes.append(sum(map(len, self.remote_bytes.values())))
            for name, data in self.remote_bytes.items():
                if not self.remote['isDraft'] and self.fault == 'final-bytes-mismatch' and name == 'collie.apk':
                    data = b'tampered after publication'
                elif self.fault == 'missing-digest-and-fallback-mismatch' and self.remote['isDraft'] and name == 'collie.apk':
                    data = b'tampered during fallback verification'
                (destination / name).write_bytes(data)
        return types.SimpleNamespace(stdout='')


class PublisherSpeedTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.commit = 'a' * 40
        self.engineering = 'b' * 40
        self.workflow = 'c' * 40
        self.certificate = 'd' * 64
        self.source_tree = 'e' * 40
        self.input_dir = self.root / 'candidate'
        self.input_dir.mkdir()
        self.notes = self.root / 'release-notes.md'
        self.notes.write_bytes(b'synthetic release notes')
        self.provenance = self.root / 'provenance.json'
        self.unsigned_digest = hashlib.sha256(b'synthetic unsigned APK').hexdigest()
        self.signed_digest = hashlib.sha256(b'synthetic signed APK').hexdigest()
        self.apks = []
        for profile in sorted(a.PROFILES):
            (self.input_dir / (profile + '.apk')).write_bytes(b'synthetic signed APK')
            self.apks.append({'profile': profile, 'file': profile + '.apk',
                              'sha256': self.unsigned_digest,
                              'signed_sha256': self.signed_digest,
                              'certificate_sha256': self.certificate})
        self._write_schema2_inputs()

    def _write_schema2_inputs(self):
        self.source = {
            'schema': 2, 'kind': 'source',
            'payload': {
                'upstream_commit': '1' * 40, 'engineering_revision': self.engineering,
                'generator_revision': '2' * 40, 'verifier_revision': '3' * 40,
                'layers': [], 'reconstructed_tree': '4' * 40, 'overlays': [],
                'published_commit': self.commit, 'published_tree': self.source_tree,
            },
            'evidence': [{'status': 'PASS', 'description': 'synthetic source fixture', 'sha256': '5' * 64}],
        }
        self.source_path = self.root / 'source-receipt.json'
        write_json(self.source_path, self.source)

        self.build_inputs = {
            'schema': 1, 'recipe_revision': '6' * 40,
            'config_sha256': '7' * 64, 'toolchain_sha256': '8' * 64,
            'dependencies_sha256': '9' * 64, 'native_inputs_sha256': 'a' * 64,
            'trust_level': 'synthetic local fixture',
        }
        self.build_inputs_path = self.root / 'build-inputs.json'
        write_json(self.build_inputs_path, self.build_inputs)

        self.plan = {
            'schema': 1, 'kind': 'release-plan', 'mode': 'new-source', 'purpose': 'fixture',
            'source_commit': self.commit, 'engineering_revision': self.engineering,
            'workflow_revision': self.workflow, 'package': a.PACKAGE,
            'version_code': 177704, 'version_name': 'synthetic-fixture',
            'certificate_sha256': self.certificate,
            'source_receipt_sha256': receipts.digest(self.source),
            'build_inputs_sha256': a.sha256(self.build_inputs_path),
            'required_test_reports': ['synthetic-report'],
        }
        self.plan_path = self.root / 'release-plan.json'
        write_json(self.plan_path, self.plan)
        self.plan_sha256 = a.sha256(self.plan_path)

        self.build = {
            'schema': 2, 'kind': 'build',
            'payload': {
                'source_receipt_sha256': receipts.digest(self.source),
                'source_commit': self.commit, 'source_tree': self.source_tree,
                'recipe_revision': self.build_inputs['recipe_revision'],
                'toolchain_sha256': self.build_inputs['toolchain_sha256'],
                'dependencies_sha256': self.build_inputs['dependencies_sha256'],
                'native_inputs_sha256': self.build_inputs['native_inputs_sha256'],
                'unsigned_apk_sha256': self.unsigned_digest,
                'trust_level': self.build_inputs['trust_level'],
            },
            'evidence': [
                {'status': 'PASS', 'description': runtime.PLAN_EVIDENCE, 'sha256': self.plan_sha256},
                {'status': 'PASS', 'description': 'synthetic test report', 'sha256': 'b' * 64},
            ],
        }
        self.build_path = self.root / 'build-receipt.json'
        write_json(self.build_path, self.build)
        self.build_sha256 = receipts.digest(self.build)

        self.unsigned = {
            'schema': 2, 'release_mode': 'new-source',
            'release_plan_sha256': self.plan_sha256,
            'source_receipt_sha256': receipts.digest(self.source),
            'build_receipt_sha256': self.build_sha256,
            'source_commit': self.commit, 'engineering_revision': self.engineering,
            'workflow_revision': self.workflow,
            'apks': [{'profile': entry['profile'], 'file': entry['file'],
                      'sha256': self.unsigned_digest} for entry in self.apks],
        }
        write_json(self.provenance, self.unsigned)

        self.final = {
            'schema': 2, 'release_mode': 'new-source', 'rehearsal': False,
            'commit': self.commit, 'source_commit': self.commit,
            'engineering_revision': self.engineering,
            'workflow_revision': self.workflow, 'release_plan_sha256': self.plan_sha256,
            'source_receipt_sha256': receipts.digest(self.source),
            'build_receipt_sha256': self.build_sha256,
            'package': a.PACKAGE, 'version_code': 177704,
            'version_name': 'synthetic-fixture', 'certificate_sha256': self.certificate,
            'unsigned_manifest_sha256': a.sha256(self.provenance),
            'apks': self.apks,
            'instrumentation_checklist': {
                'commit': self.commit, 'status': 'not run', 'evidence': '',
                'reason': 'synthetic fixture only',
            },
        }
        self.final_path = self.input_dir / 'final-manifest.json'
        write_json(self.final_path, self.final)
        context = (self.plan, self.plan_sha256)
        self.release = runtime.release(context, self.source, self.build, self.final,
                                       self.final_path.read_bytes())
        self.release_path = self.root / 'release-receipt.json'
        write_json(self.release_path, self.release)
        self.release_sha256 = receipts.digest(self.release)

    def args(self, action, phase_report=None):
        result = [
            'release_publish', action, '--repo', 'fixture/repo', '--tag', 'vfixture',
            '--approved-commit', self.commit,
            '--final-manifest', str(self.final_path),
            '--final-manifest-sha256', a.sha256(self.final_path),
            '--provenance', str(self.provenance), '--notes', str(self.notes),
            '--scratch', str(self.root),
            '--release-plan', str(self.plan_path),
            '--approved-release-plan-sha256', self.plan_sha256,
            '--source-receipt', str(self.source_path), '--build-inputs', str(self.build_inputs_path),
            '--build-receipt', str(self.build_path),
            '--approved-build-receipt-sha256', self.build_sha256,
            '--release-receipt', str(self.release_path),
            '--approved-release-receipt-sha256', self.release_sha256,
        ]
        if phase_report is not None:
            result += ['--phase-report', str(phase_report)]
        return result

    def invoke(self, action, gh, phase_report=None):
        with patch('sys.argv', self.args(action, phase_report)), \
             patch.object(publisher, 'command', side_effect=gh), \
             patch.object(publisher, 'ensure_signed_tag'), \
             patch.object(publisher, 'verify_remote_tag'), \
             contextlib.redirect_stdout(io.StringIO()), \
             contextlib.redirect_stderr(io.StringIO()):
            return publisher.main()

    def test_metadata_preflight_and_exactly_one_all_asset_postpublish_readback(self):
        gh = FakeGitHub(self.root, seed_existing=True)
        report_path = self.root / 'publisher-timing.json'
        self.assertEqual(self.invoke('publish', gh, report_path), 0)
        downloads = [call for call in gh.calls if call[2] == 'download']
        self.assertEqual(len(downloads), 1)
        self.assertEqual(set(gh.remote_bytes), {
            *(profile + '.apk' for profile in a.PROFILES), 'source.json', 'build.json', 'release.json',
            'final-manifest.json', 'provenance.json', 'release-notes.md', 'SHA256SUMS',
        })
        self.assertEqual(gh.downloaded_names, [set(gh.remote_bytes)])
        self.assertEqual(os.stat(report_path).st_mode & 0o777, 0o600)
        report_raw = report_path.read_text()
        report = json.loads(report_raw)
        self.assertEqual(report['result'], 'success')
        phases = {row['phase']: row for row in report['phases']}
        self.assertGreaterEqual(phases['prepublish_verify']['duration_ns'], 0)
        self.assertIn('started_monotonic_ns', phases['prepublish_verify'])
        self.assertEqual(phases['prepublish_verify']['method'], 'github_asset_metadata')
        self.assertEqual(phases['prepublish_verify']['bytes_downloaded'], 0)
        self.assertEqual(phases['public_readback']['method'], 'github_metadata_plus_full_byte_readback')
        self.assertEqual(phases['public_readback']['bytes_downloaded'], sum(map(len, gh.remote_bytes.values())))
        self.assertNotIn(self.commit, report_raw)
        self.assertNotIn('--approved-commit', report_raw)
        self.assertNotIn(str(self.root), report_raw)
        self.assertNotIn('token', report_raw.lower())

    def test_missing_unsupported_or_malformed_digest_falls_back_to_all_bytes(self):
        for fault in ('missing-digest', 'unsupported-digest', 'malformed-digest'):
            with self.subTest(fault=fault):
                gh = FakeGitHub(self.root, seed_existing=True, fault=fault)
                report_path = self.root / 'fallback-timing.json' if fault == 'missing-digest' else None
                self.assertEqual(self.invoke('publish', gh, report_path), 0)
                downloads = [call for call in gh.calls if call[2] == 'download']
                self.assertEqual(len(downloads), 2)
                self.assertEqual(sum(call[2] == 'edit' for call in gh.calls), 1)
                if report_path is not None:
                    report = json.loads(report_path.read_text())
                    phases = {row['phase']: row for row in report['phases']}
                    self.assertEqual(phases['prepublish_verify']['method'],
                                     'full_byte_fallback_missing_or_unsupported_digest')
                    self.assertGreater(phases['prepublish_verify']['bytes_downloaded'], 0)

    def test_mismatched_digest_duplicate_ids_and_size_mismatch_stop_before_publish(self):
        for fault in ('digest-mismatch', 'duplicate-id', 'wrong-size', 'duplicate-asset',
                      'missing-source', 'missing-notes'):
            with self.subTest(fault=fault):
                gh = FakeGitHub(self.root, seed_existing=True, fault=fault)
                self.assertEqual(self.invoke('publish', gh), 1)
                self.assertEqual(sum(call[2] == 'edit' for call in gh.calls), 0)
                self.assertEqual(sum(call[2] == 'download' for call in gh.calls), 0)

    def test_fallback_byte_mismatch_stops_before_publish(self):
        gh = FakeGitHub(self.root, seed_existing=True, fault='missing-digest-and-fallback-mismatch')
        self.assertEqual(self.invoke('publish', gh), 1)
        self.assertEqual(sum(call[2] == 'edit' for call in gh.calls), 0)

    def test_final_readback_interruption_or_wrong_bytes_is_failure(self):
        for fault in ('final-readback-interrupted', 'final-bytes-mismatch'):
            with self.subTest(fault=fault):
                gh = FakeGitHub(self.root, seed_existing=True, fault=fault)
                report_path = self.root / (fault + '.json')
                self.assertEqual(self.invoke('publish', gh, report_path), 1)
                self.assertFalse(gh.remote['isDraft'])
                report = json.loads(report_path.read_text())
                self.assertEqual(report['result'], 'failed')
                public_readback = next(row for row in report['phases']
                                       if row['phase'] == 'public_readback')
                self.assertEqual(public_readback['status'], 'failed')

    def test_published_asset_ids_must_match_pretransition_ids(self):
        gh = FakeGitHub(self.root, seed_existing=True, fault='changed-id-after-publish')
        self.assertEqual(self.invoke('publish', gh), 1)

    def test_create_success_and_lost_response_skip_resume_downloads(self):
        for fault in (None, 'create-response-lost'):
            with self.subTest(fault=fault):
                gh = FakeGitHub(self.root, seed_existing=False, fault=fault)
                report_path = self.root / ('create-timing.json' if fault is None else 'lost-create-timing.json')
                self.assertEqual(self.invoke('draft', gh, report_path), 0)
                self.assertEqual(sum(call[2] == 'create' for call in gh.calls), 1)
                self.assertEqual(sum(call[2] == 'download' for call in gh.calls), 0)
                report = json.loads(report_path.read_text())
                self.assertEqual(report['result'], 'success')
                self.assertEqual(os.stat(report_path).st_mode & 0o777, 0o600)
                if fault is None:
                    upload = next(row for row in report['phases'] if row['phase'] == 'draft_upload')
                    self.assertEqual(upload['method'], 'gh_release_create_file_payload')
                    self.assertEqual(upload['asset_file_bytes_submitted'],
                                     sum(len(data) for data in gh.remote_bytes.values()))

    def test_existing_partial_draft_resumes_missing_asset_without_replacing_or_downloading(self):
        gh = FakeGitHub(self.root, seed_existing=True)
        original_seed = gh.seed

        def seed_without_release_receipt(paths, prerelease=False):
            original_seed([path for path in paths if path.name != 'release.json'], prerelease)

        gh.seed = seed_without_release_receipt
        self.assertEqual(self.invoke('draft', gh), 0)
        self.assertEqual(sum(call[2] == 'create' for call in gh.calls), 0)
        self.assertEqual(sum(call[2] == 'download' for call in gh.calls), 0)
        uploads = [call for call in gh.calls if call[2] == 'upload']
        self.assertEqual(len(uploads), 1)
        self.assertEqual([path.name for path in uploads[0] if isinstance(path, Path)], ['release.json'])
        self.assertNotIn('--clobber', uploads[0])

    def test_timing_report_path_is_exclusive_before_any_github_call(self):
        report = self.root / 'existing-report.json'
        report.write_text('preserve')
        gh = FakeGitHub(self.root, seed_existing=True)
        self.assertEqual(self.invoke('publish', gh, report), 1)
        self.assertEqual(gh.calls, [])
        self.assertEqual(report.read_text(), 'preserve')


if __name__ == '__main__':
    unittest.main()
