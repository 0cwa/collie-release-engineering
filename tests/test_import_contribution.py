import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
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
    def changed(self,parent,name,contents,mode='100644'):
        with tempfile.TemporaryDirectory(dir=self.repo) as directory:
            env=publish.environment();env['GIT_INDEX_FILE']=str(Path(directory)/'index')
            def git(*args):return subprocess.check_output(['git','-C',str(self.repo),*args],env=env).decode().strip()
            git('read-tree',parent)
            blob=publish.git(self.repo,'hash-object','-w','--stdin',data=contents).decode().strip()
            git('update-index','--add','--cacheinfo',mode,blob,name)
            tree=git('write-tree')
        return publish.commit(self.repo,tree,[parent],'Reviewed fixture contribution\n')
    def delta(self,before,after):
        return publish.git(self.repo,'diff','--binary','--full-index','--no-ext-diff','--no-textconv','--no-renames',before,after,'--')
    def merge_independent_tip(self,base_main,prior_merge,incoming):
        patch=self.delta(base_main,incoming)
        with tempfile.TemporaryDirectory(dir=self.repo) as directory:
            env=publish.environment();env['GIT_INDEX_FILE']=str(Path(directory)/'index')
            def indexed(*args,data=None):
                return subprocess.run(['git','-C',str(self.repo),'-c','core.hooksPath=/dev/null',
                                       '-c','core.attributesFile=/dev/null',*args],
                                      input=data,env=env,check=True,stdout=subprocess.PIPE).stdout
            indexed('read-tree',prior_merge)
            indexed('apply','--cached','--binary',data=patch)
            tree=indexed('write-tree').decode().strip()
        return publish.commit(self.repo,tree,[prior_merge,incoming],'Merge independent reviewed PR\n')
    def independent_backlog(self):
        # Two PR heads intentionally branch from the same old source base.
        first=self.changed(self.base,'backlog/run.sh',b'#!/bin/sh\necho ready\n','100755')
        second=self.changed(self.base,'backlog/current',b'run.sh\n','120000')
        first_merge=publish.commit(self.repo,publish.tree(self.repo,first),[self.base,first],'Merge first independent PR\n')
        current_main=self.merge_independent_tip(self.base,first_merge,second)
        return first,second,current_main
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

    def test_aggregate_import_replays_independent_landed_backlog_and_preserves_history(self):
        first,second,current_main=self.independent_backlog()
        patch=self.delta(self.base,current_main)
        overlay=self.repo/'aggregate.patch'
        result=importer.import_aggregate(self.repo,self.path,overlay,current_main,hashlib.sha256(patch).hexdigest())
        self.assertEqual(overlay.read_bytes(),patch)
        self.assertEqual(result['plan']['old_main'],current_main)
        self.assertEqual(result['plan']['final_tree'],publish.tree(self.repo,current_main))
        self.assertEqual(result['patch_sha256'],hashlib.sha256(patch).hexdigest())
        self.assertIn('individual PR authorship is not inferred',result['plan']['layers'][-1]['provenance'])
        entries=publish.git(self.repo,'ls-tree','-r',current_main).decode()
        self.assertRegex(entries,r'100755 blob [0-9a-f]+\tbacklog/run\.sh')
        self.assertRegex(entries,r'120000 blob [0-9a-f]+\tbacklog/current')

        plan_path=self.repo/'aggregate-plan.json';plan_path.write_text(json.dumps(result['plan']))
        published=publish.publish(self.repo,plan_path,hashlib.sha256(plan_path.read_bytes()).hexdigest(),
                                  current_main,historical_messages=True)
        self.assertEqual(published['source_tree'],publish.tree(self.repo,current_main))
        for contribution in (first,second):
            publish.git(self.repo,'merge-base','--is-ancestor',contribution,published['integration_commit'])

    def test_aggregate_replays_gitlink_changes(self):
        with tempfile.TemporaryDirectory(dir=self.repo) as directory:
            env=publish.environment();env['GIT_INDEX_FILE']=str(Path(directory)/'index')
            def indexed(*args):
                return subprocess.run(['git','-C',str(self.repo),*args],env=env,check=True,
                                      stdout=subprocess.PIPE).stdout
            indexed('read-tree',self.base)
            indexed('update-index','--add','--cacheinfo','160000',self.base,'vendor/reference')
            tree=indexed('write-tree').decode().strip()
        current_main=publish.commit(self.repo,tree,[self.base],'Reviewed gitlink update\n')
        patch=self.delta(self.base,current_main)
        result=importer.import_aggregate(self.repo,self.path,self.repo/'gitlink.patch',current_main,
                                         hashlib.sha256(patch).hexdigest())
        self.assertEqual(result['after_tree'],publish.tree(self.repo,current_main))
        entry=publish.git(self.repo,'ls-tree','-r',result['after_tree'],'--','vendor/reference').decode()
        self.assertRegex(entry,r'160000 commit [0-9a-f]+\tvendor/reference\n')

    def test_aggregate_rejects_a_base_tree_that_is_not_the_complete_plan_tree(self):
        changed_base=self.changed(self.base,'unexpected-base.txt',b'unexpected\n')
        current_main=self.changed(changed_base,'later.txt',b'later\n')
        plan=copy.deepcopy(self.plan);plan['old_main']=changed_base
        changed_plan=self.repo/'changed-base-plan.json';changed_plan.write_text(json.dumps(plan))
        overlay=self.repo/'changed-base.patch'
        with self.assertRaisesRegex(ValueError,'base tree differs'):
            importer.import_aggregate(self.repo,changed_plan,overlay,current_main,'0'*64)
        self.assertFalse(overlay.exists())

    def test_aggregate_rejects_a_target_outside_approved_main_history(self):
        unrelated=publish.commit(self.repo,publish.tree(self.repo,self.base),[],'Unrelated main root\n')
        overlay=self.repo/'unrelated.patch'
        with self.assertRaisesRegex(ValueError,'not descended'):
            importer.import_aggregate(self.repo,self.path,overlay,unrelated,hashlib.sha256(b'').hexdigest())
        self.assertFalse(overlay.exists())

    def test_aggregate_rejects_a_stale_patch_digest_before_writing(self):
        _,_,current_main=self.independent_backlog();overlay=self.repo/'stale-digest.patch'
        with self.assertRaisesRegex(ValueError,'separately reviewed digest'):
            importer.import_aggregate(self.repo,self.path,overlay,current_main,'0'*64)
        self.assertFalse(overlay.exists())

    def test_aggregate_rejects_missing_pr_delta_even_when_that_patch_digest_is_reviewed(self):
        first,_,current_main=self.independent_backlog()
        omitted= self.delta(self.base,first)
        original_git=publish.git
        def incomplete_delta(repo,*args,**kwargs):
            if args and args[0]=='diff':return omitted
            return original_git(repo,*args,**kwargs)
        overlay=self.repo/'missing-pr.patch'
        with mock.patch.object(publish,'git',side_effect=incomplete_delta):
            with self.assertRaisesRegex(ValueError,'complete current-main tree'):
                importer.import_aggregate(self.repo,self.path,overlay,current_main,
                                          hashlib.sha256(omitted).hexdigest())
        self.assertFalse(overlay.exists())

    def test_aggregate_rejects_mode_and_symlink_substitution_even_with_reviewed_digests(self):
        first,second,current_main=self.independent_backlog()
        wrong_mode=self.changed(self.base,'backlog/run.sh',b'#!/bin/sh\necho ready\n','100644')
        wrong_link=self.changed(self.base,'backlog/current',b'other.sh\n','120000')
        bad_deltas=(
            self.delta(self.base,wrong_mode)+self.delta(self.base,second),
            self.delta(self.base,first)+self.delta(self.base,wrong_link),
        )
        for name,delta in zip(('mode','symlink'),bad_deltas):
            with self.subTest(change=name):
                original_git=publish.git
                def substituted_delta(repo,*args,_delta=delta,**kwargs):
                    if args and args[0]=='diff':return _delta
                    return original_git(repo,*args,**kwargs)
                overlay=self.repo/f'wrong-{name}.patch'
                with mock.patch.object(publish,'git',side_effect=substituted_delta):
                    with self.assertRaisesRegex(ValueError,'complete current-main tree'):
                        importer.import_aggregate(self.repo,self.path,overlay,current_main,
                                                  hashlib.sha256(delta).hexdigest())
                self.assertFalse(overlay.exists())

    def test_aggregate_rejects_extra_paths_even_when_the_extra_delta_is_reviewed(self):
        _,_,current_main=self.independent_backlog()
        extra= self.changed(current_main,'extra.txt',b'extra\n')
        extra_delta=self.delta(self.base,current_main)+self.delta(current_main,extra)
        original_git=publish.git
        def extra_delta_result(repo,*args,**kwargs):
            if args and args[0]=='diff':return extra_delta
            return original_git(repo,*args,**kwargs)
        overlay=self.repo/'extra-path.patch'
        with mock.patch.object(publish,'git',side_effect=extra_delta_result):
            with self.assertRaisesRegex(ValueError,'complete current-main tree'):
                importer.import_aggregate(self.repo,self.path,overlay,current_main,
                                          hashlib.sha256(extra_delta).hexdigest())
        self.assertFalse(overlay.exists())

    def test_aggregate_rejects_conflicting_patch_without_creating_an_overlay(self):
        _,_,current_main=self.independent_backlog();invalid=b'not a Git patch\n'
        original_git=publish.git
        def invalid_delta(repo,*args,**kwargs):
            if args and args[0]=='diff':return invalid
            return original_git(repo,*args,**kwargs)
        overlay=self.repo/'conflict.patch'
        with mock.patch.object(publish,'git',side_effect=invalid_delta):
            with self.assertRaisesRegex(ValueError,'does not apply cleanly'):
                importer.import_aggregate(self.repo,self.path,overlay,current_main,
                                          hashlib.sha256(invalid).hexdigest())
        self.assertFalse(overlay.exists())

    def test_aggregate_refuses_to_replace_an_existing_overlay(self):
        _,_,current_main=self.independent_backlog();overlay=self.repo/'existing.patch'
        overlay.write_bytes(b'owner data')
        with self.assertRaisesRegex(ValueError,'refusing to overwrite'):
            importer.import_aggregate(self.repo,self.path,overlay,current_main,
                                      hashlib.sha256(self.delta(self.base,current_main)).hexdigest())
        self.assertEqual(overlay.read_bytes(),b'owner data')

    def test_aggregate_requires_repository_root_to_avoid_pathspec_prefixing(self):
        current_main=self.changed(self.base,'nested-check.txt',b'changed\n')
        nested=self.repo/'nested/worktree';nested.mkdir(parents=True)
        overlay=self.repo/'nested-path.patch'
        with self.assertRaisesRegex(ValueError,'Git worktree root'):
            importer.import_aggregate(nested,self.path,overlay,current_main,
                                      hashlib.sha256(self.delta(self.base,current_main)).hexdigest())
        self.assertFalse(overlay.exists())
