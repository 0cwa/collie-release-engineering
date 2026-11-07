import hashlib,json,pathlib,tempfile,unittest
import rust_payloads
class PayloadTest(unittest.TestCase):
 def setUp(self):
  self.t=tempfile.TemporaryDirectory();self.addCleanup(self.t.cleanup);self.base=pathlib.Path(self.t.name);self.tool=self.base/'tool';(self.tool/'bin').mkdir(parents=True);self.bin=self.tool/'bin/rustc';self.bin.write_bytes(b'expected');self.bin.chmod(0o755)
  entry={'component':'rustc','path':'bin/rustc','type':'file','sha256':hashlib.sha256(b'expected').hexdigest(),'executable':True};self.ref=self.base/'ref.json';self.ref.write_text(json.dumps({'entries':[entry]}));self.hash=hashlib.sha256(self.ref.read_bytes()).hexdigest()
 def verify(self):return rust_payloads.verify(self.tool,self.ref,self.hash)
 def test_valid(self):self.assertEqual(1,self.verify())
 def test_byte_change(self):
  self.bin.write_bytes(b'changed!')
  with self.assertRaises(rust_payloads.Failure):self.verify()
 def test_writable_by_other(self):
  self.bin.chmod(0o777)
  with self.assertRaises(rust_payloads.Failure):self.verify()
 def test_reference_change(self):
  self.ref.write_text('{}')
  with self.assertRaises(rust_payloads.Failure):self.verify()
 def test_symlinked_file(self):
  self.bin.unlink();self.bin.symlink_to('/bin/true')
  with self.assertRaises(rust_payloads.Failure):self.verify()
 def test_symlinked_parent(self):
  (self.tool/'bin').rename(self.tool/'other');(self.tool/'bin').symlink_to('other',target_is_directory=True)
  with self.assertRaises(rust_payloads.Failure):self.verify()
 def test_extra_files_are_not_claimed(self):
  (self.tool/'bin/unverified-extra').write_text('not covered')
  self.assertEqual(1,self.verify())
if __name__=='__main__':unittest.main()
