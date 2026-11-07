import json
from pathlib import Path
import subprocess
import tempfile
import unittest
import hosted_eligibility as h
from build import BuildError

ROOT=Path(__file__).resolve().parents[2]


class HostedEligibilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.repo=Path(self.tmp.name)
    def test_current_locked_configuration_is_not_a_hosted_native_build(self):
        result=h.eligibility(ROOT,json.loads((ROOT/'molly-build.json').read_text()))
        self.assertFalse(result['android']);self.assertFalse(result['native_prepare'])
        self.assertEqual(result['status'],'NOT RUN');self.assertIsNotNone(result['native_lock_sha256'])
    def test_legacy_public_dependencies_keep_supported_hosted_route(self):
        config={'signal':{'commit':'a'*40},'android':{'assemble':':app:assembleDebug'}}
        result=h.eligibility(self.repo,config);self.assertTrue(result['android']);self.assertEqual(result['status'],'NOT RUN')
    def test_legacy_native_hotfix_is_still_selected_for_existing_validation(self):
        (self.repo/'legacy.json').write_text(json.dumps({'schema':1,'upstream':{'commit':'a'*40}}))
        result=h.eligibility(self.repo,{'native':'legacy.json','android':{'assemble':':app:assembleDebug'}})
        self.assertTrue(result['native_prepare']);self.assertEqual(result['status'],'NOT RUN')
    def test_multi_artifact_route_is_blocked_independent_of_signal_or_scope_name(self):
        (self.repo/'lock.json').write_text(json.dumps({'source_provenance':{'libsignal':'a'*40},'modules':[{'name':'fixture'}]}))
        result=h.eligibility(self.repo,{'signal':{'commit':'a'*40},'native':'lock.json','android':{'assemble':':app:assembleDebug'}})
        self.assertFalse(result['android']);self.assertEqual(result['status'],'NOT RUN')
    def test_no_android_configuration_does_not_enable_a_build(self):
        self.assertFalse(h.eligibility(self.repo,{})['android'])
    def test_malformed_or_missing_native_inputs_fail_instead_of_skipping_checks(self):
        for contents in ('[]','{"source_provenance":{},"modules":[]}'):
            with self.subTest(contents=contents):
                (self.repo/'lock.json').write_text(contents)
                with self.assertRaises(BuildError):h.eligibility(self.repo,{'native':'lock.json','android':{}})
        with self.assertRaises(BuildError):h.eligibility(self.repo,{'native':'absent.json','android':{}})
    def test_existing_current_port_lock_digest_guard_is_unchanged(self):
        config=json.loads((ROOT/'molly-build.json').read_text());(self.repo/'tools/native').mkdir(parents=True)
        lock=(ROOT/config['native']).read_bytes();(self.repo/config['native']).write_bytes(lock+b' ')
        with self.assertRaisesRegex(BuildError,'differs from its pin'):h.eligibility(self.repo,config)
    def test_cli_records_not_run_and_disabled_job_output_for_actual_configuration(self):
        report=self.repo/'report.json';output=self.repo/'gha-output'
        subprocess.run(['python3',str(ROOT/'tools/patches/hosted_eligibility.py'),'--repo',str(ROOT),'--report',str(report),'--github-output',str(output)],check=True,stdout=subprocess.PIPE,text=True)
        self.assertEqual(json.loads(report.read_text())['status'],'NOT RUN');self.assertEqual(output.read_text(),'android=false\n')
