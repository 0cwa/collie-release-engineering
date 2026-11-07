import hashlib
import json
import shutil
from pathlib import Path
import subprocess
import tempfile
import unittest
import zipfile
from unittest.mock import patch

import prepare_port_report as prepare


class PrepareReportTests(unittest.TestCase):
    def test_snapshot_rejects_noncanonical_paths_and_oversized_archive(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive_path = root / 'snapshot.zip'
            commit = '1' * 40
            metadata = {'baseline_profile_commit': commit,
                        'baseline_snapshot': {'path': 'snapshot.zip', 'sha256': ''}}
            for entry in ('../test/series', 'sub/../series', './series', 'a//b', '/absolute', 'a\\b', 'series'):
                with self.subTest(entry=entry):
                    with zipfile.ZipFile(archive_path, 'w') as archive:
                        archive.writestr('manifest.json', json.dumps({'baseline_profile_commit': commit}))
                        archive.writestr('molly-build.json', json.dumps({'port': {'series': 'patches/test/series'}}))
                        archive.writestr('patches/test/provenance.json', '{}')
                        archive.writestr('patches/test/series', entry + '\n')
                    metadata['baseline_snapshot']['sha256'] = hashlib.sha256(archive_path.read_bytes()).hexdigest()
                    with tempfile.TemporaryDirectory(dir=root) as destination:
                        expected = 'duplicate report baseline snapshot destination' if entry == 'series' else 'invalid report baseline snapshot path'
                        with self.assertRaisesRegex(ValueError, expected):
                            prepare.snapshot(root, metadata, Path(destination))
            with archive_path.open('wb') as output:
                output.seek(20_000_000)
                output.write(b'x')
            with tempfile.TemporaryDirectory(dir=root) as destination:
                with self.assertRaisesRegex(ValueError, 'compressed size limit'):
                    prepare.snapshot(root, metadata, Path(destination))

    def test_fresh_repo_reconstructs_historical_binary_and_mode_and_rejects_bad_pins(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, repo = root / 'source', root / 'repo'
            source.mkdir()
            def git(where, *args):
                return subprocess.check_output(['git', '-C', str(where), *args], stderr=subprocess.PIPE).decode().strip()
            git(source, 'init', '-q')
            git(source, 'config', 'user.name', 'Test')
            git(source, 'config', 'user.email', 'test@example.invalid')
            (source / 'app').write_text('old\n')
            git(source, 'add', 'app')
            git(source, 'commit', '-qm', 'base')
            base = git(source, 'rev-parse', 'HEAD')
            mechanical = git(source, 'rev-parse', 'HEAD^{tree}')
            (source / 'app').write_text('new\n')
            (source / 'app').chmod(0o755)
            (source / 'binary').write_bytes(b'\0\xff\x01')
            git(source, 'add', 'app', 'binary')
            git(source, 'commit', '-qm', 'change\n\nSource-Commit: ' + base)
            target = git(source, 'rev-parse', 'HEAD^{tree}')
            mailbox = subprocess.check_output(['git', '-C', str(source), 'format-patch', '--binary', '--stdout', '-1'])
            git(source, 'checkout', '-qb', 'metadata', base)
            port = source / 'patches/ports/test'
            port.mkdir(parents=True)
            (port / 'series').write_text('0001-change.patch\n')
            (port / '0001-change.patch').write_bytes(mailbox)
            config = {'schema': 1, 'signal': {'commit': base}, 'molly': {'target': base, 'tree': mechanical},
                      'port': {'mechanical_tree': mechanical, 'series': 'patches/ports/test/series'}, 'target_tree': target}
            provenance = {'origin_base': base, 'origin_target': base, 'signal': base, 'mechanical_tree': mechanical,
                          'target_tree': target, 'mailbox_sha256': {'0001-change.patch': hashlib.sha256(mailbox).hexdigest()}}
            (source / 'molly-build.json').write_text(json.dumps(config))
            (port / 'provenance.json').write_text(json.dumps(provenance))
            git(source, 'add', '.')
            git(source, 'commit', '-qm', 'historical profile')
            historical = git(source, 'rev-parse', 'HEAD')
            (port / 'series').write_text('')
            current_config = dict(config, target_tree=mechanical)
            current_provenance = dict(provenance, target_tree=mechanical, mailbox_sha256={})
            (source / 'molly-build.json').write_text(json.dumps(current_config))
            (port / 'provenance.json').write_text(json.dumps(current_provenance))
            (port / 'report-metadata.json').write_text(json.dumps({'baseline_profile_commit': historical,
                'baseline_target_tree': target, 'baseline_mechanical_tree': mechanical}))
            git(source, 'add', '.')
            git(source, 'commit', '-qm', 'current profile')
            git(root, 'clone', '-q', '--no-local', '--single-branch', '-b', 'metadata', str(source), str(repo))
            self.assertNotEqual(subprocess.run(['git', '-C', str(repo), 'cat-file', '-e', target], stderr=subprocess.DEVNULL).returncode, 0)
            with patch.object(prepare.release, 'mechanical_merge', return_value=(mechanical, set(), '')):
                prepare.prepare(repo)
            self.assertEqual(git(repo, 'cat-file', '-t', target), 'tree')
            (repo / 'patches/ports/test/series').write_text('0001-change.patch\n')
            bad = dict(provenance, mailbox_sha256={'0001-change.patch': '0' * 64})
            with patch.object(prepare.release, 'mechanical_merge', return_value=(mechanical, set(), '')):
                with self.assertRaisesRegex(ValueError, 'mailbox hash mismatch'):
                    prepare.materialize(repo, config, bad, repo, root / 'bad-index')
            metadata_path = repo / 'patches/ports/test/report-metadata.json'
            metadata = json.loads(metadata_path.read_text())
            metadata['baseline_target_tree'] = mechanical
            metadata_path.write_text(json.dumps(metadata))
            with self.assertRaisesRegex(ValueError, 'immutable report baseline'):
                prepare.prepare(repo)

            # Reproduce a squash: the original baseline commit does not exist in this
            # object database, while its exact inputs are available as a pinned snapshot.
            squashed = root / 'squashed'
            shutil.copytree(repo, squashed, ignore=shutil.ignore_patterns('.git'))
            git(squashed, 'init', '-q')
            git(squashed, 'config', 'user.name', 'Test')
            git(squashed, 'config', 'user.email', 'test@example.invalid')
            git(squashed, 'fetch', '-q', str(source), base)
            snapshot_path = squashed / 'patches/ports/test/baseline.zip'
            with zipfile.ZipFile(snapshot_path, 'w') as archive:
                archive.writestr('manifest.json', json.dumps({'baseline_profile_commit': historical}))
                for path in ('molly-build.json', 'patches/ports/test/provenance.json',
                             'patches/ports/test/series', 'patches/ports/test/0001-change.patch'):
                    archive.writestr(path, subprocess.check_output(['git', '-C', str(source), 'show', historical + ':' + path]))
            snapshot_metadata = {'baseline_profile_commit': historical, 'baseline_target_tree': target,
                                 'baseline_mechanical_tree': mechanical,
                                 'baseline_snapshot': {'path': 'patches/ports/test/baseline.zip',
                                     'sha256': hashlib.sha256(snapshot_path.read_bytes()).hexdigest()}}
            (squashed / 'patches/ports/test/report-metadata.json').write_text(json.dumps(snapshot_metadata))
            # Restore the current, empty series after the earlier negative test.
            (squashed / 'patches/ports/test/series').write_text('')
            self.assertNotEqual(subprocess.run(['git', '-C', str(squashed), 'cat-file', '-e', historical],
                                              stderr=subprocess.DEVNULL).returncode, 0)
            with patch.object(prepare.release, 'mechanical_merge', return_value=(mechanical, set(), '')):
                prepare.prepare(squashed)
            self.assertEqual(git(squashed, 'cat-file', '-t', target), 'tree')
            snapshot_path.write_bytes(snapshot_path.read_bytes() + b'tamper')
            with self.assertRaisesRegex(ValueError, 'snapshot hash mismatch'):
                prepare.prepare(squashed)

    def test_preparation_precedes_each_scope_projection(self):
        root = Path(__file__).resolve().parents[2]
        workflow = (root / '.github/workflows/molly-patch-release.yml').read_text()
        scope, proof, android = workflow.split('  source-proof:', 1)[0], *workflow.split('  source-proof:', 1)[1].split('  android:', 1)
        self.assertLess(scope.index('python3 tools/patches/prepare_port_report.py'), scope.index('python3 tools/patches/scope.py'))
        self.assertLess(scope.index('python3 tools/patches/prepare_port_report.py'), scope.index('python3 tools/patches/verify_source_bundles.py'))
        for job in (proof, android):
            self.assertLess(job.index('python3 tools/patches/prepare_port_report.py'), job.index('python3 tools/patches/release.py'))
        preflight = (root / 'tools/patches/preflight.sh').read_text()
        self.assertLess(preflight.index('python3 tools/patches/prepare_port_report.py'), preflight.index('python3 tools/patches/scope.py'))
        self.assertLess(preflight.index('python3 tools/patches/prepare_port_report.py'), preflight.index('python3 tools/patches/release.py'))

    def test_main_lower_reaches_every_release_reconstruction(self):
        root = Path(__file__).resolve().parents[2]
        workflow = (root / '.github/workflows/molly-patch-release.yml').read_text()
        self.assertIn('integration_lower: ${{ steps.scope.outputs.integration_lower }}', workflow)
        self.assertIn('echo "integration_lower=${integration_lower:-}" >> "$GITHUB_OUTPUT"', workflow)
        for job in workflow.split('python3 tools/patches/release.py --repo . --config molly-build.json')[1:]:
            self.assertIn('"${scope_extra[@]}"', job.split('\n\n', 1)[0])
        self.assertEqual(workflow.count('INTEGRATION_LOWER: ${{ needs.scope.outputs.integration_lower }}'), 3)
        self.assertEqual(workflow.count('scope_extra+=(--integration-lower "$INTEGRATION_LOWER")'), 3)
        for job in workflow.split('INTEGRATION_LOWER: ${{ needs.scope.outputs.integration_lower }}')[1:]:
            self.assertLess(job.index('git fetch --no-tags'), job.index('python3 tools/patches/prepare_port_report.py'))

    def test_reviewed_profiles_fetched_before_all_fork_scope_checks(self):
        root = Path(__file__).resolve().parents[2]
        workflow = (root / '.github/workflows/molly-patch-release.yml').read_text()
        scope_job, rest = workflow.split('  source-proof:', 1)
        source_job, rest = rest.split('  android:', 1)
        android_job, instrumentation_job = rest.split('  instrumentation:', 1)
        self.assertLess(scope_job.index('python3 tools/patches/fetch_reviewed_profiles.py'), scope_job.index('python3 tools/patches/scope.py'))
        for job in (source_job, android_job, instrumentation_job):
            self.assertLess(job.index('python3 tools/patches/fetch_reviewed_profiles.py'), job.index('python3 tools/patches/release.py'))

    def test_feature_workflow_routes_and_runs_both_profiles(self):
        root = Path(__file__).resolve().parents[2]
        workflow = (root / '.github/workflows/molly-patch-release.yml').read_text()
        self.assertIn('stack/5-behavior-additions) scope=features ;;', workflow)
        self.assertIn('feature/foss-8.33.2) scope=features; base_ref=stack/5-behavior-additions ;;', workflow)
        self.assertEqual(workflow.count("hashFiles('patches/ports/signal-8.33.2/followups/firebase-free-build/series') != ''"), 2)
        self.assertEqual(workflow.count('ORG_GRADLE_PROJECT_mollyFirebaseFree:'), 2)
        self.assertEqual(workflow.count('python3 tools/patches/build.py assemble'), 2)
        self.assertEqual(workflow.count('python3 tools/patches/build.py test'), 2)
        self.assertLess(workflow.index("name: ${{ matrix.profile || 'molly' }}-android-${{"), workflow.index('id: foss_assemble'))
        self.assertIn('name: molly-android-firebase-free-${{', workflow)
        foss = workflow.split('id: foss_prepare', 1)[1]
        self.assertLess(foss.index('rm -rf --'), foss.index('python3 tools/patches/build.py assemble'))
        self.assertIn("-path '*/build/test-results'", foss)
        self.assertEqual(foss.count('--report-dir "$RUNNER_TEMP/foss-reports"'), 2)
        self.assertNotIn('${{ runner.temp }}/reports', foss)
        self.assertIn("steps.foss_prepare.outcome == 'success'", foss)
        self.assertIn("steps.foss_assemble.outcome != 'skipped'", foss)
        logs, apk = foss.split('      - name: Upload the Firebase-free APK', 1)
        self.assertNotIn('outputs/apk', logs.split('      - name: Upload Firebase-free build logs', 1)[1])
        self.assertIn("steps.foss_assemble.outcome == 'success'", apk)
        self.assertIn('if-no-files-found: error', apk)
