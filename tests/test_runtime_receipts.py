import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).parents[1]/'tools/patches'))
import runtime_receipts as r


class RuntimePlanTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.manifest={'schema':1,'commit':'a'*40,'workflow_revision':'a'*40,'package':'im.collie.app','version_code':177703,'version_name':'fixture','certificate_sha256':'b'*64}
    def args(self,plan):
        path=self.root/'plan.json';r.write(path,plan)
        return argparse.Namespace(release_plan=path,approved_release_plan_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    def test_legacy_adapter_is_explicit_and_does_not_fabricate_receipts(self):
        plan=r.import_legacy(self.manifest,'c'*40,'fixture');self.assertEqual(r.plan(self.args(plan))[0],plan)
        self.assertEqual(plan['mode'],'legacy');self.assertIsNone(plan['source_receipt_sha256'])
        self.assertIsNone(plan['build_inputs_sha256']);self.assertEqual(plan['required_test_reports'],[])
    def test_no_implicit_legacy_route_even_for_rehearsal_new_evidence(self):
        args=argparse.Namespace(release_plan=None)
        with self.assertRaises(ValueError):r.plan(args)
        self.assertIsNone(r.plan(args,historical_rehearsal=True))
        r.check_manifest(None,self.manifest)
        for addition in ({'schema':2},{'release_mode':'new-source'},{'source_receipt_sha256':'a'*64}):
            changed={**self.manifest,**addition}
            with self.assertRaises(ValueError):r.check_manifest(None,changed)
            with self.assertRaises(ValueError):r.import_legacy(changed,'c'*40,'fixture')
    def test_legacy_cannot_strip_new_receipts_or_independent_identities(self):
        for change in ({'workflow_revision':'d'*40},{'release_plan_sha256':'b'*64}):
            with self.assertRaises(ValueError):r.import_legacy({**self.manifest,**change},'c'*40,'production')
        plan=r.import_legacy(self.manifest,'c'*40,'fixture');plan['source_receipt_sha256']='a'*64
        with self.assertRaises(ValueError):r.plan(self.args(plan))
        plan=r.import_legacy(self.manifest,'c'*40,'fixture');plan['required_test_reports']=['must-not-be-ignored']
        with self.assertRaisesRegex(ValueError,'unchecked required reports'):r.plan(self.args(plan))
    def test_new_source_requires_complete_stable_inputs_and_named_reports(self):
        plan={**r.import_legacy(self.manifest,'c'*40,'fixture'),'mode':'new-source','workflow_revision':'d'*40,'source_receipt_sha256':'e'*64,'build_inputs_sha256':'f'*64,'required_test_reports':['actual-report']}
        self.assertEqual(r.plan(self.args(plan))[0],plan)
        for key,value in [('source_receipt_sha256',None),('build_inputs_sha256',None),('required_test_reports',[]),('required_test_reports',[{}]),('required_test_reports',['duplicate','duplicate'])]:
            with self.assertRaises(ValueError):r.plan(self.args({**plan,key:value}))
        args=self.args(plan);args.repo='0cwa/collieim-android'
        with self.assertRaises(ValueError):r.plan(args)
    def test_unapproved_digest_and_duplicate_fields_rejected(self):
        args=self.args(r.import_legacy(self.manifest,'c'*40,'fixture'));args.approved_release_plan_sha256='0'*64
        with self.assertRaises(ValueError):r.plan(args)
        path=self.root/'dup.json';path.write_text('{"schema":1,"schema":2}')
        with self.assertRaises(ValueError):r.document(path)


class SourceCheckoutTests(unittest.TestCase):
    def setUp(self):
        import subprocess
        self.subprocess=subprocess
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.repo=Path(self.tmp.name)
        self.git('init','-q');self.git('config','user.name','Fixture');self.git('config','user.email','fixture@example.invalid')
        self.file=self.repo/'plain.txt';self.file.write_bytes(b'approved\n')
        (self.repo/'nested').mkdir();(self.repo/'nested/data.bin').write_bytes(b'\x00\xffbinary\n')
        (self.repo/'link').symlink_to('nested/data.bin')
        self.git('add','.');self.git('commit','-qm','Tracked source integrity fixture')
        self.commit=self.git('rev-parse','HEAD');self.tree=self.git('rev-parse','HEAD^{tree}')
        self.index=self.repo/'.git/index'
    def git(self,*args):
        return self.subprocess.check_output(['git','-C',str(self.repo),*args],text=True).strip()
    def check(self):
        before=self.index.read_bytes()
        try:r.source_unchanged(self.repo,self.commit,self.tree)
        finally:self.assertEqual(self.index.read_bytes(),before,'collector changed original index')
    def check_staged(self,tree,*,require_head_tree=False):
        r.source_worktree_matches_index(self.repo,self.commit,tree,require_head_tree=require_head_tree)
    def stage_overlay(self,contents):
        self.file.write_bytes(contents);self.git('add','plain.txt')
        return self.git('write-tree')
    def test_actual_complete_tracked_bytes_modes_and_symlinks_pass_without_index_change(self):
        self.check()
    def test_empty_untracked_directories_are_not_source_inputs(self):
        (self.repo/'tools/codeql').mkdir(parents=True)
        (self.repo/'tools/screenshots').mkdir(parents=True)
        self.check()
    def test_exact_engineering_tree_can_be_checked_without_fetching_promisor_history(self):
        self.git('config','extensions.partialClone','fixture')
        self.git('config','remote.fixture.promisor','true')
        pack=self.repo/'.git/objects/pack/fixture.promisor';pack.parent.mkdir(parents=True,exist_ok=True);pack.write_bytes(b'')
        with self.assertRaisesRegex(ValueError,'promisor object packs'):
            self.check()
        before=self.index.read_bytes()
        r.source_worktree_matches_index(self.repo,self.commit,self.tree,require_head_tree=True,allow_promisor=True)
        self.assertEqual(self.index.read_bytes(),before)
    def test_mode_change_is_rejected_with_local_filemode_disabled(self):
        self.git('config','core.fileMode','false');self.file.chmod(0o755)
        with self.assertRaisesRegex(ValueError,'executable mode'):self.check()
    def test_same_size_changed_bytes_with_restored_mtime_are_rejected(self):
        import os
        self.git('config','core.trustCTime','false');self.git('update-index','--refresh')
        before=self.file.stat();self.file.write_bytes(b'tampered\n');os.utime(self.file,ns=(before.st_atime_ns,before.st_mtime_ns))
        with self.assertRaisesRegex(ValueError,'source bytes'):self.check()
    def test_regular_file_cannot_stand_in_for_symlink_with_symlinks_disabled(self):
        self.git('config','core.symlinks','false');link=self.repo/'link';link.unlink();link.write_bytes(b'nested/data.bin')
        with self.assertRaisesRegex(ValueError,'source symlink'):self.check()
    def test_symlink_cannot_stand_in_for_regular_file(self):
        self.file.unlink();self.file.symlink_to('nested/data.bin')
        with self.assertRaisesRegex(ValueError,'file type'):self.check()
    def test_parent_directory_symlink_is_rejected(self):
        (self.repo/'nested').rename(self.repo/'moved');(self.repo/'nested').symlink_to('moved',target_is_directory=True)
        with self.assertRaisesRegex(ValueError,'untracked source paths|missing or unsafe'):self.check()
    def test_assumed_unchanged_and_skip_worktree_cannot_hide_source(self):
        for enabled,disabled in [('--assume-unchanged','--no-assume-unchanged'),('--skip-worktree','--no-skip-worktree')]:
            with self.subTest(flag=enabled):
                self.git('update-index',enabled,'plain.txt');self.file.write_bytes(b'tampered\n')
                try:
                    with self.assertRaisesRegex(ValueError,'hidden tracked'):self.check()
                finally:self.file.write_bytes(b'approved\n');self.git('update-index',disabled,'plain.txt')
    def test_staged_content_change_is_rejected_without_index_refresh(self):
        self.file.write_bytes(b'tampered\n');self.git('add','plain.txt')
        with self.assertRaisesRegex(ValueError,'source index'):self.check()
    def test_staged_overlay_worktree_is_checked_even_when_head_tree_is_older(self):
        overlay=self.stage_overlay(b'candidate overlay\n')
        self.check_staged(overlay)
        self.assertNotEqual(self.git('rev-parse','HEAD^{tree}'),overlay)
    def test_staged_overlay_rejects_raw_worktree_drift_before_producer_writes(self):
        overlay=self.stage_overlay(b'candidate overlay\n')
        self.file.write_bytes(b'changed after staging\n')
        with self.assertRaisesRegex(ValueError,'source bytes changed'):
            self.check_staged(overlay)
    def test_staged_overlay_rejects_hidden_executable_mode_change(self):
        overlay=self.stage_overlay(b'candidate overlay\n')
        self.git('config','core.fileMode','false');self.file.chmod(0o700)
        with self.assertRaisesRegex(ValueError,'executable mode'):
            self.check_staged(overlay)
    def test_staged_overlay_rejects_untracked_inputs_including_ignored_files(self):
        (self.repo/'.gitignore').write_text('*.cache\n');self.git('add','.gitignore');self.git('commit','-qm','Fixture ignore rule')
        self.commit=self.git('rev-parse','HEAD');tree=self.git('rev-parse','HEAD^{tree}')
        (self.repo/'ignored.cache').write_bytes(b'not an input\n')
        with self.assertRaisesRegex(ValueError,'untracked source paths'):
            r.source_worktree_matches_index(self.repo,self.commit,tree,require_head_tree=True)
    def test_dirty_tracked_manifest_input_cannot_pass_engineering_clean_check(self):
        manifest=self.repo/'manifest.json';manifest.write_text('{"schema":1}\n')
        self.git('add','manifest.json');self.git('commit','-qm','Add fixture manifest')
        self.commit=self.git('rev-parse','HEAD');tree=self.git('rev-parse','HEAD^{tree}')
        manifest.write_text('{"schema":2}\n');self.git('add','manifest.json')
        with self.assertRaisesRegex(ValueError,'tracked source index entries differ from expected tree'):
            r.source_worktree_matches_index(self.repo,self.commit,tree,require_head_tree=True)
    def test_local_clean_filter_cannot_hide_bytes_or_run_during_check(self):
        import shlex
        marker=self.repo/'filter-was-executed'
        script='from pathlib import Path;Path('+repr(str(marker))+').touch();print("approved")'
        self.git('config','filter.fixture.clean','python3 -c '+shlex.quote(script))
        (self.repo/'.git/info/attributes').write_text('plain.txt filter=fixture\n')
        self.file.write_bytes(b'tampered\n')
        with self.assertRaisesRegex(ValueError,'source bytes'):self.check()
        self.assertFalse(marker.exists(),'collector executed an ambient source conversion filter')
    def test_mode_change_between_stat_and_open_is_rejected(self):
        from unittest.mock import patch
        original=r.os.open
        def opening(path,*args,**kwargs):
            if path==b'plain.txt' and 'dir_fd' in kwargs:self.file.chmod(0o755)
            return original(path,*args,**kwargs)
        with patch.object(r.os,'open',side_effect=opening):
            with self.assertRaisesRegex(ValueError,'executable mode'):self.check()
    def test_staged_gitlink_cannot_be_hidden_by_submodule_diff_settings(self):
        self.git('config','diff.ignoreSubmodules','all')
        self.git('update-index','--add','--cacheinfo','160000',self.commit,'newmodule')
        with self.assertRaisesRegex(ValueError,'source index'):self.check()
