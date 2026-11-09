import concurrent.futures
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import tempfile
import unittest

import version_policy as policy

ROOT = Path(__file__).resolve().parent
PUBLISHED = [
    {'release': '8.33.2-1-rc1', 'code': 177201, 'sha256': '9748ae89caf809274d234d5c2a22f60c1303fea3d895c2452cf1051933d2ecbd'},
    {'release': '8.33.2-1-rc2', 'code': 177202, 'sha256': '1105fb90a7f963ebe85e365e1d7ca7cfc185c8447a5c4baa10f98cf4683c1439'},
    {'release': '8.33.2-1-rc3', 'code': 177203, 'sha256': '189b0bf0056945bd6dde9a9e9f5a9e664f8aa5e294de3ade77d2850ce7d3a313'},
    {'release': '8.35.1-1-rc1', 'code': 177702, 'sha256': '5fbd0fe817bc6711c56fbc39b8479cc0a68a7d7fe921a7d9c9fab5933fda9ee'},
]
INVENTORY_BYTES = json.dumps(PUBLISHED, separators=(',', ':')).encode()
INVENTORY_SHA = hashlib.sha256(INVENTORY_BYTES).hexdigest()
RELEASE = '8.35.1-2-rc1'
CODE = 177703
MANIFEST_SHA = 'ab' * 32
SIGNED_SHA = 'cd' * 32


def _legacy_reserve(path, release, code, artifact_sha256, published):
    """Conflict handling copied from the unmodified a7653fa helper."""
    records = json.loads(Path(path).read_text()) if Path(path).exists() else []
    candidate = {'release': release, 'code': code, 'sha256': artifact_sha256}
    known = published + records
    for item in known:
        if item['release'] == release or item['code'] == code:
            if any(item[k] != v for k, v in candidate.items()):
                raise ValueError('release identity/code already bound to different artifact')
            return candidate
    if code <= max(item['code'] for item in known):
        raise ValueError('version must exceed published and reserved codes')
    return candidate


def _hold_worker(args):
    root, release, code, inventory, inventory_sha = args
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import version_policy
    return version_policy.hold(Path(root) / 'ledger.json', release, code,
                               Path(inventory), inventory_sha)


