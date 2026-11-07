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
        for f in ['a.apk','entry.jar','index-v1.jar','index-v2.json']: (g/f).write_bytes(f.encode())
        original=b.immutable_copy
        def stop(source,dest):
            original(source,dest)
            raise OSError('interrupted')
        with patch('binary_repo.verify_indexes'),patch('binary_repo.immutable_copy',side_effect=stop):
            with self.assertRaises(OSError):b.publish_local(g,o,'f'*64)
        self.assertIsNotNone(b.load(o.parent/(o.name+'.control')/'promotion.json')['active'])
        with patch('binary_repo.verify_indexes'): first=b.publish_local(g,o,'f'*64);second=b.publish_local(g,o,'f'*64)
        self.assertEqual(first,second);self.assertEqual(b.sha(g/'a.apk'),b.sha(o/'a.apk'))
    def test_changed_generation_cannot_replace_interrupted(self):
        g=self.root/'g';g.mkdir();o=self.root/'o';o.mkdir()
        for f in ['a.apk','entry.jar','index-v1.jar','index-v2.json']:(g/f).write_bytes(b'x')
        (o.parent/(o.name+'.control')).mkdir();(o.parent/(o.name+'.control')/'promotion.json').write_text(json.dumps({'active':'other','completed':[]}))
        with patch('binary_repo.verify_indexes'):
            with self.assertRaisesRegex(ValueError,'resume'):b.publish_local(g,o,'f'*64)
    def test_symlink_not_exported(self):
        g=self.root/'g';g.mkdir();(g/'evil.apk').symlink_to('/etc/passwd')
        with patch('binary_repo.verify_indexes'):
            with self.assertRaisesRegex(ValueError,'symlink'):b.publish_local(g,self.root/'o','f'*64)
    def test_r2_plan_only_emits_reviewable_commands(self):
        import runpy,sys,io
        from contextlib import redirect_stdout
        g=self.root/'g';g.mkdir();(g/'a.apk').write_bytes(b'canonical');(g/'entry.jar').write_bytes(b'signed')
        output=io.StringIO()
        with patch.object(sys,'argv',['r2_plan.py',str(g),'--fingerprint','f'*64,'--bucket','test-bucket','--account','testaccount']),patch('binary_repo.verify_indexes'),redirect_stdout(output):
            runpy.run_path(str(Path(b.__file__).with_name('r2_plan.py')),run_name='__main__')
        commands=output.getvalue()
        self.assertIn('max-age=31536000,immutable',commands)
        self.assertIn('no-cache,max-age=0,must-revalidate',commands)
        self.assertLess(commands.index('/a.apk'),commands.index('/entry.jar'))
        self.assertIn('existing object SHA256 must equal',commands)
    def test_unapproved_unknown_field(self):
        p=self.root/'approval';p.write_text(json.dumps({'schema':1,'unexpected':True}))
        with self.assertRaisesRegex(ValueError,'fields'):b.approval(p)
if __name__=='__main__':unittest.main()
