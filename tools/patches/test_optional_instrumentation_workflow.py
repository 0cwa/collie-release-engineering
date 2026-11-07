"""Check the hosted instrumentation choice against both workflow definitions."""
import json
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / '.github/workflows'


def job(text, name):
    jobs = text.split('\njobs:\n', 1)[1]
    return re.split(r'(?m)^  [a-z][\w-]*:\s*$', jobs.split(f'  {name}:\n', 1)[1], maxsplit=1)[0]


def expression(value, *, event, choice, scope='forks', profiles='["collie","collie-foss"]'):
    """Evaluate the limited Actions expression subset used by the choice gate."""
    replacements = {
        'github.event_name': repr(event),
        'inputs.instrumentation': repr(choice),
        'needs.scope.outputs.stack_scope': repr(scope),
        'needs.scope.outputs.profiles': repr(profiles),
    }
    for name, replacement in replacements.items():
        value = value.replace(name, replacement)
    value = value.replace('&&', ' and ').replace('||', ' or ')
    return eval(value, {'__builtins__': {}}, {})  # The expression is a checked-in workflow constant.


class OptionalInstrumentationWorkflowTests(unittest.TestCase):
    def test_trigger_and_profile_selection(self):
        for filename in ('molly-patch-release.yml', 'molly-unsigned-release.yml'):
            with self.subTest(workflow=filename):
                text = (WORKFLOWS / filename).read_text()
                self.assertIn("default: 'off'", text)
                for option in ("- 'off'", '- both', '- collie', '- collie-foss'):
                    self.assertIn(option, text)
                section = job(text, 'instrumentation')
                gate = re.search(r'^    if: (?:>-\n((?:      .*\n)+)|([^\n]+))', section, re.M)
                self.assertIsNotNone(gate)
                condition = ' '.join((gate.group(1) or gate.group(2)).split())
                matrix = re.search(r'^        profile: \$\{\{ fromJSON\((.+)\) \}\}$', section, re.M)
                self.assertIsNotNone(matrix)
                for choice, expected in (('off', []), ('both', ['collie', 'collie-foss']),
                                         ('collie', ['collie']), ('collie-foss', ['collie-foss']),
                                         ('unknown', [])):
                    enabled = expression(condition, event='workflow_dispatch', choice=choice)
                    selected = json.loads(expression(matrix.group(1), event='workflow_dispatch', choice=choice)) if enabled else []
                    self.assertEqual(selected, expected, (filename, choice))
                if filename == 'molly-patch-release.yml':
                    self.assertFalse(expression(condition, event='pull_request', choice='both'))
                    self.assertFalse(expression(condition, event='workflow_dispatch', choice='both', scope='main-maintenance', profiles='[""]'))
                    self.assertFalse(expression(condition, event='workflow_dispatch', choice='both', scope='next-port'))

    def test_failure_and_diagnostics_remain_independent(self):
        for filename in ('molly-patch-release.yml', 'molly-unsigned-release.yml'):
            text = (WORKFLOWS / filename).read_text()
            section = job(text, 'instrumentation')
            self.assertNotIn('continue-on-error', section)
            self.assertIn('tools/patches/instrumentation_gate.sh', section)
            self.assertIn('if: always()', section)
            self.assertIn('run-context.txt', section)
            self.assertNotRegex(text, r'needs:\s*(?:\[[^\]]*\binstrumentation\b|instrumentation\b)')
        patch = (WORKFLOWS / 'molly-patch-release.yml').read_text()
        self.assertIn("github.event_name == 'workflow_dispatch' && github.run_id", patch)


if __name__ == '__main__':
    unittest.main()
