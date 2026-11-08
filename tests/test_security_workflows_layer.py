import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).parents[1]
MANIFEST_PATH = ROOT / 'source/layers/security-workflows.json'
PATCH_PATH = ROOT / 'source/layers/security-workflows.patch'
PUBLICATION_PATH = ROOT / 'source/layers/publication.py'
EXPECTED_PATHS = {
    '.github/workflows/codeql.yml',
    '.github/workflows/update-screenshots.yml',
    '.github/workflows/publish-screenshots.yml',
    '.github/codeql/codeql-config.yml',
    '.github/codeql/reviewed-findings.json',
}
POLICY_REVISION = '124ca006b36c64e12807db2d1737d7f1faf06471'


def load_publication():
    spec = importlib.util.spec_from_file_location('publication_workflow_fixture', PUBLICATION_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SecurityWorkflowFeatureLayerTests(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads(MANIFEST_PATH.read_text())
        self.patch = PATCH_PATH.read_bytes()
        self.patch_text = self.patch.decode()

    def output_text(self, path):
        escaped = re.escape(path)
        section = re.search(rf'(?ms)^diff --git a/{escaped} b/{escaped}\n(.*?)(?=^diff --git |\Z)', self.patch_text)
        if section is None:
            self.fail(f'missing patch output: {path}')
        return '\n'.join(line[1:] for line in section.group(1).splitlines()
                           if line.startswith('+') and not line.startswith('+++'))

    def test_manifest_binds_immutable_renderer_all_outputs_and_ordering(self):
        policy = self.manifest['policy']
        ordering = self.manifest['ordering']
        self.assertEqual(self.manifest['schema'], 1)
        self.assertEqual(self.manifest['kind'], 'feature-layer')
        self.assertEqual(self.manifest['application_base'], 'f636568af237afa0161700a09b94b09f2b70d6f6')
        self.assertEqual(policy['origin'], 'https://github.com/0cwa/collie-release-engineering.git')
        self.assertEqual(policy['revision'], POLICY_REVISION)
        self.assertEqual(policy['renderer'], 'tools/codeql/render_publication.py')
        self.assertRegex(policy['renderer_blob'], r'^[0-9a-f]{40}$')
        self.assertRegex(policy['renderer_sha256'], r'^[0-9a-f]{64}$')
        self.assertEqual(set(policy['template_paths']), {
            'tools/codeql/publication/codeql.yml.in',
            'tools/codeql/publication/update-screenshots.yml.in',
            'tools/codeql/publication/publish-screenshots.yml.in',
            'tools/codeql/publication/codeql-config.yml',
            'tools/codeql/publication/reviewed-findings.json',
        })
        self.assertEqual(set(policy['template_blobs']), set(policy['template_paths']))
        self.assertEqual(set(policy['template_sha256']), set(policy['template_paths']))
        self.assertTrue(all(re.fullmatch(r'[0-9a-f]{40}', value) for value in policy['template_blobs'].values()))
        self.assertTrue(all(re.fullmatch(r'[0-9a-f]{64}', value) for value in policy['template_sha256'].values()))
        self.assertEqual(self.manifest['modes'], {path: '100644' for path in EXPECTED_PATHS})
        self.assertEqual(self.manifest['paths'], sorted(EXPECTED_PATHS))
        self.assertEqual(ordering['apply_after'], 'source/layers/publication.py::replace_application_workflows')
        self.assertEqual(ordering['publication_output_path'], '.github/workflows/source.yml')
        self.assertRegex(ordering['publication_output_sha256'], r'^[0-9a-f]{64}$')
        self.assertEqual(set(ordering['requires_absent_paths']), EXPECTED_PATHS)
        self.assertIn('not complete reconstructed application G/I', self.manifest['tree_scope'])
        self.assertNotIn('source_g', self.manifest)
        self.assertNotIn('source_commit', self.manifest)

    def test_publication_replacement_then_layer_replays_exact_fixture_tree(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflows = root / '.github/workflows'
            workflows.mkdir(parents=True)
            legacy = workflows / 'update-screenshots.yml'
            legacy.write_text('name: Legacy screenshot updater\npermissions:\n  contents: write\n')
            publication = load_publication()
            publication.replace_application_workflows(root)
            source_workflow = root / '.github/workflows/source.yml'
            self.assertTrue(source_workflow.is_file())
            self.assertEqual(hashlib.sha256(source_workflow.read_bytes()).hexdigest(),
                             self.manifest['ordering']['publication_output_sha256'])
            self.assertFalse(legacy.exists())

            subprocess.run(['git', 'init', '-q', str(root)], check=True)
            subprocess.run(['git', '-C', str(root), 'add', '-A'], check=True)
            before = subprocess.check_output(['git', '-C', str(root), 'write-tree'], text=True).strip()
            self.assertEqual(before, self.manifest['before_tree'])
            subprocess.run(['git', '-C', str(root), 'apply', '--binary', str(PATCH_PATH)], check=True)
            subprocess.run(['git', '-C', str(root), 'add', '-A'], check=True)
            after = subprocess.check_output(['git', '-C', str(root), 'write-tree'], text=True).strip()
            self.assertEqual(after, self.manifest['after_tree'])
            self.assertEqual(hashlib.sha256(source_workflow.read_bytes()).hexdigest(),
                             self.manifest['ordering']['publication_output_sha256'])
            actual_paths = {str(path.relative_to(root)) for path in root.rglob('*') if path.is_file() and '.git' not in path.parts}
            self.assertEqual(actual_paths, EXPECTED_PATHS | {'.github/workflows/source.yml'})
            self.assertEqual({line for line in self.patch_text.splitlines() if line.startswith('diff --git ')}, {
                f'diff --git a/{path} b/{path}' for path in EXPECTED_PATHS
            })
            for path in EXPECTED_PATHS:
                self.assertEqual(hashlib.sha256((root / path).read_bytes()).hexdigest(), self.manifest['files'][path])

    def test_layer_is_additive_and_policy_preserves_security_and_screenshot_guards(self):
        self.assertEqual(hashlib.sha256(self.patch).hexdigest(), self.manifest['patch_sha256'])
        self.assertEqual(self.manifest['patch'], 'source/layers/security-workflows.patch')
        self.assertEqual(set(re.findall(r'(?m)^new file mode (\d+)$', self.patch_text)), {'100644'})
        self.assertNotRegex(self.patch_text, r'(?m)^deleted file mode ')
        self.assertNotRegex(self.patch_text, r'(?m)^old mode ')
        additions = '\n'.join(line[1:] for line in self.patch_text.splitlines()
                               if line.startswith('+') and not line.startswith('+++'))
        for required in (
            'name: Published workflow CodeQL',
            'pull_request:',
            'push:',
            'languages: actions',
            'build-mode: none',
            'persist-credentials: false',
            'Java/Kotlin and native-dependency source analysis: NOT RUN',
            'security-extended',
            'Generate Screenshot Data',
            'Publish Reviewed Screenshot Data',
            "github.event.comment.body == '!screenshots'",
            "head['repo']['full_name'] != os.environ['GITHUB_REPOSITORY']",
            'github.ref_name != \'main\'',
            'cache-disabled: true',
            'SIGNAL_BUILD_CACHE_PUSH: false',
            "if: github.ref == 'refs/heads/main'",
            'actions: read',
            'contents: write',
            'artifact_id',
            'run_id',
            'attempt',
        ):
            with self.subTest(required=required):
                self.assertIn(required, additions)
        for forbidden in ('languages: java-kotlin', 'languages: kotlin', 'languages: java',
                          'sourceG', 'GITHUB_TOKEN:', 'contents: write\ncache-mode:'):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, additions)
        self.assertIn('GitHub Actions only', self.manifest['coverage'])
        self.assertIn('NOT RUN', self.manifest['coverage'])
        for path in ('.github/workflows/codeql.yml',
                     '.github/workflows/update-screenshots.yml',
                     '.github/workflows/publish-screenshots.yml'):
            with self.subTest(path=path):
                content = self.output_text(path)
                self.assertIn(POLICY_REVISION, content)
                self.assertNotIn('__ENGINEERING_POLICY_REVISION__', content)
                self.assertNotIn('pull_request_target:', content)
                self.assertNotIn('workflow_run:', content)
        codeql = self.output_text('.github/workflows/codeql.yml')
        screenshot_generation = self.output_text('.github/workflows/update-screenshots.yml')
        screenshot_publish = self.output_text('.github/workflows/publish-screenshots.yml')
        self.assertIn('languages: actions', codeql)
        self.assertNotIn('contents: write', codeql)
        self.assertNotIn('contents: write', screenshot_generation)
        self.assertIn("if: github.ref == 'refs/heads/main'", screenshot_publish)
        self.assertIn('contents: write', screenshot_publish)


if __name__ == '__main__':
    unittest.main()
