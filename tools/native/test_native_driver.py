"""Direct tests of the native artifact integration boundary in native_driver.

These cover the checks that applied before every Gradle invocation of the
historical app build driver, without importing that driver: the staged
artifacts are verified against the pinned lock and the unchanged source
verification metadata, and nothing falls back to unverified inputs.
"""
import json
import os
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch

import native_driver
import test_stage_artifacts as artifact_fixtures
from test_gradle_environment import isolated_gradle_environment

HERE = Path(__file__).parent


class NativeIntegrationTest(unittest.TestCase):
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
        self.proof = root / 'reports/native'
        (self.repo / 'tools/native').mkdir(parents=True)
        (self.app / 'gradle').mkdir(parents=True)
        shutil.copyfile(HERE / 'stage_artifacts.py', self.repo / 'tools/native/stage_artifacts.py')
        shutil.copytree(root / 'patches', self.repo / 'tools/native/patches')
        self.lock = self.repo / 'tools/native/native-lock.json'
        shutil.copyfile(self.fixture.lock_path, self.lock)
        self.metadata = self.app / 'gradle/verification-metadata.xml'
        shutil.copyfile(self.fixture.verification, self.metadata)
        self.original = self.metadata.read_bytes()
        self.stage = self.proof / 'artifact-stage'

    def integrate(self):
        return native_driver.integrate(self.repo, self.app, self.lock, self.fixture.input, self.proof, self.original)

    def verify(self):
        return native_driver.verify(self.repo, self.app, self.lock, self.proof, self.original)

    def staged_binary(self, name):
        lock = json.loads(self.lock.read_text())
        module = next(m for m in lock['modules'] if m['name'] == name)
        version = module['output_version']
        return (self.stage / 'maven' / lock['output_group'].replace('.', '/') / name / version
                / f"{name}-{version}.{module['extension']}")

    def test_integration_installs_only_the_verified_stage(self):
        receipt = self.integrate()
        self.assertEqual((self.stage / 'verification-metadata.xml').read_bytes(), self.metadata.read_bytes())
        self.assertNotEqual(self.original, self.metadata.read_bytes())
        self.assertEqual(self.original, (self.proof / 'verification-metadata.before.xml').read_bytes())
        self.assertEqual(receipt, self.verify())

    def test_changed_source_metadata_is_rejected_before_staging(self):
        self.metadata.write_bytes(self.original + b'<!-- changed -->')
        with self.assertRaisesRegex(ValueError, 'unchanged source verification metadata'):
            self.integrate()
        self.assertFalse(self.proof.exists())

    def test_existing_proof_is_not_reused(self):
        self.integrate()
        self.metadata.write_bytes(self.original)
        with self.assertRaisesRegex(ValueError, 'fresh report directory'):
            self.integrate()

    def test_malformed_lock_fails_and_restores_source_metadata(self):
        original = json.loads(self.lock.read_text())
        for fields in (('modules',), ('source_provenance',), ('modules', 'source_provenance')):
            changed = {key: value for key, value in original.items() if key not in fields}
            self.lock.write_text(json.dumps(changed))
            shutil.rmtree(self.proof, ignore_errors=True)
            with self.subTest(fields=fields):
                with self.assertRaises(ValueError):
                    self.integrate()
                self.assertEqual(self.original, self.metadata.read_bytes())
                self.assertFalse(self.stage.exists())

    def test_unpinned_input_has_no_fallback_and_restores_source_metadata(self):
        binary = next(self.fixture.input.rglob('ringrtc-android-*.aar'))
        binary.write_bytes(b'unpinned')
        with self.assertRaises(ValueError):
            self.integrate()
        self.assertEqual(self.original, self.metadata.read_bytes())
        self.assertFalse(self.stage.exists())

    def test_tampered_init_script_is_rejected(self):
        self.integrate()
        init = self.stage / 'native.init.gradle'
        init.write_text(init.read_text() + '\n// tampered\n')
        with self.assertRaisesRegex(ValueError, 'approved transformation'):
            self.verify()

    def test_tampered_staged_binary_is_rejected(self):
        self.integrate()
        self.staged_binary('ringrtc-android').write_bytes(b'tampered')
        with self.assertRaisesRegex(ValueError, 'Staged binary differs'):
            self.verify()

    def test_app_metadata_changed_after_integration_is_rejected(self):
        self.integrate()
        self.metadata.write_bytes(self.original)
        with self.assertRaisesRegex(ValueError, 'verification metadata changed'):
            self.verify()

    def test_changed_base_metadata_record_is_rejected(self):
        self.integrate()
        (self.proof / 'verification-metadata.before.xml').write_bytes(self.original + b'\n')
        with self.assertRaisesRegex(ValueError, 'base verification metadata changed'):
            self.verify()

    def test_verification_rejects_inherited_gradle_options_and_user_init_scripts(self):
        self.integrate()
        for key, value in (('GRADLE_OPTS', '-Dmalicious=true'), ('GRADLE_HOME', '/opt/hostedtoolcache/gradle')):
            with self.subTest(key=key), patch.dict(os.environ, {key: value}):
                with self.assertRaisesRegex(ValueError, key):
                    self.verify()
        home = self.fixture.root / 'gradle-home'
        (home / 'init.d').mkdir(parents=True)
        (home / 'init.d/evil.gradle').write_text('malicious')
        with patch.dict(os.environ, {'GRADLE_USER_HOME': str(home)}):
            with self.assertRaisesRegex(ValueError, 'init.d'):
                self.verify()

    def test_missing_or_symlinked_artifact_gate_is_rejected(self):
        gate = self.repo / 'tools/native/stage_artifacts.py'
        gate.unlink()
        with self.assertRaisesRegex(ValueError, 'native artifact gate'):
            self.integrate()
        gate.symlink_to(HERE / 'stage_artifacts.py')
        with self.assertRaisesRegex(ValueError, 'native artifact gate'):
            self.integrate()
        self.assertEqual(self.original, self.metadata.read_bytes())


if __name__ == '__main__':
    unittest.main()
