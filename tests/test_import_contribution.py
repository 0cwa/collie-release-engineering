import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).parents[1]/'source'))
import import_contribution as importer
import publish
import test_publish as fixtures


class ReviewedImportTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.PublicationTests('runTest');self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.repo=self.fixture.repo;self.base=self.fixture.generate()['integration_commit']
        self.plan=copy.deepcopy(self.fixture.plan);self.plan['old_main']=self.base
        self.path=self.repo/'approved-plan.json';self.path.write_text(json.dumps(self.plan))
    def changed(self,parent,name,contents):
        with tempfile.TemporaryDirectory(dir=self.repo) as directory:
            env=publish.environment();env['GIT_INDEX_FILE']=str(Path(directory)/'index')
            def git(*args):return subprocess.check_output(['git','-C',str(self.repo),*args],env=env).decode().strip()
            git('read-tree',parent)
            blob=publish.git(self.repo,'hash-object','-w','--stdin',data=contents).decode().strip()
            git('update-index','--add','--cacheinfo','100644',blob,name)
            tree=git('write-tree')
        return publish.commit(self.repo,tree,[parent],'Reviewed fixture contribution\n')
    def test_later_main_changes_cannot_be_omitted_by_importing_an_earlier_commit(self):
        first=self.changed(self.base,'first.txt',b'first\n');later=self.changed(first,'later.txt',b'later\n')
        patch=self.repo/'rejected.patch'
        with self.assertRaisesRegex(ValueError,'current main tree differs'):
            importer.import_reviewed(self.repo,self.path,first,patch,current_main=later)
        self.assertFalse(patch.exists(),'stale import wrote an overlay before rejecting its base')
    def test_merge_with_exact_reviewed_tree_preserves_history_and_tree(self):
        reviewed=self.changed(self.base,'first.txt',b'first\n')
        merged=publish.commit(self.repo,publish.tree(self.repo,reviewed),[self.base,reviewed],'Merge reviewed fixture\n')
        result=importer.import_reviewed(self.repo,self.path,reviewed,self.repo/'accepted.patch',current_main=merged)
        self.assertEqual(result['plan']['old_main'],merged);self.assertEqual(result['plan']['final_tree'],publish.tree(self.repo,merged))
        self.path.write_text(json.dumps(result['plan']));out=publish.publish(self.repo,self.path,hashlib.sha256(self.path.read_bytes()).hexdigest(),merged,historical_messages=True)
        self.assertEqual(out['source_tree'],publish.tree(self.repo,merged));publish.git(self.repo,'merge-base','--is-ancestor',reviewed,out['integration_commit'])
    def test_sequential_imports_retain_both_deltas_and_the_final_main(self):
        first=self.changed(self.base,'first.txt',b'first\n');second=self.changed(first,'second.txt',b'second\n')
        one=importer.import_reviewed(self.repo,self.path,first,self.repo/'first.patch',current_main=first)
        self.path.write_text(json.dumps(one['plan']))
        two=importer.import_reviewed(self.repo,self.path,second,self.repo/'second.patch',current_main=second)
        self.assertEqual(two['plan']['final_tree'],publish.tree(self.repo,second));self.assertEqual(two['plan']['old_main'],second)
        self.assertEqual(len(two['plan']['layers']),len(self.plan['layers'])+2)
    def test_an_explicit_current_main_snapshot_is_required_by_the_api(self):
        reviewed=self.changed(self.base,'first.txt',b'first\n')
        with self.assertRaisesRegex(ValueError,'current main snapshot required'):
            importer.import_reviewed(self.repo,self.path,reviewed,self.repo/'unbound.patch')
