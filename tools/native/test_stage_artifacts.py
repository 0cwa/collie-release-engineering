import hashlib
import importlib.util
import json
import shutil
from pathlib import Path
import tempfile
import unittest
from zipfile import ZipFile

HERE = Path(__file__).parent
spec = importlib.util.spec_from_file_location('stage_artifacts', HERE / 'stage_artifacts.py')
subject = importlib.util.module_from_spec(spec)
spec.loader.exec_module(subject)

class ArtifactStagingTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='native-stage-test-')
        self.root = Path(self.temp.name)
        self.input = self.root / 'input'
        self.output = self.root / 'output'
        self.verification = self.root / 'verification.xml'
        self.verification.write_text('''<verification-metadata xmlns="https://schema.gradle.org/dependency-verification">\n   <configuration><verify-metadata>true</verify-metadata></configuration>\n   <components>\n   </components>\n</verification-metadata>\n''')
        self.lock = json.loads((HERE / 'native-lock.json').read_text())
        for module in self.lock['modules']:
            directory = self.input / 'org/signal' / module['name'] / module['input_version']
            directory.mkdir(parents=True)
            stem = module['name'] + '-' + module['input_version']
            binary = directory / (stem + '.' + module['extension'])
            with ZipFile(binary, 'w') as archive:
                archive.writestr('classes.jar', b'fixture')
                for abi in self.lock['required_abis']:
                    for library in module['native_libraries']:
                        archive.writestr(f'jni/{abi}/{library}', b'fixture')
            dependency = ''
            if module['name'] == 'libsignal-android':
                dependency = '<dependencies><dependency><groupId>org.signal</groupId><artifactId>libsignal-client</artifactId><version>0.106.1</version></dependency></dependencies>'
            pom = directory / (stem + '.pom')
            pom.write_text(f'<project xmlns="http://maven.apache.org/POM/4.0.0"><modelVersion>4.0.0</modelVersion><groupId>org.signal</groupId><artifactId>{module["name"]}</artifactId><version>{module["input_version"]}</version>{dependency}</project>')
            module['binary_sha256'] = hashlib.sha256(binary.read_bytes()).hexdigest()
            module['pom_sha256'] = hashlib.sha256(pom.read_bytes()).hexdigest()
        shutil.copytree(HERE / 'patches', self.root / 'patches')
        self.lock_path = self.root / 'lock.json'
        self.save_lock()

    def tearDown(self):
        self.temp.cleanup()

    def save_lock(self):
        self.lock_path.write_text(json.dumps(self.lock))

    def run_stage(self):
        return subject.stage(self.lock_path, self.input, self.output, self.verification)

    def test_valid_input_is_exclusive_and_uses_distinct_coordinates(self):
        self.run_stage()
        subject.verify_stage(self.lock_path, self.output, self.verification)
        init = (self.output / 'native.init.gradle').read_text()
        self.assertIn('exclusiveContent', init)
        for group in ('im.molly', 'org.signal'):
            for module in ('libsignal-android', 'libsignal-client', 'ringrtc-android'):
                self.assertIn(f"substitute module('{group}:{module}')", init)
        self.assertIn("im.collie.native:libsignal-android:0.106.1-collie.1", init)
        pom = self.output / 'maven/im/collie/native/libsignal-android/0.106.1-collie.1/libsignal-android-0.106.1-collie.1.pom'
        text = pom.read_text()
        self.assertIn('im.collie.native', text)
        self.assertIn('0.106.1-collie.1', text)
        self.assertNotIn('org.signal</', text)
        metadata = (self.output / 'verification-metadata.xml').read_text()
        self.assertIn('verify-metadata>true', metadata)
        self.assertIn('im.collie.native', metadata)

    def test_wrong_hash_is_rejected_before_output_exists(self):
        self.lock['modules'][0]['binary_sha256'] = '0' * 64
        self.save_lock()
        with self.assertRaises(subject.Failure):
            self.run_stage()
        self.assertFalse(self.output.exists())

    def test_modified_source_patch_is_rejected(self):
        patch = self.root / self.lock['source_patches'][0]['path']
        patch.write_bytes(patch.read_bytes() + b'tampered')
        with self.assertRaises(subject.Failure):
            self.run_stage()
        self.assertFalse(self.output.exists())

    def test_missing_abi_is_rejected_even_with_matching_hash(self):
        m = self.lock['modules'][0]
        binary = self.input / 'org/signal' / m['name'] / m['input_version'] / (m['name'] + '-' + m['input_version'] + '.aar')
        with ZipFile(binary, 'w') as archive:
            archive.writestr('classes.jar', b'fixture')
        m['binary_sha256'] = hashlib.sha256(binary.read_bytes()).hexdigest()
        self.save_lock()
        with self.assertRaises(subject.Failure):
            self.run_stage()
        self.assertFalse(self.output.exists())

    def test_missing_x86_is_rejected_for_each_native_aar_even_with_matching_hash(self):
        for m in self.lock['modules']:
            if m['extension'] != 'aar':
                continue
            with self.subTest(module=m['name']):
                binary = self.input / 'org/signal' / m['name'] / m['input_version'] / (m['name'] + '-' + m['input_version'] + '.aar')
                original = binary.read_bytes()
                original_hash = m['binary_sha256']
                try:
                    with ZipFile(binary, 'w') as archive:
                        archive.writestr('classes.jar', b'fixture')
                        for abi in self.lock['required_abis']:
                            if abi != 'x86':
                                for library in m['native_libraries']:
                                    archive.writestr(f'jni/{abi}/{library}', b'fixture')
                    m['binary_sha256'] = hashlib.sha256(binary.read_bytes()).hexdigest()
                    self.save_lock()
                    with self.assertRaisesRegex(subject.Failure, 'missing a required ABI'):
                        self.run_stage()
                    self.assertFalse(self.output.exists())
                finally:
                    binary.write_bytes(original)
                    m['binary_sha256'] = original_hash
                    self.save_lock()

    def test_post_staging_artifact_tampering_is_rejected(self):
        self.run_stage()
        target = self.output / 'maven/im/collie/native/ringrtc-android/2.74.0-collie.1/ringrtc-android-2.74.0-collie.1.aar'
        target.write_bytes(b'tampered')
        with self.assertRaises(subject.Failure):
            subject.verify_stage(self.lock_path, self.output, self.verification)

    def test_forged_receipt_cannot_authorize_a_modified_init_script(self):
        self.run_stage()
        init = self.output / 'native.init.gradle'
        init.write_text(init.read_text() + '// forged\n')
        receipt_file = self.output / 'receipt.json'
        receipt = json.loads(receipt_file.read_text())
        receipt['init_sha256'] = hashlib.sha256(init.read_bytes()).hexdigest()
        receipt_file.write_text(json.dumps(receipt))
        with self.assertRaises(subject.Failure):
            subject.verify_stage(self.lock_path, self.output, self.verification)

    def test_forged_receipt_cannot_authorize_a_modified_pom(self):
        self.run_stage()
        target = self.output / 'maven/im/collie/native/libsignal-client/0.106.1-collie.1/libsignal-client-0.106.1-collie.1.pom'
        target.write_bytes(target.read_bytes() + b' ')
        receipt_file = self.output / 'receipt.json'
        receipt = json.loads(receipt_file.read_text())
        receipt['artifacts']['libsignal-client'][target.name] = hashlib.sha256(target.read_bytes()).hexdigest()
        receipt_file.write_text(json.dumps(receipt))
        with self.assertRaises(subject.Failure):
            subject.verify_stage(self.lock_path, self.output, self.verification)

    def test_trust_bypass_is_rejected(self):
        self.verification.write_text('''<verification-metadata xmlns="https://schema.gradle.org/dependency-verification">\n   <configuration><verify-metadata>true</verify-metadata><trusted-artifacts><trust group="im.collie.native"/></trusted-artifacts></configuration>\n   <components>\n   </components>\n</verification-metadata>\n''')
        with self.assertRaises(subject.Failure):
            self.run_stage()
        self.assertFalse(self.output.exists())

    def test_generic_pom_trust_is_narrowed_away_from_native_group(self):
        text = self.verification.read_text().replace('</configuration>', '<trusted-artifacts><trust file=".*[.]pom" regex="true"/></trusted-artifacts></configuration>')
        self.verification.write_text(text)
        self.run_stage()
        generated = (self.output / 'verification-metadata.xml').read_text()
        self.assertIn('group="(?!im[.]collie[.]native$).*"', generated)
        self.assertNotIn('<trust file=".*[.]pom" regex="true"/>', generated)
        subject.verify_stage(self.lock_path, self.output, self.verification)

    def test_symlinked_input_is_rejected(self):
        m = self.lock['modules'][0]
        binary = self.input / 'org/signal' / m['name'] / m['input_version'] / (m['name'] + '-' + m['input_version'] + '.aar')
        other = self.root / 'elsewhere.aar'
        binary.rename(other)
        binary.symlink_to(other)
        with self.assertRaises(subject.Failure):
            self.run_stage()
        self.assertFalse(self.output.exists())

if __name__ == '__main__':
    unittest.main()
