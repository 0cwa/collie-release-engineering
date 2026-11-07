import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('reconstruct', Path(__file__).parents[1] / 'source/reconstruct.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

class AdapterTests(unittest.TestCase):
    def test_existing_store_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, 'must not exist'):
                m.reconstruct(Path(directory), Path(directory))

    def test_git_environment_scrubbed(self):
        with patch.dict(os.environ, {'GIT_OBJECT_DIRECTORY': '/hostile', 'GIT_CONFIG_COUNT': '1',
                                    'GIT_CONFIG_KEY_0': 'url.evil.insteadOf', 'GIT_DIR': '/hostile'}):
            env = m.environment()
            self.assertNotIn('GIT_OBJECT_DIRECTORY', env)
            self.assertNotIn('GIT_CONFIG_COUNT', env)
            self.assertNotIn('GIT_DIR', env)
            self.assertEqual(env['GIT_NO_REPLACE_OBJECTS'], '1')

    def test_only_official_origins(self):
        self.assertEqual(m.ORIGINS['signal'], 'https://github.com/signalapp/Signal-Android.git')
        self.assertEqual(m.ORIGINS['molly'], 'https://github.com/mollyim/mollyim-android.git')

    def test_all_output_collisions_fail_before_any_subprocess_or_creation(self):
        for leaf in ('workspace','workspace-source','workspace-reconstruction.json'):
            for kind in ('file','directory','symlink','dangling-symlink'):
                with self.subTest(leaf=leaf,kind=kind), tempfile.TemporaryDirectory() as directory:
                    root=Path(directory);target=root/leaf
                    if kind=='file':target.write_text('preserve existing data')
                    elif kind=='directory':target.mkdir()
                    elif kind=='symlink':
                        original=root/'original';original.write_text('preserve target data');target.symlink_to(original)
                    else:target.symlink_to(root/'missing-target')
                    with patch.object(m,'run') as run:
                        with self.assertRaisesRegex(ValueError,'must not exist'):
                            m.reconstruct(root,root/'workspace')
                        run.assert_not_called()
                    if leaf!='workspace':self.assertFalse((root/'workspace').exists())
                    if kind=='file':self.assertEqual(target.read_text(),'preserve existing data')
                    if kind=='symlink':self.assertEqual((root/'original').read_text(),'preserve target data')
                    if kind=='dangling-symlink':self.assertFalse((root/'missing-target').exists())

    def test_missing_parent_dotdot_does_not_hide_canonical_collision(self):
        for leaf in ('work','work-source','work-reconstruction.json'):
            for dangling in (False,True):
                with self.subTest(leaf=leaf,dangling=dangling), tempfile.TemporaryDirectory() as directory:
                    root=Path(directory);target=root/leaf
                    if dangling:target.symlink_to(root/'missing-target')
                    else:target.write_text('preserve canonical output')
                    with patch.object(m,'run') as run:
                        with self.assertRaisesRegex(ValueError,'must not exist'):
                            m.reconstruct(root,root/'missing'/'..'/'work')
                        run.assert_not_called()
                    self.assertFalse((root/'missing').exists())
                    if not dangling:self.assertEqual(target.read_text(),'preserve canonical output')
