"""Release builds must consume the proved source, even after Gradle has run."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest
import zipfile
from unittest.mock import patch

import build


class BuildSourceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='molly-build-source-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.repo = self.root / 'pipeline'
        self.app = self.root / 'application'
        self.reports = self.root / 'reports'
        self.repo.mkdir()
        self.app.mkdir()
        self.reports.mkdir()
        (self.app / 'gradle').mkdir()
        (self.app / 'module').mkdir()
        (self.app / 'application.kt').write_bytes(b'original\n')
        (self.app / 'build.gradle.kts').write_text('// source fixture\n')
        (self.app / 'module/build.gradle.kts').write_text('// module fixture\n')
        (self.app / 'gradle/verification-metadata.xml').write_bytes(b'original verification\n')
        self.git('init', '-q')
        self.git('config', 'user.name', 'Source Fixture')
        self.git('config', 'user.email', 'source-fixture@example.invalid')
        self.git('add', '.')
        self.git('commit', '-qm', 'Source fixture')
        self.config = {'schema': 1, 'signal': {'commit': 'a' * 40},
                       'target_tree': self.git('rev-parse', 'HEAD^{tree}'), 'android': {}}
        self.proof = {'status': 'PASS', 'signal': self.config['signal']['commit'],
                      'tree': self.config['target_tree'], 'worktree': str(self.app), 'native': None}
        self.save()
        self.args = argparse.Namespace(repo=self.repo, config='molly-build.json',
                                       worktree=self.app, report_dir=self.reports,
                                       native_source_dir=None, maven_dir=None, heap='5g')

    def git(self, *args):
        env = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
        env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull)
        return subprocess.check_output(['git', '-C', str(self.app), *args],
                                       text=True, stderr=subprocess.PIPE, env=env).strip()

    def save(self):
        (self.repo / 'molly-build.json').write_text(json.dumps(self.config))
        (self.reports / 'source.json').write_text(json.dumps(self.proof))

    def test_incomplete_signal_port_fails_closed_for_all_release_profiles(self):
        self.config['signal']['commit'] = build.BLOCKED_SIGNAL_PORT
        self.save()
        runner = build.Build(self.args)
        with patch.object(runner, 'source_proof') as proof, patch.object(runner, 'run') as run:
            for profile in (None, *sorted(build.release_profiles.NAMES)):
                runner.profile = profile
                with self.subTest(profile=profile), self.assertRaisesRegex(build.BuildError, 'release assembly is blocked'):
                    runner.gradle('assemble-release')
            proof.assert_not_called()
            run.assert_not_called()

    def test_incomplete_port_blocks_real_profile_selection(self):
        self.args.repo = Path(__file__).resolve().parents[2]
        for name in sorted(build.release_profiles.NAMES):
            self.args.profile = name
            runner = build.Build(self.args)
            with self.subTest(profile=name), patch.object(runner, 'run') as run:
                with self.assertRaisesRegex(build.BuildError, 'release assembly is blocked'):
                    runner.gradle('assemble-release')
                run.assert_not_called()

    def test_835_profiles_require_the_same_staged_native_lock(self):
        self.args.repo = Path(__file__).resolve().parents[2]
        for name in sorted(build.release_profiles.NAMES):
            self.args.profile = name
            runner = build.Build(self.args)
            with self.subTest(profile=name):
                self.assertEqual(runner.artifact_native_lock(), self.args.repo / 'tools/native/native-lock.json')
                with self.assertRaisesRegex(build.BuildError, 'no fallback'):
                    runner.native_verification_bytes()

    def test_835_profiles_select_proxy_fail_closed_unit_tests(self):
        root = Path(__file__).resolve().parents[2]
        config = json.loads((root / 'molly-build.json').read_text())
        selector = 'org.thoughtcrime.securesms.net.MollyProxyFailClosedTest'
        for name in sorted(build.release_profiles.NAMES):
            selected, _ = build.release_profiles.select(config, name, root)
            tasks = build.gradle_tasks(selected['android']['tests'], 'tests', tests=True)
            with self.subTest(profile=name):
                self.assertEqual(tasks.count(selector), 1)
                i = tasks.index(selector)
                self.assertEqual(tasks[i - 1], '--tests')
                self.assertEqual([task for task in tasks[:i] if task.startswith(':')][-1], ':app:testProdWebsiteDebugUnitTest')

    def test_incomplete_port_allows_debug_but_rejects_release_task_aliases(self):
        self.config['signal']['commit'] = build.BLOCKED_SIGNAL_PORT
        self.config['android'] = {'assemble': ':app:assembleProdWebsiteDebug', 'tests': [':core:network:test']}
        self.save()
        init = self.repo / 'tools/patches/test-worker-heap.init.gradle'
        init.parent.mkdir(parents=True)
        init.write_text('// fixture\n')
        runner = build.Build(self.args)
        with patch.object(runner, 'source_proof'), patch.object(runner, 'artifact_native_lock', return_value=None), patch.object(runner, 'run') as run:
            runner.gradle('assemble')
            runner.gradle('test')
            self.assertEqual(run.call_count, 2)
            for task in (':app:assemble', ':app:assembleRelease', ':app:bundleProdWebsiteRelease', ':app:build'):
                runner.android['assemble'] = task
                with self.subTest(task=task), self.assertRaisesRegex(build.BuildError, 'diagnostic debug'):
                    runner.gradle('assemble')
            for task in (':app:assembleRelease', ':app:testProdWebsiteReleaseUnitTest', ':app:test', ':app:testProdWebsiteR', ':app:build'):
                runner.android['tests'] = [task]
                with self.subTest(task=task), self.assertRaisesRegex(build.BuildError, 'non-release test'):
                    runner.gradle('test')
            self.assertEqual(run.call_count, 2)

    def test_incomplete_signal_port_does_not_allow_missing_native_lock(self):
        self.config['signal']['commit'] = build.BLOCKED_SIGNAL_PORT
        self.proof['signal'] = build.BLOCKED_SIGNAL_PORT
        self.save()
        runner = build.Build(self.args)
        with patch.object(runner, 'run') as run:
            with self.assertRaisesRegex(build.BuildError, 'requires the pinned native dependency lock'):
                runner.source_proof()
            run.assert_not_called()

    def test_incomplete_signal_port_rejects_altered_valid_native_lock(self):
        self.config['signal']['commit'] = build.BLOCKED_SIGNAL_PORT
        self.config['native'] = 'candidate-lock.json'
        (self.repo / 'candidate-lock.json').write_text(json.dumps({'source_provenance': {'libsignal': 'x'}, 'modules': ['x']}))
        self.save()
        runner = build.Build(self.args)
        with self.assertRaisesRegex(build.BuildError, 'native dependency lock differs from its pin'):
            runner.artifact_native_lock()

    def test_old_signal_release_is_not_blocked_by_port_guard(self):
        self.config['signal']['commit'] = '6e9dddc01f1b26e608ac683807c51e139c141ed2'
        self.save()
        runner = build.Build(self.args)
        with patch.object(runner, 'source_proof') as proof, patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(build.BuildError, 'release-version-name'):
                runner.gradle('assemble-release')
            proof.assert_called_once()

    def test_gradle_subprocess_keeps_single_worker_limit(self):
        self.config['android'] = {'assemble': [':app:assembleDebug']}
        self.save()
        script = self.app / 'gradlew'
        script.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
        script.chmod(0o755)
        init = self.repo / 'tools/patches/test-worker-heap.init.gradle'
        init.parent.mkdir(parents=True)
        init.write_text('// fixture\n')
        runner = build.Build(self.args)
        with patch.object(runner, 'source_proof'):
            runner.gradle('assemble')
        self.assertIn('--max-workers=1', (self.reports / 'assemble.log').read_text().splitlines())

    def test_release_cache_requires_opt_in_and_absolute_path(self):
        self.config['android'] = {'assemble': [':app:assembleDebug']}
        self.save()
        init = self.repo / 'tools/patches/test-worker-heap.init.gradle'
        init.parent.mkdir(parents=True)
        init.write_text('// fixture\n')
        cache_init = self.repo / 'tools/patches/release-cache.init.gradle'
        cache_init.write_text('// fixture\n')
        runner = build.Build(self.args)
        self.args.release_cache_dir = Path('/runner/release-task-cache')
        with patch.object(runner, 'source_proof'), patch.object(runner, 'run') as run:
            with self.assertRaisesRegex(build.BuildError, '--release-cache-dir'):
                runner.gradle('assemble')
            self.args.build_cache = True
            self.args.release_cache_dir = Path('relative')
            with self.assertRaisesRegex(build.BuildError, '--release-cache-dir'):
                runner.gradle('assemble')
            self.args.release_cache_dir = Path('/runner/release-task-cache')
            runner.gradle('assemble')
            command = run.call_args.args[0]
            self.assertIn('--build-cache', command)
            self.assertIn(str(cache_init), command)
            self.assertIn('-Dcollie.releaseTaskCache=/runner/release-task-cache', command)
            self.assertEqual(run.call_count, 1)

    def test_gradle_parallelism_is_explicit_and_bounded(self):
        self.config['android'] = {'assemble': [':app:assembleDebug']}
        self.save()
        init = self.repo / 'tools/patches/test-worker-heap.init.gradle'
        init.parent.mkdir(parents=True)
        init.write_text('// fixture\n')
        runner = build.Build(self.args)
        self.args.workers = 3
        self.args.parallel_projects = True
        with patch.object(runner, 'source_proof'), patch.object(runner, 'run') as run:
            runner.gradle('assemble')
            command = run.call_args.args[0]
            self.assertIn('--max-workers=3', command)
            self.assertIn('--parallel', command)
            self.assertNotIn('--no-parallel', command)
            self.assertIn('--dependency-verification=strict', command)
            self.assertNotIn('--build-cache', command)
            self.args.build_cache = True
            runner.gradle('assemble')
            command = run.call_args.args[0]
            self.assertIn('--build-cache', command)
            self.assertIn('--dependency-verification=strict', command)
            for invalid in (0, 13, True, '3'):
                self.args.workers = invalid
                with self.assertRaisesRegex(build.BuildError, '--workers'):
                    runner.gradle('assemble')
            self.assertEqual(run.call_count, 2)

    def test_instrumentation_uses_proved_source_and_separate_imo_runner(self):
        init = self.repo / 'tools/patches/test-worker-heap.init.gradle'
        init.parent.mkdir(parents=True)
        init.write_text('// fixture\n')
        runner = build.Build(self.args)
        with patch.object(runner, 'source_proof') as proof, patch.object(runner, 'run') as run:
            runner.gradle('instrumentation')
            default = run.call_args.args[0]
            runner.gradle('instrumentation-imo')
            imo = run.call_args.args[0]
        self.assertEqual(proof.call_count, 2)
        self.assertIn(':app:connectedProdWebsiteDebugAndroidTest', default)
        self.assertNotIn('-PimoTests', default)
        self.assertIn('-PimoTests', imo)
        self.assertIn('-Pandroid.testInstrumentationRunnerArguments.package=org.thoughtcrime.securesms.messages.incomingmessageobserver', imo)

    def test_release_assembly_uses_release_task_and_rejects_ci_signing(self):
        self.save()
        init = self.repo / 'tools/patches/test-worker-heap.init.gradle'
        init.parent.mkdir(parents=True)
        init.write_text('// fixture\n')
        (init.parent / 'release-version.init.gradle').write_text('// fixture\n')
        self.args.release_version_name = '8.33.2-1'
        self.args.release_version_code = '177201'
        runner = build.Build(self.args)
        with patch.object(runner, 'source_proof'), patch.object(runner, 'run') as run:
            with patch.dict(os.environ, {}, clear=True):
                runner.gradle('assemble-release')
            self.assertEqual(run.call_args.args[0][-1], ':app:assembleProdWebsiteRelease')
            self.assertNotIn('--build-cache', run.call_args.args[0])
            self.args.build_cache = True
            with patch.dict(os.environ, {}, clear=True):
                runner.gradle('assemble-release')
            self.assertIn('--build-cache', run.call_args.args[0])
            self.assertIn('--dependency-verification=strict', run.call_args.args[0])
            with patch.dict(os.environ, {'CI_KEYSTORE_PATH': '/key'}, clear=True):
                with self.assertRaisesRegex(build.BuildError, 'must be unsigned'):
                    runner.gradle('assemble-release')
            self.assertEqual(run.call_count, 2)

    def test_firebase_free_workflow_hands_off_real_source_proof(self):
        workflow = (Path(__file__).resolve().parents[2] / '.github/workflows/molly-patch-release.yml').read_text()
        prepare = workflow.split('id: foss_prepare', 1)[1].split('        run: |\n', 1)[1].split('\n      - name:', 1)[0]
        (self.root / 'molly-release').symlink_to(self.app, target_is_directory=True)
        env = dict(os.environ, RUNNER_TEMP=str(self.root))
        subprocess.run(['bash', '-e', '-c', textwrap.dedent(prepare)], env=env, check=True)
        self.assertEqual((self.root / 'foss-reports/source.json').read_bytes(), (self.reports / 'source.json').read_bytes())
        self.args.report_dir = self.root / 'foss-reports'
        self.assertEqual(build.Build(self.args).source_proof(), self.proof)
        self.proof['tree'] = 'bad-tree'
        (self.args.report_dir / 'source.json').write_text(json.dumps(self.proof))
        with self.assertRaisesRegex(build.BuildError, 'different release configuration'):
            build.Build(self.args).source_proof()

    def test_clean_source_and_generated_outputs_are_allowed(self):
        for path in ('module/build/generated/Generated.kt', 'build/outputs/app.apk',
                     '.gradle/cache/entry', '.kotlin/sessions/entry', 'module/.cxx/object.o'):
            file = self.app / path
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(b'generated output\n')
        self.assertEqual(build.Build(self.args).source_proof(), self.proof)

    def test_untracked_source_and_fake_build_package_are_rejected(self):
        for name in ('module/Added.kt', 'module/src/main/java/build/Added.java'):
            with self.subTest(name=name):
                path = self.app / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('unproved source\n')
                with self.assertRaisesRegex(build.BuildError, 'untracked source'):
                    build.Build(self.args).source_proof()
                path.unlink()

    def test_changed_working_bytes_with_same_size_and_timestamp_are_rejected(self):
        path = self.app / 'application.kt'
        before = path.stat()
        self.git('update-index', '--refresh')
        path.write_bytes(b'tampered\n')
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        with self.assertRaisesRegex(build.BuildError, 'working source differs'):
            build.Build(self.args).source_proof()

    def test_staged_edit_is_rejected_even_if_working_bytes_match_head(self):
        path = self.app / 'application.kt'
        path.write_bytes(b'staged change\n')
        self.git('add', 'application.kt')
        path.write_bytes(b'original\n')
        with self.assertRaisesRegex(build.BuildError, 'staged source differs'):
            build.Build(self.args).source_proof()

    def test_new_staged_source_path_is_rejected(self):
        (self.app / 'new.kt').write_text('new source\n')
        self.git('add', 'new.kt')
        with self.assertRaisesRegex(build.BuildError, 'adds or removes tracked paths'):
            build.Build(self.args).source_proof()

    def test_skip_worktree_and_assume_unchanged_are_rejected_even_when_clean(self):
        for flag, undo in (('--skip-worktree', '--no-skip-worktree'),
                           ('--assume-unchanged', '--no-assume-unchanged')):
            with self.subTest(flag=flag):
                self.git('update-index', flag, 'application.kt')
                with self.assertRaisesRegex(build.BuildError, 'skip-worktree, assume-unchanged'):
                    build.Build(self.args).source_proof()
                self.git('update-index', undo, 'application.kt')

    def test_sparse_checkout_setting_is_rejected_without_skip_flags(self):
        self.git('config', 'core.sparseCheckout', 'true')
        with self.assertRaisesRegex(build.BuildError, 'sparse source checkouts'):
            build.Build(self.args).source_proof()

    def test_executable_mode_change_is_rejected_even_if_git_ignores_it(self):
        self.git('config', 'core.filemode', 'false')
        (self.app / 'application.kt').chmod(0o755)
        with self.assertRaisesRegex(build.BuildError, 'executable mode changed'):
            build.Build(self.args).source_proof()

    def test_exact_native_metadata_is_allowed_but_any_extra_change_is_rejected(self):
        # Native verification independently proves receipts, artifact hashes,
        # XML rendering and the generated init script. Here its verified bytes
        # are the fixture input to the application's source-boundary check.
        native = b'original verification\nexact native coordinate and checksums\n'
        self.config['native'] = 'native-ringrtc.json'
        self.proof['native'] = 'native-ringrtc.json'
        self.save()
        runner = build.Build(self.args)
        metadata = self.app / 'gradle/verification-metadata.xml'
        with patch.object(runner, 'native_verification_bytes', return_value=native):
            metadata.write_bytes(native)
            self.assertEqual(runner.source_proof(), self.proof)
            self.git('add', 'gradle/verification-metadata.xml')
            self.assertEqual(runner.source_proof(), self.proof)
            metadata.write_bytes(native + b'extra trust rule\n')
            with self.assertRaisesRegex(build.BuildError, 'working source differs'):
                runner.source_proof()
            metadata.write_bytes(native)
            (self.app / 'application.kt').write_bytes(b'tampered\n')
            with self.assertRaisesRegex(build.BuildError, 'working source differs'):
                runner.source_proof()

    def test_verification_metadata_without_native_proof_has_no_exception(self):
        (self.app / 'gradle/verification-metadata.xml').write_bytes(b'changed verification\n')
        with self.assertRaisesRegex(build.BuildError, 'working source differs'):
            build.Build(self.args).source_proof()


class CachedSdkTests(BuildSourceTests):
    def setUp(self):
        super().setUp()
        self.config['android'] = {'sdk': '36.1', 'build_tools': '36.0.0', 'ndk': '28.0.13004108'}
        self.save()
        self.sdk_root = self.root / 'sdk'
        self.env = patch.dict(os.environ, {'ANDROID_HOME': str(self.sdk_root)})
        self.env.start()
        self.addCleanup(self.env.stop)
        for identity, version, properties, payloads, archives in (
            ('platforms;android-36.1', '1', 'AndroidVersion.ApiLevel=36.1',
             ['framework.aidl', 'build.prop'], ['android.jar']),
            ('build-tools;36.0.0', '36.0.0', '',
             ['aapt', 'aapt2', 'zipalign', 'd8'], ['lib/d8.jar']),
            ('ndk;28.0.13004108', '28.0.13004108', '',
             ['toolchains/llvm/prebuilt/linux-x86_64/bin/clang'], [])):
            directory = self.sdk_root.joinpath(*identity.split(';'))
            directory.mkdir(parents=True)
            parts = (version + '.0.0').split('.')[:3]
            (directory / 'package.xml').write_text(
                f'<repository><localPackage path="{identity}"><revision>'
                f'<major>{parts[0]}</major><minor>{parts[1]}</minor><micro>{parts[2]}</micro>'
                '</revision></localPackage></repository>')
            (directory / 'source.properties').write_text(f'Pkg.Revision={version}\n{properties}\n')
            for name in payloads:
                path = directory / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'payload')
                path.chmod(0o755)
            for name in archives:
                path = directory / name
                path.parent.mkdir(parents=True, exist_ok=True)
                with zipfile.ZipFile(path, 'w') as archive:
                    archive.writestr('payload', 'fixture')

    def test_valid_cache_skips_manager_and_records_actual_method(self):
        runner = build.Build(self.args)
        with patch.object(runner, 'run') as run:
            runner.sdk()
        run.assert_not_called()
        report = json.loads((self.reports / 'sdk.json').read_text())
        self.assertEqual((report['status'], report['method'], report['executed'], report['command']),
                         ('PASS', 'local-validation', False, []))
        self.assertIn('sdkmanager was not executed', (self.reports / 'sdk.log').read_text())

    def test_invalid_cache_calls_installer(self):
        cases = ('missing', 'malformed', 'identity', 'revision', 'payload', 'archive')
        for case in cases:
            with self.subTest(case=case):
                directory = self.sdk_root / 'build-tools/36.0.0'
                target = {'missing': 'source.properties', 'malformed': 'package.xml',
                          'identity': 'package.xml', 'revision': 'source.properties',
                          'payload': 'aapt2', 'archive': 'lib/d8.jar'}[case]
                path = directory / target
                original = path.read_bytes()
                if case == 'missing': path.unlink()
                elif case == 'malformed': path.write_text('<broken')
                elif case == 'identity': path.write_bytes(original.replace(b'build-tools;36.0.0', b'build-tools;35.0.0'))
                elif case == 'revision': path.write_text('Pkg.Revision=35.0.0\n')
                elif case == 'payload': path.write_bytes(b'')
                else: path.write_bytes(b'not a zip')
                runner = build.Build(self.args)
                with patch.object(runner, 'run') as run:
                    runner.sdk()
                run.assert_called_once_with(
                    [str(self.sdk_root / 'cmdline-tools/latest/bin/sdkmanager'), '--install',
                     'platforms;android-36.1', 'build-tools;36.0.0', 'ndk;28.0.13004108'], 'sdk')
                path.write_bytes(original)
                if case == 'payload': path.chmod(0o755)

    def test_cold_install_accepts_licenses_only_when_requested(self):
        (self.sdk_root / 'platforms/android-36.1/android.jar').unlink()
        self.args.accept_licenses = True
        runner = build.Build(self.args)
        with patch('build.subprocess.run') as licenses, patch.object(runner, 'source_proof'), \
             patch.object(runner, 'run') as install:
            runner.sdk()
        self.assertEqual(licenses.call_args.args[0][-1], '--licenses')
        install.assert_called_once()


class NamedProfileBuildTests(BuildSourceTests):
    def test_release_profiles_preserve_firebase_setting_with_isolated_cache(self):
        self.save()
        init = self.repo / 'tools/patches/test-worker-heap.init.gradle'
        init.parent.mkdir(parents=True)
        for name in ('test-worker-heap.init.gradle', 'release-version.init.gradle', 'release-cache.init.gradle'):
            (init.parent / name).write_text('// fixture\n')
        self.args.release_version_name = '8.33.2-1-rc3'
        self.args.release_version_code = '177203'
        self.args.workers = 2
        self.args.build_cache = True
        self.args.release_cache_dir = Path('/runner/release-task-cache')
        runner = build.Build(self.args)
        with patch.object(runner, 'source_proof'), patch.object(runner, 'run') as run, patch.dict(os.environ, {}, clear=True):
            for profile in sorted(build.release_profiles.NAMES):
                with self.subTest(profile=profile):
                    runner.profile = {'firebase_free': profile in build.release_profiles.FOSS,
                                      'app': dict(zip(('title', 'file_name', 'package_id'),
                                                      build.release_profiles.APP[profile]))}
                    runner.gradle('assemble-release')
                    command = run.call_args.args[0]
                    self.assertIn('-PmollyFirebaseFree=' + str(profile in build.release_profiles.FOSS).lower(), command)
                    self.assertIn('--build-cache', command)
                    self.assertIn('-PbasePackageId=im.collie.app', command)
                    self.assertIn('-PbaseAppTitle=' + build.release_profiles.APP[profile][0], command)
                    self.assertIn('-PbaseAppFileName=' + build.release_profiles.APP[profile][1], command)
                    self.assertIn('-Dcollie.releaseTaskCache=/runner/release-task-cache', command)
                    self.assertIn('--max-workers=2', command)
                    self.assertIn('--dependency-verification=strict', command)
                    self.assertIn('-PreleaseVersionName=8.33.2-1-rc3', command)
                    self.assertIn('-PreleaseVersionCode=177203', command)

    @patch.object(build.release_profiles, 'layer_test_sources', new=lambda *_: [])
    def test_profile_bound_to_proof_and_forces_firebase_property(self):
        self.config['port'] = {'series': build.release_profiles.PORT_SERIES}
        self.config['android'] = {'assemble': ':app:assembleDebug', 'tests': [':app:test']}
        self.config['fork_source'] = {'url': 'https://github.com/tw-hx/Signal-Android', 'head': 'c' * 40}
        fork_series = [build.release_profiles.FIREBASE, build.release_profiles.COLLIE_ICON,
                       build.release_profiles.COLLIE_BRANDING, build.release_profiles.COLLIE_VISUALS,
                       build.release_profiles.RESTORE_PICKER]
        entry = {'base_series': build.release_profiles.PORT_SERIES, 'base_checkpoint': 'b' * 40, 'base_tree': self.config['target_tree'],
                 'fork_series': fork_series, 'target_tree': self.config['target_tree'],
                 'firebase_free': False}
        self.config['profiles'] = {'molly-minimal': entry,
                                   'molly-continued': dict(entry)}
        self.args.profile = 'molly-minimal'
        _, selection = build.release_profiles.select(self.config, self.args.profile, self.repo)
        self.proof['profile'] = selection
        self.save()
        runner = build.Build(self.args)
        self.assertEqual(runner.source_proof(), self.proof)
        script = self.app / 'gradlew'
        script.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
        script.chmod(0o755)
        init = self.repo / 'tools/patches/test-worker-heap.init.gradle'
        init.parent.mkdir(parents=True)
        init.write_text('// fixture\n')
        with patch.object(runner, 'source_proof', return_value=self.proof):
            runner.gradle('assemble')
        self.assertIn('-PmollyFirebaseFree=false', (self.reports / 'assemble.log').read_text().splitlines())
        self.assertIn('-PbasePackageId=im.collie.app', (self.reports / 'assemble.log').read_text().splitlines())
        self.assertIn('-PbaseAppTitle=Collie', (self.reports / 'assemble.log').read_text().splitlines())
        for name, (title, filename, package) in build.release_profiles.APP.items():
            with self.subTest(profile=name):
                runner.profile['app'] = {'title': title, 'file_name': filename, 'package_id': package}
                with patch.object(runner, 'source_proof', return_value=self.proof):
                    runner.gradle('assemble')
                args = (self.reports / 'assemble.log').read_text().splitlines()
                self.assertIn('-PbasePackageId=im.collie.app', args)
                self.assertIn('-PbaseAppTitle=' + title, args)
                self.assertIn('-PbaseAppFileName=' + filename, args)
        self.args.profile = 'molly-continued'
        with self.assertRaisesRegex(build.BuildError, 'different release profile'):
            build.Build(self.args).source_proof()
        self.args.profile = 'molly-minimal'
        self.config['android']['tests'] = [':app:otherTest']
        self.save()
        with self.assertRaisesRegex(build.BuildError, 'different release profile'):
            build.Build(self.args).source_proof()


if __name__ == '__main__':
    unittest.main()
