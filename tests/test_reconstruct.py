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
