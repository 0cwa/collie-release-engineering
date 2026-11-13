import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import native_driver
import prepare_sources


class FreshEnvironmentTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='native-fresh-env-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.project = self.root / 'source'
        self.project.mkdir()
        self.env = {'CARGO_HOME': str(self.root / 'cargo-cache')}
        homedir = patch.object(Path, 'home', return_value=self.root / 'home')
        homedir.start()
        self.addCleanup(homedir.stop)

    def test_dependency_cache_without_configuration_is_allowed(self):
        (self.root / 'cargo-cache/registry').mkdir(parents=True)
        native_driver.audit_fresh_environment(self.project, self.env)

    def test_relative_cargo_home_cannot_hide_child_process_configuration(self):
        with self.assertRaisesRegex(ValueError, 'absolute CARGO_HOME'):
            native_driver.audit_fresh_environment(self.project, {'CARGO_HOME': 'relative-cache'})

    def test_compiler_cache_remote_execution_and_build_shortcuts_are_rejected(self):
        for name in ('RUSTC_WRAPPER', 'RUSTC_WORKSPACE_WRAPPER', 'RUSTFLAGS', 'RUSTC_BOOTSTRAP',
                     'CARGO_ENCODED_RUSTFLAGS', 'CC', 'CXX', 'CARGO_TARGET_DIR',
                     'CARGO_BUILD_TARGET_DIR', 'CARGO_BUILD_RUSTC_WRAPPER',
                     'CARGO_TARGET_AARCH64_LINUX_ANDROID_LINKER', 'CARGO_PROFILE_RELEASE_OPT_LEVEL',
                     'CC_aarch64_linux_android', 'CXX_x86_64_linux_android', 'LDFLAGS',
                     'LD_PRELOAD', 'LD_LIBRARY_PATH', 'ORG_GRADLE_PROJECT_customRepo',
                     'SISO_CACHE', 'RBE_SERVER_ADDRESS', 'RECLIENT_FLAGS', 'CCACHE_PREFIX',
                     'USE_PREBUILD', 'BUILD_WHAT', 'PYTHONPATH', 'BASH_ENV'):
            with self.subTest(variable=name), self.assertRaisesRegex(ValueError, name):
                native_driver.audit_fresh_environment(self.project, {**self.env, name: 'override'})

    def test_user_and_ancestor_cargo_configuration_are_rejected(self):
        for directory in (self.root / 'cargo-cache', self.root / '.cargo'):
            directory.mkdir(parents=True, exist_ok=True)
            for name in ('config', 'config.toml'):
                config = directory / name
                config.write_text('[build]\nrustc-wrapper = "/cache"\n')
                with self.subTest(config=config), self.assertRaisesRegex(ValueError, 'external Cargo configuration'):
                    native_driver.audit_fresh_environment(self.project, self.env)
                config.unlink()

    def test_dangling_cargo_configuration_symlink_is_rejected(self):
        directory = self.root / 'cargo-cache'
        directory.mkdir()
        (directory / 'config.toml').symlink_to(self.root / 'missing')
        with self.assertRaisesRegex(ValueError, 'external Cargo configuration'):
            native_driver.audit_fresh_environment(self.project, self.env)

    def test_local_project_cargo_config_must_be_tracked(self):
        (self.project / '.cargo').mkdir()
        (self.project / '.cargo/config.toml').write_text('[build]\nrustc-wrapper = "/cache"\n')
        with patch.object(prepare_sources, 'git', side_effect=prepare_sources.Failure('untracked configuration')):
            with self.assertRaisesRegex(prepare_sources.Failure, 'untracked'):
                native_driver.audit_fresh_environment(self.project, self.env)

    def test_nested_cargo_config_and_symlinked_directory_are_rejected(self):
        nested = self.project / 'src/rust/.cargo'
        nested.mkdir(parents=True)
        (nested / 'config.toml').write_text('[build]\ntarget-dir = "/old-output"\n')
        with patch.object(prepare_sources, 'git', side_effect=prepare_sources.Failure('untracked nested configuration')) as command:
            with self.assertRaisesRegex(prepare_sources.Failure, 'untracked nested'):
                native_driver.audit_fresh_environment(self.project, self.env)
            command.assert_called_with(self.project, 'ls-files', '--error-unmatch', 'src/rust/.cargo/config.toml')
        (nested / 'config.toml').unlink()
        nested.rmdir()
        nested.symlink_to(self.root / 'cargo-cache', target_is_directory=True)
        (self.root / 'cargo-cache').mkdir()
        with self.assertRaisesRegex(ValueError, 'symlinked Cargo'):
            native_driver.audit_fresh_environment(self.project, self.env)

    def test_maven_local_artifacts_settings_and_redirects_are_rejected(self):
        repository = Path.home() / '.m2/repository'
        repository.mkdir(parents=True)
        native_driver.audit_fresh_environment(self.project, self.env)
        artifact = repository / 'prebuilt.jar'
        artifact.write_bytes(b'old output')
        with self.assertRaisesRegex(ValueError, 'Maven local repository'):
            native_driver.audit_fresh_environment(self.project, self.env)
        artifact.unlink()
        settings = repository.parent / 'settings.xml'
        settings.write_text('<settings><localRepository>/old-output</localRepository></settings>')
        with self.assertRaisesRegex(ValueError, 'Maven settings.xml'):
            native_driver.audit_fresh_environment(self.project, self.env)
        settings.unlink()
        for name in ('M2_HOME', 'MAVEN_HOME', 'MAVEN_USER_HOME', 'MAVEN_OPTS'):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, name):
                native_driver.audit_fresh_environment(self.project, {**self.env, name: '/old-output'})


if __name__ == '__main__':
    unittest.main()
