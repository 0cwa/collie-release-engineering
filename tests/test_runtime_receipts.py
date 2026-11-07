import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).parents[1]/'tools/patches'))
import runtime_receipts as r


class RuntimePlanTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.manifest={'schema':1,'commit':'a'*40,'workflow_revision':'a'*40,'package':'im.collie.app','version_code':177703,'version_name':'fixture','certificate_sha256':'b'*64}
    def args(self,plan):
        path=self.root/'plan.json';r.write(path,plan)
        return argparse.Namespace(release_plan=path,approved_release_plan_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    def test_legacy_adapter_is_explicit_and_does_not_fabricate_receipts(self):
        plan=r.import_legacy(self.manifest,'c'*40,'fixture');self.assertEqual(r.plan(self.args(plan))[0],plan)
        self.assertEqual(plan['mode'],'legacy');self.assertIsNone(plan['source_receipt_sha256'])
        self.assertIsNone(plan['build_inputs_sha256']);self.assertEqual(plan['required_test_reports'],[])
    def test_no_implicit_legacy_route_even_for_rehearsal_new_evidence(self):
        args=argparse.Namespace(release_plan=None)
        with self.assertRaises(ValueError):r.plan(args)
        self.assertIsNone(r.plan(args,historical_rehearsal=True))
        r.check_manifest(None,self.manifest)
        for addition in ({'schema':2},{'release_mode':'new-source'},{'source_receipt_sha256':'a'*64}):
            changed={**self.manifest,**addition}
            with self.assertRaises(ValueError):r.check_manifest(None,changed)
            with self.assertRaises(ValueError):r.import_legacy(changed,'c'*40,'fixture')
    def test_legacy_cannot_strip_new_receipts_or_independent_identities(self):
        for change in ({'workflow_revision':'d'*40},{'release_plan_sha256':'b'*64}):
            with self.assertRaises(ValueError):r.import_legacy({**self.manifest,**change},'c'*40,'production')
        plan=r.import_legacy(self.manifest,'c'*40,'fixture');plan['source_receipt_sha256']='a'*64
        with self.assertRaises(ValueError):r.plan(self.args(plan))
    def test_new_source_requires_complete_stable_inputs_and_named_reports(self):
        plan={**r.import_legacy(self.manifest,'c'*40,'fixture'),'mode':'new-source','workflow_revision':'d'*40,'source_receipt_sha256':'e'*64,'build_inputs_sha256':'f'*64,'required_test_reports':['actual-report']}
        self.assertEqual(r.plan(self.args(plan))[0],plan)
        for key,value in [('source_receipt_sha256',None),('build_inputs_sha256',None),('required_test_reports',[]),('required_test_reports',['duplicate','duplicate'])]:
            with self.assertRaises(ValueError):r.plan(self.args({**plan,key:value}))
        args=self.args(plan);args.repo='0cwa/collieim-android'
        with self.assertRaises(ValueError):r.plan(args)
    def test_unapproved_digest_and_duplicate_fields_rejected(self):
        args=self.args(r.import_legacy(self.manifest,'c'*40,'fixture'));args.approved_release_plan_sha256='0'*64
        with self.assertRaises(ValueError):r.plan(args)
        path=self.root/'dup.json';path.write_text('{"schema":1,"schema":2}')
        with self.assertRaises(ValueError):r.document(path)
