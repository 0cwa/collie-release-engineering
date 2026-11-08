import json
import hashlib
import os
import stat
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

    def test_composer_output_is_owner_only_even_with_permissive_umask(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'draft-plan.json'
            previous_umask = os.umask(0)
            try:
                composer.write_new(output, b'draft plan\n')
            finally:
                os.umask(previous_umask)
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)

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

    def test_renderer_and_template_inputs_are_checked_against_commit_blobs(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            subprocess.run(['git', 'init', '-q', str(repo)], check=True)
            subprocess.run(['git', '-C', str(repo), 'config', 'user.name', 'Fixture'], check=True)
            subprocess.run(['git', '-C', str(repo), 'config', 'user.email', 'fixture@example.invalid'], check=True)
            renderer = repo / 'tools/codeql/render_publication.py'
            template = repo / 'tools/codeql/publication/template.yml.in'
            renderer.parent.mkdir(parents=True)
            template.parent.mkdir(parents=True)
            renderer.write_bytes(b'RENDERER\n')
            template.write_bytes(b'TEMPLATE\n')
            subprocess.run(['git', '-C', str(repo), 'add', '-A'], check=True)
            subprocess.run(['git', '-C', str(repo), 'commit', '-qm', 'Pinned renderer fixture'], check=True)
            revision = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True).strip()
            blobs = {
                path: subprocess.check_output(['git', '-C', str(repo), 'rev-parse', f'HEAD:{path}'], text=True).strip()
                for path in ('tools/codeql/render_publication.py', 'tools/codeql/publication/template.yml.in')
            }
            policy = {
                'revision': revision,
                'renderer': 'tools/codeql/render_publication.py',
                'renderer_blob': blobs['tools/codeql/render_publication.py'],
                'renderer_sha256': hashlib.sha256(b'RENDERER\n').hexdigest(),
                'template_paths': ['tools/codeql/publication/template.yml.in'],
                'template_blobs': {'tools/codeql/publication/template.yml.in': blobs['tools/codeql/publication/template.yml.in']},
                'template_sha256': {'tools/codeql/publication/template.yml.in': hashlib.sha256(b'TEMPLATE\n').hexdigest()},
            }
            composer.verify_policy_objects(repo, policy)
            policy['template_blobs']['tools/codeql/publication/template.yml.in'] = '0' * 40
            with self.assertRaisesRegex(ValueError, 'Git blob differs'):
                composer.verify_policy_objects(repo, policy)

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

    def test_shallow_checkpoint_repository_fails_before_any_input_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            subprocess.run(['git', 'init', '-q', str(repo)], check=True)
            sentinel = repo / 'input-sentinel'
            sentinel.write_bytes(b'unchanged\n')
            git_dir = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', '--absolute-git-dir'], text=True).strip()
            (Path(git_dir) / 'shallow').write_text('1' * 40 + '\n')
            with self.assertRaisesRegex(ValueError, 'shallow history'):
                composer.verify_checkpoint_repository(repo, repo)
            self.assertEqual(sentinel.read_bytes(), b'unchanged\n')

    def test_engineering_checkout_must_match_composer_root_before_input_reads(self):
        with tempfile.TemporaryDirectory() as directory:
            other_checkout = Path(directory) / 'clean-engineering'
            other_checkout.mkdir()
            with mock.patch.object(composer, 'verify_checkpoint_repository') as verify_repo:
                with self.assertRaisesRegex(ValueError, 'engineering checkout must match'):
                    composer.compose(
                        repo=other_checkout / 'app.git', checkpoint_repo=other_checkout / 'app.git',
                        stage=other_checkout / 'stage', engineering=other_checkout,
                        plan_path=other_checkout / 'plan.json', prior_generated='1' * 40,
                        prior_integration='2' * 40, reconstruction_commit='3' * 40,
                        current_main='4' * 40, output_plan=other_checkout / 'out-plan.json',
                        output_messages=other_checkout / 'out-messages.json',
                        output_markdown=other_checkout / 'out-messages.md')
                verify_repo.assert_not_called()

    def test_engineering_root_symlink_alias_is_normalized(self):
        with tempfile.TemporaryDirectory() as directory:
            alias = Path(directory) / 'engineering-alias'
            alias.symlink_to(ROOT, target_is_directory=True)
            self.assertEqual(composer.verify_engineering_root(alias), ROOT.resolve())

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
