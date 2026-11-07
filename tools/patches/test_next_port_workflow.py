"""The 8.35 candidate gets its own checks without changing historical PR routing."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


class NextPortWorkflowTests(unittest.TestCase):
    def test_native_discovery_uses_repository_build_module(self):
        repo = Path(__file__).resolve().parents[2]
        workflow = (repo / '.github/workflows/molly-patch-release.yml').read_text()
        self.assertIn("PYTHONPATH=tools/patches python3 -m unittest discover -s tools/native", workflow)
        self.assertEqual(workflow.count("python3 -m unittest discover -s tools/native"), 1)
        scope_job, source_job = workflow.split('  source-proof:', 1)
        self.assertNotIn("python3 -m unittest discover -s tools/native", scope_job)
        self.assertIn("python3 -m unittest discover -s tools/native", source_job)
        env = {key: value for key, value in os.environ.items() if key != 'PYTHONPATH'}
        env['PYTHONPATH'] = 'tools/patches'
        actual = subprocess.check_output(['python3', '-c', 'import build; print(build.__file__)'], cwd=repo, env=env, text=True).strip()
        self.assertEqual(Path(actual).resolve(), repo / 'tools/patches/build.py')

    def test_private_release_variants_selects_only_the_exact_candidate(self):
        workflow = (Path(__file__).resolve().parents[2] / '.github/workflows/molly-patch-release.yml').read_text()
        start = workflow.index('              private/release-variants)')
        end = workflow.index('              feature/collie-theme-pr17-126352)', start)
        case = workflow[start:end]
        self.assertIn('scope=next-port', case)
        expected_base = '7d486880ddc991af0d36f1e4709030255ef4dbad'
        expected_signal = 'a74583b01e260944f699c4da03ba4e8ce04de32b'
        with tempfile.TemporaryDirectory() as tmp:
            for base, signal, expected in ((expected_base, expected_signal, 'next-port'),
                                           ('0' * 40, expected_signal, 'next-port'),
                                           (expected_base, '0' * 40, 'forks')):
                (Path(tmp) / 'molly-build.json').write_text(json.dumps({'signal': {'commit': signal}}))
                script = ('check_collie_base() { :; }\ncase private/release-variants in\n' + case +
                          '\nesac\nprintf "%s" "$scope"\n')
                result = subprocess.check_output(['bash', '-eu', '-c', script], cwd=tmp,
                                                 env={**os.environ, 'BASE_SHA': base}, text=True)
                self.assertEqual(result.splitlines()[-1], expected)
        self.assertEqual(workflow.count('"$scope" == next-port'), 3)
        self.assertIn("android:\n    if: needs.scope.outputs.stack_scope != 'next-port'", workflow)
        self.assertIn("Prove the upstream native cherry-pick when configured\n        if: needs.scope.outputs.stack_scope != 'next-port'", workflow)
        unsigned = (Path(__file__).resolve().parents[2] / '.github/workflows/molly-unsigned-release.yml').read_text()
        self.assertEqual(unsigned.count('Signal 8.35.1 release blocked: native integration and port review incomplete'), 2)
        instrumentation = workflow.split('  instrumentation:', 1)[1]
        self.assertNotIn("needs.scope.outputs.stack_scope == 'next-port'", instrumentation)


if __name__ == '__main__':
    unittest.main()
