import hashlib
import importlib.util
import json
import os
import shutil
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import test_prepare_sources as source_fixtures
from test_gradle_environment import isolated_gradle_environment

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
spec = importlib.util.spec_from_file_location('build_sources', HERE / 'build_sources.py')
subject = importlib.util.module_from_spec(spec)
spec.loader.exec_module(subject)

class PartialSourceBuildTest(unittest.TestCase):
    def setUp(self):
        self.fixture = source_fixtures.SourcePreparationTest('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.root = self.fixture.root
        environment = isolated_gradle_environment(self.root / 'gradle-user-home')
        environment.start()
        self.addCleanup(environment.stop)
        self.sources = self.root / 'prepared'
        subject.prepare_sources.prepare(self.fixture.lock_file, self.fixture.sources, self.sources)
        (self.sources / 'libsignal/.git/info').mkdir(exist_ok=True)
        (self.sources / 'libsignal/.git/info/exclude').write_text('java/\n')
        self.sdk = self.root / 'sdk'
        ndk = self.sdk / 'ndk/28.0.13004108'
        ndk.mkdir(parents=True)
        (ndk / 'source.properties').write_text('Pkg.Revision = 28.0.13004108\n')
        self.java = self.root / 'jdk'
        (self.java / 'bin').mkdir(parents=True)
        (self.java / 'bin/java').write_text('fixture')
        self.rust_bin = self.root / 'toolchain/bin'
        self.rust_bin.mkdir(parents=True)
        for name in ('cargo', 'rustc', 'rustup'):
            tool = self.rust_bin / name
            tool.write_text('fixture')
            tool.chmod(0o755)
        (self.java / 'bin/java').chmod(0o755)
        self.report = self.root / 'partial.json'
        (self.root / 'gradle.zip').write_bytes(b'fixture gradle')
        self.calls = []
        self.lock = json.loads(self.fixture.lock_file.read_text())
        self.binaries = {'libsignal-android': b'fixture-aar', 'libsignal-client': b'fixture-jar'}
        for module in self.lock['modules']:
            if module['name'] in self.binaries:
                module['binary_sha256'] = hashlib.sha256(self.binaries[module['name']]).hexdigest()
        self.fixture.lock_file.write_text(json.dumps(self.lock))

    def runner(self, args, cwd, env):
        self.calls.append(args)
        self.assertEqual('1.98.1', env['RUSTUP_TOOLCHAIN'])
        if args[-1] == '-version':
            return ['openjdk version \"21.0.13\" 2026-01-01']
        if args[-1] == '--version':
            return ['rustc 1.98.1 (fixture)']
        if args[-3:] == ['target', 'list', '--installed']:
            return ['aarch64-linux-android', 'armv7-linux-androideabi', 'i686-linux-android', 'x86_64-linux-android']
        if 'test' in args:
            return ['test result: ok. 4 passed; 0 failed; 0 ignored']
        if args[0] == str(self.root / 'verified-gradle/bin/gradle'):
            self.assertEqual(str(self.sdk / 'ndk/28.0.13004108'), env['ANDROID_NDK_HOME'])
            self.assertEqual(str(self.sdk / 'ndk/28.0.13004108'), env['ANDROID_NDK_ROOT'])
            aar = self.sources / 'libsignal/java/android/build/outputs/aar'
            jar = self.sources / 'libsignal/java/client/build/libs'
            aar.mkdir(parents=True)
            jar.mkdir(parents=True)
            (aar / 'libsignal.aar').write_bytes(self.binaries['libsignal-android'])
            (jar / 'libsignal.jar').write_bytes(self.binaries['libsignal-client'])
            return []
        raise AssertionError('unexpected diagnostic command')

    def run_build(self, rust_effect=None, ndk_effect=None, fresh=False):
        with patch.object(subject.verify_gradle, 'verify', return_value=self.root / 'verified-gradle/bin/gradle'), patch.object(subject.rust_payloads, 'verify', return_value=372, side_effect=rust_effect), patch.object(subject.ndk_payloads, 'verify', return_value=8449, side_effect=ndk_effect):
            return subject.diagnostic_build(self.fixture.lock_file, self.sources, self.sdk, self.java, self.rust_bin, self.report, self.root / 'gradle.zip', self.root / 'verified-gradle', self.runner, fresh=fresh)

    def test_success_is_explicitly_partial_and_never_release_proof(self):
        receipt = self.run_build()
        self.assertEqual('PARTIAL_LIBSIGNAL_ONLY', receipt['status'])
        self.assertFalse(receipt['release_allowed'])
        self.assertTrue(any('configured_proxy_never_falls_back_to_direct' in command for command in self.calls))
        self.assertTrue(self.report.is_file())

    def test_fresh_mode_runs_offline_without_gradle_caches(self):
        receipt = self.run_build(fresh=True)
        self.assertTrue(receipt['fresh_output_check'])
        self.assertEqual('', receipt['selected_environment']['RUSTC_WRAPPER'])
        self.assertEqual('', receipt['selected_environment']['RUSTC_WORKSPACE_WRAPPER'])
        self.assertEqual(str(self.rust_bin / 'rustc'), receipt['selected_environment']['RUSTC'])
        command = next(c for c in self.calls if c[0] == str(self.root / 'verified-gradle/bin/gradle'))
        for flag in ('--offline', '--no-build-cache', '--no-configuration-cache', '--rerun-tasks'):
            self.assertIn(flag, command)

    def test_fresh_mode_rejects_existing_output(self):
        for relative in ('target', 'java/.gradle', 'java/build', 'java/android/build', 'java/client/build', 'java/server/build', 'java/android/src/main/jniLibs', 'java/android/src/main/assets'):
            path = self.sources / 'libsignal' / relative
            path.mkdir(parents=True, exist_ok=True)
            with self.subTest(relative=relative), self.assertRaisesRegex(subject.Failure, 'Fresh diagnostic output already exists'):
                self.run_build(fresh=True)
            shutil.rmtree(path)
        self.assertFalse(self.report.exists())

    def test_fresh_mode_rejects_redirected_cargo_target(self):
        with patch.dict(os.environ, {'CARGO_TARGET_DIR': str(self.root / 'old-target')}):
            with self.assertRaisesRegex(subject.Failure, 'default Cargo target'):
                self.run_build(fresh=True)
        self.assertFalse(self.report.exists())

    def test_fresh_mode_rejects_inherited_compiler_wrappers(self):
        for name in ('RUSTC_WRAPPER', 'RUSTC_WORKSPACE_WRAPPER'):
            with self.subTest(name=name), patch.dict(os.environ, {name: '/unexpected/cache'}):
                with self.assertRaisesRegex(subject.Failure, 'compiler wrappers'):
                    self.run_build(fresh=True)
        self.assertFalse(self.report.exists())

    def test_existing_report_created_during_build_is_not_overwritten(self):
        original_runner = self.runner
        def racing_runner(args, cwd, env):
            result = original_runner(args, cwd, env)
            if args[0] == str(self.root / 'verified-gradle/bin/gradle'):
                self.report.write_text('existing data')
            return result
        self.runner = racing_runner
        with self.assertRaises(FileExistsError):
            self.run_build(fresh=True)
        self.assertEqual('existing data', self.report.read_text())

    def test_rust_payload_change_before_or_after_build_rejects_receipt(self):
        for effect in ([subject.rust_payloads.Failure('before')], [372, subject.rust_payloads.Failure('after')]):
            with self.subTest(effect=effect):
                with self.assertRaises(subject.rust_payloads.Failure): self.run_build(rust_effect=effect)
                self.assertFalse(self.report.exists())
                import shutil
                for path in (self.sources/'libsignal/java/android/build', self.sources/'libsignal/java/client/build'):
                    if path.exists(): shutil.rmtree(path)

    def test_ndk_change_before_or_after_build_rejects_receipt(self):
        for effect in ([subject.ndk_payloads.Failure('before')], [8449, subject.ndk_payloads.Failure('after')]):
            with self.subTest(effect=effect):
                with self.assertRaises(subject.ndk_payloads.Failure): self.run_build(ndk_effect=effect)
                self.assertFalse(self.report.exists())
                import shutil
                for path in (self.sources/'libsignal/java/android/build', self.sources/'libsignal/java/client/build'):
                    if path.exists(): shutil.rmtree(path)

    def test_mismatched_output_fails_with_failure_only_receipt(self):
        self.binaries['libsignal-client'] = b'unexpected'
        with self.assertRaisesRegex(subject.Failure, 'reproducibility is unproved'):
            self.run_build()
        failure = json.loads(self.report.read_text())
        self.assertEqual('FAILED_ARTIFACT_MISMATCH', failure['status'])
        self.assertFalse(failure['release_allowed'])
        self.assertNotEqual(failure['artifacts']['libsignal-client'], failure['expected_artifacts']['libsignal-client'])
        with self.assertRaisesRegex(subject.Failure, 'already exists'):
            self.run_build()

    def test_lock_change_during_build_rejects_report(self):
        original_runner = self.runner
        def changing_runner(args, cwd, env):
            result = original_runner(args, cwd, env)
            if args[0] == str(self.root / 'verified-gradle/bin/gradle'):
                with self.fixture.lock_file.open('a') as output:
                    output.write(' ')
            return result
        self.runner = changing_runner
        with self.assertRaisesRegex(subject.Failure, 'lock changed'):
            self.run_build()
        self.assertFalse(self.report.exists())

    def test_ringrtc_preflight_requires_requested_java_version(self):
        def runner(args, cwd, env):
            if args[-1] == '-version': return ['openjdk version \"25.0.4\"']
            return self.runner(args, cwd, env)
        with patch.object(subject.rust_payloads, 'verify', return_value=372), patch.object(subject.ndk_payloads, 'verify', return_value=8449):
            subject.preflight(self.fixture.lock_file, self.sources, self.sdk, self.java, self.rust_bin, runner, java_major=25)
            with self.assertRaisesRegex(subject.Failure, 'Java 21'):
                subject.preflight(self.fixture.lock_file, self.sources, self.sdk, self.java, self.rust_bin, runner)

    def test_direct_toolchain_accepts_installed_android_targets(self):
        (self.rust_bin / 'rustup').unlink()
        for target in ('aarch64-linux-android', 'armv7-linux-androideabi', 'i686-linux-android', 'x86_64-linux-android'):
            libs = self.rust_bin.parent / 'lib/rustlib' / target / 'lib'
            libs.mkdir(parents=True)
            (libs / 'libstd-fixture.rlib').write_bytes(b'fixture')
        self.assertFalse(self.run_build()['release_allowed'])
        self.assertFalse(any('rustup' in command[0] for command in self.calls))

    def test_direct_toolchain_rejects_missing_android_target(self):
        (self.rust_bin / 'rustup').unlink()
        with self.assertRaisesRegex(subject.Failure, 'target libraries are missing'):
            self.run_build()
        self.assertFalse(self.report.exists())

    def test_incorrect_ndk_fails_before_commands(self):
        (self.sdk / 'ndk/28.0.13004108/source.properties').write_text('Pkg.Revision = 29.0.0\n')
        with self.assertRaisesRegex(subject.Failure, 'NDK version'):
            self.run_build()
        self.assertFalse(self.report.exists())
        self.assertEqual([], self.calls)

if __name__ == '__main__':
    unittest.main()
