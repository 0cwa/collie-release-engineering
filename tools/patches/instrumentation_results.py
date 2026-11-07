#!/usr/bin/env python3
"""Check Android instrumentation XML, confirming ambiguous UTP failures on device.

Run immediately after connectedAndroidTest, before changing the installed APK or
running another variant. The emulator must contain disposable synthetic data.
"""
import argparse
import json
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

IDENTIFIER = re.compile(r'^[A-Za-z_$][A-Za-z0-9_.$]*$')


def confirmed_ignored(output, classname, method):
    # Require a complete response for exactly the requested test. A runner
    # crash, zero tests, a failed test or an assumption failure is not @Ignore.
    blocks = re.split(r'INSTRUMENTATION_STATUS_CODE: (-?\d+)\s*', output)
    statuses = []
    for i in range(0, len(blocks) - 1, 2):
        fields = dict(re.findall(r'^INSTRUMENTATION_STATUS: (class|test)=(.*)$', blocks[i], re.M))
        if fields.get('class') == classname and fields.get('test') == method:
            statuses.append(int(blocks[i + 1]))
    return statuses == [1, -3] and bool(re.search(r'^INSTRUMENTATION_CODE: -1\s*$', output, re.M))


def discover(component, run, package=None, not_package=None):
    command = ['adb', 'shell', 'am', 'instrument', '-w', '-r', '-e', 'log', 'true']
    if package:
        command += ['-e', 'package', package]
    if not_package:
        command += ['-e', 'notPackage', not_package]
    discovery = run(command + [component], capture_output=True, text=True, timeout=120)
    if discovery.returncode or not re.search(r'^INSTRUMENTATION_CODE: -1\s*$', discovery.stdout, re.M):
        raise ValueError('test discovery did not complete')
    blocks = re.split(r'INSTRUMENTATION_STATUS_CODE: (-?\d+)\s*', discovery.stdout)
    expected = []
    finished = []
    reported_counts = set()
    for i in range(0, len(blocks) - 1, 2):
        fields = dict(re.findall(r'^INSTRUMENTATION_STATUS: (class|test|numtests|current)=(.*)$', blocks[i], re.M))
        if not all(fields.get(key) for key in ('class', 'test', 'numtests', 'current')):
            raise ValueError('incomplete discovery event')
        if not fields['numtests'].isdigit() or not fields['current'].isdigit():
            raise ValueError('invalid discovery count')
        reported_counts.add(int(fields['numtests']))
        entry = (fields['class'], fields['test'], int(fields['current']))
        if blocks[i + 1] == '1':
            expected.append(entry)
        elif blocks[i + 1] == '0':
            finished.append(entry)
        else:
            raise ValueError('discovery returned a non-success status')
    summary = re.findall(r'^OK \((\d+) tests?\)\s*$', discovery.stdout, re.M)
    count = len(expected)
    if (not count or reported_counts != {count} or summary != [str(count)]
            or expected != finished or [entry[2] for entry in expected] != list(range(1, count + 1))
            or len({entry[:2] for entry in expected}) != count):
        raise ValueError('test discovery was incomplete or inconsistent')
    return [entry[:2] for entry in expected]


