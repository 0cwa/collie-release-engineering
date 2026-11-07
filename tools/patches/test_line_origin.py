import json
import contextlib
import io
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import line_origin


class Fixture:
    """A tiny history: old Signal -> Molly, old Signal -> new Signal, plus a port result."""

    def __init__(self, directory):
        self.path = Path(directory)
        self.run('init', '-q')
        self.run('config', 'user.name', 'Test')
        self.run('config', 'user.email', 'test@example.invalid')

    def run(self, *args, data=None):
        env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
        return subprocess.run(['git', '-C', str(self.path), *args], input=data, env=env, check=True,
                              stdout=subprocess.PIPE).stdout.decode().strip()

    def commit(self, files, parent=None, message='c'):
        self.run('read-tree', '--empty')
        for name, text in files.items():
            blob = self.run('hash-object', '-w', '--stdin', data=text.encode())
            self.run('update-index', '--add', '--cacheinfo', f'100644,{blob},{name}')
        tree = self.run('write-tree')
        args = ['commit-tree', tree, '-m', message] + (['-p', parent] if parent else [])
        return self.run(*args)


class LineOriginTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.objects = tempfile.TemporaryDirectory()
        self.fixture = Fixture(self.tmp.name)
        f = self.fixture
        shared = 'a\nb\nc\nd\ne\nf\ng\nh\n'
        self.old = f.commit({'app/src/main/A.kt': shared, 'app/src/main/Gone.kt': 'x\ny\n',
                             'app/src/main/res/values-de/strings.xml': 'de\n'})
        self.molly = f.commit({'app/src/main/A.kt': 'a\nMOLLY\nc\nd\ne\nf\ng\nh\n',
                               'app/src/main/Gone.kt': 'x\ny\nmolly-only\n',
                               'app/src/main/res/values-de/strings.xml': 'de\n'}, self.old)
        self.signal = f.commit({'app/src/main/A.kt': 'a\nb\nc\nd\ne\nf\ng\nSIGNAL\n',
                                'app/src/main/Gone.kt': 'x\ny\n',
                                'app/src/main/res/values-de/strings.xml': 'de\nneu\n'}, self.old)
        self.fork_base = f.commit({'app/src/main/A.kt': 'a\nb\nc\nd\ne\nf\ng\nSIGNAL\n',
                                   'app/src/main/Gone.kt': 'x\ny\n',
                                   'app/src/main/res/values-de/strings.xml': 'de\nneu\n'}, self.signal)
        patcher = patch.object(line_origin, 'FORK_SIGNAL_BASE', self.fork_base)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.repo = line_origin.Repo(f.path, self.objects.name)

    def tearDown(self):
        self.tmp.cleanup()
        self.objects.cleanup()

    def result(self, files):
        commit = self.fixture.commit(files)
        return line_origin.combined(self.repo, commit, [self.molly, self.signal])

    def test_side_choices_count_nothing(self):
        files = self.result({'app/src/main/A.kt': 'a\nMOLLY\nc\nd\ne\nf\ng\nSIGNAL\n',
                             'app/src/main/Gone.kt': 'x\ny\nmolly-only\n',
                             'app/src/main/res/values-de/strings.xml': 'de\nneu\n'})
        self.assertEqual(files, {})

    def test_new_lines_and_lines_removed_from_both_parents(self):
        files = self.result({'app/src/main/A.kt': 'a\nMOLLY\nc\nOURS\ne\nf\ng\nSIGNAL\n',
                             'app/src/main/New.kt': 'one\ntwo\n',
                             'app/src/main/res/values-de/strings.xml': 'de\nneu\n'})
        self.assertEqual(files['app/src/main/A.kt'], [1, 1])
        self.assertEqual(files['app/src/main/New.kt'], [2, 0])
        self.assertEqual(files['app/src/main/Gone.kt'], [0, 2])
        summary = line_origin.summarize(files)
        self.assertEqual(summary['production'], {'files': 3, 'written': 3, 'removed': 3})

    def test_molly_lines_not_carried(self):
        index = line_origin.molly_lines(self.repo, self.old, self.molly)
        self.assertEqual(index['app/src/main/A.kt'], {b'MOLLY': 1})
        tree = self.fixture.commit({'app/src/main/A.kt': 'a\nb\nc\n'})
        result, files = line_origin.not_carried(self.repo, tree, index)
        self.assertEqual(result['production'], {'molly_lines': 2, 'not_carried': 2, 'in_removed_paths': 1})
        self.assertEqual(files, {'app/src/main/A.kt': [1, True], 'app/src/main/Gone.kt': [1, False]})

    def test_physical_diff_follows_renames(self):
        before = self.fixture.commit({'app/src/main/A.kt': 'a\nb\nc\nd\ne\nf\n', 'app/src/test/T.kt': 't\n'})
        after = self.fixture.commit({'app/src/main/B.kt': 'a\nb\nc\nd\ne\nF\n', 'app/src/test/T.kt': 't\nu\n'})
        result = line_origin.physical(self.repo, before, after)
        self.assertEqual(result['production'], {'files': 1, 'written': 1, 'removed': 1})
        self.assertEqual(result['tests'], {'files': 1, 'written': 1, 'removed': 0})

    def test_three_parents_exclude_fork_lines(self):
        fork = self.fixture.commit({'app/src/main/A.kt': 'a\nb\nc\nd\nFORK\nf\ng\nSIGNAL\n',
                                    'app/src/main/Gone.kt': 'x\ny\n',
                                    'app/src/main/res/values-de/strings.xml': 'de\nneu\n'}, self.signal)
        commit = self.fixture.commit({'app/src/main/A.kt': 'a\nMOLLY\nc\nd\nFORK\nf\nOURS\nSIGNAL\n',
                                      'app/src/main/Gone.kt': 'x\ny\nmolly-only\n',
                                      'app/src/main/res/values-de/strings.xml': 'de\nneu\n'})
        two = line_origin.combined(self.repo, commit, [self.molly, self.signal])
        three = line_origin.combined(self.repo, commit, [self.molly, self.signal, fork])
        self.assertEqual(two['app/src/main/A.kt'], [2, 2])
        self.assertEqual(three['app/src/main/A.kt'], [1, 1])

    def test_repository_is_not_modified(self):
        commit = self.fixture.commit({'app/src/main/A.kt': 'new\n'})
        before = self.fixture.run('count-objects', '-v')
        line_origin.combined(self.repo, commit, [self.molly, self.signal])
        self.assertEqual(self.fixture.run('count-objects', '-v'), before)
        self.assertTrue(os.listdir(self.objects.name))

    def test_profiles_load_fork_bundle_and_separate_fork_lines(self):
        f = self.fixture
        fork = f.commit({'app/src/main/A.kt': 'a\nb\nc\nd\nFORK\nf\ng\nSIGNAL\n',
                         'app/src/main/Gone.kt': 'x\ny\n',
                         'app/src/main/res/values-de/strings.xml': 'de\nneu\n'}, self.signal)
        target = f.commit({'app/src/main/A.kt': 'a\nMOLLY\nc\nd\nFORK\nf\nOURS\nSIGNAL\n',
                           'app/src/main/Gone.kt': 'x\ny\nmolly-only\n',
                           'app/src/main/res/values-de/strings.xml': 'de\nneu\n'})
        base = f.commit({'app/src/main/A.kt': 'a\nMOLLY\nc\nd\ne\nf\nOURS\nSIGNAL\n',
                         'app/src/main/Gone.kt': 'x\ny\nmolly-only\n',
                         'app/src/main/res/values-de/strings.xml': 'de\nneu\n'})
        root = Path(self.tmp.name)
        (root / 'patches/ports/p').mkdir(parents=True)
        (root / 'patches/ports/p/provenance.json').write_text(json.dumps(
            {'origin_base': self.old, 'origin_target': self.molly, 'signal': self.signal}))
        (root / 'patches/forks/f/layer').mkdir(parents=True)
        f.run('update-ref', 'refs/heads/fork', fork)
        f.run('bundle', 'create', str(root / 'patches/forks/f/upstream.bundle'), 'refs/heads/fork', '^' + self.signal)
        f.run('update-ref', '-d', 'refs/heads/fork')
        (root / '.git/objects' / fork[:2] / fork[2:]).unlink()
        (root / 'patches/forks/f/provenance.json').write_text(json.dumps({'upstream_bundle': 'upstream.bundle', 'upstream_bundle_prerequisite': self.fork_base}))
        with self.assertRaises(ValueError):
            self.repo.commit(fork)
        config = {'port': {'series': 'patches/ports/p/series'}, 'fork_source': {'head': fork},
                  'profiles': {'plain': {'target_tree': base, 'fork_series': []},
                               'forked': {'target_tree': target, 'fork_series': ['patches/forks/f/layer/series'],
                                          'firebase_free': True},
                               'molly-continued': {'target_tree': base, 'fork_series': [], 'firebase_free': False, 'icon_overlay': line_origin.release_profiles.COLLIE_ICON},
                               'molly-foss-continued': {'target_tree': target, 'fork_series': ['patches/forks/f/layer/series'],
                                                        'firebase_free': True, 'icon_overlay': line_origin.release_profiles.COLLIE_ICON}}}
        before = f.run('count-objects', '-v')
        rows = line_origin.profiles(self.repo, root, config)
        self.assertEqual(f.run('count-objects', '-v'), before)
        self.assertEqual([r['name'] for r in rows], ['plain', 'forked', 'molly-continued', 'molly-foss-continued'])
        self.assertIsNone(rows[0]['from_fork'])
        self.assertEqual(rows[0]['classes']['production']['written'], 1)
        self.assertEqual(rows[1]['parents'][2:], [self.old, self.fork_base, fork])
        self.assertEqual(rows[1]['classes']['production']['written'], 1)
        self.assertEqual(rows[1]['from_fork']['production'], 1)
        self.assertEqual(rows[0]['release_overlays'], [])
        self.assertEqual(rows[2]['release_overlays'], [line_origin.release_profiles.COLLIE_ICON])
        self.assertEqual(rows[3]['release_overlays'], [line_origin.release_profiles.COLLIE_ICON])
        self.assertEqual(len(rows[2]['parents']), 3)
        self.assertIsNone(rows[2]['from_fork'])
        self.assertEqual(rows[3]['parents'][2:], [self.old, self.fork_base, fork])
        self.assertEqual(rows[3]['from_fork']['production'], 1)
        text = line_origin.markdown_profiles(rows, 10)
        self.assertIn('| forked | `', text)
        self.assertIn('| `layer` | none | removed | +1 / −1 | 10.0% | 1 |', text)
        self.assertIn('| none | `collie-icon` | included |', text)
        self.assertIn('| `layer` | `collie-icon` | removed |', text)
        self.assertEqual(line_origin.profiles(self.repo, root, {'port': None}), [])

    def test_profiles_do_not_attribute_restored_old_signal_lines_to_fork(self):
        f = self.fixture
        path = 'app/src/main/Restored.kt'
        old = f.commit({path: 'OLD\n'})
        molly = f.commit({path: 'MOLLY\n'}, old)
        signal = f.commit({path: 'SIGNAL\n'}, old)
        fork = f.commit({path: 'OLD\n'}, signal)
        target = f.commit({path: 'OLD\n'})
        root = Path(self.tmp.name)
        (root / 'patches/ports/p').mkdir(parents=True)
        (root / 'patches/ports/p/provenance.json').write_text(json.dumps(
            {'origin_base': old, 'origin_target': molly, 'signal': signal}))
        (root / 'patches/forks/f/layer').mkdir(parents=True)
        f.run('update-ref', 'refs/heads/fork-test', fork)
        f.run('bundle', 'create', str(root / 'patches/forks/f/upstream.bundle'), 'refs/heads/fork-test')
        (root / 'patches/forks/f/provenance.json').write_text(json.dumps({'upstream_bundle': 'upstream.bundle', 'upstream_bundle_prerequisite': self.fork_base}))
        config = {'port': {'series': 'patches/ports/p/series'}, 'fork_source': {'head': fork},
                  'profiles': {'forked': {'target_tree': target, 'fork_series': ['patches/forks/f/layer/series']}}}
        row = line_origin.profiles(self.repo, root, config)[0]
        self.assertEqual(row['from_fork']['production'], 0)
        self.assertEqual(row['classes']['production']['written'], 0)

    def test_profile_fork_base_distinct_from_molly_base_is_not_fork_created(self):
        f = self.fixture
        path = 'app/src/main/Distinct.kt'
        molly_old = f.commit({path: 'MOLLY-OLD\n'})
        molly = f.commit({path: 'MOLLY\n'}, molly_old)
        fork_base = f.commit({path: 'SIGNAL-833\n'}, molly_old)
        signal = f.commit({path: 'SIGNAL-835\n'}, fork_base)
        fork = f.commit({path: 'SIGNAL-833\n'}, fork_base)
        target = f.commit({path: 'SIGNAL-833\n'})
        root = Path(self.tmp.name)
        (root / 'patches/ports/p').mkdir(parents=True)
        (root / 'patches/ports/p/provenance.json').write_text(json.dumps(
            {'origin_base': molly_old, 'origin_target': molly, 'signal': signal}))
        (root / 'patches/forks/f/layer').mkdir(parents=True)
        f.run('update-ref', 'refs/heads/distinct-fork', fork)
        f.run('bundle', 'create', str(root / 'patches/forks/f/upstream.bundle'), 'refs/heads/distinct-fork')
        (root / 'patches/forks/f/provenance.json').write_text(json.dumps(
            {'upstream_bundle': 'upstream.bundle', 'upstream_bundle_prerequisite': fork_base}))
        config = {'port': {'series': 'patches/ports/p/series'}, 'fork_source': {'head': fork},
                  'profiles': {'forked': {'target_tree': target, 'fork_series': ['patches/forks/f/layer/series']}}}
        with patch.object(line_origin, 'FORK_SIGNAL_BASE', fork_base):
            row = line_origin.profiles(self.repo, root, config)[0]
        self.assertEqual(row['from_fork']['production'], 0)
        self.assertEqual(row['classes']['production']['written'], 0)
        self.assertEqual(row['parents'][2:], [molly_old, fork_base, fork])
        (root / 'patches/forks/f/provenance.json').write_text(json.dumps(
            {'upstream_bundle': 'upstream.bundle', 'upstream_bundle_prerequisite': molly_old}))
        with patch.object(line_origin, 'FORK_SIGNAL_BASE', fork_base), self.assertRaisesRegex(ValueError, 'prerequisite differs'):
            line_origin.profiles(self.repo, root, config)

    def test_configured_cli_prepares_profile_trees_before_measurement(self):
        import fork_profiles
        root = Path(self.tmp.name)
        config_file = root / 'config.json'
        config = {'profiles': {'molly-continued': {}}}
        config_file.write_text(json.dumps(config))
        events = []
        def prepare(repo, selected, path):
            events.append(('prepare', repo, selected, path))
        def measure_port(repo, series):
            events.append(('port',))
            return {}
        def measure_profiles(repo, source, selected):
            events.append(('profiles', selected))
            return []
        with patch('sys.argv', ['line_origin.py', '--repo', str(root), 'port', '--config', str(config_file)]), \
             patch.object(fork_profiles, 'prepare_profile_trees', side_effect=prepare), \
             patch.object(line_origin, 'port', side_effect=measure_port), \
             patch.object(line_origin, 'profiles', side_effect=measure_profiles), \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(line_origin.main(), 0)
        self.assertEqual(events, [('prepare', str(root), config, config_file.resolve()), ('port',), ('profiles', config)])

    def test_classification(self):
        cases = {
            'app/src/main/java/A.kt': 'production',
            'app/src/main/res/values/strings.xml': 'production',
            'app/src/main/res/values-night/themes.xml': 'production',
            'app/src/main/res/values-pt-rBR/strings.xml': 'translations',
            'feature/x/src/main/res/values-de/strings2.xml': 'translations',
            'app/src/test/java/T.kt': 'tests',
            'app/src/screenshotTestDebug/A.kt': 'tests',
            'gradle/verification-metadata.xml': 'generated',
            'patches/ports/x/series': 'repository',
            'README.md': 'repository',
        }
        self.assertEqual({p: line_origin.classify(p) for p in cases}, cases)

    def test_stage_of_uses_scope_layer_sets(self):
        self.assertEqual(line_origin.stage_of('initial/maps'), 'PR3 compatibility')
        self.assertEqual(line_origin.stage_of('followups/netcipher-sdk-dsl'), 'PR3 compatibility')
        self.assertEqual(line_origin.stage_of('followups/legacy-backup-precedence'), 'PR4 behavior fixes')
        for name in ('locked-startup-notification-restore', 'molly-donation-label', 'arabic-molly-donation', 'persian-molly-donation'):
            self.assertEqual(line_origin.stage_of('followups/' + name), 'PR4 behavior fixes')
        self.assertEqual(line_origin.stage_of('followups/donation-completion-copy'), 'PR5 behavior additions')
        self.assertEqual(line_origin.stage_of('followups/retention-controls'), 'Feature branch fork layers')

    def test_quoted_paths(self):
        self.assertEqual(line_origin.unquote(b'"a\\tb\\303\\251"'), 'a\tbé')
        self.assertEqual(line_origin.unquote(b'plain/path'), 'plain/path')

    def test_markdown_names_both_inputs(self):
        zero = {k: {'files': 0, 'written': 0, 'removed': 0} for k in line_origin.CLASSES}
        molly = {k: {'molly_lines': 0, 'not_carried': 0, 'in_removed_paths': 0} for k in line_origin.CLASSES}
        molly['production'].update(molly_lines=100, not_carried=5)
        classes = json.loads(json.dumps(zero)); classes['production'].update(written=7, removed=2)
        result = {'molly': 'm' * 40, 'signal': 's' * 40, 'old_signal': 'o' * 40, 'classes': line_origin.CLASS_NOTES,
                  'stages': {'PR3 compatibility': {'tree': 't' * 40, 'classes': classes, 'molly': molly,
                                                   'top_production_files': [], 'top_not_carried': []}},
                  'layers': [{'layer': 'followups/x', 'stage': 'PR4 behavior fixes',
                              'kind': 'physical diff from previous checkpoint',
                              'classes': dict(zero, production={'files': 1, 'written': 3, 'removed': 2})}]}
        text = line_origin.markdown(result)
        self.assertNotIn('may include lines matching the fork', text)
        result['stages'][line_origin.FORK_STAGE] = dict(result['stages']['PR3 compatibility'], includes_fork_lines=True)
        self.assertIn('| Feature branch fork layers (may include lines matching the fork), tree', line_origin.markdown(result))
        self.assertIn('Molly `mmmmmmmmmmmm` and Signal `ssssssssssss`', text)
        self.assertIn('| +7 / −2 | 7.0% | 5 (of which 0 in removed paths) |', text)
        self.assertIn('| `followups/x` | PR4 behavior fixes | +3 / −2 |', text)


if __name__ == '__main__':
    unittest.main()
