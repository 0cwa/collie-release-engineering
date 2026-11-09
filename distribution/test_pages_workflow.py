"""Trust-boundary regression checks, not proof of live GitHub protections."""
from pathlib import Path
import re,unittest
class WorkflowBoundaryTest(unittest.TestCase):
    def setUp(self):self.root=Path(__file__).resolve().parents[1]/'.github/workflows'
    def test_pr_checks_have_no_deployment_or_privileged_event(self):
        text=(self.root/'pages-validation.yml').read_text()
        for forbidden in ('pull_request_target','workflow_run','pages: write','id-token: write','environment:','deploy-pages@','secrets.'):
            self.assertNotIn(forbidden,text)
        self.assertIn('persist-credentials: false',text)
    def test_manual_two_phase_gate_and_immutable_action_refs(self):
        text=(self.root/'pages-release.yml').read_text()
        self.assertNotRegex(text,r'(?m)^\s*(pull_request_target|workflow_run)\s*:')
        self.assertNotIn('secrets.',text)
        self.assertIn("github.ref == 'refs/heads/main'",text)
        self.assertIn("vars.COLLIE_PAGES_PRODUCTION_ENABLED == 'true'",text)
        self.assertIn('test "$APPROVED_POLICY" = "$RUN_POLICY"',text)
        self.assertIn('needs: phase1-readback',text)
        self.assertIn("if: ${{ !cancelled() && needs['phase1-readback'].result == 'success' }}",text)
        self.assertIn('pages.py preflight',text)
        self.assertEqual(text.count("          timeout: '1800000'\n"),2)
        for phase in ('phase1','phase2'):
            self.assertIn(f"artifact_name: collie-pages-{phase}\n          timeout: '1800000'",text)
        self.assertEqual(text.count('name: github-pages'),2)
        self.assertEqual(text.count('pages: write'),2)
        self.assertEqual(text.count('id-token: write'),2)
        for action in re.findall(r'uses:\s*(\S+)',text):self.assertRegex(action,r'^actions/[a-z-]+@[0-9a-f]{40}$')
        self.assertEqual(text.count('actions/upload-pages-artifact@5f327015a708106f6f1b7c9a633901af30fe80cb'),2)
        self.assertEqual(text.count('include-hidden-files: true'),2)
        self.assertIn('path: ${{ runner.temp }}/pages/phase1/site',text)
        self.assertIn('path: ${{ runner.temp }}/pages/phase2/site',text)
        self.assertIn("if: inputs.preparation_mode == 'feed'",text)
        self.assertEqual(len(re.findall(r'(?m)^\s+name: collie-pages-phase1$',text)),1)
        self.assertEqual(len(re.findall(r'(?m)^\s+name: collie-pages-phase2$',text)),1)
        self.assertEqual(text.count('artifact_name: collie-pages-phase1'),1)
        self.assertEqual(text.count('artifact_name: collie-pages-phase2'),1)
        self.assertIn('path: ${{ runner.temp }}/control/',text)
