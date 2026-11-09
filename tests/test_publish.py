import hashlib
import importlib.util
import json
import os
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).parents[1]/'source'))
spec = importlib.util.spec_from_file_location('publish', Path(__file__).parents[1] / 'source/publish.py')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)

class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        m.git(self.repo, 'init', '--template=', '-q')
        self.empty = m.git(self.repo, 'mktree', data=b'').decode().strip()
        self.signal = m.commit(self.repo, self.empty, [], 'fixture upstream\n')
        self.old = m.commit(self.repo, self.empty, [], 'fixture old Collie history\n')
        m.git(self.repo, 'update-ref', 'HEAD', self.signal)
        # Binary blob, executable mode and symlink are part of the full tree.
        blob = m.git(self.repo, 'hash-object', '-w', '--stdin', data=b'\0binary\xff').decode().strip()
        link = m.git(self.repo, 'hash-object', '-w', '--stdin', data=b'binary').decode().strip()
        self.final = m.git(self.repo, 'mktree', data=(f'100755 blob {blob}\tbinary\n120000 blob {link}\tlink\n').encode()).decode().strip()
        self.plan = {'schema':1, 'signal':self.signal, 'old_main':self.old,
                     'reconstructed_tree':self.final, 'final_tree':self.final,
                     'layers':[{'name':'Composite feature', 'before_tree':self.empty,
                                'after_tree':self.final, 'kind':'feature', 'provenance':'fixture authors'}]}
        self.path = self.repo / 'plan.json'

    def generate(self):
        self.path.write_text(json.dumps(self.plan))
        return m.publish(self.repo, self.path, hashlib.sha256(self.path.read_bytes()).hexdigest(), self.old, historical_messages=True)

    def test_deterministic_binary_mode_symlink_and_histories(self):
        a, b = self.generate(), self.generate()
        self.assertEqual(a, b)
        self.assertEqual(m.tree(self.repo, a['source_commit']), self.final)
        self.assertEqual(m.git(self.repo, 'ls-tree', a['source_commit']), m.git(self.repo, 'ls-tree', self.final))
        for revision in [*a['layer_commits'], a['source_commit'], a['integration_commit']]:
            dates = m.git(self.repo, 'show', '-s', '--format=%at%x00%ct', revision).decode().strip()
            self.assertEqual(dates.split('\x00'), ['946684800', '946684800'])

    def test_approved_publication_timestamp_is_deterministic_and_tree_preserving(self):
        timestamp = '2026-11-09T12:43:27Z'
        self.plan.update(schema=2, publication_timestamp=timestamp)
        with patch.dict(os.environ, {
            'GIT_AUTHOR_DATE': '1999-12-31T23:59:59Z',
            'GIT_COMMITTER_DATE': '1999-12-31T23:59:59Z',
        }):
            a = self.generate()
            b = self.generate()
        self.assertEqual(a, b)
        self.assertEqual(m.tree(self.repo, a['source_commit']), self.final)
        self.assertEqual(m.tree(self.repo, a['integration_commit']), self.final)
        generated = [*a['layer_commits'], a['source_commit'], a['integration_commit']]
        for revision in generated:
            raw = m.git(self.repo, 'show', '-s', '--format=%aI%x00%cI', revision).decode().strip()
            self.assertEqual(raw.split('\x00'), [timestamp, timestamp])

    def test_schema2_rejects_historical_fixture_timestamp(self):
        self.plan.update(schema=2, publication_timestamp='2000-01-01T00:00:00Z')
        with self.assertRaisesRegex(ValueError, 'historical fixture timestamp'):
            m.validate_plan(self.plan)

    def test_tampered_plan(self):
        self.path.write_text(json.dumps(self.plan))
        with self.assertRaisesRegex(ValueError, 'external approval'):
            m.publish(self.repo, self.path, '0'*64, self.old, historical_messages=True)

    def test_stale_main_wrong_parent(self):
        a = self.generate()
        with self.assertRaisesRegex(ValueError, 'stale base'):
            m.verify(self.repo, self.plan, a['source_commit'], a['integration_commit'], self.signal)
        with self.assertRaisesRegex(ValueError, 'integration parents'):
            m.verify(self.repo, self.plan, a['source_commit'], a['source_commit'], self.old)

    def test_hostile_ambient_git_ignored_and_replace_rejected(self):
        with patch.dict(os.environ, {'GIT_DIR':'/nonexistent','GIT_CONFIG_COUNT':'1', 'GIT_CONFIG_KEY_0':'alias.commit-tree','GIT_CONFIG_VALUE_0':'!false'}):
            self.generate()
        m.git(self.repo, 'update-ref', 'refs/replace/' + self.signal, self.old)
        with self.assertRaisesRegex(ValueError, 'replace refs'):
            self.generate()

    def test_alternates_and_grafts_rejected(self):
        for name in ('info/grafts', 'objects/info/alternates'):
            file = self.repo / '.git' / name; file.parent.mkdir(parents=True, exist_ok=True); file.write_text('hostile')
            with self.assertRaisesRegex(ValueError, 'grafts, alternates'):
                self.generate()
            file.unlink()

    def test_partial_clone_and_promisor_object_packs_rejected(self):
        m.git(self.repo, 'config', '--local', 'extensions.partialClone', 'remote-origin')
        with self.assertRaisesRegex(ValueError, 'partial clone configuration'):
            self.generate()
        m.git(self.repo, 'config', '--local', '--unset', 'extensions.partialClone')
        pack = self.repo / '.git' / 'objects' / 'pack' / 'fixture.promisor'
        pack.parent.mkdir(parents=True, exist_ok=True);pack.write_bytes(b'')
        with self.assertRaisesRegex(ValueError, 'promisor object packs'):
            self.generate()
        pack.unlink()

    def test_unreviewed_overlay_and_reordered_layers(self):
        self.plan['layers'][0]['kind'] = 'publication'
        with self.assertRaisesRegex(ValueError, 'overlay precedes'):
            self.generate()
        self.plan['layers'][0]['kind'] = 'feature'
        self.plan['layers'].append(dict(self.plan['layers'][0]))
        with self.assertRaisesRegex(ValueError, 'continuity'):
            self.generate()

    def test_round_trip_exact_binary_delta(self):
        a = self.generate()
        delta = self.repo / 'contribution.patch'
        record = m.round_trip(self.repo, self.signal, a['source_commit'], delta)
        self.assertEqual(record['after_tree'], self.final)
        m.git(self.repo, 'read-tree', self.empty)
        m.git(self.repo, 'apply', '--cached', '--binary', str(delta))
        self.assertEqual(m.git(self.repo, 'write-tree').decode().strip(), self.final)

    def test_synthetic_upgrade_keeps_old_history_and_new_upstream(self):
        old = self.generate()
        upgraded = m.commit(self.repo, self.empty, [self.signal], 'fixture upstream upgrade\n')
        self.plan['signal'] = upgraded
        self.plan['old_main'] = old['integration_commit']
        self.old = old['integration_commit']
        new = self.generate()
        m.git(self.repo, 'merge-base', '--is-ancestor', old['integration_commit'], new['integration_commit'])
        m.git(self.repo, 'merge-base', '--is-ancestor', upgraded, new['integration_commit'])

    def test_duplicate_fields_and_boolean_schema_rejected(self):
        with self.assertRaisesRegex(ValueError, 'duplicate JSON'):
            m.load_plan('{"schema":1,"schema":1}')
        self.plan['schema'] = True
        with self.assertRaisesRegex(ValueError, 'publication-plan'):
            self.generate()
