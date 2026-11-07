import importlib.util
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / 'tools/patches'))
import git_utils

class IdentityTests(unittest.TestCase):
    def test_fetch_diagnostics_do_not_change_identity_stdout(self):
        result = subprocess.CompletedProcess([], 0, stdout=b'1234\n', stderr=b'fetch diagnostic\n')
        with patch.object(subprocess, 'run', return_value=result):
            self.assertEqual(git_utils.git('.', 'show').stdout, '1234\n')