class HoldAndBind(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.ledger = self.root / 'ledger.json'
        self.inventory = self.root / 'published-inventory.json'
        self.inventory.write_bytes(INVENTORY_BYTES)

    def hold(self, release=RELEASE, code=CODE):
        return policy.hold(self.ledger, release, code, self.inventory, INVENTORY_SHA)

    def records(self):
        return json.loads(self.ledger.read_text())

    def test_hold_is_durable_private_and_idempotent(self):
        expected = {'release': RELEASE, 'code': CODE, 'state': 'held', 'sha256': None}
        self.assertEqual(self.hold(), expected)
        before = self.ledger.read_bytes()
        self.assertEqual(self.hold(), expected)
        self.assertEqual(self.ledger.read_bytes(), before)
        self.assertEqual(self.records(), [expected])
        self.assertEqual(self.ledger.stat().st_mode & 0o777, 0o600)
        self.assertEqual(Path(str(self.ledger) + '.lock').stat().st_mode & 0o777, 0o600)

    def test_only_exact_held_identity_binds_actual_manifest_hash(self):
        self.hold()
        candidate = policy.reserve(self.ledger, RELEASE, CODE, MANIFEST_SHA,
                                   PUBLISHED)
        self.assertEqual(candidate, {'release': RELEASE, 'code': CODE, 'sha256': MANIFEST_SHA})
        self.assertEqual(self.records(), [candidate])
        self.assertEqual(self.ledger.stat().st_mode & 0o777, 0o600)
        self.assertEqual(policy.reserve(self.ledger, RELEASE, CODE, MANIFEST_SHA,
                                        PUBLISHED), candidate)
        signed_manifest = {'version_name': RELEASE, 'version_code': CODE,
                           'unsigned_manifest_sha256': MANIFEST_SHA,
                           'apks': [{'signed_sha256': SIGNED_SHA}]}
        policy.bind_signed(self.ledger, signed_manifest)
        self.assertEqual(self.records(), [{**candidate, 'signed_sha256': SIGNED_SHA}])
        with self.assertRaisesRegex(ValueError, 'different signed bytes'):
            policy.bind_signed(self.ledger, {**signed_manifest,
                'apks': [{'signed_sha256': 'ef' * 32}]})
        with self.assertRaisesRegex(ValueError, 'already bound'):
            policy.reserve(self.ledger, RELEASE, CODE, SIGNED_SHA,
                           PUBLISHED)
        self.assertEqual(self.records(), [{**candidate, 'signed_sha256': SIGNED_SHA}])

    def test_cross_release_and_cross_code_cannot_consume_or_replace_hold(self):
        self.hold()
        published = PUBLISHED
        with self.assertRaisesRegex(ValueError, 'already reserved'):
            policy.hold(self.ledger, 'other-release', CODE, self.inventory, INVENTORY_SHA)
        with self.assertRaisesRegex(ValueError, 'already reserved'):
            policy.hold(self.ledger, RELEASE, CODE + 1, self.inventory, INVENTORY_SHA)
        with self.assertRaisesRegex(ValueError, 'already bound'):
            policy.reserve(self.ledger, 'other-release', CODE, MANIFEST_SHA, published)
        with self.assertRaisesRegex(ValueError, 'already bound'):
            policy.reserve(self.ledger, RELEASE, CODE + 1, MANIFEST_SHA, published)
        self.assertEqual(self.records()[0]['state'], 'held')
        self.assertIsNone(self.records()[0]['sha256'])

    def test_hold_requires_authenticated_unchanged_inventory(self):
        altered = self.root / 'altered-inventory.json'
        altered.write_bytes(self.inventory.read_bytes() + b' ')
        with self.assertRaisesRegex(ValueError, 'inventory digest differs'):
            policy.hold(self.ledger, RELEASE, CODE, altered, INVENTORY_SHA)
        with self.assertRaisesRegex(ValueError, 'inventory digest differs'):
            policy.enforce({'version_name': RELEASE, 'version_code': CODE,
                            'unsigned_manifest_sha256': MANIFEST_SHA},
                           self.ledger, altered, INVENTORY_SHA)
        self.assertFalse(self.ledger.exists())

    def test_hold_must_exceed_published_maximum(self):
        for release, code in [('old', 177202), ('rc1-reuse', 177702), ('gap', 177203)]:
            with self.subTest(code=code), self.assertRaisesRegex(ValueError, 'reserved|exceed'):
                self.hold(release, code)
        self.assertFalse(self.ledger.exists())

    def test_unbound_hold_cannot_pass_signed_binding(self):
        self.hold()
        signed_manifest = {
            'version_name': RELEASE,
            'version_code': CODE,
            'unsigned_manifest_sha256': MANIFEST_SHA,
            'apks': [{'signed_sha256': SIGNED_SHA}],
        }
        with self.assertRaisesRegex(ValueError, 'lacks exact reservation'):
            policy.bind_signed(self.ledger, signed_manifest)
        self.assertEqual(self.records()[0]['state'], 'held')
        self.assertIsNone(self.records()[0]['sha256'])

    def test_old_allocator_fails_closed_on_hold_without_dropping_it(self):
        self.hold()
        before = self.ledger.read_bytes()
        with self.assertRaises(ValueError):
            _legacy_reserve(self.ledger, RELEASE, CODE, MANIFEST_SHA, PUBLISHED)
        self.assertEqual(self.ledger.read_bytes(), before)
        self.assertEqual(self.records()[0]['state'], 'held')

    def test_existing_bound_record_format_remains_compatible(self):
        published = PUBLISHED
        first_sha = '12' * 32
        self.assertEqual(policy.reserve(self.ledger, '8.35.1-3-rc1', 177704,
                                        first_sha, published),
                         {'release': '8.35.1-3-rc1', 'code': 177704, 'sha256': first_sha})
        self.assertEqual(self.records(), [{'release': '8.35.1-3-rc1',
                                           'code': 177704, 'sha256': first_sha}])

    def test_concurrent_identical_holds_are_idempotent(self):
        ctx = multiprocessing.get_context('fork')
        args = [(str(self.root), RELEASE, CODE, str(self.inventory), INVENTORY_SHA)] * 8
        with concurrent.futures.ProcessPoolExecutor(max_workers=8, mp_context=ctx) as pool:
            results = list(pool.map(_hold_worker, args))
        expected = {'release': RELEASE, 'code': CODE, 'state': 'held', 'sha256': None}
        self.assertEqual(results, [expected] * 8)
        self.assertEqual(self.records(), [expected])

    def test_concurrent_different_release_same_code_only_one_wins(self):
        ctx = multiprocessing.get_context('fork')
        args = [(str(self.root), name, CODE, str(self.inventory), INVENTORY_SHA) for name in ('8.35.1-2-rc1', 'different-name')]
        with concurrent.futures.ProcessPoolExecutor(max_workers=2, mp_context=ctx) as pool:
            futures = [pool.submit(_hold_worker, item) for item in args]
            outcomes = []
            for future in futures:
                try:
                    outcomes.append(future.result())
                except ValueError:
                    outcomes.append('collision')
        self.assertEqual(sum(x == 'collision' for x in outcomes), 1)
        rows = self.records()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['code'], CODE)
        self.assertEqual(rows[0]['state'], 'held')

    def test_reserve_inventory_and_signing_gates_remain(self):
        manifest = {'version_name': RELEASE, 'version_code': CODE,
                    'unsigned_manifest_sha256': MANIFEST_SHA}
        with self.assertRaisesRegex(ValueError, 'must exceed published RC1'):
            policy.enforce({**manifest, 'version_code': 177702}, self.ledger,
                           self.inventory, INVENTORY_SHA)
        self.hold()
        self.assertEqual(policy.enforce(manifest, self.ledger, self.inventory, INVENTORY_SHA),
                         {'release': RELEASE, 'code': CODE, 'sha256': MANIFEST_SHA})
        self.assertEqual(self.records(), [{'release': RELEASE, 'code': CODE,
                                           'sha256': MANIFEST_SHA}])


if __name__ == '__main__':
    unittest.main(verbosity=2)
