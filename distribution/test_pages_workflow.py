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
        self.assertIn('needs: phase1-readback',text)
        self.assertIn('pages.py preflight',text)
        self.assertEqual(text.count('name: github-pages'),2)
        for action in re.findall(r'uses:\s*(\S+)',text):self.assertRegex(action,r'^actions/[a-z-]+@[0-9a-f]{40}$')
        self.assertNotIn('upload-pages-artifact@',text)
