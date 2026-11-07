"""Independent fork checkpoints and strict product compositions."""
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import forest
import fork_profiles
import release
import release_profiles


class ForkProfilesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='fork-profile-')
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name).resolve()
        self.git('init', '-q')
        self.git('config', 'user.name', 'Fork Author')
        self.git('config', 'user.email', 'author@example.invalid')
        (self.repo / 'app').write_text('base\n')
        self.git('add', 'app')
        self.git('commit', '-qm', 'base')
        base = self.git('rev-parse', 'HEAD')
        base_tree = self.git('rev-parse', 'HEAD^{tree}')
        (self.repo / 'app').write_text('original retention\n')
        self.git('add', 'app')
        self.git('commit', '-qm', 'original retention')
        upstream_retention = self.git('rev-parse', 'HEAD')
        (self.repo / 'app').write_text('original Firebase\n')
        self.git('add', 'app')
        self.git('commit', '-qm', 'original Firebase')
        upstream_head = self.git('rev-parse', 'HEAD')
        for name, value in (('FORK_SIGNAL_BASE', base), ('FORK_RETENTION_ANCESTOR', upstream_retention), ('FORK_SOURCE_HEAD', upstream_head)):
            mocked = patch.object(fork_profiles, name, value)
            mocked.start()
            self.addCleanup(mocked.stop)
        upstream_ref = 'refs/heads/upstream-fork/signal-foss-8.33.2.0-FOSS'
        self.git('update-ref', upstream_ref, upstream_head)
        self.git('checkout', '--detach', '-q', base)
        self.root = self.repo / release_profiles.FORK_ROOT
        self.root.mkdir(parents=True)
        upstream_bundle = self.root / 'upstream-source.bundle'
        self.git('bundle', 'create', str(upstream_bundle), upstream_ref, '^' + base)
        previous, before = base, base_tree
        layers = []
        for label in ('firebase-free-build', 'collie-icon', 'collie-branding', 'collie-visuals', 'restore-picker', 'notification-default'):
            directory = self.root / label
            directory.mkdir()
            path = 'icon' if label == 'collie-icon' else 'app'
            (self.repo / path).write_text(label + '\n')
            self.git('add', path)
            inspired = label == 'firebase-free-build'
            ours = label in ('retention-controls-fixes', 'collie-icon', 'collie-branding', 'collie-visuals', 'restore-picker', 'notification-default')
            if inspired or ours:
                self.git('-c', 'user.name=Port Author', '-c', 'user.email=port@example.invalid', 'commit', '-qm',
                         label + ('' if ours else '\n\nInspired by ' + upstream_head + '.\n\nInspired-by: Fork Author <author@example.invalid>'))
            else:
                self.git('commit', '-qm', label)
            commit = self.git('rev-parse', 'HEAD')
            tree = self.git('rev-parse', 'HEAD^{tree}')
            raw = subprocess.check_output(['git', '-C', str(self.repo), 'format-patch', '--stdout', '-1', commit])
            raw = raw.replace(b'\n---\n', b'\nSource-Commit: ' + commit.encode() + b'\n\n---\n', 1)
            mailbox = directory / 'change.patch'
            mailbox.write_bytes(raw)
            (directory / 'series').write_text('change.patch\n')
            ref = 'refs/heads/source-checkpoint/' + label
            self.git('update-ref', ref, commit)
            bundle = directory / 'checkpoint.bundle'
            self.git('bundle', 'create', str(bundle), ref, '^' + previous)
            layers.append(({'fixes': 'retention-controls'} if label == 'retention-controls-fixes' else {}) | {'id': label, 'series': label + '/series', 'mailbox': label + '/change.patch',
                'mailbox_sha256': hashlib.sha256(raw).hexdigest(), 'mailbox_bytes': len(raw),
                'source_commit': commit, 'before_tree': before, 'after_tree': tree,
                'source_bundle': label + '/checkpoint.bundle',
                'source_bundle_sha256': hashlib.sha256(bundle.read_bytes()).hexdigest(),
                'source_bundle_bytes': bundle.stat().st_size, 'source_bundle_prerequisite': previous,
                'source_bundle_ref': ref,
                'author': 'Port Author <port@example.invalid>' if inspired or ours else 'Fork Author <author@example.invalid>',
                'upstream_credit': 'ours' if ours else 'inspired' if inspired else 'adapted'} | ({} if ours else {
                'upstream_commit': upstream_head if label == 'firebase-free-build' else upstream_retention,
                'upstream_author': 'Fork Author <author@example.invalid>'}))
            previous, before = commit, tree
        self.data = {'schema': 1, 'source_url': 'https://github.com/tw-hx/Signal-Android',
                     'source_head': upstream_head, 'base_commit': base,
                     'base_tree': base_tree, 'layers': layers,
                     'upstream_bundle': upstream_bundle.name,
                     'upstream_bundle_sha256': hashlib.sha256(upstream_bundle.read_bytes()).hexdigest(),
                     'upstream_bundle_bytes': upstream_bundle.stat().st_size,
                     'upstream_bundle_prerequisite': base, 'upstream_bundle_ref': upstream_ref}
        self.selection = {'base_checkpoint': base, 'base_tree': base_tree,
                          'target_tree': layers[-1]['after_tree'],
                          'signal_commit': base, 'fork_source': {'url': self.data['source_url'], 'head': upstream_head},
                          'fork_series': [release_profiles.FIREBASE, release_profiles.COLLIE_ICON, release_profiles.COLLIE_BRANDING, release_profiles.COLLIE_VISUALS, release_profiles.RESTORE_PICKER, release_profiles.NOTIFICATION_DEFAULT]}
        self.save()

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.repo), *args], text=True, stderr=subprocess.PIPE).strip()

    def save(self):
        (self.root / 'provenance.json').write_text(json.dumps(self.data))

    def test_real_bundles_mailboxes_and_each_replayed_tree(self):
        layers = fork_profiles.fork_layers(self.repo, self.selection)
        output = self.repo.parent / (self.repo.name + '-output')
        self.addCleanup(lambda: subprocess.run(['git', '-C', str(self.repo), 'worktree', 'remove', '--force', str(output)], capture_output=True))
        for i, (mailbox, metadata, expected) in enumerate(layers):
            tree = self.selection['base_tree'] if i == 0 else layers[i-1][2]
            release.apply_resolutions(self.repo, output, tree, [(mailbox, metadata)], existing=i > 0)
            actual = subprocess.check_output(['git', '-C', str(output), 'rev-parse', 'HEAD^{tree}'], text=True).strip()
            self.assertEqual(actual, expected)
        self.assertEqual(actual, self.selection['target_tree'])
        single = dict(self.selection, fork_series=[release_profiles.FIREBASE], target_tree=self.data['layers'][0]['after_tree'])
        self.assertEqual(len(fork_profiles.fork_layers(self.repo, single)), 1)

    def test_new_signal_target_keeps_historical_fork_verification(self):
        base = self.data['upstream_bundle_prerequisite']
        newer = subprocess.check_output(
            ['git', '-C', str(self.repo), 'commit-tree', self.data['base_tree'], '-p', base],
            input='new Signal target\n', text=True).strip()
        selection = dict(self.selection, signal_commit=newer)
        self.assertEqual(len(fork_profiles.fork_layers(self.repo, selection)), 6)
        self.data['upstream_bundle_prerequisite'] = newer
        self.save()
        with self.assertRaisesRegex(forest.ForestError, 'historical Signal source'):
            fork_profiles.fork_layers(self.repo, selection)

    def test_forged_fork_head_rejected_even_if_config_matches(self):
        forged = fork_profiles.FORK_RETENTION_ANCESTOR
        self.data['source_head'] = forged
        self.save()
        selection = dict(self.selection, fork_source={'url': self.data['source_url'], 'head': forged})
        with self.assertRaisesRegex(forest.ForestError, 'pinned fork release'):
            fork_profiles.fork_layers(self.repo, selection)

    def test_continued_overlay_keeps_each_existing_prefix(self):
        for count in (0, 1):
            base = self.data['base_tree'] if not count else self.data['layers'][0]['after_tree']
            self.git('read-tree', base)
            (self.repo / 'icon').write_text('collie-icon\n')
            self.git('add', 'icon')
            expected = self.git('write-tree')
            selected = dict(self.selection, fork_series=[release_profiles.FIREBASE][:count],
                            icon_overlay=release_profiles.COLLIE_ICON, target_tree=expected)
            self.assertEqual(fork_profiles.materialize_continued_icon(self.repo, selected), expected)
            self.assertEqual(self.git('diff-tree', '--no-commit-id', '--name-only', '-r', base, expected), 'icon')
            with self.assertRaisesRegex(forest.ForestError, 'source tree differs'):
                fork_profiles.materialize_continued_icon(self.repo, dict(selected, target_tree='0' * 40))
            with self.assertRaisesRegex(forest.ForestError, 'invalid Continued'):
                fork_profiles.continued_icon(self.repo, dict(selected, icon_overlay='wrong'))

    def test_preparation_handles_a_single_foss_continued_profile(self):
        selection = dict(self.selection, fork_series=[release_profiles.FIREBASE],
                         icon_overlay=release_profiles.COLLIE_ICON)
        config = {'profiles': {'molly-foss-continued': {'fork_series': [release_profiles.FIREBASE], 'icon_overlay': release_profiles.COLLIE_ICON}}}
        with patch.object(fork_profiles.verify_source_bundles, 'verify') as verify, \
             patch.object(fork_profiles, 'ensure_fork_signal_base') as ensure, \
             patch.object(release_profiles, 'select', return_value=(config, selection)), \
             patch.object(fork_profiles, 'fork_layers') as layers, \
             patch.object(fork_profiles, 'materialize_continued_icon') as overlay:
            fork_profiles.prepare_profile_trees(self.repo, config)
        verify.assert_called_once_with(self.repo, None)
        ensure.assert_called_once_with(self.repo)
        layers.assert_called_once_with(self.repo, selection, check_target=False)
        overlay.assert_called_once_with(self.repo, selection)

    def test_fork_base_recovers_missing_partial_clone_blob(self):
        import os
        with tempfile.TemporaryDirectory(prefix='fork-partial-') as directory:
            partial = Path(directory)
            subprocess.run(['git', '-C', str(partial), 'init', '-q'], check=True)
            self.git('config', 'uploadpack.allowFilter', 'true')
            remote = self.repo.as_uri()
            subprocess.run(['git', '-C', str(partial), 'fetch', '--filter=blob:none', '--depth=1',
                            remote, fork_profiles.FORK_SIGNAL_BASE], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            def missing():
                env = dict(os.environ, GIT_NO_LAZY_FETCH='1')
                result = subprocess.run(['git', '-C', str(partial), 'rev-list', '--objects', '--missing=print',
                                         fork_profiles.FORK_SIGNAL_BASE + '^{tree}'], check=True,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, text=True)
                return [line for line in result.stdout.splitlines() if line.startswith('?')]
            self.assertTrue(missing())
            with patch.object(fork_profiles, 'SIGNAL_URL', remote):
                fork_profiles.ensure_fork_signal_base(partial)
                self.assertFalse(missing())
                with patch.object(fork_profiles.git_utils, 'git', wraps=fork_profiles.git_utils.git) as git:
                    fork_profiles.ensure_fork_signal_base(partial)
                    self.assertFalse(any(call.args[1] == 'fetch' for call in git.call_args_list))

    def test_fork_base_repairs_preserve_full_history(self):
        with tempfile.TemporaryDirectory(prefix='fork-history-repair-') as directory:
            root = Path(directory)
            origin = root / 'origin'
            origin.mkdir()
            def git(repo, *args, check=True, env=None):
                return subprocess.run(['git', '-C', str(repo), *args], check=check, env=env,
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            git(origin, 'init', '-q')
            git(origin, 'config', 'user.name', 'Fixture')
            git(origin, 'config', 'user.email', 'fixture@example.invalid')
            git(origin, 'config', 'uploadpack.allowFilter', 'true')
            commits = []
            for number in range(3):
                (origin / 'content').write_text('fixture ' + str(number))
                git(origin, 'add', 'content')
                git(origin, 'commit', '-qm', 'fixture')
                commits.append(git(origin, 'rev-parse', 'HEAD').stdout.strip())
            remote = origin.as_uri()
            env = dict(os.environ, GIT_NO_LAZY_FETCH='1')
            for present in (True, False):
                with self.subTest(commit_present=present):
                    target = root / ('partial' if present else 'missing')
                    target.mkdir()
                    git(target, 'init', '--bare', '-q')
                    args = ['fetch', '--no-tags']
                    if present:
                        args += ['--filter=blob:none']
                    args += [remote, commits[2] if present else commits[1]]
                    git(target, *args)
                    self.assertEqual('false', git(target, 'rev-parse', '--is-shallow-repository').stdout.strip())
                    self.assertEqual(0, git(target, 'merge-base', '--is-ancestor', commits[0], commits[1], check=False).returncode)
                    if present:
                        missing = git(target, 'rev-list', '--objects', '--missing=print', commits[2] + '^{tree}', env=env).stdout
                        self.assertIn('?', missing)
                    with patch.object(fork_profiles, 'SIGNAL_URL', remote), patch.object(fork_profiles, 'FORK_SIGNAL_BASE', commits[2]):
                        fork_profiles.ensure_fork_signal_base(target)
                    self.assertEqual('false', git(target, 'rev-parse', '--is-shallow-repository').stdout.strip())
                    self.assertEqual(0, git(target, 'merge-base', '--is-ancestor', commits[0], commits[2], check=False).returncode)
                    missing = git(target, 'rev-list', '--objects', '--missing=print', commits[2] + '^{tree}', env=env).stdout
                    self.assertNotIn('?', missing)

    def test_rejects_wrong_pins_order_bytes_and_prerequisites(self):
        original = copy.deepcopy(self.data)
        cases = [
            lambda d: d['layers'].reverse(),
            lambda d: d.__setitem__('upstream_bundle_sha256', '0' * 64),
            lambda d: d.__setitem__('upstream_bundle_prerequisite', '0' * 40),
            lambda d: d['layers'][0].__setitem__('before_tree', '0' * 40),
            lambda d: d['layers'][0].__setitem__('source_bundle_prerequisite', '0' * 40),
            lambda d: d['layers'][0].__setitem__('mailbox_sha256', '0' * 64),
            lambda d: d['layers'][0].__setitem__('source_bundle_sha256', '0' * 64),
            lambda d: d['layers'][0].__setitem__('source_commit', '0' * 40),
            lambda d: d['layers'][0].__setitem__('upstream_author', 'wrong'),
            lambda d: d['layers'][0].__setitem__('author', 'Fork Author <author@example.invalid>'),
            lambda d: d['layers'][0].__setitem__('upstream_credit', 'adapted'),
            lambda d: d['layers'][1].__setitem__('upstream_credit', 'inspired'),
            lambda d: d['layers'][1].pop('upstream_credit'),
            lambda d: d['layers'][2].__setitem__('fixes', 'firebase-free-build'),
            lambda d: d['layers'][2].__setitem__('author', 'Fork Author <author@example.invalid>'),
            lambda d: d['layers'][2].__setitem__('upstream_commit', fork_profiles.FORK_RETENTION_ANCESTOR),
            lambda d: d['layers'][2].__setitem__('upstream_credit', 'adapted'),
            lambda d: d['layers'][-1].__setitem__('fixes', 'collie-branding'),
            lambda d: d['layers'][-1].__setitem__('upstream_commit', fork_profiles.FORK_SOURCE_HEAD),
            lambda d: d['layers'][0].__setitem__('mailbox', '../escape.patch'),
        ]
        for mutate in cases:
            with self.subTest(mutate=mutate):
                self.data = copy.deepcopy(original)
                mutate(self.data)
                self.save()
                with self.assertRaises((ValueError, RuntimeError, OSError)):
                    fork_profiles.fork_layers(self.repo, self.selection)
        self.data = original
        self.save()
        bundle = self.root / self.data['upstream_bundle']
        bundle_bytes = bundle.read_bytes()
        bundle.unlink()
        with self.assertRaises((OSError, ValueError)):
            fork_profiles.fork_layers(self.repo, self.selection)
        bundle.write_bytes(bundle_bytes)
        with self.assertRaisesRegex(forest.ForestError, 'final source tree'):

            fork_profiles.fork_layers(self.repo, dict(self.selection, target_tree='0' * 40))
        with self.assertRaisesRegex(forest.ForestError, 'base checkpoint'):
            fork_profiles.fork_layers(self.repo, dict(self.selection, base_checkpoint='0' * 40))

    def test_product_policy_rejects_missing_reordered_and_wrong_flag(self):
        for name, (_, expected, free) in release_profiles.POLICY.items():
            entry = {'base_series': release_profiles.PORT_SERIES, 'base_checkpoint': 'a'*40,
                     'base_tree': 'b'*40, 'fork_series': list(expected), 'target_tree': 'c'*40,
                     'firebase_free': free}
            config = {'profiles': {name: entry}, 'port': {}, 'android': {'tests': [':app:test']},
                      'signal': {'commit': 'd'*40},
                      'fork_source': {'url': 'https://github.com/tw-hx/Signal-Android', 'head': 'e'*40}}
            config['port'] = {'series': release_profiles.PORT_SERIES}
            with patch.object(release_profiles, 'derived_tests', return_value=[':app:test']) as derived:
                _, selected = release_profiles.select(config, name, self.repo)
            derived.assert_called_once_with(self.repo, [':app:test'], expected)
            self.assertEqual(selected['fork_series'], expected)
            self.assertEqual(selected['app']['package_id'], 'im.collie.app')
            entry['icon_overlay'] = release_profiles.COLLIE_ICON
            if name in release_profiles.CONTINUED_ICON:
                with patch.object(release_profiles, 'derived_tests', return_value=[':app:test']):
                    _, selected = release_profiles.select(config, name, self.repo)
                self.assertEqual(selected['icon_overlay'], release_profiles.COLLIE_ICON)
            else:
                with self.assertRaisesRegex(ValueError, 'invalid Continued icon overlay'):
                    release_profiles.select(config, name, self.repo)
            entry['icon_overlay'] = 'wrong'
            with self.assertRaisesRegex(ValueError, 'invalid Continued icon overlay'):
                release_profiles.select(config, name, self.repo)
            del entry['icon_overlay']
            entry['tests'] = [':app:test']
            with self.assertRaisesRegex(ValueError, 'exactly'):
                release_profiles.select(config, name, self.repo)
            del entry['tests']
            entry['firebase_free'] = not free
            with self.assertRaisesRegex(ValueError, 'firebase_free'):
                release_profiles.select(config, name)
            entry['firebase_free'] = free
            entry['fork_series'] = list(reversed(expected)) if len(expected) == 2 else ['wrong']
            with self.assertRaisesRegex(ValueError, 'fork_series'):
                release_profiles.select(config, name)

    def test_profile_tests_derive_from_fork_layer_test_sources(self):
        root = self.repo / release_profiles.FORK_ROOT
        (root / 'one').mkdir(parents=True)
        (root / 'two').mkdir(parents=True)
        def diff(path, deleted=False):
            mode = 'deleted file mode 100644\n' if deleted else 'new file mode 100644\n'
            return f'diff --git a/{path} b/{path}\n{mode}index 0000000..1111111\n--- a/{path}\n+++ b/{path}\n@@ -0,0 +1 @@\n+x\n'
        (root / 'one/0001.patch').write_text('From x\n\n---\n' + diff('app/src/main/java/A.kt')
            + diff('app/src/test/java/org/x/NewTest.kt') + diff('app/src/test/java/org/x/OldTest.kt', deleted=True)
            + diff('app/src/test/java/org/x/KeptTest.kt') + '-- \n2.0\n')
        (root / 'two/0001.patch').write_text(diff('feature/app-settings/src/test/java/org/s/ScreenTest.kt')
            + diff('lib/network/src/test/java/org/n/NetTest.kt') + diff('app/src/test/java/org/x/NewTest.kt'))
        (root / 'provenance.json').write_text(json.dumps({'layers': [
            {'series': 'one/series', 'mailbox': 'one/0001.patch'},
            {'series': 'two/series', 'mailbox': 'two/0001.patch'}]}))
        base = [':lib:network:test', ':app:testDebugUnitTest', '--tests', 'org.x.KeptTest',
                ':feature:app-settings:testDebugUnitTest', '--tests', 'org.s.Other']
        one, two = release_profiles.FORK_ROOT + '/one/series', release_profiles.FORK_ROOT + '/two/series'
        self.assertEqual(release_profiles.derived_tests(self.repo, base, []), base)
        self.assertEqual(release_profiles.derived_tests(self.repo, base, [one, two]),
                         [':lib:network:test', ':app:testDebugUnitTest', '--tests', 'org.x.KeptTest',
                          '--tests', 'org.x.NewTest', ':feature:app-settings:testDebugUnitTest',
                          '--tests', 'org.s.Other', '--tests', 'org.s.ScreenTest'])
        with self.assertRaisesRegex(ValueError, 'exactly one :feature:app-settings: test task'):
            release_profiles.derived_tests(self.repo, base[:4], [two])
        with self.assertRaisesRegex(ValueError, 'no single provenance layer'):
            release_profiles.derived_tests(self.repo, base, [release_profiles.FORK_ROOT + '/three/series'])
        (root / 'one/0001.patch').write_text(diff('app/src/androidTest/java/org/x/DeviceTest.kt'))
        self.assertEqual(release_profiles.derived_tests(self.repo, base, [one]), base)
        self.assertEqual(release_profiles.instrumentation_sources(self.repo, [one]),
                         ['app/src/androidTest/java/org/x/DeviceTest.kt'])
        (root / 'one/0001.patch').write_text(diff('app/src/testFixtures/java/org/x/Fixture.kt'))
        with self.assertRaisesRegex(ValueError, 'no unit test task rule'):
            release_profiles.derived_tests(self.repo, base, [one])

    def test_device_sources_are_recorded_for_both_gated_profiles(self):
        path = 'app/src/androidTest/java/org/x/DeviceTest.kt'
        mailbox = self.root / 'collie-branding/change.patch'
        mailbox.write_text(f'diff --git a/{path} b/{path}\nnew file mode 100644\n')
        base_tests = [':app:testDebugUnitTest', '--tests', 'org.x.UnitTest']
        for name in sorted(release_profiles.NAMES):
            boundary, fork, free = release_profiles.POLICY[name]
            config = {'profiles': {name: {'base_series': release_profiles.PORT_SERIES,
                'base_checkpoint': 'a'*40, 'base_tree': 'b'*40, 'fork_series': list(fork),
                'target_tree': 'c'*40, 'firebase_free': free}},
                'port': {'series': release_profiles.PORT_SERIES},
                'android': {'tests': base_tests}, 'signal': {'commit': 'd'*40},
                'fork_source': {'url': 'https://github.com/tw-hx/Signal-Android', 'head': 'e'*40}}
            selected, profile = release_profiles.select(config, name, self.repo)
            self.assertEqual(selected['android']['tests'], base_tests)
            self.assertEqual(profile['instrumentation_sources'], [path])

    def test_port_prefix_requires_verified_order_and_exact_boundary(self):
        directory = self.repo / release_profiles.PORT_SERIES.rsplit('/', 1)[0]
        directory.mkdir(parents=True)
        (directory / 'series').write_text('a.patch\nr.patch\nb.patch\nu.patch\n')
        layers = [{'id': 'followups/network-initialization-cycle', 'source_commit': 'a'*40,
                   'after_tree': 'b'*40, 'patches': ['a.patch']},
                  {'id': 'followups/registration-network-settings', 'source_commit': 'e'*40,
                   'after_tree': 'f'*40, 'patches': ['r.patch']},
                  {'id': 'followups/donation-completion-copy', 'source_commit': '9'*40,
                   'after_tree': '8'*40, 'patches': ['b.patch']},
                  {'id': 'followups/unifiedpush-registration-retry', 'source_commit': 'c'*40,
                   'after_tree': 'd'*40, 'patches': ['u.patch']}]
        provenance = {'layers': layers, 'mailbox_sha256': {'a.patch': '1', 'r.patch': '3', 'b.patch': '2', 'u.patch': '4'}}
        (directory / 'provenance.json').write_text(json.dumps(provenance))
        records = [(directory / 'a.patch', {'sha256': '1'}), (directory / 'r.patch', {'sha256': '3'}),
                   (directory / 'b.patch', {'sha256': '2'}), (directory / 'u.patch', {'sha256': '4'})]
        selection = {'base_layer': layers[0]['id'], 'base_checkpoint': 'a'*40, 'base_tree': 'b'*40}
        with patch.object(fork_profiles.verify_source_bundles, 'verify') as verify:
            self.assertEqual(fork_profiles.port_prefix(self.repo, 'config.json', selection, records), records[:1])
            verify.assert_called_once_with(self.repo, 'config.json')
            with self.assertRaisesRegex(forest.ForestError, 'checkpoint or tree'):
                fork_profiles.port_prefix(self.repo, 'config.json', dict(selection, base_tree='d'*40), records)
            continued = {'base_layer': layers[3]['id'], 'base_checkpoint': 'c'*40, 'base_tree': 'd'*40}
            self.assertEqual(fork_profiles.port_prefix(self.repo, 'config.json', continued, records), records)
            missing = dict(provenance, layers=[layers[0], layers[2], layers[3]])
            (directory / 'provenance.json').write_text(json.dumps(missing))
            (directory / 'series').write_text('a.patch\nb.patch\nu.patch\n')
            with self.assertRaisesRegex(forest.ForestError, 'missing, repeated, or misordered'):
                fork_profiles.port_prefix(self.repo, 'config.json', continued, [records[0], records[2], records[3]])
            for altered in ([layers[1], layers[0], layers[2], layers[3]],
                            [layers[0], layers[1], layers[1], layers[2], layers[3]]):
                (directory / 'provenance.json').write_text(json.dumps(dict(provenance, layers=altered)))
                flattened = [p for item in altered for p in item['patches']]
                if len(set(flattened)) != len(flattened):
                    continue  # series parser rejects duplicate mailbox entries before layer validation
                (directory / 'series').write_text('\n'.join(flattened) + '\n')
                by_path = {'a.patch': records[0], 'r.patch': records[1], 'b.patch': records[2], 'u.patch': records[3]}
                with self.assertRaisesRegex(forest.ForestError, 'missing, repeated, or misordered'):
                    fork_profiles.port_prefix(self.repo, 'config.json', continued, [by_path[p] for p in flattened])
            (directory / 'provenance.json').write_text(json.dumps(provenance))
            (directory / 'series').write_text('b.patch\na.patch\nr.patch\nu.patch\n')
            with self.assertRaisesRegex(forest.ForestError, 'order'):
                fork_profiles.port_prefix(self.repo, 'config.json', selection, records)

    def test_new_minimal_prefix_requires_all_universal_fixes(self):
        directory = self.repo / release_profiles.PORT_SERIES.rsplit('/', 1)[0]
        directory.mkdir(parents=True)
        names = ['network-initialization-cycle', 'locked-startup-notification-restore',
                 'molly-donation-label', 'arabic-molly-donation', 'persian-molly-donation', 'native-catalog',
                 'registration-network-settings', 'donation-completion-copy', 'unifiedpush-registration-retry']
        layers = [{'id': 'followups/' + name, 'source_commit': 'a' * 40, 'after_tree': 'b' * 40,
                   'patches': [name + '.patch']} for name in names]
        hashes = {layer['patches'][0]: str(i) for i, layer in enumerate(layers)}
        provenance = {'signal': 'a74583b01e260944f699c4da03ba4e8ce04de32b',
                      'layers': layers, 'mailbox_sha256': hashes}
        records = [(directory / path, {'sha256': digest}) for path, digest in hashes.items()]
        def save(items):
            (directory / 'provenance.json').write_text(json.dumps(dict(provenance, layers=items)))
            (directory / 'series').write_text(''.join(x['patches'][0] + '\n' for x in items))
        selection = {'base_layer': layers[5]['id'], 'base_checkpoint': 'a' * 40, 'base_tree': 'b' * 40}
        with patch.object(fork_profiles.verify_source_bundles, 'verify'):
            save(layers)
            self.assertEqual(fork_profiles.port_prefix(self.repo, 'config.json', selection, records), records[:6])
            without_catalog = layers[:5] + layers[6:]
            save(without_catalog)
            with self.assertRaisesRegex(forest.ForestError, 'boundary missing or duplicated'):
                fork_profiles.port_prefix(self.repo, 'config.json', selection, records[:5] + records[6:])
            save(layers)
            with self.assertRaisesRegex(forest.ForestError, 'missing, repeated, or misordered'):
                fork_profiles.port_prefix(self.repo, 'config.json', dict(selection, base_layer=layers[0]['id']), records)
            missing = layers[:1] + layers[2:]
            save(missing)
            with self.assertRaisesRegex(forest.ForestError, 'missing, repeated, or misordered'):
                fork_profiles.port_prefix(self.repo, 'config.json', selection, [records[0]] + records[2:])


if __name__ == '__main__':
    unittest.main()
