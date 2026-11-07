import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest
from unittest.mock import patch

import build
import test_stage_artifacts as artifact_fixtures
from test_gradle_environment import isolated_gradle_environment

HERE = Path(__file__).parent

class ProvisionalNativeBuildDriverTest(unittest.TestCase):
    def setUp(self):
        self.fixture = artifact_fixtures.ArtifactStagingTest('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        root = self.fixture.root
        environment = isolated_gradle_environment(root / 'gradle-user-home')
        environment.start()
        self.addCleanup(environment.stop)
        self.repo = root / 'pipeline'
        self.app = root / 'app'
        self.reports = root / 'reports'
        (self.repo / 'tools/native').mkdir(parents=True)
        (self.repo / 'tools/patches').mkdir(parents=True)
        (self.app / 'gradle').mkdir(parents=True)
        self.reports.mkdir()
        for name in ('stage_artifacts.py', 'native_driver.py', 'build_sources.py', 'build_ringrtc.py'):
            shutil.copyfile(HERE / name, self.repo / 'tools/native' / name)
        shutil.copytree(self.fixture.root / 'patches', self.repo / 'tools/native/patches')
        shutil.copyfile(self.fixture.lock_path, self.repo / 'tools/native/native-lock.json')
        shutil.copyfile(self.fixture.verification, self.app / 'gradle/verification-metadata.xml')
        (self.app / 'build.gradle').write_text('// fixture\n')
        (self.repo / 'tools/patches/test-worker-heap.init.gradle').write_text('// fixture\n')
        (self.repo / 'tools/patches/release-version.init.gradle').write_text('// fixture\n')
        self.git('init', '-q')
        self.git('config', 'user.name', 'Fixture')
        self.git('config', 'user.email', 'fixture@example.invalid')
        self.git('add', '.')
        self.git('commit', '-qm', 'fixture')
        tree = self.git('rev-parse', 'HEAD^{tree}')
        self.config = {'schema': 1, 'signal': {'commit': 'a' * 40}, 'target_tree': tree,
                       'native': 'tools/native/native-lock.json', 'android': {'assemble': [':app:assembleDebug']}}
        (self.repo / 'molly-build.json').write_text(json.dumps(self.config))
        (self.reports / 'source.json').write_text(json.dumps({'status':'PASS', 'signal':'a'*40, 'tree':tree,
                                                              'worktree':str(self.app), 'native':self.config['native']}))
        self.args = argparse.Namespace(repo=self.repo, config='molly-build.json', worktree=self.app,
                                       report_dir=self.reports, native_source_dir=None, maven_dir=self.fixture.input,
                                       heap='5g', release_version_name='8.35.1-test', release_version_code='1')
        self.runner = build.Build(self.args)

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.app), *args], stderr=subprocess.DEVNULL, text=True).strip()

    def test_no_native_stage_means_no_debug_fallback(self):
        with self.assertRaisesRegex(build.BuildError, 'no fallback'):
            self.runner.gradle('assemble')

    def test_source_proof_requires_native_receipt_but_preparation_checks_source(self):
        with self.assertRaisesRegex(build.BuildError, 'no fallback'):
            self.runner.source_proof()
        (self.app / 'build.gradle').write_text('// tampered\n')
        with patch.object(self.runner, 'run') as run:
            with self.assertRaisesRegex(build.BuildError, 'working source differs'):
                self.runner.native('native-prepare')
            run.assert_not_called()

    def test_malformed_multi_artifact_lock_fails_before_source_or_gradle(self):
        lock = self.repo / 'tools/native/native-lock.json'
        original = json.loads(lock.read_text())
        for fields in (('modules',), ('source_provenance',), ('modules', 'source_provenance')):
            changed = dict(original)
            for field in fields:
                del changed[field]
            lock.write_text(json.dumps(changed))
            with self.subTest(fields=fields), patch.object(self.runner, 'run') as run:
                with self.assertRaisesRegex(build.BuildError, 'native dependency lock is malformed'):
                    self.runner.gradle('assemble')
                run.assert_not_called()
        lock.write_text(json.dumps(original))

    def test_legacy_lock_remains_legacy_but_835_rejects_a_renamed_malformed_lock(self):
        legacy = self.repo / 'native-ringrtc.json'
        legacy.write_text(json.dumps({'source_tree': 'a' * 40}))
        self.runner.config['native'] = 'native-ringrtc.json'
        self.assertIsNone(self.runner.artifact_native_lock())
        self.runner.config['signal']['commit'] = build.BLOCKED_SIGNAL_PORT
        with self.assertRaisesRegex(build.BuildError, 'native dependency lock is malformed'):
            self.runner.artifact_native_lock()

    def test_tampered_init_is_rejected_before_gradle(self):
        self.runner.native('native-integrate')
        init = self.reports / 'native/artifact-stage/native.init.gradle'
        init.write_text(init.read_text() + '\n// tampered\n')
        with patch.object(self.runner, 'run') as run:
            with self.assertRaisesRegex(build.BuildError, 'native artifact integration'):
                self.runner.gradle('assemble')
            run.assert_not_called()

    def test_integrated_stage_is_strict_and_verified_before_debug(self):
        self.runner.native('native-integrate')
        self.runner.source_proof()
        with patch.object(self.runner, 'run') as run, patch.object(self.runner, 'verified_native_gradle', return_value=Path('/verified/gradle/bin/gradle')):
            self.runner.gradle('assemble')
        command = run.call_args.args[0]
        self.assertEqual(['/verified/gradle/bin/gradle', '--project-dir', str(self.app)], command[:3])
        self.assertIn('--dependency-verification=strict', command)
        self.assertIn(str(self.reports / 'native/artifact-stage/native.init.gradle'), command)

    def test_gradle_rejects_inherited_options_before_launch(self):
        self.runner.native('native-integrate')
        for key, value in (('GRADLE_OPTS', '-Dmalicious=true'), ('GRADLE_HOME', '/opt/hostedtoolcache/gradle')):
            with self.subTest(key=key), patch.dict(os.environ, {key: value}), patch.object(self.runner, 'run') as run:
                with self.assertRaisesRegex(build.BuildError, key):
                    self.runner.gradle('assemble')
                run.assert_not_called()

    def test_gradle_rejects_user_init_script_before_launch(self):
        self.runner.native('native-integrate')
        home = self.fixture.root / 'gradle-home'
        (home / 'init.d').mkdir(parents=True)
        (home / 'init.d/evil.gradle').write_text('malicious')
        with patch.dict(os.environ, {'GRADLE_USER_HOME': str(home)}), patch.object(self.runner, 'run') as run:
            with self.assertRaisesRegex(build.BuildError, 'init.d'):
                self.runner.gradle('assemble')
            run.assert_not_called()

    def test_distribution_rechecked_after_gradle_execution(self):
        self.runner.native('native-integrate')
        with patch.object(self.runner, 'run'), patch.object(self.runner, 'verified_native_gradle', side_effect=[Path('/verified/gradle'), Path('/changed/gradle')]):
            with self.assertRaisesRegex(build.BuildError, 'changed during execution'):
                self.runner.gradle('assemble')

    def test_missing_verified_distribution_stops_before_gradle(self):
        self.runner.native('native-integrate')
        with patch.object(self.runner, 'run') as run:
            with self.assertRaisesRegex(build.BuildError, 'native-gradle-zip'):
                self.runner.gradle('assemble')
            run.assert_not_called()

    def test_relative_host_stage_has_same_path_for_verification_and_execution(self):
        helper = self.repo/'tools/native/host_jni_guard.py'
        helper.write_text('# fixture')
        self.args.native_host_stage = Path('relative-host-stage')
        self.args.native_host_sha256 = 'a' * 64
        original_cwd = Path.cwd()
        try:
            os.chdir(self.fixture.root)
            with patch.object(self.runner, 'artifact_native_lock', return_value=self.repo/'native.json'), patch('build.subprocess.run') as run:
                run.return_value.returncode = 0
                init = self.runner.verified_host_jni_init()
            command = run.call_args.args[0]
            expected = self.fixture.root/'relative-host-stage'
            self.assertEqual(str(expected), command[command.index('--output')+1])
            self.assertEqual(expected/'host-jni.init.gradle', init)
            self.assertEqual(self.repo, run.call_args.kwargs['cwd'])
        finally:
            os.chdir(original_cwd)

    def test_835_unit_test_requires_host_stage_before_launch(self):
        self.runner.config['signal']['commit'] = build.BLOCKED_SIGNAL_PORT
        self.runner.android['tests'] = [':app:testProdWebsiteDebugUnitTest']
        with patch.object(self.runner, 'source_proof'), patch.object(self.runner, 'artifact_native_lock', return_value=self.repo/'native.json'), patch.object(self.runner, 'native_verification_bytes', return_value=b'fixture'), patch.object(self.runner, 'verified_native_gradle', return_value=Path('/verified/gradle')), patch.object(self.runner, 'run') as run:
            with self.assertRaisesRegex(build.BuildError, 'native-host-stage'):
                self.runner.gradle('test')
            run.assert_not_called()

    def test_835_unit_test_checks_host_stage_before_and_after(self):
        self.runner.config['signal']['commit'] = build.BLOCKED_SIGNAL_PORT
        self.runner.android['tests'] = [':app:testProdWebsiteDebugUnitTest']
        init = self.fixture.root/'host/host-jni.init.gradle'
        with patch.object(self.runner, 'source_proof'), patch.object(self.runner, 'artifact_native_lock', return_value=self.repo/'native.json'), patch.object(self.runner, 'native_verification_bytes', return_value=b'fixture'), patch.object(self.runner, 'verified_native_gradle', return_value=Path('/verified/gradle')), patch.object(self.runner, 'verified_host_jni_init', return_value=init) as verified, patch.object(self.runner, 'run') as run:
            self.runner.gradle('test')
            self.assertEqual(2, verified.call_count)
            self.assertIn(str(init), run.call_args.args[0])

    def test_host_guard_does_not_apply_to_instrumentation(self):
        self.runner.config['signal']['commit'] = build.BLOCKED_SIGNAL_PORT
        with patch.object(self.runner, 'source_proof'), patch.object(self.runner, 'artifact_native_lock', return_value=self.repo/'native.json'), patch.object(self.runner, 'native_verification_bytes', return_value=b'fixture'), patch.object(self.runner, 'verified_native_gradle', return_value=Path('/verified/gradle')), patch.object(self.runner, 'verified_host_jni_init') as verified, patch.object(self.runner, 'run'):
            self.runner.gradle('instrumentation')
            verified.assert_not_called()

    def test_tampered_staged_binary_fails_source_proof(self):
        self.runner.native('native-integrate')
        artifact = self.reports / 'native/artifact-stage/maven/im/collie/native/ringrtc-android/2.74.0-collie.1/ringrtc-android-2.74.0-collie.1.aar'
        artifact.write_bytes(b'tampered')
        with self.assertRaisesRegex(build.BuildError, 'native artifact integration'):
            self.runner.source_proof()

    def test_release_is_blocked_without_native_source_build_implementation(self):
        self.runner.native('native-integrate')
        with self.assertRaisesRegex(build.BuildError, 'native source build proof is not implemented'):
            self.runner.gradle('assemble-release')

    def test_partial_libsignal_phase_cannot_be_mistaken_for_complete_build(self):
        self.args.native_rust_bin = self.fixture.root / 'rust-bin'
        self.args.native_gradle_zip = self.fixture.root / 'gradle.zip'
        self.args.native_gradle_distribution = self.fixture.root / 'gradle'
        with patch.dict(os.environ, {'ANDROID_HOME': '/test/sdk', 'JAVA_HOME': '/test/jdk'}), patch.object(self.runner, 'run') as run:
            self.runner.native('native-build-libsignal')
        command, phase = run.call_args.args[:2]
        self.assertEqual('native-build-libsignal', phase)
        self.assertIn('libsignal-build.json', ' '.join(command))
        self.assertIn('--gradle-zip', command)
        self.assertIn('--gradle-distribution', command)
        with self.assertRaisesRegex(build.BuildError, 'RingRTC/WebRTC source build orchestration is not implemented'):
            self.runner.native('native-build')

    def test_partial_ringrtc_phase_forwards_explicit_inputs(self):
        self.args.native_rust_bin = self.fixture.root / 'rust-bin'
        self.args.native_webrtc_workspace = self.fixture.root / 'webrtc'
        self.args.native_ringrtc_output = self.fixture.root / 'ringrtc-out'
        self.args.native_gradle_zip = self.fixture.root / 'gradle.zip'
        self.args.native_gradle_distribution = self.fixture.root / 'gradle'
        with patch.dict(os.environ, {'ANDROID_HOME': '/test/sdk', 'JAVA_HOME': '/test/jdk'}), patch.object(self.runner, 'run') as run:
            self.runner.native('native-build-ringrtc')
        command, phase = run.call_args.args[:2]
        self.assertEqual('native-build-ringrtc', phase)
        for flag, expected in (('--workspace', self.args.native_webrtc_workspace), ('--output', self.args.native_ringrtc_output), ('--rust-bin', self.args.native_rust_bin), ('--gradle-zip', self.args.native_gradle_zip), ('--gradle-distribution', self.args.native_gradle_distribution)):
            self.assertEqual(str(expected), command[command.index(flag) + 1])
        with self.assertRaisesRegex(build.BuildError, 'RingRTC/WebRTC source build orchestration is not implemented'):
            self.runner.native('native-build')

    def test_partial_ringrtc_relative_paths_use_invocation_directory(self):
        self.args.native_rust_bin = Path('rust-bin')
        self.args.native_webrtc_workspace = Path('webrtc')
        self.args.native_ringrtc_output = Path('ringrtc-out')
        self.args.native_gradle_zip = Path('gradle.zip')
        self.args.native_gradle_distribution = Path('gradle')
        old = Path.cwd()
        try:
            os.chdir(self.fixture.root)
            with patch.dict(os.environ, {'ANDROID_HOME': 'sdk', 'JAVA_HOME': 'jdk'}), patch.object(self.runner, 'run') as run:
                self.runner.native('native-build-ringrtc')
            command = run.call_args.args[0]
            for flag, name in (('--workspace', 'webrtc'), ('--output', 'ringrtc-out'), ('--rust-bin', 'rust-bin'), ('--gradle-zip', 'gradle.zip'), ('--gradle-distribution', 'gradle'), ('--sdk', 'sdk'), ('--java-home', 'jdk')):
                self.assertEqual(str(self.fixture.root / name), command[command.index(flag) + 1])
            self.assertEqual(self.repo, run.call_args.kwargs['cwd'])
        finally:
            os.chdir(old)

    def test_partial_ringrtc_phase_rejects_missing_inputs(self):
        with patch.dict(os.environ, {'ANDROID_HOME': '/test/sdk', 'JAVA_HOME': '/test/jdk'}), patch.object(self.runner, 'run') as run:
            with self.assertRaisesRegex(build.BuildError, 'partial RingRTC build requires'):
                self.runner.native('native-build-ringrtc')
            run.assert_not_called()

    def test_partial_ringrtc_rejects_absent_native_lock(self):
        self.runner.config.pop('native')
        with patch.object(self.runner, 'source_proof'), patch.object(self.runner, 'run') as run:
            with self.assertRaisesRegex(build.BuildError, 'requires the 8.35 native artifact lock'):
                self.runner.native('native-build-ringrtc')
            run.assert_not_called()

    def test_partial_ringrtc_rejects_legacy_native_lock(self):
        with patch.object(self.runner, 'artifact_native_lock', return_value=None), patch.object(self.runner, 'run') as run:
            with self.assertRaisesRegex(build.BuildError, 'requires the 8.35 native artifact lock'):
                self.runner.native('native-build-ringrtc')
            run.assert_not_called()

    def test_source_build_phase_is_explicitly_unavailable(self):
        with self.assertRaisesRegex(build.BuildError, 'RingRTC/WebRTC source build orchestration is not implemented'):
            self.runner.native('native-build')

if __name__ == '__main__':
    unittest.main()
