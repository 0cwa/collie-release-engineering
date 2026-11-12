import json
from pathlib import Path
import unittest

ROOT = Path(__file__).parents[1]


class NativeSourcePatchRecordTests(unittest.TestCase):
    def test_static_native_patch_has_only_public_build_and_host_test_status(self):
        patch = (ROOT / 'source/layers/native-repository.patch').read_text()
        docs_start = patch.index('diff --git a/docs/BUILD.md b/docs/BUILD.md\n')
        host_start = patch.index('diff --git a/build-inputs/host-test-inputs.json b/build-inputs/host-test-inputs.json\n', docs_start)
        docs_added = '\n'.join(line[1:] for line in patch[docs_start:host_start].splitlines()
                                if line.startswith('+') and not line.startswith('+++'))
        for private_marker in ('agent-heavy', 'agent-scratch', 'Preserve build outputs',
                               'releaseVersionCode', 'trusted-local', 'staging-guard'):
            self.assertNotIn(private_marker, docs_added)
        host_end = patch.index('diff --git a/build-inputs/native-artifacts.json b/build-inputs/native-artifacts.json\n', host_start)
        host_added = '\n'.join(line[1:] for line in patch[host_start:host_end].splitlines()
                                if line.startswith('+') and not line.startswith('+++'))
        host = json.loads(host_added)
        self.assertEqual(host['status'], 'NOT RUN')
        for private_field in ('historical_local_candidate_sha256', 'historical_receipt_lock_sha256',
                              'prepared_libsignal_tree', 'staging_guard', 'platform', 'resource'):
            self.assertNotIn(private_field, host)


if __name__ == '__main__':
    unittest.main()
