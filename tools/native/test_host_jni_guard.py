import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import host_jni_guard as subject

class HostJniGuardTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='host-jni-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.lock = self.root/'lock.json'; self.lock.write_text('{}')
        self.sources = self.root/'sources'; self.sources.mkdir()
        self.lib = self.root/'lib.so'
        self.lib.write_bytes(b'\x7fELF\x02\x01'+b'\x00'*10+b'\x03\x00\x3e\x00'+b'fixture')
        self.sha = hashlib.sha256(self.lib.read_bytes()).hexdigest()
        self.output = self.root/'stage'
        self.proof = {'sources':{'libsignal':{'prepared_tree':'tree'}}}
        self.patcher = patch.object(subject.prepare_sources, 'verify_prepared', return_value=self.proof)
        self.patcher.start(); self.addCleanup(self.patcher.stop)

    def stage(self): return subject.stage(self.lock,self.sources,self.lib,self.sha,self.output)
    def verify(self): return subject.verify(self.lock,self.sources,self.output,self.sha)

    def test_stage_is_test_only_and_independently_checked(self):
        self.assertFalse(self.stage()['release_allowed'])
        self.assertFalse(self.verify()['release_allowed'])
        self.assertNotEqual(self.lib.stat().st_ino,(self.output/'resource/libsignal_jni_testing_amd64.so').stat().st_ino)
        self.lib.write_bytes(b'changed after snapshot')
        self.assertFalse(self.verify()['release_allowed'])

    def test_forged_script_and_receipt_are_rejected(self):
        self.stage()
        script=self.output/'host-jni.init.gradle'; script.write_text(script.read_text()+'// forged\n')
        receipt=self.output/'receipt.json'; data=json.loads(receipt.read_text())
        data['init_sha256']=hashlib.sha256(script.read_bytes()).hexdigest();receipt.write_text(json.dumps(data))
        with self.assertRaisesRegex(subject.Failure,'init script differs'): self.verify()

    def test_foreign_fallback_and_resource_are_rejected(self):
        self.stage(); (self.output/'empty-library-path/stock.so').write_bytes(b'bad')
        with self.assertRaisesRegex(subject.Failure,'not empty'): self.verify()
        (self.output/'empty-library-path/stock.so').unlink()
        (self.output/'resource/other.so').write_bytes(b'bad')
        with self.assertRaisesRegex(subject.Failure,'Unexpected'): self.verify()

    def test_host_binary_tamper_and_wrong_arch_are_rejected(self):
        self.stage(); (self.output/'resource/libsignal_jni_testing_amd64.so').write_bytes(b'bad')
        with self.assertRaises(subject.Failure): self.verify()
        self.lib.write_bytes(b'bad')
        with self.assertRaises(subject.Failure): subject.stage(self.lock,self.sources,self.lib,self.sha,self.root/'second')

    def test_groovy_paths_are_encoded_as_uris(self):
        path = self.root / 'dollar$injection'
        path.mkdir()
        script = subject.init_text(path, path, self.sha)
        self.assertNotIn('$injection', script)
        self.assertIn('%24injection', script)

    def test_failed_staging_cleans_only_new_output(self):
        with patch.object(subject.shutil, 'copyfileobj', side_effect=OSError('copy failed')):
            with self.assertRaises(OSError): self.stage()
        self.assertFalse(self.output.exists())
        self.output.mkdir()
        (self.output/'keep').write_text('keep')
        with self.assertRaises(subject.Failure): self.stage()
        self.assertEqual('keep', (self.output/'keep').read_text())

    def test_symlinked_stage_is_rejected(self):
        self.stage(); link=self.root/'link';link.symlink_to(self.output, target_is_directory=True)
        with self.assertRaisesRegex(subject.Failure,'real directory'): subject.verify(self.lock,self.sources,link,self.sha)
