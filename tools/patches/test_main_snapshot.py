import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import main_snapshot


class MainSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.previous = os.getcwd()
        os.chdir(self.tmp.name)
        self.git('init', '-q')
        self.git('config', 'user.name', 'Test')
        self.git('config', 'user.email', 'test@example.invalid')
        Path('README.md').write_text('old')
        self.git('add', '.')
        self.git('commit', '-qm', 'base')

    def tearDown(self):
        os.chdir(self.previous)
        self.tmp.cleanup()

    def git(self, *args):
        return subprocess.check_output(['git', *args], text=True).strip()

    def commit(self, path, content):
        Path(path).write_text(content)
        self.git('add', path)
        self.git('commit', '-qm', 'change')
        return self.git('rev-parse', 'HEAD')

    def result(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            main_snapshot.main()
        return output.getvalue().strip()

    def test_pre_and_post_profile_snapshot_with_later_docs(self):
        self.assertEqual(self.result(), 'main-snapshot-transition ' + main_snapshot.INITIAL_MAIN)
        base = self.commit('molly-build.json', '{}')
        self.assertEqual(self.result(), 'main-maintenance ' + base)
        self.commit('molly-build.json', json.dumps({'profiles': {}}))
        final = self.commit('README.md', 'later docs')
        self.assertEqual(self.result(), 'main-maintenance ' + final)

    def test_malformed_profile_is_rejected(self):
        self.commit('molly-build.json', '{')
        with self.assertRaises(json.JSONDecodeError):
            self.result()

    def test_merge_from_side_branch_uses_main_first_parent(self):
        self.commit('molly-build.json', '{}')
        main_branch = self.git('branch', '--show-current')
        self.git('checkout', '-qb', 'fork')
        self.commit('molly-build.json', json.dumps({'profiles': {}}))
        self.git('checkout', '-q', main_branch)
        self.git('merge', '-q', '--no-ff', '-m', 'land fork', 'fork')
        self.assertEqual(self.result(), 'main-maintenance ' + self.git('rev-parse', 'HEAD'))
