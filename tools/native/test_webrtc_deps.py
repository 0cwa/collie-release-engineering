import hashlib
from pathlib import Path
import tempfile
import unittest

import webrtc_deps

class WebRtcDepsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='webrtc-deps-test-')
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'DEPS'
        self.reference = {'webrtc_depot_tools_revision':'depot-rev', 'webrtc_linux_jdk_cipd_instance':'jdk-pin',
                          'webrtc_siso_revision':'siso-rev', 'webrtc_gn_android_ndk_version':'ndk-pin',
                          'opus_model':'opus-pin'}
        self.original = b'\n'.join(value.encode() for value in self.reference.values()) + b'\n' + webrtc_deps.TEST_MEDIA_HOOK + b'\nproduction hooks remain\n'
        self.reference['webrtc_deps_sha256'] = hashlib.sha256(self.original).hexdigest()
        self.path.write_bytes(self.original)

    def test_only_test_media_is_omitted_and_original_is_restored(self):
        with webrtc_deps.production_hooks_only(self.path, self.reference):
            modified = self.path.read_bytes()
            self.assertNotIn(webrtc_deps.TEST_MEDIA_HOOK, modified)
            self.assertIn(b'production hooks remain', modified)
        self.assertEqual(self.original, self.path.read_bytes())

    def test_original_is_restored_even_if_hook_fails(self):
        with self.assertRaisesRegex(RuntimeError, 'hook failed'):
            with webrtc_deps.production_hooks_only(self.path, self.reference):
                raise RuntimeError('hook failed')
        self.assertEqual(self.original, self.path.read_bytes())

    def test_modified_deps_is_rejected_before_writing(self):
        changed = self.original + b'unexpected\n'
        self.path.write_bytes(changed)
        with self.assertRaises(webrtc_deps.Failure):
            with webrtc_deps.production_hooks_only(self.path, self.reference):
                pass
        self.assertEqual(changed, self.path.read_bytes())

    def test_mismatched_toolchain_reference_is_rejected(self):
        self.reference['webrtc_siso_revision'] = 'wrong-pin'
        with self.assertRaises(webrtc_deps.Failure):
            webrtc_deps.verify_reference(self.original, self.reference)

    def test_duplicate_hook_is_rejected_even_when_hash_matches(self):
        duplicate = self.original + webrtc_deps.TEST_MEDIA_HOOK
        self.reference['webrtc_deps_sha256'] = hashlib.sha256(duplicate).hexdigest()
        with self.assertRaises(webrtc_deps.Failure):
            webrtc_deps.verify_reference(duplicate, self.reference)

if __name__ == '__main__':
    unittest.main()
