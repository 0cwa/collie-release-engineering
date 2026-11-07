import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent


class GateTest(unittest.TestCase):
    def test_reinstalls_each_suite_before_discovery_and_next_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scripts = root / 'scripts'
            scripts.mkdir()
            (scripts / 'instrumentation_gate.sh').write_bytes((ROOT / 'instrumentation_gate.sh').read_bytes())
            (scripts / 'instrumentation_install.py').write_bytes((ROOT / 'instrumentation_install.py').read_bytes())
            (scripts / 'instrumentation_results.py').write_text('import os, sys\nwith open(os.environ["EVENTS"], "a") as f: f.write("discover\\n")\nsys.exit(1 if os.environ.get("FAIL_DISCOVERY") else 0)\n')
            (scripts / 'build.py').write_text('''import json, os, pathlib, sys
with open(os.environ['EVENTS'], 'a') as f: f.write('build ' + sys.argv[1] + '\\n')
root = pathlib.Path(sys.argv[sys.argv.index('--worktree') + 1])
if not os.environ.get('MISSING_RESULTS'):
 results = root / 'app/build/outputs/androidTest-results/connected'
 results.mkdir(parents=True, exist_ok=True)
 (results / 'current.xml').write_text('<testsuite/>')
for kind in ('apk/prodWebsite/debug', 'apk/androidTest/prodWebsite/debug'):
 p = root / 'app/build/outputs' / kind
 p.mkdir(parents=True, exist_ok=True)
 (p / 'fixture.apk').write_text(sys.argv[1])
 (p / 'output-metadata.json').write_text(json.dumps({'applicationId': ('im.collie.app.imo' if sys.argv[1] == 'instrumentation-imo' else 'im.collie.app') + ('.test' if 'androidTest' in kind else ''), 'variantName':'prodWebsiteDebugAndroidTest' if 'androidTest' in kind else 'prodWebsiteDebug', 'elements':[{'outputFile':'fixture.apk', 'filters':[]}]}))
 if os.environ.get('WRONG_ID') or (os.environ.get('WRONG_IMO_ID') and sys.argv[1] == 'instrumentation-imo'):
  metadata = json.loads((p / 'output-metadata.json').read_text())
  metadata['applicationId'] = 'im.wrong.app'
  (p / 'output-metadata.json').write_text(json.dumps(metadata))
if os.environ.get('FAIL_BUILD'): sys.exit(17)
''')
            bin_dir = root / 'bin'
            bin_dir.mkdir()
            adb = bin_dir / 'adb'
            adb.write_text('''#!/bin/bash
printf '%s\\n' "$*" >> "$EVENTS"
if [[ "${FAIL_INSTALL:-}" == 1 && "$1" == install ]]; then exit 1; fi
if [[ "${FAIL_RUNNER:-}" == 1 && "$1" == shell ]]; then exit 1; fi
if [[ "$*" == 'shell pm list instrumentation' ]]; then
 echo 'instrumentation:im.collie.app.test/org.thoughtcrime.securesms.testing.SignalTestRunner (target=im.collie.app)'
 echo 'instrumentation:im.collie.app.imo.test/org.thoughtcrime.securesms.testing.incomingmessageobserver.IncomingMessageObserverTestRunner (target=im.collie.app.imo)'
fi
''')
            adb.chmod(0o755)
            rm = bin_dir / 'rm'
            rm.write_text('''#!/bin/bash
if [[ "${FAIL_CLEANUP:-}" == 1 ]]; then exit 1; fi
exec /usr/bin/rm "$@"
''')
            rm.chmod(0o755)
            events = root / 'events'
            env = dict(os.environ, PATH=str(bin_dir) + ':' + os.environ['PATH'], EVENTS=str(events))
            stale = root / 'reports/default-results/stale.xml'
            stale.parent.mkdir(parents=True)
            stale.write_text('<testsuite/>')
            result = subprocess.run(['bash', str(scripts / 'instrumentation_gate.sh'), str(root / 'source'), str(root / 'reports'), 'collie'], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(stale.exists())
            self.assertTrue((stale.parent / 'current.xml').is_file())
            lines = events.read_text().splitlines()
            self.assertEqual([line.split()[0] for line in lines], ['build', 'install', 'install', 'shell', 'discover', 'build', 'install', 'install', 'shell', 'discover', 'discover'])
            self.assertIn('instrumentation', lines[0])
            self.assertIn('instrumentation-imo', lines[5])
            for flag in ('FAIL_CLEANUP', 'FAIL_BUILD', 'WRONG_ID', 'FAIL_INSTALL', 'FAIL_RUNNER', 'FAIL_DISCOVERY', 'CI_PACKAGE_ID', 'MISSING_RESULTS'):
                with self.subTest(failure=flag):
                    events.unlink(missing_ok=True)
                    bad_env = dict(env, **{flag: 'im.other.app' if flag == 'CI_PACKAGE_ID' else '1'})
                    failed = subprocess.run(['bash', str(scripts / 'instrumentation_gate.sh'), str(root / 'source'), str(root / 'reports'), 'collie'], env=bad_env, capture_output=True, text=True)
                    self.assertNotEqual(failed.returncode, 0, flag)
                    if flag == 'FAIL_CLEANUP':
                        self.assertIn('Failed to clean default instrumentation outputs', failed.stderr)
                        self.assertFalse(events.exists(), 'cleanup failure must stop before any build')
                    if flag == 'MISSING_RESULTS':
                        self.assertIn('Failed to copy default instrumentation results', failed.stderr)
                        self.assertFalse(stale.parent.exists())
                    failed_lines = events.read_text().splitlines() if events.exists() else []
                    self.assertFalse(any('build instrumentation-imo' in line for line in failed_lines), failed_lines)
                    if flag in ('WRONG_ID', 'CI_PACKAGE_ID'):
                        self.assertFalse(any(line.startswith('install ') for line in failed_lines), failed_lines)
            events.unlink(missing_ok=True)
            failed = subprocess.run(['bash', str(scripts / 'instrumentation_gate.sh'), str(root / 'source'), str(root / 'reports'), 'collie-foss'], env=dict(env, WRONG_IMO_ID='1'), capture_output=True, text=True)
            self.assertNotEqual(failed.returncode, 0)
            self.assertEqual(sum(line.startswith('install ') for line in events.read_text().splitlines()), 2)

class MetadataTest(unittest.TestCase):
    def test_rejects_missing_ambiguous_or_unsafe_outputs(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('instrumentation_install', ROOT / 'instrumentation_install.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            metadata = {'applicationId': 'org.test.app', 'variantName': 'prodWebsiteDebug',
                        'elements': [{'outputFile': 'app.apk', 'filters': []}]}
            (directory / 'app.apk').touch()
            def check():
                (directory / 'output-metadata.json').write_text(json.dumps(metadata))
                return module.output(directory, 'prodWebsiteDebug')
            self.assertEqual(check()[0], 'org.test.app')
            for key, value in [('variantName', 'other'), ('applicationId', 'invalid'),
                               ('elements', []), ('elements', [{'outputFile': '../app.apk'}]),
                               ('elements', [{'outputFile': 'app.apk', 'filters': ['split']}]),
                               ('elements', [{'outputFile': 'app.apk'}, {'outputFile': 'app.apk'}]),
                               ('elements', [{'outputFile': 'missing.apk'}])]:
                old = metadata[key]
                metadata[key] = value
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    check()
                metadata[key] = old


if __name__ == '__main__':
    unittest.main()
