import subprocess
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from instrumentation_results import check, confirmed_ignored, compare_coverage

CLASS = 'org.thoughtcrime.securesms.profiles.manage.UsernameEditFragmentTest'
IGNORED = f'''INSTRUMENTATION_STATUS: class={CLASS}
INSTRUMENTATION_STATUS: test=testNicknameUpdateHappyPath
INSTRUMENTATION_STATUS_CODE: 1
INSTRUMENTATION_STATUS: class={CLASS}
INSTRUMENTATION_STATUS: test=testNicknameUpdateHappyPath
INSTRUMENTATION_STATUS_CODE: -3
INSTRUMENTATION_CODE: -1
'''


def discovery(names):
    lines = []
    for i, name in enumerate(names, 1):
        for status in (1, 0):
            lines += [f'INSTRUMENTATION_STATUS: class={CLASS}', f'INSTRUMENTATION_STATUS: current={i}',
                      f'INSTRUMENTATION_STATUS: numtests={len(names)}', f'INSTRUMENTATION_STATUS: test={name}',
                      f'INSTRUMENTATION_STATUS_CODE: {status}']
    return '\n'.join(lines) + f'\nOK ({len(names)} tests)\nINSTRUMENTATION_CODE: -1\n'


def xml(cases, tests, failures=0, errors=0, skipped=0, extra=''):
    counts = f'tests="{tests}" failures="{failures}" errors="{errors}" skipped="{skipped}"'
    return f'<testsuites {counts}><testsuite {counts}>{cases}{extra}</testsuite></testsuites>'


class ResultsTest(unittest.TestCase):
    def test_captured_ignored_status(self):
        self.assertTrue(confirmed_ignored(IGNORED, CLASS, 'testNicknameUpdateHappyPath'))
        self.assertFalse(confirmed_ignored(IGNORED.replace('-3', '-2'), CLASS, 'testNicknameUpdateHappyPath'))
        self.assertFalse(confirmed_ignored(IGNORED.replace('INSTRUMENTATION_CODE: -1', ''), CLASS, 'testNicknameUpdateHappyPath'))

    def test_utp_and_discovery_fail_closed(self):
        passed = f'<testcase classname="{CLASS}" name="pass"/>'
        ignored = f'<testcase classname="{CLASS}" name="testNicknameUpdateHappyPath"><failure/></testcase>'
        raw = discovery(['pass', 'testNicknameUpdateHappyPath'])
        run = Mock(side_effect=lambda command, **kwargs: subprocess.CompletedProcess([], 0, raw if 'log' in command else IGNORED, ''))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'TEST-device.xml'
            path.write_text(xml(passed + ignored, 2, failures=1))
            self.assertEqual(check(tmp, 'example/Runner', run), (1, 1))
            path.write_text(xml(passed + ignored.replace('<failure/>', '<skipped/><failure>oops</failure>'), 2, failures=1, skipped=1))
            with self.assertRaisesRegex(ValueError, 'conflicting test outcomes'):
                check(tmp, 'example/Runner', run)
            path.write_text(xml(passed, 1, extra='<error>crash</error>'))
            with self.assertRaisesRegex(ValueError, 'suite-level failure'):
                check(tmp, 'example/Runner', run)
            path.write_text(xml(passed, 2))
            with self.assertRaisesRegex(ValueError, 'invalid tests count'):
                check(tmp, 'example/Runner', run)
            path.write_text(xml(passed, 1))
            run.side_effect = lambda command, **kwargs: subprocess.CompletedProcess([], 0, discovery(['pass']).replace('numtests=1', 'numtests=2'), '')
            with self.assertRaisesRegex(ValueError, 'incomplete or inconsistent'):
                check(tmp, 'example/Runner', run)

    def test_partition_must_cover_discovery_without_overlap(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = [Path(tmp) / name for name in ('all', 'default', 'imo')]
            full = [["sample.DefaultTest", "one"], ["sample.imo.Test", "two"]]
            for path, value in zip(paths, (full, full[:1], full[1:])):
                path.write_text(json.dumps(value))
            self.assertEqual(compare_coverage(paths), (1, 1))
            paths[2].write_text('[]')
            with self.assertRaisesRegex(ValueError, 'coverage does not match'):
                compare_coverage(paths)
            paths[2].write_text(json.dumps(full))
            with self.assertRaisesRegex(ValueError, 'coverage does not match'):
                compare_coverage(paths)

    def test_discovery_uses_matching_package_exclusion(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'TEST-device.xml'
            path.write_text(xml(f'<testcase classname="{CLASS}" name="pass"/>', 1))
            run = Mock(return_value=subprocess.CompletedProcess([], 0, discovery(['pass']), ''))
            check(tmp, 'example/Runner', run, not_package='sample.imo')
            self.assertEqual(run.call_args.args[0][-4:], ['-e', 'notPackage', 'sample.imo', 'example/Runner'])

    def test_captured_archive_xml(self):
        # Same counters and cases as the UTP result captured on the emulator.
        cls = 'org.thoughtcrime.securesms.backup.v2.ArchiveImportExportTests'
        cases = ''.join(f'<testcase classname="{cls}" name="shard{i}"/>' for i in range(4))
        raw = discovery([f'shard{i}' for i in range(4)]).replace(CLASS, cls)
        run = Mock(return_value=subprocess.CompletedProcess([], 0, raw, ''))
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / 'TEST-device.xml').write_text(xml(cases, 4))
            self.assertEqual(check(tmp, 'example/Runner', run), (4, 0))


if __name__ == '__main__':
    unittest.main()