def check(directory, component, run=subprocess.run, package=None, not_package=None,
          coverage_output=None, all_coverage_output=None):
    files = sorted(Path(directory).rglob('TEST-*.xml'))
    if not files:
        raise ValueError('no instrumentation XML results found')
    expected = discover(component, run, package, not_package)
    actual = []
    passed = ignored = 0
    for path in files:
        root = ET.parse(path).getroot()
        if root.tag not in ('testsuites', 'testsuite'):
            raise ValueError(f'invalid result root in {path}')
        for suite in root.iter():
            if suite.tag in ('testsuite', 'testsuites'):
                cases_in_suite = suite.findall('.//testcase')
                for attribute, nodes in (
                    ('tests', cases_in_suite),
                    ('failures', [case for case in cases_in_suite if case.find('failure') is not None]),
                    ('errors', [case for case in cases_in_suite if case.find('error') is not None]),
                    ('skipped', [case for case in cases_in_suite if case.find('skipped') is not None]),
                ):
                    value = suite.get(attribute)
                    if value is None or not value.isdigit() or int(value) != len(nodes):
                        raise ValueError(f'invalid {attribute} count in {path}')
                if suite.find('error') is not None or suite.find('failure') is not None:
                    raise ValueError(f'suite-level failure in {path}')
        cases = root.findall('.//testcase')
        if not cases:
            raise ValueError(f'no test cases in {path}')
        for case in cases:
            name, cls = case.get('name', ''), case.get('classname', '')
            actual.append((cls, name))
            failures = case.findall('failure') + case.findall('error')
            if failures and case.find('skipped') is not None:
                raise ValueError(f'conflicting test outcomes: {cls}#{name}')
            if case.find('skipped') is not None:
                ignored += 1
            elif not failures:
                passed += 1
            elif (len(failures) == 1 and failures[0].tag == 'failure'
                  and not failures[0].attrib and not ''.join(failures[0].itertext()).strip()
                  and IDENTIFIER.fullmatch(cls) and IDENTIFIER.fullmatch(name)):
                result = run(['adb', 'shell', 'am', 'instrument', '-w', '-r', '-e', 'class',
                              cls if name == 'null' else cls + '#' + name, component],
                             capture_output=True, text=True, timeout=120)
                if result.returncode or not confirmed_ignored(result.stdout, cls, name):
                    raise ValueError(f'unconfirmed empty failure: {cls}#{name}')
                ignored += 1
            else:
                raise ValueError(f'test failed: {cls}#{name}')
    if len(set(actual)) != len(actual) or set(actual) != set(expected):
        raise ValueError(f'incomplete or mismatched results: discovered {len(expected)}, reported {len(actual)}')
    if not passed:
        raise ValueError('no non-ignored tests passed')
    if coverage_output:
        coverage_output.write_text(json.dumps(expected) + '\n')
    if all_coverage_output:
        all_coverage_output.write_text(json.dumps(discover(component, run)) + '\n')
    return passed, ignored


def compare_coverage(paths):
    sets = [set(map(tuple, json.loads(path.read_text()))) for path in paths]
    all_tests, default_tests, imo_tests = sets
    if default_tests & imo_tests or default_tests | imo_tests != all_tests:
        raise ValueError('default and IMO coverage does not match the complete discovered suite')
    return len(default_tests), len(imo_tests)


def main():
    if len(sys.argv) == 5 and sys.argv[1] == '--compare':
        try:
            default, imo = compare_coverage([Path(arg) for arg in sys.argv[2:]])
        except (ValueError, OSError, json.JSONDecodeError) as error:
            print(f'instrumentation coverage: {error}', file=sys.stderr)
            return 1
        print(f'complete test coverage: {default} default, {imo} IMO')
        return 0
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('results', type=Path)
    parser.add_argument('component', help='installed test package/runner')
    parser.add_argument('--package', help='package filter used for the test run')
    parser.add_argument('--not-package', help='package excluded from the test run')
    parser.add_argument('--coverage-output', type=Path)
    parser.add_argument('--all-coverage-output', type=Path)
    args = parser.parse_args()
    try:
        passed, ignored = check(args.results, args.component, package=args.package, not_package=args.not_package,
                                 coverage_output=args.coverage_output, all_coverage_output=args.all_coverage_output)
    except (ValueError, ET.ParseError, OSError, subprocess.TimeoutExpired) as error:
        print(f'instrumentation results: {error}', file=sys.stderr)
        return 1
    print(f'instrumentation results: {passed} passed, {ignored} ignored or skipped')
    return 0


if __name__ == '__main__':
    sys.exit(main())
