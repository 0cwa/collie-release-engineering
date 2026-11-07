"""Focused tests for the trusted-local native release route."""
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

import build
import test_build

class RouteTest(unittest.TestCase):
    def setUp(self):
        self.fixture = test_build.BuildSourceTests('test_incomplete_signal_port_fails_closed_for_all_release_profiles')
        self.fixture.setUp()
        self.addCleanup(self.fixture.temporary.cleanup)
        self.args = self.fixture.args
        self.args.accept_trusted_local_native = True
        self.args.release_version_name = '8.35.1-1'
        self.args.release_version_code = '177701'
        self.runner = build.Build(self.args)
        self.runner.config['signal']['commit'] = build.BLOCKED_SIGNAL_PORT
        self.runner.profile = {'firebase_free': False, 'app': {'title': 'Collie', 'file_name': 'Collie', 'package_id': 'im.collie.app'}}
        self.runner.android.update({'build_tools':'36.0.0','ndk':'28.0.13004108'})
        directory = self.fixture.app / 'app/build/outputs/apk/prodWebsite/release'
        directory.mkdir(parents=True)
        (directory / 'output-metadata.json').write_text(json.dumps({'elements':[{'type':'SINGLE','outputFile':'out.apk'}]}))
        self.apk = directory / 'out.apk'
        self.apk.write_bytes(b'apk')

    def run_build(self, *, verification=None, native=None):
        checks = native if native is not None else [b'proof', b'proof', b'proof']
        def fake_run(*args, **kwargs):
            (self.fixture.reports / 'assemble-release.json').write_text(json.dumps({'status':'PASS'}))
        with patch.dict(os.environ, {'ANDROID_HOME': str(self.fixture.root)}, clear=True), \
             patch.object(self.runner, 'source_proof'), \
             patch.object(self.runner, 'artifact_native_lock', return_value=self.fixture.root / 'lock'), \
             patch.object(self.runner, 'native_verification_bytes', side_effect=checks) as stage, \
             patch.object(self.runner, 'verified_native_gradle', return_value=Path('/verified/gradle')), \
             patch.object(build, 'relative_file', return_value=Path('/test/init.gradle')), \
             patch.object(build.native_release_check, 'verify', side_effect=verification or (lambda *a: {'apk_sha256':'test'})), \
             patch.object(self.runner, 'run', side_effect=fake_run):
            self.runner.gradle('assemble-release')
            return stage.call_count

    def test_successful_gradle_stays_pending_before_postcheck(self):
        child = MagicMock()
        child.stdout = iter([])
        child.wait.return_value = 0
        with patch.object(build.subprocess, 'Popen', return_value=child):
            self.runner.run(['gradle'], 'assemble-release')
        result = json.loads((self.fixture.reports / 'assemble-release.json').read_text())
        self.assertEqual(result['status'], 'PENDING_NATIVE_VERIFICATION')

    def test_explicit_acceptance_checks_staging_before_and_after(self):
        self.assertEqual(self.run_build(), 3)
        result = json.loads((self.fixture.reports / 'assemble-release.json').read_text())
        self.assertEqual(result['status'], 'PASS')
        self.assertEqual(result['trusted_local_native']['apk_sha256'], 'test')

    def test_wrong_apk_marks_report_failed(self):
        with self.assertRaisesRegex(build.BuildError, 'wrong APK'):
            self.run_build(verification=ValueError('wrong APK'))
        self.assertEqual(json.loads((self.fixture.reports / 'assemble-release.json').read_text())['status'], 'FAIL')

    def test_changed_stage_marks_report_failed(self):
        with self.assertRaisesRegex(build.BuildError, 'changed stage'):
            self.run_build(native=[b'proof', build.BuildError('changed stage')])
        self.assertEqual(json.loads((self.fixture.reports / 'assemble-release.json').read_text())['status'], 'FAIL')

    def test_prior_report_cannot_be_mistaken_for_this_run(self):
        (self.fixture.reports / 'assemble-release.json').write_text('{"status":"PASS"}')
        with self.assertRaisesRegex(build.BuildError, 'release report already exists'):
            self.runner.gradle('assemble-release')

    def test_no_acceptance_fails_before_build(self):
        self.args.accept_trusted_local_native = False
        with patch.object(self.runner, 'run') as run, self.assertRaisesRegex(build.BuildError, 'release assembly is blocked'):
            self.runner.gradle('assemble-release')
        run.assert_not_called()

if __name__ == '__main__':
    unittest.main()
