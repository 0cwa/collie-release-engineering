import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).parents[1]/'tools/patches'))
import coordinated_promotion as m

class PromotionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.plan={'schema':1,'release_id':1,'tag':'fixture','release_manifest_sha256':'a'*64,'apk_sha256':'b'*64,'apk_certificate_sha256':'c'*64,'package':'im.collie.app','version_code':177702,'repository_generation_sha256':'d'*64}
        self.github={k:v for k,v in self.plan.items() if k!='repository_generation_sha256'}
        self.github.update(endpoint='github',status='PASS',published=True,aliases={name:'b'*64 for name in ['collie.apk','collie-foss.apk','molly-continued.apk','molly-foss-continued.apk','molly-minimal.apk']})
        self.repo=dict(self.plan,endpoint='repository',status='PASS',index_signatures_verified=True)
    def write(self,name,data):
        p=self.root/name;p.write_text(json.dumps(data));return p,hashlib.sha256(p.read_bytes()).hexdigest()
    def run_phase(self,endpoint):
        plan,digest=self.write('plan.json',self.plan);report,rd=self.write(endpoint+'.json',self.github if endpoint=='github' else self.repo)
        return m.observe(self.root/'promotion.json',plan,digest,endpoint,report,rd)
    def test_resumes_after_github_without_rebuilding(self):
        one=self.run_phase('github');self.assertEqual(one,self.run_phase('github'))
        self.assertEqual(self.run_phase('repository')['phase'],'complete')
        self.assertEqual(self.run_phase('repository')['phase'],'complete')
    def test_input_substitution_and_incomplete_readback_rejected(self):
        self.run_phase('github');self.plan['apk_sha256']='e'*64;self.repo['apk_sha256']='e'*64
        with self.assertRaisesRegex(ValueError,'original immutable'):self.run_phase('repository')
    def test_alias_or_signature_tamper_rejected(self):
        self.github['aliases']['collie.apk']='e'*64
        with self.assertRaisesRegex(ValueError,'alias'):self.run_phase('github')
        self.github['aliases']['collie.apk']='b'*64;self.run_phase('github');self.repo['index_signatures_verified']=False
        with self.assertRaisesRegex(ValueError,'signature'):self.run_phase('repository')
    def test_interruption_before_journal_commit_retries_same_bytes(self):
        with patch.object(m.os,'replace',side_effect=OSError('interruption')):
            with self.assertRaises(OSError):self.run_phase('github')
        self.assertEqual(self.run_phase('github')['phase'],'github-verified')
        self.assertEqual(self.run_phase('repository')['phase'],'complete')
    def test_repository_cannot_hide_unverified_github(self):
        with self.assertRaisesRegex(ValueError,'GitHub'):self.run_phase('repository')
