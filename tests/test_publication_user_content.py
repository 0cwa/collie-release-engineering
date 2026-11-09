import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
sys.path.insert(0,str(Path(__file__).parents[1]/'source'))
import publish
import test_publish as fixtures
spec=importlib.util.spec_from_file_location('publication_layer',Path(__file__).parents[1]/'source/layers/publication.py')
layer=importlib.util.module_from_spec(spec);spec.loader.exec_module(layer)


class UserContentPublicationTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.PublicationTests('runTest');self.f.setUp();self.addCleanup(self.f.doCleanups);self.repo=self.f.repo
        for name,content in [('README.md',b'Welcome user. We use a patch-based release system.\n<img height="60">\n'),('LICENSE',b'fixture license\n'),('NOTICE',b'fixture attribution\n'),('assets/banner',b'old matrix artwork\n'),('assets/executable',b'fixture asset\n')]:
            path=self.repo/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(content)
        (self.repo/'assets/executable').chmod(0o755);(self.repo/'assets/link').symlink_to('banner')
        publish.git(self.repo,'add','README.md','LICENSE','NOTICE','assets')
        first=publish.git(self.repo,'write-tree').decode().strip();self.original=publish.commit(self.repo,first,[self.f.old],'Fixture f6-style approved content\n')
        self.readme=(self.repo/'README.md').read_bytes().replace(b'height="60"',b'height="50"');(self.repo/'README.md').write_bytes(self.readme);(self.repo/'assets/banner').write_bytes(b'current approved matrix artwork\n')
        publish.git(self.repo,'add','README.md','assets');current=publish.git(self.repo,'write-tree').decode().strip();self.current=publish.commit(self.repo,current,[self.original],'Fixture 62-style owner update\n')
        self.app=self.repo/'published';self.app.mkdir()
    def test_explicit_current_base_keeps_readme_bytes_matrix_and_asset_modes(self):
        (self.app/'assets').mkdir();(self.app/'assets/unreviewed-leftover').write_text('must not survive')
        layer.preserve_user_files(self.app,self.repo,self.current,{'old_main':self.current})
        self.assertEqual((self.app/'README.md').read_bytes(),self.readme)
        self.assertEqual((self.app/'assets/banner').read_bytes(),b'current approved matrix artwork\n')
        self.assertTrue((self.app/'assets/executable').stat().st_mode&0o100);self.assertEqual(os.readlink(self.app/'assets/link'),'banner')
        self.assertFalse((self.app/'assets/unreviewed-leftover').exists())
        self.assertNotIn(b'Published application source is inspected here',(self.app/'README.md').read_bytes())
    def test_old_base_is_not_silently_substituted_for_the_current_approved_base(self):
        with self.assertRaisesRegex(ValueError,'plan old_main'):
            layer.preserve_user_files(self.app,self.repo,self.original,{'old_main':self.current})
        self.assertFalse((self.app/'README.md').exists())
        with self.assertRaisesRegex(ValueError,'immutable'):
            layer.preserve_user_files(self.app,self.repo,'HEAD',{'old_main':'HEAD'})
    def test_publisher_rejects_stale_readme_assets_or_modes_even_with_reviewed_full_tree(self):
        with self.assertRaisesRegex(ValueError,'README.md'):publish.verify_user_files(self.repo,self.original,self.current)
        publish.verify_user_files(self.repo,self.current,self.current)
        plan={'schema':1,'signal':self.f.signal,'old_main':self.current,'reconstructed_tree':publish.tree(self.repo,self.original),'final_tree':publish.tree(self.repo,self.original),'layers':[{'name':'Fixture source layer','kind':'feature','before_tree':self.f.empty,'after_tree':publish.tree(self.repo,self.original),'provenance':'fixture'}]}
        path=self.repo/'stale-plan.json';path.write_text(json.dumps(plan))
        with self.assertRaisesRegex(ValueError,'user-facing content'):
            publish.publish(self.repo,path,hashlib.sha256(path.read_bytes()).hexdigest(),self.current,historical_messages=True)
    def test_nested_repository_path_does_not_hide_root_owner_file_changes(self):
        nested=self.repo/'nested/worktree';nested.mkdir(parents=True)
        with self.assertRaisesRegex(ValueError,'README.md'):
            publish.verify_user_files(nested,self.original,self.current)
        publish.verify_user_files(nested,self.current,self.current)
    def test_existing_plan_base_and_current_main_are_always_bound(self):
        plan={'schema':1,'signal':self.f.signal,'old_main':self.original,'reconstructed_tree':publish.tree(self.repo,self.original),'final_tree':publish.tree(self.repo,self.original),'layers':[{'name':'Fixture source layer','kind':'feature','before_tree':self.f.empty,'after_tree':publish.tree(self.repo,self.original),'provenance':'fixture'}]}
        path=self.repo/'old-approved-plan.json';path.write_text(json.dumps(plan))
        with self.assertRaisesRegex(ValueError,'current base differs'):
            publish.publish(self.repo,path,hashlib.sha256(path.read_bytes()).hexdigest(),self.current,historical_messages=True)
    def test_asset_content_and_mode_substitution_fail_with_identical_readme(self):
        for mode,contents in [('100644',b'changed approved asset'),('100644',b'fixture asset\n')]:
            with self.subTest(mode=mode,contents=contents):
                publish.git(self.repo,'read-tree',self.current)
                blob=publish.git(self.repo,'hash-object','-w','--stdin',data=contents).decode().strip()
                publish.git(self.repo,'update-index','--cacheinfo',mode,blob,'assets/executable')
                changed=publish.git(self.repo,'write-tree').decode().strip()
                with self.assertRaisesRegex(ValueError,'assets'):publish.verify_user_files(self.repo,changed,self.current)

    def test_reconstruction_checks_ignore_inherited_git_directory(self):
        # An inherited GIT_DIR must not redirect the explicit app checkout.
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            publish.git(repo, 'init', '-q')
            publish.git(repo, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                        'commit', '--allow-empty', '-qm', 'Clean reconstructed fixture')
            expected = publish.git(repo, 'rev-parse', 'HEAD^{tree}').decode().strip()
            with mock.patch.dict(os.environ, {'GIT_DIR': str(repo / 'nonexistent.git')}), \
                    mock.patch.object(layer, 'RECONSTRUCTED_TREE', expected), \
                    mock.patch.object(layer, 'apply_outputs') as outputs:
                layer.apply(repo, repo, repo, application_base='HEAD', publication_plan={})
            outputs.assert_called_once()
