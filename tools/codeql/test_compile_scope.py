"""The coverage gate must reject missing compiler inputs in either language."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

import compile_scope
import report
from test_report import fixture


class ScopeTests(unittest.TestCase):
    def test_compiler_scope_and_missing_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(['git', 'init', '-q', str(root)], check=True)
            for name in ('A.java', 'B.kt', 'test/Unused.kt'):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('')
            subprocess.run(['git', 'add', '.'], cwd=root, check=True)
            records = [{'task': ':compileJava', 'language': 'java', 'files': [str(root / 'A.java')]},
                       {'task': ':compileKotlin', 'language': 'kotlin', 'files': [str(root / 'B.kt')]}]
            inputs, extracted = root / 'inputs', root / 'extracted'
            inputs.write_text(''.join(json.dumps(record) + '\n' for record in records))
            for data, passed in [('A.java\nB.kt\n', True), ('A.java\n', False),
                                 ('B.kt\n', False), ('', False)]:
                extracted.write_text(data)
                self.assertEqual(compile_scope.check(root, inputs, extracted)['passed'], passed)
            records[1]['files'] = []
            inputs.write_text(''.join(json.dumps(record) + '\n' for record in records))
            extracted.write_text('A.java\nB.kt\n')
            self.assertFalse(compile_scope.check(root, inputs, extracted)['passed'])

    def test_java_kotlin_sarif_alone_cannot_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'java.sarif'
            path.write_text(json.dumps(fixture()).replace('py/', 'java/'))
            result = report.audit([path], 'java-kotlin')
            self.assertFalse(result['passed'])
            self.assertIn('Java/Kotlin compiler scope has not been verified', result['errors'])


if __name__ == '__main__':
    unittest.main()
