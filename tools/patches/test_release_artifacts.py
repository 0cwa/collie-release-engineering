import json
import os
import io
from contextlib import redirect_stdout
import subprocess
from pathlib import Path
import tempfile
import types
import unittest
import zipfile
from unittest.mock import patch

import release_artifacts as a
import release_collect
import release_publish
import release_sign


class ReleaseArtifactsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.commit = 'a' * 40
        self.cert = 'b' * 64
        self.entries = [{'profile': p, 'file': p + '.apk', 'sha256': 'c' * 64} for p in sorted(a.PROFILES)]
        self.manifest = {'schema': 1, 'commit': self.commit, 'workflow_revision': self.commit,
                         'run_id': '123', 'run_attempt': '1', 'package': a.PACKAGE, 'version_code': 177204,
                         'version_name': '1.2.3', 'certificate_sha256': self.cert, 'apks': self.entries}

    def test_manifest_rejects_wrong_identity_or_duplicate_profile(self):
        self.assertEqual(a.validate_manifest(self.manifest, self.commit, '123', self.cert), self.entries)
        with self.assertRaisesRegex(ValueError, 'commit and run'):
            a.validate_manifest(self.manifest, self.commit, '124', self.cert)
        self.entries[-1] = self.entries[0]
        with self.assertRaisesRegex(ValueError, 'five profiles'):
            a.validate_manifest(self.manifest, self.commit, '123', self.cert)

    def test_manifest_rejects_wrong_package_and_certificate(self):
        self.assertEqual(a.PACKAGE, 'im.collie.app')
        self.manifest['package'] = 'im.molly.app'
        with self.assertRaisesRegex(ValueError, 'package'):
            a.validate_manifest(self.manifest, self.commit, '123', self.cert)
        self.manifest['package'] = a.PACKAGE
        with self.assertRaisesRegex(ValueError, 'certificate'):
            a.validate_manifest(self.manifest, self.commit, '123', 'd' * 64)

    def test_collie_release_code_must_exceed_the_published_rc3(self):
        for value in (1, 177202, 177203):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'exceed.*RC3'):
                self.manifest['version_code'] = value
                a.validate_manifest(self.manifest, self.commit, '123', self.cert)
        self.manifest['version_code'] = 177204
        a.validate_manifest(self.manifest, self.commit, '123', self.cert)
        # The floor is specific to the published Collie package, not other apps.
        a.validate_version_code(42, 'org.example.other')
        with self.assertRaisesRegex(ValueError, 'invalid version code'):
            a.validate_version_code(True, a.PACKAGE)

    def test_unsigned_dispatch_passes_override_to_version_guard(self):
        root = Path(__file__).resolve().parents[2]
        path = root / '.github/workflows/molly-unsigned-release.yml'
        if not path.exists():
            # Engineering archives legacy workflows as inert historical fixtures.
            path = root / 'docs/legacy-workflows/molly-unsigned-release.yml'
        workflow = path.read_text()
        step = workflow.split('      - name: Validate the dispatch and prepare proof\n', 1)[1].split('      - name:', 1)[0]
        self.assertIn('VERSION_CODE: ${{ inputs.version_code }}', step)
        commands = [line.strip() for line in step.splitlines() if 'a.validate_version_code(' in line]
        self.assertEqual(len(commands), 1)
        for code, expected in (('177203', False), ('177204', True), ('invalid', False)):
            with self.subTest(code=code):
                run = subprocess.run(['bash', '-e', '-c', commands[0]], cwd=root,
                                     env=dict(os.environ, VERSION_CODE=code), capture_output=True, text=True)
                self.assertEqual(run.returncode == 0, expected, run.stderr)

    def test_apk_metadata_rejects_wrong_package(self):
        with patch.object(a, 'apk_metadata', return_value=('im.molly.app', '42', '1.2.3')):
            with self.assertRaisesRegex(ValueError, 'package or version'):
                a.check_metadata('aapt2', 'app.apk', 42, '1.2.3')

    def test_unsigned_rejects_jar_signature(self):
        apk = self.root / 'app.apk'
        with zipfile.ZipFile(apk, 'w') as z:
            z.writestr('META-INF/CERT.RSA', b'signature')
        with self.assertRaisesRegex(ValueError, 'v1 signature'):
            a.check_unsigned(apk)

    def test_sign_rejects_untrusted_manifest_before_running_tools(self):
        manifest = self.root / 'manifest.json'
        manifest.write_text(json.dumps(self.manifest))
        args = ['release_sign', '--manifest', str(manifest), '--manifest-sha256', '0' * 64,
                '--approved-commit', self.commit, '--approved-run', '123', '--certificate-sha256', self.cert,
                '--apks', str(self.root), '--output', str(self.root / 'out'), '--build-tools', str(self.root),
                '--scratch', str(self.root), '--instrumentation-checklist', str(self.root / 'unused.json')]
        with patch('sys.argv', args), patch.object(release_sign, 'run') as run:
            self.assertEqual(release_sign.main(), 1)
            run.assert_not_called()

    def test_current_signer_rejects_legacy_profiles_before_keys(self):
        for mode in (None,'five-profiles'):
            m=dict(self.manifest)
            if mode: m['artifact_mode']=mode
            manifest=self.root/'manifest.json';manifest.write_text(json.dumps(m))
            args=['release_sign','--manifest',str(manifest),'--manifest-sha256',a.sha256(manifest),
                  '--approved-commit',self.commit,'--approved-run','123','--certificate-sha256',self.cert,
                  '--apks',str(self.root),'--output',str(self.root/'out'),'--build-tools',str(self.root),'--scratch',str(self.root)]
            with patch('sys.argv',args), patch.object(release_sign,'run') as run, patch.object(release_sign.getpass,'getpass') as prompt:
                self.assertEqual(release_sign.main(),1)
                run.assert_not_called();prompt.assert_not_called()

    def test_own_repository_publisher_rejects_legacy_profiles_before_api(self):
        final=self.root/'final.json'
        final.write_text(json.dumps({'schema':1,'rehearsal':False,'commit':self.commit,'version_code':177703}))
        args=['release_publish','draft','--repo','0cwa/collieim-android','--tag','vnext','--approved-commit',self.commit,
              '--final-manifest',str(final),'--final-manifest-sha256',a.sha256(final),'--provenance',str(self.root/'unused'),
              '--notes',str(self.root/'unused'),'--scratch',str(self.root)]
        with patch('sys.argv',args),patch.object(release_publish,'command') as command:
            self.assertEqual(release_publish.main(),1)
            command.assert_not_called()

    def test_sign_records_the_unsigned_to_signed_mapping(self):
        for entry in self.entries:
            apk = self.root / entry['file']
            apk.write_bytes(entry['profile'].encode())
            entry['sha256'] = a.sha256(apk)
        self.manifest.update(artifact_mode='single-collie', source_profile='collie')
        for entry in self.entries:
            entry['sha256'] = a.sha256(self.root / 'collie.apk')
        self.manifest.update(version_code=177703, build_tools='36.0.0', input_pins={'android': {'build_tools': '36.0.0'}})
        manifest = self.root / 'manifest.json'
        manifest.write_text(json.dumps(self.manifest))
        output = self.root / 'signed'
        checklist = self.root / 'instrumentation.json'
        record = {'commit': self.commit, 'status': 'failed', 'evidence': 'local run log',
                  'reason': 'maintainer accepted this documented gap'}
        checklist.write_text(json.dumps(record))
        args = ['release_sign', '--manifest', str(manifest), '--manifest-sha256', a.sha256(manifest),
                '--approved-commit', self.commit, '--approved-run', '123', '--certificate-sha256', self.cert,
                '--apks', str(self.root), '--output', str(output), '--build-tools', str(self.root / '36.0.0'),
                '--scratch', str(self.root), '--instrumentation-checklist', str(checklist)]
        inventory=self.root / 'published.json'
        inventory.write_text(json.dumps([{'release':'v8.35.1-1-rc1','code':177702,'sha256':'a'*64}]))
        args += ['--version-ledger',str(self.root/'versions.json'),'--published-inventory',str(inventory),'--published-inventory-sha256',a.sha256(inventory)]
        snapshot = a.snapshot
        class TrackingStream(io.StringIO):
            def __init__(self):
                super().__init__()
                self.flushes = 0

            def flush(self):
                self.flushes += 1
                return super().flush()

        console = TrackingStream()

        def replace_after_snapshot(source, destination):
            snapshot(source, destination)
            Path(source).write_bytes(b'replaced after snapshot')

        def fake_run(command, **kwargs):
            self.assertIn('Instrumentation checklist (maintainer-provided; not independently verified):', console.getvalue())
            if '-f' in command and 'zipalign' in str(command[0]):
                self.assertIn(Path(command[-2]).read_bytes(), [x['profile'].encode() for x in self.entries])
            if 'sign' in command:
                Path(command[command.index('--out') + 1]).write_bytes(b'signed')
            return types.SimpleNamespace(stdout=('Verified using v2 scheme (APK Signature Scheme v2): true\n'
                                                'Verified using v3 scheme (APK Signature Scheme v3): true\n'
                                                'Signer #1 certificate SHA-256 digest: ' + self.cert + '\n'))
        def fake_prompt(_):
            self.assertGreater(console.flushes, 0, 'checklist must be flushed before a credential prompt')
            self.assertIn('"status": "failed"', console.getvalue())
            return 'test'
        environment = {'MOLLY_KEYSTORE': '/offline/key', 'MOLLY_KEY_ALIAS': 'release',
                       'COLLIE_INSTRUMENTATION_CHECKLIST': str(self.root / 'missing-checklist.json')}
        with redirect_stdout(console), patch('sys.argv', args), patch.dict('os.environ', environment, clear=True), \
             patch.object(release_sign, 'run', side_effect=fake_run), \
             patch.object(release_sign.getpass, 'getpass', side_effect=fake_prompt) as prompt, \
             patch.object(a, 'snapshot', side_effect=replace_after_snapshot), \
             patch.object(a, 'check_unsigned'), patch.object(a, 'check_metadata'):
            self.assertEqual(release_sign.main(), 0)
        self.assertEqual(prompt.call_count, 2)
        result = json.loads((output / 'final-manifest.json').read_text())
        self.assertEqual(len(result['apks']), 5)
        self.assertEqual(result['instrumentation_checklist'], record)
        self.assertIn('"status": "failed"', console.getvalue())
        self.assertEqual(result['apks'][0]['unsigned_sha256'], self.entries[0]['sha256'])
        self.assertEqual(result['apks'][0]['signed_sha256'], a.sha256(output / self.entries[0]['file']))

    def test_collect_refuses_a_mismatched_build_tools_pin(self):
        config = self.root / 'config.json'
        config.write_text(json.dumps({'android': {'build_tools': '36.0.0'}}))
        args = ['release_collect', '--directory', str(self.root), '--build-tools', str(self.root / 'wrong'),
                '--config', str(config), '--commit', self.commit, '--workflow-revision', self.commit,
                '--run-id', '123', '--run-attempt', '1', '--version-name', '1.2.3',
                '--version-code', '42', '--certificate-sha256', self.cert]
        with patch('sys.argv', args), patch.object(release_collect.subprocess, 'run') as run:
            self.assertEqual(release_collect.main(), 1)
            run.assert_not_called()

    def test_publish_refuses_rehearsal_without_calling_github(self):
        final = self.root / 'final-manifest.json'
        final.write_text(json.dumps({'schema': 1, 'rehearsal': True, 'commit': self.commit}))
        args = ['release_publish', 'draft', '--repo', 'owner/repo', '--tag', 'v1.2.3',
                '--approved-commit', self.commit, '--final-manifest', str(final),
                '--final-manifest-sha256', a.sha256(final), '--provenance', str(final), '--notes', str(final),
                '--scratch', str(self.root)]
        with patch('sys.argv', args), patch.object(release_publish, 'command') as command:
            self.assertEqual(release_publish.main(), 1)
            command.assert_not_called()

    def test_publisher_rejects_collie_rc3_or_older_before_github(self):
        final = self.root / 'final-manifest.json'
        final.write_text(json.dumps({'schema': 1, 'rehearsal': False, 'commit': self.commit, 'version_code': 177203}))
        args = ['release_publish', 'draft', '--repo', '0cwa/collieim-android', '--tag', 'v1',
                '--approved-commit', self.commit, '--final-manifest', str(final),
                '--final-manifest-sha256', a.sha256(final), '--provenance', str(final), '--notes', str(final),
                '--scratch', str(self.root)]
        with patch('sys.argv', args), patch.object(release_publish, 'command') as command:
            self.assertEqual(release_publish.main(), 1)
            command.assert_not_called()

    def test_publisher_requires_commit_bound_assessment_for_both_actions(self):
        apks = []
        for profile in sorted(a.PROFILES):
            path = self.root / (profile + '.apk')
            path.write_bytes(profile.encode())
            apks.append({'profile': profile, 'file': path.name, 'signed_sha256': a.sha256(path)})
        final = self.root / 'final-manifest.json'
        provenance = self.root / 'provenance.json'
        provenance.write_text('provenance')
        cases = [
            (None, False),
            ({'commit': 'c' * 40, 'status': 'passed', 'evidence': 'test', 'reason': ''}, False),
            ({'commit': self.commit, 'status': 'passed', 'evidence': '', 'reason': ''}, False),
            ({'commit': self.commit, 'status': 'failed', 'evidence': '', 'reason': ''}, False),
            ({'commit': self.commit, 'status': 'passed', 'evidence': 'test', 'reason': ''}, True),
            ({'commit': self.commit, 'status': 'failed', 'evidence': '', 'reason': 'accepted'}, True),
            ({'commit': self.commit, 'status': 'not run', 'evidence': '', 'reason': 'accepted'}, True),
        ]
        for action in ('draft', 'publish'):
            for record, accepted in cases:
                with self.subTest(action=action, record=record):
                    manifest = {'schema': 1, 'rehearsal': False, 'commit': self.commit, 'apks': apks,
                                'unsigned_manifest_sha256': a.sha256(provenance)}
                    if record is not None:
                        manifest['instrumentation_checklist'] = record
                    final.write_text(json.dumps(manifest))
                    args = ['release_publish', action, '--repo', 'owner/repo', '--tag', 'v1.2.3',
                            '--approved-commit', self.commit, '--final-manifest', str(final),
                            '--final-manifest-sha256', a.sha256(final), '--provenance', str(provenance),
                            '--notes', str(final), '--scratch', str(self.root), '--dry-run', '--signing-key', '/key',
                            '--tagger-name', 'Signer', '--tagger-email', 'signer@example.invalid']
                    with patch('sys.argv', args), patch.object(release_publish, 'command') as command:
                        self.assertEqual(release_publish.main(), 0 if accepted else 1)
                        if not accepted:
                            command.assert_not_called()

    def test_publish_dry_run_never_calls_subprocess(self):
        apks = []
        for profile in sorted(a.PROFILES):
            path = self.root / (profile + '.apk')
            path.write_bytes(profile.encode())
            apks.append({'profile': profile, 'file': path.name, 'signed_sha256': a.sha256(path)})
        final = self.root / 'final-manifest.json'
        provenance = self.root / 'provenance.json'
        provenance.write_text('provenance')
        final.write_text(json.dumps({'schema': 1, 'rehearsal': False, 'commit': self.commit, 'apks': apks,
                                     'unsigned_manifest_sha256': a.sha256(provenance),
                                     'instrumentation_checklist': {'commit': self.commit, 'status': 'not run', 'evidence': '', 'reason': 'fixture'}}))
        args = ['release_publish', 'draft', '--repo', 'owner/repo', '--tag', 'v1.2.3',
                '--approved-commit', self.commit, '--final-manifest', str(final),
                '--final-manifest-sha256', a.sha256(final), '--provenance', str(provenance), '--notes', str(final),
                '--scratch', str(self.root), '--dry-run', '--signing-key', '/key',
                '--tagger-name', 'Signer', '--tagger-email', 'signer@example.invalid']
        with patch('sys.argv', args), patch.object(release_publish.subprocess, 'run') as run:
            self.assertEqual(release_publish.main(), 0)
            run.assert_not_called()

    def test_publish_remote_verification_and_input_replacement(self):
        cases = [('publish', x) for x in ('none', 'tag', 'non-draft', 'wrong-prerelease', 'missing', 'extra', 'download', 'snapshot-and-download')]
        cases += [('draft', 'none'), ('draft', 'download')]
        cases += [('prerelease-' + action, failure) for action in ('draft', 'publish')
                  for failure in ('none', 'wrong-prerelease')]
        for action, failure in cases:
            with self.subTest(action=action, failure=failure), tempfile.TemporaryDirectory(dir=self.root) as directory:
                prerelease = action.startswith('prerelease-')
                action = action.removeprefix('prerelease-')
                root = Path(directory)
                entries = []
                for profile in sorted(a.PROFILES):
                    path = root / (profile + '.apk')
                    path.write_bytes(profile.encode())
                    entries.append({'profile': profile, 'file': path.name, 'signed_sha256': a.sha256(path)})
                provenance = root / 'provenance.json'
                provenance.write_bytes(b'provenance')
                notes = root / 'notes.md'
                notes.write_bytes(b'notes')
                final = root / 'final-manifest.json'
                final.write_text(json.dumps({'schema': 1, 'rehearsal': False, 'commit': self.commit,
                                             'apks': entries, 'unsigned_manifest_sha256': a.sha256(provenance),
                                     'instrumentation_checklist': {'commit': self.commit, 'status': 'not run', 'evidence': '', 'reason': 'fixture'}}))
                args = ['release_publish', action, '--repo', 'owner/repo', '--tag', 'v1',
                        '--approved-commit', self.commit, '--final-manifest', str(final),
                        '--final-manifest-sha256', a.sha256(final), '--provenance', str(provenance),
                        '--notes', str(notes), '--scratch', str(root)] + (['--prerelease'] if prerelease else [])
                commands = []

                def gh(command, *unused, **kwargs):
                    commands.append(command)
                    inputs = next(root.glob('release-publish-*/inputs'))
                    if len(commands) == 1:
                        for original in [final, provenance, notes] + [root / x['file'] for x in entries]:
                            original.write_bytes(b'replaced')
                    if command[1] == 'api':
                        if '--method' in command:
                            return types.SimpleNamespace(stdout='')
                        sha = 'd' * 40 if failure == 'tag' else self.commit
                        return types.SimpleNamespace(stdout=json.dumps({'object': {'type': 'commit', 'sha': sha}}))
                    if command[2] == 'create':
                        self.assertEqual('--prerelease' in command, prerelease)
                        self.assertEqual('--latest=false' in command, prerelease)
                        for entry in entries:
                            self.assertIn(inputs / entry['file'], command)
                            self.assertEqual(a.sha256(inputs / entry['file']), entry['signed_sha256'])
                    if command[2] == 'view':
                        names = [p.name for p in inputs.iterdir()]
                        if failure == 'missing': names.pop()
                        if failure == 'extra': names.append('extra.apk')
                        return types.SimpleNamespace(stdout=json.dumps({'isDraft': failure != 'non-draft' and not any(c[1:3] == ['release', 'edit'] for c in commands),
                                                                         'isPrerelease': (not prerelease if failure == 'wrong-prerelease' else prerelease),
                                                                         'assets': [{'name': n} for n in names]}))
                    if command[2] == 'download':
                        dest = Path(command[command.index('--dir') + 1])
                        if failure == 'snapshot-and-download':
                            (inputs / entries[0]['file']).write_bytes(b'replaced')
                        for path in inputs.iterdir():
                            (dest / path.name).write_bytes(path.read_bytes())
                        if failure == 'download':
                            (dest / entries[0]['file']).write_bytes(b'replaced')
                    return types.SimpleNamespace(stdout='')

                with patch('sys.argv', args), patch.object(release_publish, 'command', side_effect=gh), \
                     patch.object(release_publish, 'create_signed_tag'), \
                     patch.object(release_publish, 'verify_remote_tag',
                                  side_effect=ValueError('wrong tag') if failure == 'tag' else None):
                    self.assertEqual(release_publish.main(), 0 if failure in ('none', 'non-draft') else 1)
                self.assertEqual(any(len(c) > 2 and c[1:3] == ['release', 'edit'] for c in commands),
                                 action == 'publish' and failure == 'none')

    def test_signed_tag_creation_passes_keychain_helper_and_removes_local_tag(self):
        with patch.object(release_publish, 'command') as command:
            release_publish.create_signed_tag('owner/repo', 'v1', self.commit, Path('/key'),
                                              'Signer', 'signer@example.invalid', Path('/askpass'), False)
        calls = command.call_args_list
        self.assertEqual(len(calls), 3)
        tag_command = calls[0].args[0]
        self.assertIn('user.signingkey=/key', tag_command)
        self.assertIn('-s', tag_command)
        self.assertEqual(calls[0].kwargs['env']['SSH_ASKPASS'], '/askpass')
        self.assertEqual(calls[0].kwargs['env']['SSH_ASKPASS_REQUIRE'], 'force')
        self.assertEqual(calls[-1].args[0], ['git', 'tag', '-d', 'v1'])

    def test_signed_tag_cleanup_does_not_remove_existing_local_tag(self):
        failure = subprocess.CalledProcessError(1, 'git')
        with patch.object(release_publish, 'command', side_effect=failure) as command:
            with self.assertRaises(subprocess.CalledProcessError):
                release_publish.create_signed_tag('owner/repo', 'v1', self.commit, Path('/key'),
                                                  'Signer', 'signer@example.invalid', None, False)
            self.assertEqual(command.call_count, 1)
        with patch.object(release_publish, 'command', side_effect=[None, failure, None]) as command:
            with self.assertRaises(subprocess.CalledProcessError):
                release_publish.create_signed_tag('owner/repo', 'v1', self.commit, Path('/key'),
                                                  'Signer', 'signer@example.invalid', None, False)
            self.assertEqual(command.call_args_list[-1].args[0], ['git', 'tag', '-d', 'v1'])

    def test_rejects_unsigned_unverified_and_wrong_commit_tags(self):
        valid = {'tag': 'v1', 'object': {'type': 'commit', 'sha': self.commit},
                 'verification': {'verified': True}}
        cases = [({'type': 'commit', 'sha': self.commit}, valid),
                 ({'type': 'tag', 'sha': 'd' * 40}, dict(valid, verification={'verified': False})),
                 ({'type': 'tag', 'sha': 'd' * 40}, dict(valid, object={'type': 'commit', 'sha': 'e' * 40}))]
        for ref, tag in cases:
            responses = [types.SimpleNamespace(stdout=json.dumps({'object': ref})),
                         types.SimpleNamespace(stdout=json.dumps(tag))]
            with self.subTest(ref=ref, tag=tag), patch.object(release_publish, 'command', side_effect=responses):
                with self.assertRaises(ValueError):
                    release_publish.verify_remote_tag('owner/repo', 'v1', self.commit)
        responses = [types.SimpleNamespace(stdout=json.dumps({'object': {'type': 'tag', 'sha': 'd' * 40}})),
                     types.SimpleNamespace(stdout=json.dumps(valid))]
        with patch.object(release_publish, 'command', side_effect=responses):
            release_publish.verify_remote_tag('owner/repo', 'v1', self.commit)

    def test_snapshot_rejects_symlinks_and_nonregular_files(self):
        import os
        target = self.root / 'target'
        target.write_bytes(b'data')
        a.snapshot(target, self.root / 'regular-snapshot')
        self.assertEqual((self.root / 'regular-snapshot').stat().st_mode & 0o777, 0o600)
        link = self.root / 'link'
        link.symlink_to(target)
        with self.assertRaises(OSError):
            a.snapshot(link, self.root / 'snapshot')
        fifo = self.root / 'fifo'
        os.mkfifo(fifo)
        with self.assertRaisesRegex(ValueError, 'regular file'):
            a.snapshot(fifo, self.root / 'snapshot')
