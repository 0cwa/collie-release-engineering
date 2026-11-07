import pathlib
import subprocess
import unittest


class EmulatorStartupTest(unittest.TestCase):
    def test_shell_fixture(self):
        fixture = pathlib.Path(__file__).with_suffix('.sh')
        subprocess.run(['bash', str(fixture)], check=True)
