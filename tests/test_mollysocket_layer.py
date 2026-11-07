import hashlib
import json
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).parents[1]
MANIFEST_PATH = ROOT / 'source/layers/mollysocket.json'
PATCH_PATH = ROOT / 'source/layers/mollysocket.patch'
EXPECTED_PATHS = {
    'lib/libsignal-service/src/main/java/org/whispersystems/signalservice/internal/util/BlacklistingTrustManager.java',
    'lib/libsignal-service/src/test/java/org/whispersystems/signalservice/internal/util/BlacklistingTrustManagerTest.kt',
}


class MollySocketFeatureLayerTests(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads(MANIFEST_PATH.read_text())
        self.patch = PATCH_PATH.read_bytes()
        self.patch_text = self.patch.decode()

    def test_manifest_pins_original_feature_and_exact_parent_tree(self):
        candidate = json.loads((ROOT / 'source/candidate-plan-v3.json').read_text())
        self.assertEqual(self.manifest['schema'], 1)
        self.assertEqual(self.manifest['kind'], 'feature-layer')
        self.assertEqual(self.manifest['source_commit'], '6f2595515d8466da725c1dda65f252ef02d05456')
        self.assertEqual(self.manifest['source_parent'], '8b119e357d1c9c6863eff31756660bc07d6cfb8b')
        self.assertEqual(self.manifest['before_tree'], candidate['final_tree'])
        self.assertEqual(self.manifest['before_tree'], 'b5fbed355186be1e728d556a48718e7158e7ab8f')
        self.assertEqual(self.manifest['after_tree'], 'db433757a9a9526f3a604f95f05d5fe031753fae')
        self.assertEqual(self.manifest['author'], {'name': '0cwa', 'email': '0@none.org'})
        self.assertEqual(self.manifest['subject'], 'Fix MollySocket TLS discovery with Android domain trust rules')
        self.assertFalse(self.manifest['policy_documentation_included'])

    def test_patch_digest_and_changed_paths_match_manifest(self):
        self.assertEqual(hashlib.sha256(self.patch).hexdigest(), self.manifest['patch_sha256'])
        paths = {
            second
            for first, second in re.findall(r'^diff --git a/(.+) b/(.+)$', self.patch_text, re.MULTILINE)
            if first == second
        }
        self.assertEqual(paths, EXPECTED_PATHS)
        self.assertEqual(set(self.manifest['paths']), EXPECTED_PATHS)
        self.assertEqual(self.manifest['patch'], 'source/layers/mollysocket.patch')

    def test_feature_and_regression_cases_are_inside_the_preserved_patch(self):
        additions = '\n'.join(
            line[1:]
            for line in self.patch_text.splitlines()
            if line.startswith('+') and not line.startswith('+++')
        )
        for source in (
            'extends X509ExtendedTrustManager',
            'checkServerTrusted(X509Certificate[] chain, String authType, Socket socket)',
            'checkServerTrusted(X509Certificate[] chain, String authType, SSLEngine engine)',
            'checkBlacklist(chain);',
            '((X509ExtendedTrustManager) trustManager).checkServerTrusted(chain, authType, socket)',
            '((X509ExtendedTrustManager) trustManager).checkServerTrusted(chain, authType, engine)',
        ):
            with self.subTest(source=source):
                self.assertIn(source, additions)
        for test_name in (
            'socket context is forwarded to an extended delegate',
            'engine context is forwarded to an extended delegate',
            'blacklisted chain is rejected before contextual delegate',
            'legacy delegate is used when contextual validation is unavailable',
        ):
            with self.subTest(test_name=test_name):
                self.assertIn(test_name, additions)


if __name__ == '__main__':
    unittest.main()
