import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import binary_repo as b

class BinaryRepositoryTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
    def test_immutable_retry_and_substitution(self):
        s=self.root/'source.apk';d=self.root/'repo/a.apk';s.write_bytes(b'approved')
        b.immutable_copy(s,d);b.immutable_copy(s,d);s.write_bytes(b'replacement')
        with self.assertRaisesRegex(ValueError,'immutable'): b.immutable_copy(s,d)
        self.assertEqual(d.read_bytes(),b'approved')
    def test_duplicate_approval_fields_rejected(self):
        p=self.root/'a.json';p.write_text('{"schema":1,"schema":1}')
        with self.assertRaisesRegex(ValueError,'duplicate'):b.load(p)
    def test_signature_failure_stops_metadata(self):
        import subprocess
        with patch('binary_repo.subprocess.check_output',side_effect=subprocess.CalledProcessError(1,'apksigner')):
            with self.assertRaises(subprocess.CalledProcessError): b.inspect('x','aapt','apksigner')
    def test_partial_publish_resumes_same_generation(self):
        g=self.root/'generation';g.mkdir();o=self.root/'origin'
        for f in ['im.collie.app_1.apk','entry.jar','index-v1.jar','index-v2.json']: (g/f).write_bytes(f.encode())
        original=b.immutable_copy
        def stop(source,dest,expected=None):
            original(source,dest,expected)
            raise OSError('interrupted')
        with patch('binary_repo.approved_generation',return_value=(b.generation_digest(g),{})),patch('binary_repo.immutable_copy',side_effect=stop):
            with self.assertRaises(OSError):b.publish_local(g,o,'f'*64,b.generation_digest(g),[])
        self.assertIsNotNone(b.load(o.parent/(o.name+'.control')/'promotion.json')['active'])
        with patch('binary_repo.approved_generation',return_value=(b.generation_digest(g),{})),patch('binary_repo.verify_indexes'): first=b.publish_local(g,o,'f'*64,b.generation_digest(g),[]);second=b.publish_local(g,o,'f'*64,b.generation_digest(g),[])
        self.assertEqual(first,second);self.assertEqual(b.sha(g/'im.collie.app_1.apk'),b.sha(o/'im.collie.app_1.apk'))
    def test_changed_generation_cannot_replace_interrupted(self):
        g=self.root/'g';g.mkdir();o=self.root/'o';o.mkdir()
        for f in ['im.collie.app_1.apk','entry.jar','index-v1.jar','index-v2.json']:(g/f).write_bytes(b'x')
        (o.parent/(o.name+'.control')).mkdir();(o.parent/(o.name+'.control')/'promotion.json').write_text(json.dumps({'active':'other','completed':[]}))
        with patch('binary_repo.approved_generation',return_value=(b.generation_digest(g),{})):
            with self.assertRaisesRegex(ValueError,'resume'):b.publish_local(g,o,'f'*64,b.generation_digest(g),[])
    def test_symlink_not_exported(self):
        g=self.root/'g';g.mkdir();(g/'evil.apk').symlink_to('/etc/passwd')
        with patch('binary_repo.verify_indexes'):
            with self.assertRaisesRegex(ValueError,'symlink'):b.publish_local(g,self.root/'o','f'*64,'f'*64,[])
    def test_r2_plan_only_emits_reviewable_commands(self):
        import runpy,sys,io
        from contextlib import redirect_stdout
        g=self.root/'g';g.mkdir();(g/'im.collie.app_1.apk').write_bytes(b'canonical');(g/'entry.jar').write_bytes(b'signed');(g/'index-v1.jar').write_bytes(b'signed');(g/'index-v2.json').write_bytes(b'{}')
        output=io.StringIO()
        with patch.object(sys,'argv',['r2_plan.py',str(g),'--fingerprint','f'*64,'--bucket','test-bucket','--account','testaccount','--approved-generation',b.generation_digest(g),'--approval','fixture.json','--scratch',str(self.root)]),patch('binary_repo.approved_generation'),redirect_stdout(output):
            runpy.run_path(str(Path(b.__file__).with_name('r2_plan.py')),run_name='__main__')
        commands=output.getvalue()
        self.assertIn('max-age=31536000,immutable',commands)
        self.assertIn('no-cache,max-age=0,must-revalidate',commands)
        self.assertLess(commands.index('/im.collie.app_1.apk'),commands.index('/entry.jar'))
        self.assertIn('existing object SHA256 must equal',commands)
    def test_source_mutation_during_approved_copy_rejected(self):
        g=self.root/'g';g.mkdir();o=self.root/'o'
        for name in ['im.collie.app_1.apk','entry.jar','index-v1.jar','index-v2.json']:(g/name).write_bytes(b'approved')
        expected=b.generation_digest(g);original=b.immutable_copy
        def mutate(source,dest,pinned=None):
            source.write_bytes(b'replaced')
            return original(source,dest,pinned)
        with patch('binary_repo.approved_generation',return_value=(expected,{})),patch('binary_repo.immutable_copy',side_effect=mutate):
            with self.assertRaisesRegex(ValueError,'copy digest'):b.publish_local(g,o,'f'*64,expected,[])
        self.assertFalse((o/'im.collie.app_1.apk').exists())
    def test_r2_upload_uses_private_pinned_snapshot(self):
        import upload_r2
        source=self.root/'object';source.write_bytes(b'approved');expected=b.sha(source)
        def fake_aws(command,check):
            source.write_bytes(b'mutated')
            self.assertEqual(Path(command[5]).read_bytes(),b'approved')
        with patch('upload_r2.subprocess.run',side_effect=fake_aws):
            upload_r2.upload(source,expected,'https://testaccount.r2.cloudflarestorage.com','s3://bucket/key','no-cache',self.root)
        with self.assertRaisesRegex(ValueError,'upload bytes mismatch'):
            upload_r2.upload(source,expected,'https://testaccount.r2.cloudflarestorage.com','s3://bucket/key','no-cache',self.root)
    def indexes(self):
        row={'manifest':{'versionCode':1,'versionName':'1','signer':{'sha256':['c'*64]}},'file':{'name':'/im.collie.app_1.apk','sha256':'a'*64}}
        data={'packages':{b.APP:{'versions':{'a'*64:row}}}}
        legacy={'apps':[{'packageName':b.APP}],'packages':{b.APP:[{'versionCode':1,'versionName':'1','signer':'c'*64,'hash':'a'*64,'hashType':'sha256','apkName':'im.collie.app_1.apk'}]}}
        return data,legacy
    def test_v1_v2_parity_rejects_crossed_signed_release(self):
        data,legacy=self.indexes();b.validate_index_parity(data,legacy)
        for field,value in [('hash','b'*64),('versionCode',2),('signer','b'*64),('apkName','wrong.apk')]:
            import copy
            changed=copy.deepcopy(legacy);changed['packages'][b.APP][0][field]=value
            with self.assertRaisesRegex(ValueError,'v1/v2'):b.validate_index_parity(data,changed)
    def test_expected_generation_cannot_be_substituted(self):
        g=self.root/'g';g.mkdir()
        for name in ['entry.jar','index-v1.jar','index-v2.json']:(g/name).write_bytes(b'x')
        with self.assertRaisesRegex(ValueError,'generation mismatch'):b.approved_generation(g,'f'*64,'0'*64,[])
    def test_signed_unapproved_release_rejected(self):
        g=self.root/'g';g.mkdir()
        for name in ['entry.jar','index-v1.jar','index-v2.json']:(g/name).write_bytes(b'x')
        data,_=self.indexes()
        with patch('binary_repo.verify_indexes',return_value=data):
            with self.assertRaisesRegex(ValueError,'unapproved indexed'):b.approved_generation(g,'f'*64,b.generation_digest(g),[])
    def test_private_file_rejected_before_r2_plan(self):
        g=self.root/'g';g.mkdir();(g/'keystore.p12').write_bytes(b'private')
        with self.assertRaisesRegex(ValueError,'unexpected public file'):b.public_files(g)
    def test_alternate_apk_certificate_cannot_be_approved(self):
        metadata=(Path(__file__).parent/'metadata/im.collie.app.yml').read_text()
        self.assertIn("AllowedAPKSigningKeys:\n  - '"+b.APK_CERTIFICATE+"'",metadata)
        self.assertNotIn('@APK_CERTIFICATE@',metadata)
        a=b.load(Path(__file__).parent/'approval.fixture.json');a['apk_certificate_sha256']='0'*64
        path=self.root/'alternate-signer.json';path.write_text(json.dumps(a))
        with self.assertRaisesRegex(ValueError,'signing certificate differs'):b.approval(path)
    def test_boolean_schema_is_not_legacy_integer_one(self):
        a=b.load(Path(__file__).parent/'approval.fixture.json');a['schema']=True
        path=self.root/'boolean.json';path.write_text(json.dumps(a))
        with self.assertRaisesRegex(ValueError,'fields/schema'):b.approval(path)
    def test_unapproved_unknown_field(self):
        p=self.root/'approval';p.write_text(json.dumps({'schema':1,'unexpected':True}))
        with self.assertRaisesRegex(ValueError,'fields'):b.approval(p)
if __name__=='__main__':unittest.main()
