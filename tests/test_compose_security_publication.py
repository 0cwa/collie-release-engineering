import json
import hashlib
import sys
from pathlib import Path
import subprocess
import tempfile
from unittest import mock
import unittest

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / 'source'))
import compose_security_publication as composer


class SecurityCompositionTests(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads((ROOT / 'source/layers/security-workflows.json').read_text())

    def test_security_manifest_binds_all_five_outputs_and_publication_order(self):
        composer.validate_security_manifest(self.manifest)
        changed = json.loads(json.dumps(self.manifest))
        changed['ordering']['apply_after'] = 'some other cleanup'
        with self.assertRaisesRegex(ValueError, 'actual publication cleanup'):
            composer.validate_security_manifest(changed)

    def test_security_manifest_rejects_a_missing_canonical_output(self):
        changed = json.loads(json.dumps(self.manifest))
        changed['paths'].pop()
        with self.assertRaisesRegex(ValueError, 'five canonical'):
            composer.validate_security_manifest(changed)

    def test_message_preview_shortens_known_legacy_subjects_without_changing_layer_records(self):
        layer = 'Resolve modify/delete conflicts for modules Molly removes and remove new upstream files added beneath those excluded modules'
        messages = {'layers': [{'subject': layer, 'changes': layer}]}
        composer.shorten_message_subjects(messages)
        self.assertEqual(messages['layers'][0]['subject'], 'Resolve excluded-module modify/delete conflicts')
        self.assertEqual(messages['layers'][0]['changes'], layer)

    def test_publication_marker_digest_is_checked(self):
        oid = '1' * 40
        with mock.patch.object(composer, 'tree_blob', return_value=('100644', 'blob', oid, '.github/workflows/source.yml')):
            with mock.patch.object(composer, 'git', return_value=b'wrong source workflow'):
                with mock.patch.object(composer, 'tree_entries', return_value=[]):
                    with self.assertRaisesRegex(ValueError, 'preimage'):
                        composer.verify_publication_preimage('repo', 'tree', self.manifest)

    def test_security_layer_rejects_an_output_that_survived_publication_cleanup(self):
        oid = '1' * 40
        workflow = b'name: Direct source checks\n'
        manifest = json.loads(json.dumps(self.manifest))
        manifest['ordering']['publication_output_sha256'] = hashlib.sha256(workflow).hexdigest()
        with mock.patch.object(composer, 'tree_blob', return_value=('100644', 'blob', oid, '.github/workflows/source.yml')):
            with mock.patch.object(composer, 'git', return_value=workflow):
                with mock.patch.object(composer, 'tree_entries', side_effect=lambda _repo, _tree, path: [('100644', 'blob', oid, path)] if path == self.manifest['paths'][0] else []):
                    with self.assertRaisesRegex(ValueError, 'already exists after publication cleanup'):
                        composer.verify_publication_preimage('repo', 'tree', manifest)

    def test_release_gate_and_version_properties_are_required(self):
        tree = 'a' * 40
        def show(_repo, _command, spec):
            if spec.endswith(':BUILD.md'):
                return (b'releaseVersionCode and releaseVersionName are normal source-defined Gradle properties '
                        b'DRAFT candidate proposal:** 8.35.1-2-rc1 / 177703. This value is not allocated')
            if spec.endswith(':gradle.properties'):
                return b'releaseVersionCode=177703\nreleaseVersionName=8.35.1-2-rc1\nDRAFT proposal only; not reserved'
            return (b'providers.gradleProperty("releaseVersionCode") '
                    b'providers.gradleProperty("releaseVersionName") '
                    b'throw GradleException("Direct Android builds BLOCKED:')
        with mock.patch.object(composer, 'git', side_effect=show):
            composer.verify_release_metadata('repo', tree)
        with mock.patch.object(composer, 'git', return_value=b'no direct build gate'):
            with self.assertRaisesRegex(ValueError, 'omitted the explicit DRAFT version proposal'):
                composer.verify_release_metadata('repo', tree)

    def test_ordinary_application_pr_policy_passes_without_a_release_tree_pin(self):
        workflow = (ROOT / 'source/layers/publication.py').read_text()
        with tempfile.TemporaryDirectory() as directory:
            stage = Path(directory)
            subprocess.run(['git', 'init', '-q', str(stage)], check=True)
            (stage / 'app').mkdir()
            (stage / 'app/build.gradle.kts').write_text('// ordinary application PR\n')
            with mock.patch.object(composer, 'git', return_value=workflow.encode()):
                composer.verify_ordinary_pr_policy('repo', 'tree', stage)
        self.assertIn('pull_request', workflow)
        self.assertNotIn('publication_plan_sha256', workflow)


if __name__ == '__main__':
    unittest.main()
