import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import release_artifacts as artifacts
import release_sign


class InstrumentationChecklistTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.commit = 'a' * 40
        self.path = self.root / 'instrumentation.json'

    def write(self, status, evidence='', reason='', commit=None):
        record = {'commit': self.commit if commit is None else commit, 'status': status,
                  'evidence': evidence, 'reason': reason}
        self.path.write_text(json.dumps(record))
        return record

    def test_all_three_explicit_outcomes_are_allowed(self):
        for status, evidence, reason in (
                ('passed', 'local test results for both profiles', ''),
                ('failed', 'test log', 'maintainer accepted the failed run'),
                ('not run', '', 'device was unavailable; gap accepted')):
            with self.subTest(status=status):
                record = self.write(status, evidence, reason)
                self.assertEqual(release_sign.read_instrumentation_checklist(self.path, self.commit), record)

    def test_status_evidence_and_reason_are_not_implicit(self):
        for status, evidence, reason in (('passed', '', ''), ('failed', 'log', ''),
                                         ('not run', '', '  '), ('unknown', 'log', 'reason')):
            with self.subTest(status=status):
                self.write(status, evidence, reason)
                with self.assertRaises(ValueError):
                    release_sign.read_instrumentation_checklist(self.path, self.commit)

    def test_wrong_commit_extra_duplicate_and_control_fields_rejected(self):
        self.write('passed', 'log', commit='b' * 40)
        with self.assertRaisesRegex(ValueError, 'commit differs'):
            release_sign.read_instrumentation_checklist(self.path, self.commit)
        record = self.write('passed', 'log')
        record['extra'] = 'value'
        self.path.write_text(json.dumps(record))
        with self.assertRaisesRegex(ValueError, 'exactly'):
            release_sign.read_instrumentation_checklist(self.path, self.commit)
        self.path.write_text('{"commit":"' + self.commit + '","status":"passed","status":"failed","evidence":"log","reason":"ok"}')
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            release_sign.read_instrumentation_checklist(self.path, self.commit)
        self.write('passed', 'log\x1b[31m')
        with self.assertRaisesRegex(ValueError, 'single-line'):
            release_sign.read_instrumentation_checklist(self.path, self.commit)

    def test_missing_symlink_and_oversized_file_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'required'):
            release_sign.read_instrumentation_checklist(None, self.commit)
        self.write('passed', 'log')
        link = self.root / 'link.json'
        link.symlink_to(self.path)
        with self.assertRaises(OSError):
            release_sign.read_instrumentation_checklist(link, self.commit)
        self.path.write_bytes(b' ' * 16385)
        with self.assertRaisesRegex(ValueError, '16 KiB'):
            release_sign.read_instrumentation_checklist(self.path, self.commit)

    def test_missing_record_stops_before_signing_or_password_prompt(self):
        cert = 'b' * 64
        manifest = self.root / 'manifest.json'
        manifest.write_text(json.dumps({
            'schema': 1, 'commit': self.commit, 'workflow_revision': self.commit,
            'run_id': '1', 'run_attempt': '1', 'package': artifacts.PACKAGE,
            'version_code': 177701, 'version_name': '8.35.1', 'certificate_sha256': cert,
            'apks': [{'profile': p, 'file': p + '.apk', 'sha256': 'c' * 64}
                     for p in sorted(artifacts.PROFILES)]}))
        args = ['release_sign', '--manifest', str(manifest), '--manifest-sha256', artifacts.sha256(manifest),
                '--approved-commit', self.commit, '--approved-run', '1', '--certificate-sha256', cert,
                '--apks', str(self.root), '--output', str(self.root / 'out'),
                '--build-tools', str(self.root), '--scratch', str(self.root)]
        with patch('sys.argv', args), patch.dict(os.environ, {}, clear=True), \
             patch.object(release_sign, 'run') as run, patch.object(release_sign.getpass, 'getpass') as prompt:
            self.assertEqual(release_sign.main(), 1)
            run.assert_not_called()
            prompt.assert_not_called()
            self.assertFalse((self.root / 'out').exists())
        self.write('passed', 'test results', commit='b' * 40)
        with patch('sys.argv', args + ['--instrumentation-checklist', str(self.path)]), \
             patch.dict(os.environ, {}, clear=True), patch.object(release_sign, 'run') as run, \
             patch.object(release_sign.getpass, 'getpass') as prompt:
            self.assertEqual(release_sign.main(), 1)
            run.assert_not_called()
            prompt.assert_not_called()
            self.assertFalse((self.root / 'out').exists())

    def test_wrapper_inherits_explicit_checklist_environment(self):
        cert = 'b' * 64
        manifest = self.root / 'manifest.json'
        manifest.write_text(json.dumps({
            'schema': 1, 'commit': self.commit, 'workflow_revision': self.commit,
            'run_id': '1', 'run_attempt': '1', 'package': artifacts.PACKAGE,
            'version_code': 177701, 'version_name': '8.35.1', 'certificate_sha256': cert,
            'apks': [{'profile': p, 'file': p + '.apk', 'sha256': 'c' * 64}
                     for p in sorted(artifacts.PROFILES)]}))
        self.write('not run', '', 'maintainer accepted the gap')
        command = [sys.executable, release_sign.__file__, '--manifest', str(manifest),
                   '--manifest-sha256', artifacts.sha256(manifest), '--approved-commit', self.commit,
                   '--approved-run', '1', '--certificate-sha256', cert, '--apks', str(self.root),
                   '--output', str(self.root / 'out'), '--build-tools', str(self.root), '--scratch', str(self.root)]
        env = dict(os.environ, COLLIE_INSTRUMENTATION_CHECKLIST=str(self.path))
        result = subprocess.run(['bash', '-c', 'exec "$@"', 'wrapper', *command], env=env,
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn('"status": "not run"', result.stdout)
        self.assertIn('build-tools directory does not match', result.stderr)
        self.assertFalse((self.root / 'out').exists())


if __name__ == '__main__':
    unittest.main()
