import hashlib,json,pathlib,tempfile,unittest
import ndk_payloads
class NdkTest(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=pathlib.Path(self.tmp.name);(self.root/'bin').mkdir();self.file=self.root/'bin/cc';self.file.write_bytes(b'expected');self.file.chmod(0o755);(self.root/'liblink').symlink_to('bin/cc');(self.root/'package.xml').write_text('<repository><localPackage path="ndk;28.0.13004108"/></repository>')
  rows=[{'path':'bin/cc','type':'file','sha256':hashlib.sha256(b'expected').hexdigest(),'executable':True},{'path':'liblink','type':'symlink','target':'bin/cc'}];h=hashlib.sha256()
  for row in sorted(rows,key=lambda r:r['path']):h.update(json.dumps(row,sort_keys=True,separators=(',',':')).encode()+b'\n')
  self.digest=h.hexdigest()
 def check(self):return ndk_payloads.verify(self.root,self.digest,2)
 def test_valid(self):self.assertEqual(2,self.check())
 def test_content_change(self):
  self.file.write_bytes(b'changed!')
  with self.assertRaises(ndk_payloads.Failure):self.check()
 def test_unexpected_file(self):
  (self.root/'extra').write_text('x')
  with self.assertRaises(ndk_payloads.Failure):self.check()
 def test_missing_file(self):
  self.file.unlink()
  with self.assertRaises(ndk_payloads.Failure):self.check()
 def test_group_writable(self):
  self.file.chmod(0o775)
  with self.assertRaises(ndk_payloads.Failure):self.check()
 def test_group_writable_directory(self):
  (self.root/'bin').chmod(0o775)
  with self.assertRaises(ndk_payloads.Failure):self.check()
 def test_nonexecuting_file_extra_execute(self):
  self.file.chmod(0o641)
  with self.assertRaises(ndk_payloads.Failure):self.check()
 def test_symlink_target(self):
  (self.root/'liblink').unlink();(self.root/'liblink').symlink_to('other')
  with self.assertRaises(ndk_payloads.Failure):self.check()
 def test_bad_package_metadata(self):
  (self.root/'package.xml').write_text('<repository><localPackage path="ndk;wrong"/></repository>')
  with self.assertRaises(ndk_payloads.Failure):self.check()
 def test_malformed_metadata_has_clean_failure(self):
  (self.root/'package.xml').write_text('<repository')
  with self.assertRaisesRegex(ndk_payloads.Failure,'metadata is malformed'):self.check()
 def test_metadata_symlink(self):
  (self.root/'package.xml').unlink();(self.root/'package.xml').symlink_to('bin/cc')
  with self.assertRaises(ndk_payloads.Failure):self.check()
if __name__=='__main__':unittest.main()
