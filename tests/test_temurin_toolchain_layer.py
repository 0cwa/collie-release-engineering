import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
MANIFEST = ROOT / 'source/layers/temurin-21.0.13.json'
PATCH = ROOT / 'source/layers/temurin-21.0.13.patch'
FIXTURE = ROOT / 'tests/fixtures/temurin-53d'
APP_BASE = 'fd83dbfc5323f7fb8689b697f141656399e87249'


class TemurinToolchainLayerTests(unittest.TestCase):
    def test_production_patch_applies_to_synthetic_build_guide_fixture(self):
        manifest = json.loads(MANIFEST.read_text())
        patch = PATCH.read_bytes()
        self.assertEqual(manifest['schema'], 1)
        self.assertEqual(manifest['kind'], 'feature-layer')
        self.assertEqual(manifest['application_base_commit'], APP_BASE)
        self.assertNotIn('before_tree', manifest)
        self.assertEqual(manifest['patch_sha256'], hashlib.sha256(patch).hexdigest())
        self.assertEqual(manifest['paths'], ['.tool-versions', 'docs/BUILD.md', 'Dockerfile'])
        fixture_files = {p.relative_to(FIXTURE).as_posix() for p in FIXTURE.rglob('*') if p.is_file()}
        self.assertEqual(fixture_files, set(manifest['paths']))
        for path in manifest['paths']:
            before = (FIXTURE / path).read_bytes()
            if path == 'docs/BUILD.md':
                self.assertEqual(hashlib.sha256(before).hexdigest(),
                                 'd5220c8cc66de6052e571d1c9a0bb55064d482b524f50e59a4d632dcb1cce9f0')
            else:
                self.assertEqual(hashlib.sha256(before).hexdigest(), manifest['before'][path]['sha256'])
            self.assertRegex(manifest['before'][path]['blob'], r'^[0-9a-f]{40}$')
            self.assertRegex(manifest['after'][path]['blob'], r'^[0-9a-f]{40}$')
        patch_text = patch.decode()
        patch_paths = {
            row.split(' b/', 1)[1]
            for row in patch_text.splitlines() if row.startswith('diff --git a/')
        }
        self.assertEqual(patch_paths, set(manifest['paths']))
        replacements = {
            '.tool-versions': (b'java openjdk-21.0.2\n', b'java temurin-21.0.13+9\n'),
            'docs/BUILD.md': (
                b'an assembler or inject source from another worktree. JDK/Gradle/SDK/NDK and strict\n'
                b'dependency inputs are declared in this tree. Canonical identity is im.collie.app,\n',
                b'an assembler or inject source from another worktree. Its JDK is Eclipse Temurin\n'
                b'21.0.13+9: `.tool-versions` pins that release and `Dockerfile` pins the matching\n'
                b'official Linux/amd64 image by digest. Gradle/SDK/NDK and strict dependency inputs\n'
                b'are also declared in this tree. Canonical identity is im.collie.app,\n',
            ),
            'Dockerfile': (
                b'FROM docker.io/eclipse-temurin:21.0.11_10-jdk-alpine-3.23@sha256:4ec2402e5ebb803add08b063b9e5e52e7c11961caaae1f490479d925753f0d92 AS base\n',
                b'FROM docker.io/eclipse-temurin:21.0.13_9-jdk-alpine-3.23@sha256:f1d8aa86ba3a2a7119b157df01d1832281cd122989cddb46bc86a9f49f99cc42 AS base\n',
            ),
        }
        for path in manifest['paths']:
            before = (FIXTURE / path).read_bytes()
            old, new = replacements[path]
            self.assertEqual(before.count(old), 1, path)
            output = before.replace(old, new, 1)
            if path != 'docs/BUILD.md':
                self.assertEqual(hashlib.sha256(output).hexdigest(), manifest['after'][path]['sha256'])
            else:
                # Production preimage pins remain historical; the public fixture
                # deliberately contains no actual operator procedure.
                self.assertEqual(manifest['before'][path]['sha256'],
                                 '095072d51c802640ef51adf2d0af0af9b14e4707fef249c8bbbf8d3f18cb0d28')
                self.assertEqual(manifest['after'][path]['sha256'],
                                 '234770193cdf05c3b3338087e22927c8479e2cb1465920a6c4fbe22c1e1d6081')
        # Exercise the actual production patch against portable synthetic files.
        with tempfile.TemporaryDirectory(prefix='temurin-patch-') as directory:
            stage = Path(directory)
            for path in manifest['paths']:
                target = stage / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes((FIXTURE / path).read_bytes())
            result = subprocess.run(['git', 'apply', '--unidiff-zero', '-'],
                                    cwd=stage, input=patch, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            for path in manifest['paths']:
                before = (FIXTURE / path).read_bytes()
                old, new = replacements[path]
                self.assertEqual((stage / path).read_bytes(), before.replace(old, new, 1))
        self.assertEqual(manifest['java'], 'Eclipse Temurin 21.0.13+9')
        self.assertEqual(manifest['image']['manifest_digest'],
                         'sha256:f1d8aa86ba3a2a7119b157df01d1832281cd122989cddb46bc86a9f49f99cc42')
        self.assertEqual(manifest['image']['architecture'], 'linux/amd64')
        text = patch.decode()
        self.assertIn('java temurin-21.0.13+9', text)
        self.assertIn('21.0.13_9-jdk-alpine-3.23@sha256:f1d8aa86ba3a2a7119b157df01d1832281cd122989cddb46bc86a9f49f99cc42', text)
        additions = '\n'.join(line[1:] for line in text.splitlines() if line.startswith('+') and not line.startswith('+++'))
        self.assertNotIn('21.0.11_10', additions)
        self.assertNotIn('openjdk-21.0.2', additions)
        self.assertIn('Its JDK is Eclipse Temurin', additions)

    def test_composer_layer_is_application_base_bound_and_native_gate_independent(self):
        source = (ROOT / 'source/compose_security_publication.py').read_text()
        security_record = source.index("record('feature', security_manifest['name']")
        jdk_record = source.index('apply_toolchain_layer(\n        repo, stage, tree, current_main)')
        final_plan = source.index("plan['old_main'] = current_main")
        self.assertLess(security_record, jdk_record)
        self.assertLess(jdk_record, final_plan)
        self.assertIn("manifest.get('application_base_commit') != application_base", source)
        helper_start = source.index('def apply_toolchain_layer(')
        helper_end = source.index('def verify_ordinary_pr_policy(', helper_start)
        helper = source[helper_start:helper_end]
        self.assertNotIn('native_acceptance', helper)
        self.assertNotIn('transition_native_state', helper)

    def test_metadata_is_official_and_does_not_claim_image_execution(self):
        manifest = json.loads(MANIFEST.read_text())
        self.assertTrue(any('Docker Hub API tag metadata' in item for item in manifest['provenance']))
        self.assertTrue(any('setup-java version-file documentation' in item for item in manifest['provenance']))
        self.assertTrue(any('without claiming the Docker image was pulled or run' in item for item in manifest['provenance']))
        self.assertTrue(all(url.startswith('https://') for url in manifest['references']))


if __name__ == '__main__':
    unittest.main()
