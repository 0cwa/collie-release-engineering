"""Release workflow policy checks."""
import unittest
from pathlib import Path


class ReleaseWorkflowPolicyTests(unittest.TestCase):
    def test_instrumentation_does_not_gate_unsigned_release(self):
        text = (Path(__file__).resolve().parents[2] / '.github/workflows/molly-unsigned-release.yml').read_text()
        release = text.split('  unsigned-release:\n', 1)[1].split('\n  instrumentation:', 1)[0]
        self.assertNotIn('needs: instrumentation', release)
        self.assertIn('  instrumentation:\n', text)
        self.assertNotIn('continue-on-error', text)

    def test_release_assembly_and_regressions_use_cache_and_two_workers(self):
        text = (Path(__file__).resolve().parents[2] / '.github/workflows/molly-unsigned-release.yml').read_text()
        for phase in ('assemble-release', 'test'):
            command = next(line for line in text.splitlines() if f'build.py {phase} ' in line)
            self.assertIn('--workers 2', command)
            self.assertIn('--build-cache', command)
            self.assertIn('--release-cache-dir', command)
        release = text.split('  unsigned-release:\n', 1)[1].split('\n  instrumentation:', 1)[0]
        self.assertIn('cache-disabled: true', release)
        self.assertIn('export GRADLE_USER_HOME=', release)
