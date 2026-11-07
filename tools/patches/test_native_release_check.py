import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import native_release_check as check

class CheckTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.stage = self.root / 'stage'
        self.aar = self.stage / 'maven/im/collie/native/libsignal-android/1/libsignal-android-1.aar'
        self.aar.parent.mkdir(parents=True)
        self.lock = self.root / 'lock.json'
        self.lock.write_text(json.dumps({'modules': [{'name':'libsignal-android', 'output_version':'1', 'native_libraries':['libsignal_jni.so']}]}))
        with zipfile.ZipFile(self.aar, 'w') as z:
            for abi in check.ABIS:
                z.writestr('jni/' + abi + '/libsignal_jni.so', abi.encode())
        self.apk = self.root / 'app.apk'
        self.package()

    def package(self, changed=False, extra=None):
        with zipfile.ZipFile(self.apk, 'w') as z:
            for abi in check.ABIS:
                z.writestr('lib/' + abi + '/libsignal_jni.so', b'wrong' if changed else abi.encode())
            if extra:
                z.writestr(extra, b'bad')

    def verify(self):
        with patch.object(check.release_artifacts, 'check_metadata'), patch.object(check.release_artifacts, 'check_unsigned'), \
             patch.object(check.subprocess, 'check_output', side_effect=['package: test\n', 'org.thoughtcrime.securesms.gcm.FcmReceiveService']), patch.object(check.subprocess, 'run'):
            return check.verify(self.apk, self.lock, self.stage, self.root, '1', '1', '8.35', 177701, False)

    def test_debuggable_apk_fails(self):
        with patch.object(check.release_artifacts, 'check_metadata'), patch.object(check.release_artifacts, 'check_unsigned'), \
             patch.object(check.subprocess, 'check_output', side_effect=['application-debuggable\n', 'org.thoughtcrime.securesms.gcm.FcmReceiveService']), patch.object(check.subprocess, 'run'):
            with self.assertRaisesRegex(ValueError, 'debuggable'):
                check.verify(self.apk, self.lock, self.stage, self.root, '1', '1', '8.35', 177701, False)

    def test_wrong_firebase_selection_fails(self):
        with patch.object(check.release_artifacts, 'check_metadata'), patch.object(check.release_artifacts, 'check_unsigned'), \
             patch.object(check.subprocess, 'check_output', side_effect=['package: test\n', '']), patch.object(check.subprocess, 'run'):
            with self.assertRaisesRegex(ValueError, 'Firebase service differs'):
                check.verify(self.apk, self.lock, self.stage, self.root, '1', '1', '8.35', 177701, False)

    def test_matches_all_packaged_bytes(self):
        self.assertEqual(len(self.verify()['native_libraries']), 3)

    def test_bad_packaged_bytes_fail(self):
        self.package(changed=True)
        with self.assertRaisesRegex(ValueError, 'native library mismatch'):
            self.verify()

    def test_testing_native_fails(self):
        self.package(extra='lib/x86_64/libsignal_jni_testing.so')
        with self.assertRaisesRegex(ValueError, 'testing JNI'):
            self.verify()

    def test_unexpected_abi_fails(self):
        self.package(extra='lib/x86/libsignal_jni.so')
        with self.assertRaisesRegex(ValueError, 'unexpected native library ABI'):
            self.verify()

if __name__ == '__main__':
    unittest.main()
