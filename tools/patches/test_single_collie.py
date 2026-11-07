"""Single binary compatibility aliases preserve exact signed bytes."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import types
import unittest
from unittest.mock import patch

import release_artifacts as a
import release_collect
import release_sign
import release_publish


class SingleCollieTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.commit = 'a' * 40
        self.cert = 'b' * 64
        self.config = self.root / 'config.json'
        self.config.write_text(json.dumps({'android': {'build_tools': '36.0.0'}}))
        (self.root / 'collie.apk').write_bytes(b'unsigned binary')
        self.tools = self.root / '36.0.0'
        self.checklist = self.root / 'instrumentation.json'
        self.checklist.write_text(json.dumps({'commit': self.commit, 'status': 'not run', 'evidence': '', 'reason': 'accepted'}))

    def collect(self):
        args = ['release_collect', '--directory', str(self.root), '--build-tools', str(self.tools),
                '--config', str(self.config), '--commit', self.commit, '--workflow-revision', self.commit,
                '--run-id', '1', '--run-attempt', '1', '--version-name', '8.35.1-rc', '--version-code', '177701',
                '--certificate-sha256', self.cert, '--single-collie']
        with patch('sys.argv', args), patch.object(a, 'check_unsigned'), patch.object(a, 'check_metadata'), \
             patch.object(release_collect.subprocess, 'run', return_value=types.SimpleNamespace(stderr='test java')):
            self.assertEqual(release_collect.main(), 0)
        return json.loads((self.root / 'manifest.json').read_text())

    def test_collect_needs_only_one_input_and_marks_aliases(self):
        m = self.collect()
        self.assertEqual(m['artifact_mode'], 'single-collie')
        self.assertEqual(m['source_profile'], 'collie')
        self.assertEqual({e['profile'] for e in m['apks']}, a.PROFILES)
        self.assertEqual({e['sha256'] for e in m['apks']}, {a.sha256(self.root / 'collie.apk')})
        self.assertFalse((self.root / 'molly-minimal.apk').exists())
        m['apks'][0]['sha256'] = 'c' * 64
        with self.assertRaisesRegex(ValueError, 'identical hashes'):
            a.validate_manifest(m, self.commit, '1', self.cert)

    def test_sign_once_then_verify_five_identical_aliases(self):
        unsigned = self.collect()
        manifest = self.root / 'manifest.json'
        out = self.root / 'signed'
        args = ['release_sign', '--manifest', str(manifest), '--manifest-sha256', a.sha256(manifest),
                '--approved-commit', self.commit, '--approved-run', '1', '--certificate-sha256', self.cert,
                '--apks', str(self.root), '--output', str(out), '--build-tools', str(self.tools),
                '--scratch', str(self.root), '--instrumentation-checklist', str(self.checklist)]
        calls = []
        def run(command, **kwargs):
            calls.append(command)
            if 'zipalign' in str(command[0]) and '-f' in command:
                shutil.copyfile(command[-2], command[-1])
            if 'sign' in command:
                Path(command[command.index('--out') + 1]).write_bytes(b'signed binary')
            return types.SimpleNamespace(stdout=('Verified using v2 scheme (APK Signature Scheme v2): true\n'
                                                 'Verified using v3 scheme (APK Signature Scheme v3): true\n'
                                                 'Signer #1 certificate SHA-256 digest: ' + self.cert + '\n'))
        env = {'MOLLY_KEYSTORE': '/fixture/key', 'MOLLY_KEY_ALIAS': 'key',
               'MOLLY_KEYSTORE_PASSWORD': 'fixture', 'MOLLY_KEY_PASSWORD': 'fixture'}
        with patch('sys.argv', args), patch.dict(os.environ, env, clear=True), patch.object(release_sign, 'run', side_effect=run), \
             patch.object(a, 'check_unsigned'), patch.object(a, 'check_metadata'):
            self.assertEqual(release_sign.main(), 0)
        final = json.loads((out / 'final-manifest.json').read_text())
        self.assertEqual(sum('sign' in c for c in calls), 1)
        self.assertEqual(sum('verify' in c for c in calls), 5)
        self.assertEqual({a.sha256(out / e['file']) for e in final['apks']}, {hashlib.sha256(b'signed binary').hexdigest()})
        a.validate_alias_provenance(final, unsigned, self.commit)
        final['apks'][0]['signed_sha256'] = 'd' * 64
        with self.assertRaisesRegex(ValueError, 'aliases differ'):
            a.validate_alias_provenance(final, unsigned, self.commit)

    def test_publisher_checks_signed_aliases_before_remote_commands(self):
        unsigned = self.collect()
        provenance = self.root / 'manifest.json'
        signed_dir = self.root / 'final'
        signed_dir.mkdir()
        signed = []
        for item in unsigned['apks']:
            path = signed_dir / item['file']
            path.write_bytes(b'signed')
            signed.append({'profile': item['profile'], 'file': item['file'], 'unsigned_sha256': item['sha256'],
                           'signed_sha256': a.sha256(path), 'certificate_sha256': self.cert})
        final = {'schema': 1, 'rehearsal': False, 'commit': self.commit, 'run_id': '1',
                 'certificate_sha256': self.cert, 'artifact_mode': 'single-collie', 'source_profile': 'collie', 'package': a.PACKAGE,
                 'version_code': unsigned['version_code'], 'version_name': unsigned['version_name'],
                 'unsigned_manifest_sha256': a.sha256(provenance), 'apks': signed,
                 'instrumentation_checklist': json.loads(self.checklist.read_text())}
        path = signed_dir / 'final-manifest.json'
        notes = self.root / 'notes.md'
        notes.write_text('fixture')
        def call():
            path.write_text(json.dumps(final))
            args = ['release_publish', 'draft', '--repo', 'owner/repo', '--tag', 'v1', '--approved-commit', self.commit,
                    '--final-manifest', str(path), '--final-manifest-sha256', a.sha256(path), '--provenance', str(provenance),
                    '--notes', str(notes), '--scratch', str(self.root), '--dry-run', '--signing-key', '/key',
                    '--tagger-name', 'Signer', '--tagger-email', 'signer@example.invalid']
            with patch('sys.argv', args), patch.object(release_publish.subprocess, 'run') as command:
                result = release_publish.main()
                return result, command.call_count
        self.assertEqual(call(), (0, 0))
        final['apks'][0]['signed_sha256'] = 'd' * 64
        result, calls = call()
        self.assertEqual(result, 1)
        self.assertEqual(calls, 0)
        final['apks'][0]['signed_sha256'] = signed[1]['signed_sha256']
        final.pop('artifact_mode')
        result, calls = call()
        self.assertEqual(result, 1)
        self.assertEqual(calls, 0)

    def test_alias_provenance_rejects_mismatch(self):
        unsigned = self.collect()
        signed = [{'profile': e['profile'], 'file': e['file'], 'unsigned_sha256': e['sha256'],
                   'signed_sha256': 'd' * 64, 'certificate_sha256': self.cert} for e in unsigned['apks']]
        final = {'run_id': '1', 'certificate_sha256': self.cert, 'artifact_mode': 'single-collie', 'source_profile': 'collie', 'apks': signed,
                 'package': a.PACKAGE, 'version_code': unsigned['version_code'], 'version_name': unsigned['version_name']}
        a.validate_alias_provenance(final, unsigned, self.commit)
        signed[0]['unsigned_sha256'] = 'e' * 64
        with self.assertRaisesRegex(ValueError, 'unsigned provenance'):
            a.validate_alias_provenance(final, unsigned, self.commit)
        signed[0]['unsigned_sha256'] = unsigned['apks'][0]['sha256']
        signed[0]['certificate_sha256'] = 'f' * 64
        with self.assertRaisesRegex(ValueError, 'certificate'):
            a.validate_alias_provenance(final, unsigned, self.commit)
        final.pop('artifact_mode')
        with self.assertRaisesRegex(ValueError, 'artifact modes differ'):
            a.validate_alias_provenance(final, unsigned, self.commit)

if __name__ == '__main__':
    unittest.main()
