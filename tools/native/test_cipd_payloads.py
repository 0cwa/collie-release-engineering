import base64,hashlib,json,os,pathlib,tempfile,unittest
import cipd_payloads
from unittest.mock import patch
class PayloadTest(unittest.TestCase):
 def setUp(self):
  self.t=tempfile.TemporaryDirectory();self.addCleanup(self.t.cleanup);self.base=pathlib.Path(self.t.name);self.root=self.base/'src/pkg';self.root.mkdir(parents=True)
  self.ident='A'*43+'C';self.name='fixture/pkg';(self.root/'bin').write_bytes(b'expected');(self.root/'bin').chmod(0o555);(self.root/'link').symlink_to('bin');(self.root/'version').write_text(json.dumps({'package_name':self.name,'instance_id':self.ident}));(self.root/'version').chmod(0o444)
  def file(name,executable=False):
   data=(self.root/name).read_bytes();return {'name':name,'size':len(data),'executable':executable,'hash':base64.urlsafe_b64encode(hashlib.sha256(data).digest()+b'\x02').decode().rstrip('=')}
  self.manifest={'package_name':self.name,'version_file':'version','files':[file('bin',True),{'name':'link','size':0,'symlink':'bin'},file('version')]};self.meta=self.base/'.cipd/pkgs/0';(self.meta/self.ident/'.cipdpkg').mkdir(parents=True);(self.meta/'_current').symlink_to(self.ident);(self.meta/'description.json').write_text(json.dumps({'subdir':'src/pkg','package_name':self.name}));self.manpath=self.meta/self.ident/'.cipdpkg/manifest.json';self.manpath.write_text(json.dumps(self.manifest));row={'subdir':'src/pkg','package':self.name,'instance':self.ident,'manifest_sha256':hashlib.sha256(self.manpath.read_bytes()).hexdigest(),'method':'reconstructed-archive'};self.ref=self.base/'ref.json';self.ref.write_text(json.dumps({'schema':1,'packages':[row]}));self.hash=hashlib.sha256(self.ref.read_bytes()).hexdigest()
 def check(self):return cipd_payloads.verify(self.base,self.ref,self.hash)
 def test_valid(self):self.assertEqual((1,3),self.check())
 def test_modified_same_size_payload(self):
  (self.root/'bin').chmod(0o755);(self.root/'bin').write_bytes(b'changed!');(self.root/'bin').chmod(0o555)
  with self.assertRaises(cipd_payloads.Failure):self.check()
 def test_wrong_mode(self):
  (self.root/'bin').chmod(0o644)
  with self.assertRaises(cipd_payloads.Failure):self.check()
 def test_wrong_symlink(self):
  (self.root/'link').unlink();(self.root/'link').symlink_to('other')
  with self.assertRaises(cipd_payloads.Failure):self.check()
 def test_undeclared_file_rejected(self):
  (self.root/'unexpected').write_text('not declared')
  with self.assertRaises(cipd_payloads.Failure):self.check()
 def test_undeclared_directory_rejected(self):
  (self.root/'unexpected').mkdir()
  with self.assertRaises(cipd_payloads.Failure):self.check()
 def test_group_or_other_writable_rejected(self):
  (self.root/'bin').chmod(0o777)
  with self.assertRaises(cipd_payloads.Failure):self.check()
 def test_nonexecuting_file_extra_execute_rejected(self):
  (self.root/'version').chmod(0o445)
  with self.assertRaises(cipd_payloads.Failure):self.check()
 def test_supplemental_source_rejects_oversized_file(self):
  p=self.root/'supplemental';p.write_bytes(b'x'*(1024*1024+1));digest=hashlib.sha256(p.read_bytes()).hexdigest()
  with patch.object(cipd_payloads,'SUPPLEMENTAL',{'src/pkg/supplemental':digest}):
   with self.assertRaises(cipd_payloads.Failure):self.check()
 def test_manifest_tamper(self):
  self.manpath.write_text('{}')
  with self.assertRaises(cipd_payloads.Failure):self.check()
 def test_version_file_tamper(self):
  (self.root/'version').chmod(0o644);(self.root/'version').write_text('{}');(self.root/'version').chmod(0o444)
  with self.assertRaises(cipd_payloads.Failure):self.check()
 def test_reference_tamper(self):
  self.ref.write_text('{}')
  with self.assertRaises(cipd_payloads.Failure):self.check()
 def test_missing_package(self):
  (self.meta/'description.json').unlink()
  with self.assertRaises(cipd_payloads.Failure):self.check()
 def test_symlinked_package_parent(self):
  self.root.rename(self.root.with_name('other'));self.root.symlink_to(self.root.with_name('other'),target_is_directory=True)
  with self.assertRaises(cipd_payloads.Failure):self.check()
if __name__=='__main__':unittest.main()
