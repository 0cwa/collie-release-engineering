"""Real-Git fixtures for the bounded operator-side preparation verifier."""
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / 'tools/patches'))
import preparation_verifier as verifier
import runtime_receipts


class PreparationVerifierTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name) / 'repo'
        self.repo.mkdir()
        self.git('init', '-q')
        self.git('config', 'user.name', 'Preparation fixture')
        self.git('config', 'user.email', 'fixture@example.invalid')
        (self.repo / 'gradle.properties').write_text(
            'ordinary=kept\ncollieBuildRevision=' + '1' * 40 + '\ncollieBuildTimestamp=1\n')
        pending = self.repo / 'app/src/main/assets/collie-log/unreleased/12.txt'
        pending.parent.mkdir(parents=True)
        pending.write_text('topic: fixes\npr: 12\nFixes a fixture issue.\n')
        self.git('add', '.')
        self.commit('baseline')
        self.baseline = self.git('rev-parse', 'HEAD')
        self.timestamp = self.git('show', '-s', '--format=%ct', self.baseline)

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.repo), *args], text=True,
                                       stderr=subprocess.PIPE).strip()

    def commit(self, message):
        self.git('-c', 'commit.gpgsign=false', 'commit', '-qm', message)

    def prepare(self, *, extra=False, code='177801', version='8.35.2-1-rc1', timestamp=None,
                channel='stable'):
        (self.repo / 'gradle.properties').write_text(
            f'ordinary=kept\ncollieBuildRevision={self.baseline}\n'
            f'collieBuildTimestamp={timestamp or self.timestamp}\nreleaseVersionName={version}\nreleaseVersionCode={code}\n')
        pending = self.repo / 'app/src/main/assets/collie-log/unreleased/12.txt'
        pending.unlink()
        log = self.repo / f'app/src/main/assets/collie-log/releases/{version}.txt'
        log.parent.mkdir(parents=True)
        log.write_text(f'version: {version}\ndate: 2026-11-17\n\ntopic: fixes\npr: 12\nFixes a fixture issue.\n')
        edition = 'trailblazer-beta' if channel == 'trailblazer-beta' else 'android'
        changelog = self.repo / f'fastlane/metadata/{edition}/en-US/changelogs/{code}.txt'
        changelog.parent.mkdir(parents=True)
        changelog.write_bytes(b'- Fixes a fixture issue.\n')
        if extra:
            (self.repo / 'unexpected.txt').write_text('extra\n')
        self.git('add', '-A')
        self.commit('prepared release')
        self.final = self.git('rev-parse', 'HEAD')
        self.plan_path = self.repo.parent / 'approved-plan.json'
        plan = {'schema': 1, 'kind': 'release-plan', 'mode': 'new-source', 'purpose': 'production',
                'source_commit': self.final, 'engineering_revision': '2' * 40,
                'workflow_revision': '3' * 40, 'package': 'im.collie.app',
                'version_code': int(code), 'version_name': version, 'certificate_sha256': 'a' * 64,
                'source_receipt_sha256': 'b' * 64, 'build_inputs_sha256': 'c' * 64,
                'required_test_reports': ['unit']}
        if channel == 'trailblazer-beta':
            plan.update(channel=channel, tag='trailblazer-v' + version,
                        title='Collie TrailBlazer Beta ' + version, prerelease=True,
                        certificate_sha256=runtime_receipts.CHANNELS[channel]['apk_certificate'],
                        package=runtime_receipts.CHANNELS[channel]['package'])
        self.plan_path.write_text(json.dumps(plan, sort_keys=True, indent=2) + '\n')
        self.plan_sha = hashlib.sha256(self.plan_path.read_bytes()).hexdigest()

    def check(self, *, baseline=None, channel='stable'):
        return verifier.preparation(self.repo, baseline or self.baseline, self.plan_path,
                                     self.plan_sha, channel)

    def update_plan_source(self):
        self.final = self.git('rev-parse', 'HEAD')
        plan = json.loads(self.plan_path.read_text())
        plan['source_commit'] = self.final
        self.plan_path.write_text(json.dumps(plan))
        self.plan_sha = hashlib.sha256(self.plan_path.read_bytes()).hexdigest()

    def run_cli(self, output):
        return subprocess.run([sys.executable, str(Path(verifier.__file__).resolve()),
            '--checkout', str(self.repo), '--baseline-commit', self.baseline,
            '--release-plan', str(self.plan_path), '--approved-release-plan-sha256', self.plan_sha,
            '--channel', 'stable', '--output', str(output)], text=True, capture_output=True)

    def test_timestamp_does_not_execute_configured_signature_verifier(self):
        raw = subprocess.check_output(['git', '-C', str(self.repo), 'cat-file', 'commit', self.baseline])
        headers, message = raw.split(b'\n\n', 1)
        signed = (headers + b'\ngpgsig -----BEGIN PGP SIGNATURE-----\n \n'
                  b' Zml4dHVyZQ==\n -----END PGP SIGNATURE-----\n\n' + message)
        self.baseline = subprocess.check_output(
            ['git', '-C', str(self.repo), 'hash-object', '-t', 'commit', '-w', '--stdin'],
            input=signed).decode().strip()
        self.git('reset', '--hard', self.baseline)
        self.prepare()
        marker = self.repo.parent / 'signature-verifier-ran'
        program = self.repo.parent / 'signature-verifier'
        program.write_text('#!/bin/sh\n: > ' + shlex.quote(str(marker)) + '\nexit 1\n')
        program.chmod(0o755)
        self.git('config', 'gpg.format', 'openpgp')
        self.git('config', 'gpg.program', str(program))
        self.git('config', 'log.showSignature', 'true')
        # Confirm this signed object exercises the configured executable.
        self.git('show', '--show-signature', '-s', self.baseline)
        self.assertTrue(marker.exists())
        marker.unlink()
        self.check()
        self.assertFalse(marker.exists())

    def test_form_feed_cannot_move_revision_into_another_property_value(self):
        path = self.repo / 'gradle.properties'
        path.write_bytes(path.read_bytes().replace(b'ordinary=kept\n', b'ordinary=kept\f\n'))
        self.git('add', 'gradle.properties')
        self.commit('baseline with form feed')
        self.baseline = self.git('rev-parse', 'HEAD')
        self.timestamp = self.git('show', '--no-show-signature', '-s', '--format=%ct', self.baseline)
        self.prepare()
        data = path.read_bytes().replace(b'ordinary=kept\ncollieBuildRevision=',
                                         b'ordinary=kept\fcollieBuildRevision=')
        data = data.replace((self.baseline + '\n').encode(), (self.baseline + '\n\n').encode(), 1)
        path.write_bytes(data)
        self.git('add', 'gradle.properties')
        self.commit('move revision into ordinary value')
        self.update_plan_source()
        with self.assertRaisesRegex(ValueError, 'non-release Gradle properties'):
            self.check()

    def test_java_natural_line_endings_preserve_other_bytes(self):
        for ending in (b'\n', b'\r', b'\r\n'):
            with self.subTest(ending=ending):
                data = b'ordinary=kept\f' + ending + b'releaseVersionCode=177801' + ending
                values, retained = verifier.properties(data)
                self.assertEqual(values, {'releaseVersionCode': '177801'})
                self.assertEqual(retained.encode(), b'ordinary=kept\f' + ending)

    def test_existing_manifest_and_dangling_symlink_are_not_overwritten(self):
        self.prepare()
        output = self.repo.parent / 'preparation.json'
        output.write_bytes(b'keep existing bytes')
        result = self.run_cli(output)
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertEqual(output.read_bytes(), b'keep existing bytes')
        output.unlink()
        target = self.repo.parent / 'must-not-be-created.json'
        output.symlink_to(target)
        result = self.run_cli(output)
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertTrue(output.is_symlink())
        self.assertFalse(target.exists())

    def test_deleted_gitlink_is_not_hidden_by_local_diff_configuration(self):
        self.git('update-index', '--add', '--cacheinfo', '160000,' + self.baseline + ',unexpected-submodule')
        self.commit('baseline with gitlink')
        self.baseline = self.git('rev-parse', 'HEAD')
        self.timestamp = self.git('show', '--no-show-signature', '-s', '--format=%ct', self.baseline)
        self.git('rm', '--cached', 'unexpected-submodule')
        self.prepare()
        self.git('config', 'diff.ignoreSubmodules', 'all')
        with self.assertRaisesRegex(ValueError, 'unapproved path'):
            self.check()

    def test_valid_preparation_emits_canonical_bound_manifest(self):
        self.prepare()
        manifest = self.check()
        self.assertEqual(manifest['verifier_id'], verifier.VERIFIER_ID)
        self.assertEqual(manifest['release_plan_sha256'], self.plan_sha)
        self.assertEqual(manifest['baseline_commit'], self.baseline)
        self.assertEqual(manifest['baseline_tree'], self.git('rev-parse', self.baseline + '^{tree}'))
        self.assertEqual(manifest['baseline_committer_time'], int(self.timestamp))
        self.assertEqual(manifest['final_commit'], self.final)
        self.assertEqual([entry['change'] for entry in manifest['delta']].count('D'), 1)
        self.assertNotIn('receipt', json.dumps(manifest))

    def test_cli_writes_canonical_manifest_outside_checkout(self):
        self.prepare()
        output = self.repo.parent / 'preparation.json'
        script = Path(verifier.__file__).resolve()
        result = subprocess.run([sys.executable, str(script), '--checkout', str(self.repo),
            '--baseline-commit', self.baseline, '--release-plan', str(self.plan_path),
            '--approved-release-plan-sha256', self.plan_sha, '--channel', 'stable',
            '--output', str(output)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Machine verification only', result.stdout)
        data = output.read_bytes()
        value = json.loads(data)
        self.assertEqual(data, (json.dumps(value, sort_keys=True, indent=2) + '\n').encode())
        self.assertEqual(os.stat(output).st_mode & 0o777, 0o600)

    def test_cli_refuses_manifest_inside_source_checkout(self):
        self.prepare()
        output = self.repo / 'preparation.json'
        result = subprocess.run([sys.executable, str(Path(verifier.__file__).resolve()),
            '--checkout', str(self.repo), '--baseline-commit', self.baseline,
            '--release-plan', str(self.plan_path), '--approved-release-plan-sha256', self.plan_sha,
            '--channel', 'stable', '--output', str(output)], text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(output.exists())

    def test_beta_plan_selects_only_beta_changelog_path(self):
        self.prepare(channel='trailblazer-beta')
        manifest = self.check(channel='trailblazer-beta')
        added = {entry['path'] for entry in manifest['delta'] if entry['change'] == 'A'}
        self.assertIn('fastlane/metadata/trailblazer-beta/en-US/changelogs/177801.txt', added)
        self.assertNotIn('fastlane/metadata/android/en-US/changelogs/177801.txt', added)

    def test_local_external_diff_configuration_is_not_executed(self):
        self.prepare()
        marker = self.repo.parent / 'external-diff-ran'
        script = self.repo.parent / 'external-diff'
        script.write_text('#!/bin/sh\ntouch "$1"\n')
        script.chmod(0o755)
        self.git('config', 'diff.external', str(script) + ' ' + str(marker))
        self.check()
        self.assertFalse(marker.exists())

    def test_replacement_refs_and_shallow_git_stores_are_rejected(self):
        self.prepare()
        replacement = self.git('commit-tree', self.git('rev-parse', self.baseline + '^{tree}'),
                               '-m', 'replacement object')
        self.git('replace', self.baseline, replacement)
        with self.assertRaisesRegex(ValueError, 'hostile or incomplete'):
            self.check()
        self.git('replace', '-d', self.baseline)
        (self.repo / '.git/shallow').write_text(self.baseline + '\n')
        with self.assertRaisesRegex(ValueError, 'hostile or incomplete'):
            self.check()

    def test_unapproved_extra_delta_is_rejected(self):
        self.prepare(extra=True)
        with self.assertRaisesRegex(ValueError, 'unapproved path'):
            self.check()

    def test_untracked_final_checkout_is_rejected(self):
        self.prepare()
        (self.repo / 'untracked.txt').write_text('late change\n')
        with self.assertRaisesRegex(ValueError, 'untracked source paths'):
            self.check()

    def test_staged_index_change_after_final_commit_is_rejected(self):
        self.prepare()
        (self.repo / 'gradle.properties').write_text((self.repo / 'gradle.properties').read_text() + 'late=value\n')
        self.git('add', 'gradle.properties')
        with self.assertRaisesRegex(ValueError, 'index entries'):
            self.check()

    def test_wrong_operator_baseline_or_final_plan_identity_is_rejected(self):
        self.prepare()
        with self.assertRaises((ValueError, subprocess.CalledProcessError)):
            self.check(baseline='4' * 40)
        plan = json.loads(self.plan_path.read_text())
        plan['source_commit'] = self.baseline
        self.plan_path.write_text(json.dumps(plan))
        self.plan_sha = hashlib.sha256(self.plan_path.read_bytes()).hexdigest()
        with self.assertRaises(ValueError):
            self.check()

    def test_wrong_baseline_timestamp_is_rejected(self):
        self.prepare(timestamp='1')
        with self.assertRaisesRegex(ValueError, 'timestamp'):
            self.check()

    def test_wrong_version_or_path_is_rejected(self):
        self.prepare(code='177802')
        plan = json.loads(self.plan_path.read_text())
        plan['version_code'] = 177801
        self.plan_path.write_text(json.dumps(plan))
        self.plan_sha = hashlib.sha256(self.plan_path.read_bytes()).hexdigest()
        with self.assertRaises(ValueError):
            self.check()

    def test_noncanonical_duplicate_release_property_is_rejected(self):
        self.prepare()
        path = self.repo / 'gradle.properties'
        path.write_text(path.read_text() + 'releaseVersionCode=177801\n')
        self.git('add', 'gradle.properties')
        self.commit('duplicate property')
        self.final = self.git('rev-parse', 'HEAD')
        plan = json.loads(self.plan_path.read_text())
        plan['source_commit'] = self.final
        self.plan_path.write_text(json.dumps(plan))
        self.plan_sha = hashlib.sha256(self.plan_path.read_bytes()).hexdigest()
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            self.check()

    def test_wrong_edition_path_and_invalid_changelog_bytes_are_rejected(self):
        self.prepare()
        self.git('rm', '-q', 'fastlane/metadata/android/en-US/changelogs/177801.txt')
        path = self.repo / 'fastlane/metadata/trailblazer-beta/en-US/changelogs/177801.txt'
        path.parent.mkdir(parents=True)
        path.write_bytes(b'\xff' * 501)
        self.git('add', '-A')
        self.commit('wrong edition')
        self.final = self.git('rev-parse', 'HEAD')
        plan = json.loads(self.plan_path.read_text())
        plan['source_commit'] = self.final
        self.plan_path.write_text(json.dumps(plan))
        self.plan_sha = hashlib.sha256(self.plan_path.read_bytes()).hexdigest()
        with self.assertRaisesRegex(ValueError, 'unapproved path'):
            self.check()

    def test_malformed_project_log_and_changelog_size_are_rejected(self):
        self.prepare()
        log = self.repo / 'app/src/main/assets/collie-log/releases/8.35.2-1-rc1.txt'
        log.write_text('not a release header\n')
        self.git('add', str(log.relative_to(self.repo)))
        self.commit('malformed log')
        self.final = self.git('rev-parse', 'HEAD')
        plan = json.loads(self.plan_path.read_text())
        plan['source_commit'] = self.final
        self.plan_path.write_text(json.dumps(plan))
        self.plan_sha = hashlib.sha256(self.plan_path.read_bytes()).hexdigest()
        with self.assertRaisesRegex(ValueError, 'project log'):
            self.check()

    def test_executable_mode_change_in_required_output_is_rejected(self):
        self.prepare()
        log = self.repo / 'app/src/main/assets/collie-log/releases/8.35.2-1-rc1.txt'
        log.chmod(0o755)
        self.git('add', str(log.relative_to(self.repo)))
        self.commit('executable release log')
        self.final = self.git('rev-parse', 'HEAD')
        plan = json.loads(self.plan_path.read_text())
        plan['source_commit'] = self.final
        self.plan_path.write_text(json.dumps(plan))
        self.plan_sha = hashlib.sha256(self.plan_path.read_bytes()).hexdigest()
        with self.assertRaisesRegex(ValueError, 'ordinary non-executable'):
            self.check()

    def test_non_ascii_or_oversized_changelog_is_rejected(self):
        self.prepare()
        changelog = self.repo / 'fastlane/metadata/android/en-US/changelogs/177801.txt'
        changelog.write_bytes(b'a' * 501)
        self.git('add', str(changelog.relative_to(self.repo)))
        self.commit('oversized changelog')
        self.final = self.git('rev-parse', 'HEAD')
        plan = json.loads(self.plan_path.read_text())
        plan['source_commit'] = self.final
        self.plan_path.write_text(json.dumps(plan))
        self.plan_sha = hashlib.sha256(self.plan_path.read_bytes()).hexdigest()
        with self.assertRaisesRegex(ValueError, 'ASCII and at most 500'):
            self.check()
        self.git('reset', '--hard', self.baseline)
        self.prepare()
        changelog.write_bytes(b'- Caf\xc3\xa9.\n')
        self.git('add', str(changelog.relative_to(self.repo)))
        self.commit('non-ASCII changelog')
        self.final = self.git('rev-parse', 'HEAD')
        plan = json.loads(self.plan_path.read_text())
        plan['source_commit'] = self.final
        self.plan_path.write_text(json.dumps(plan))
        self.plan_sha = hashlib.sha256(self.plan_path.read_bytes()).hexdigest()
        with self.assertRaisesRegex(ValueError, 'ASCII and at most 500'):
            self.check()

    def test_project_log_uses_app_topic_and_sentence_limits(self):
        self.prepare()
        log = self.repo / 'app/src/main/assets/collie-log/releases/8.35.2-1-rc1.txt'
        log.write_text('version: 8.35.2-1-rc1\ndate: 2026-11-17\n\ntopic: weather\nA short sentence.\n')
        self.git('add', str(log.relative_to(self.repo)))
        self.commit('unknown project log topic')
        self.final = self.git('rev-parse', 'HEAD')
        plan = json.loads(self.plan_path.read_text())
        plan['source_commit'] = self.final
        self.plan_path.write_text(json.dumps(plan))
        self.plan_sha = hashlib.sha256(self.plan_path.read_bytes()).hexdigest()
        with self.assertRaisesRegex(ValueError, 'unknown project log topic'):
            self.check()
        self.git('reset', '--hard', self.baseline)
        self.prepare()
        log.write_text('version: 8.35.2-1-rc1\ndate: 2026-11-17\n\n' + 'x' * 121 + '\n')
        self.git('add', str(log.relative_to(self.repo)))
        self.commit('overlong project log entry')
        self.final = self.git('rev-parse', 'HEAD')
        plan = json.loads(self.plan_path.read_text())
        plan['source_commit'] = self.final
        self.plan_path.write_text(json.dumps(plan))
        self.plan_sha = hashlib.sha256(self.plan_path.read_bytes()).hexdigest()
        with self.assertRaisesRegex(ValueError, '120 characters'):
            self.check()


if __name__ == '__main__':
    unittest.main()
