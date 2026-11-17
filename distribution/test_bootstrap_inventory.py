"""Inventory migration tests exercise real copying/hashing; signatures use the shared verifier seam."""
import hashlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

import bootstrap_inventory as bootstrap
import channel_profile as channels
import feed
import site_page


def record(data):
    return {'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)}


class Response(io.BytesIO):
    status = 200

    def __init__(self, body, url, reads):
        super().__init__(body)
        self.url = url
        self.reads = reads

    def geturl(self):
        return self.url

    def read(self, size=-1):
        self.reads.append(size)
        if size < 0:
            raise AssertionError('origin downloads must use bounded streaming reads')
        return super().read(size)


class BootstrapInventoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.policy = 'a' * 40
        native_path = 'native-inputs/v1/maven/example/native/1/native-1.aar'
        self.blobs = {name: ('old ' + name).encode() for name in site_page.STATIC}
        self.blobs.update({
            native_path: b'unchanged native input',
            'CNAME': b'repo.collie.im\n',
            'index.html': b'<html>unchanged old landing</html>',
            'add-repository.png': b'old QR code',
            'fdroid/repo/entry.jar': b'signed entry',
            'fdroid/repo/index-v1.jar': b'signed v1',
            'fdroid/repo/im.collie.app_42.apk': b'unchanged signed APK',
            'fdroid/repo/im.collie.app/en-US/icon_abc=.png': b'unchanged store icon',
        })
        self.native = {native_path: record(self.blobs[native_path])}
        pin = patch.object(bootstrap, '_native_files', return_value=self.native)
        pin.start()
        self.addCleanup(pin.stop)
        apk = self.blobs['fdroid/repo/im.collie.app_42.apk']
        icon = self.blobs['fdroid/repo/im.collie.app/en-US/icon_abc=.png']
        self.index = {
            'repo': {'address': channels.STABLE.repository_url, 'icon': {
                'en-US': {'name': '/im.collie.app/en-US/icon_abc=.png', 'sha256': record(icon)['sha256'], 'size': len(icon)}}},
            'packages': {channels.STABLE.package: {'versions': {'apk': {
                'manifest': {'versionCode': 42, 'versionName': '8.35.1-3-rc2',
                             'signer': {'sha256': [channels.STABLE.apk_certificate]}},
                'file': {'name': '/im.collie.app_42.apk', 'sha256': record(apk)['sha256'], 'size': len(apk)},
            }}}},
        }
        self.blobs['fdroid/repo/index-v2.json'] = json.dumps(self.index).encode()
        self.blobs['feed-state.json'] = json.dumps({
            'schema': 1, 'latest': 'v8.35.1-3-rc2',
            'repository_fingerprint': channels.STABLE.repository_certificate,
            'apks': ['im.collie.app_42.apk'], 'native_files': 1,
        }).encode()
        self.inventory = {
            'schema': 1, 'kind': 'collie-pages-legacy-inventory', 'origin': bootstrap.ORIGIN,
            'policy_revision': self.policy, 'previous_deployment_run_id': 123,
            'provenance': 'complete archived test deployment',
            'files': {name: record(data) for name, data in self.blobs.items()},
        }
        self.verifier = patch.object(bootstrap.binary_repo, 'verify_indexes', return_value=self.index)
        self.verify = self.verifier.start()
        self.addCleanup(self.verifier.stop)
        self.calls = []
        self.reads = []
        self.statuses = {}

    def origin(self, request, timeout):
        self.assertEqual(timeout, 60)
        self.assertTrue(request.full_url.startswith(bootstrap.ORIGIN))
        self.assertIsNone(request.get_header('Authorization'))
        self.assertEqual(request.get_header('Cache-control'), 'no-cache, no-store')
        path = request.full_url[len(bootstrap.ORIGIN):]
        self.calls.append(path)
        status = self.statuses.get(path, 200 if path in self.blobs else 404)
        if status != 200:
            raise HTTPError(request.full_url, status, 'test origin status', {}, io.BytesIO())
        return Response(self.blobs.get(path, b''), request.full_url, self.reads)

    def input_file(self, value=None, name='inventory.json', raw=None):
        if raw is None:
            raw = json.dumps(self.inventory if value is None else value).encode()
        path = self.root / name
        path.write_bytes(raw)
        return path, record(raw)['sha256']

    def prepare(self):
        path, digest = self.input_file()
        output = self.root / 'prepared'
        bootstrap.prepare(path, digest, output, self.policy, self.origin)
        return output, output / 'baseline.json', output / 'site'

    def test_roundtrip_preserves_all_bytes_and_unlocks_normal_inventory_capture(self):
        output, baseline, site = self.prepare()
        expected = set(self.blobs) | {feed.SITE_INVENTORY}
        self.assertEqual(set(bootstrap._local_files(site)), expected)
        for name, data in self.blobs.items():
            self.assertEqual((site / name).read_bytes(), data)
        self.verify.assert_called_once_with(site / 'fdroid/repo',
                                           channels.STABLE.repository_certificate, channels.STABLE)
        self.assertTrue(bootstrap.verify_deploy_baseline(baseline, site, self.origin))
        self.blobs[feed.SITE_INVENTORY] = (site / feed.SITE_INVENTORY).read_bytes()
        self.assertTrue(bootstrap.verify_readback(baseline, self.origin))
        def fetch(path, *, max_bytes):
            self.assertLessEqual(len(self.blobs[path]), max_bytes)
            return 200, self.blobs[path]
        result = feed.capture_live_site(self.root / 'normal-capture', fetch=fetch)
        self.assertEqual(result['files'], self.inventory['files'])
        self.assertEqual(result['inventory_sha256'], record(self.blobs[feed.SITE_INVENTORY])['sha256'])
        self.assertLessEqual(max(self.reads), bootstrap.CHUNK_BYTES)

    def test_make_inventory_requires_a_complete_snapshot_and_does_not_contact_origin(self):
        snapshot = self.root / 'old-site'
        for name, data in self.blobs.items():
            path = snapshot / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        output = self.root / 'review.json'
        with patch.object(bootstrap, '_open') as network:
            digest = bootstrap.make_inventory(snapshot, output, self.policy, 123, 'reviewed archive')
            network.assert_not_called()
        self.assertEqual(digest, record(output.read_bytes())['sha256'])
        self.assertEqual(json.loads(output.read_bytes())['files'], self.inventory['files'])

    def test_missing_inventory_remains_an_error_for_normal_feed_capture(self):
        with self.assertRaisesRegex(ValueError, 'inventory must exist'):
            feed.capture_deploy_baseline(self.root / 'normal.json', fetch=lambda *_a, **_k: (404, b''))

    def test_existing_inventory_or_any_beta_index_prevents_bootstrap(self):
        for position, path in enumerate(bootstrap.ABSENT):
            self.statuses = {path: 200}
            inventory, digest = self.input_file(name=f'input-{position}.json')
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, 'requires absent'):
                bootstrap.prepare(inventory, digest, self.root / f'out-{position}', self.policy, self.origin)
        self.verify.assert_not_called()

    def test_all_inventory_errors_stop_before_network_or_output_creation(self):
        def changed():
            return json.loads(json.dumps(self.inventory))
        bad = []
        for name in ('../outside', '/absolute', 'assets//double.css', 'assets/./dot.css',
                     'assets/../escape.css', 'assets/%2e.css', 'assets/name?query.css',
                     'fdroid/beta/repo/entry.jar', 'site-manifest.json'):
            value = changed()
            value['files'][name] = record(b'bad')
            bad.append(value)
        value = changed(); value['files'].pop(next(iter(self.native))); bad.append(value)
        value = changed(); value['files']['index.html']['bytes'] = True; bad.append(value)
        value = changed(); value['files']['index.html']['bytes'] = -1; bad.append(value)
        value = changed(); value['files']['index.html']['bytes'] = bootstrap.MAX_BYTES; bad.append(value)
        value = changed(); value['files']['index.html']['sha256'] = 'no'; bad.append(value)
        value = changed(); value['files'][next(iter(self.native))]['sha256'] = 'b' * 64; bad.append(value)
        value = changed(); value['policy_revision'] = 'b' * 40; bad.append(value)
        value = changed(); value['unexpected'] = 'value'; bad.append(value)
        value = changed(); value['schema'] = True; bad.append(value)
        for position, value in enumerate(bad):
            path, digest = self.input_file(value, name=f'bad-{position}.json')
            output = self.root / f'bad-out-{position}'
            with self.subTest(position=position), self.assertRaises(ValueError):
                bootstrap.prepare(path, digest, output, self.policy, self.origin)
            self.assertFalse(output.exists())
        self.assertEqual(self.calls, [])

    def test_duplicate_fields_raw_digest_and_count_limit_rejected_before_network(self):
        raw = json.dumps(self.inventory).replace('"schema": 1', '"schema": 1, "schema": 1').encode()
        path, digest = self.input_file(raw=raw)
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            bootstrap.prepare(path, digest, self.root / 'dup', self.policy, self.origin)
        path, digest = self.input_file(name='valid.json')
        with self.assertRaisesRegex(ValueError, 'raw inventory digest'):
            bootstrap.prepare(path, 'b' * 64, self.root / 'hash', self.policy, self.origin)
        with patch.object(bootstrap, 'MAX_FILES', 1), self.assertRaisesRegex(ValueError, 'file count'):
            bootstrap.prepare(path, digest, self.root / 'count', self.policy, self.origin)
        self.assertEqual(self.calls, [])

    def test_control_symlinks_and_existing_output_are_rejected(self):
        path, digest = self.input_file()
        link = self.root / 'link.json'; link.symlink_to(path)
        with self.assertRaisesRegex(ValueError, 'regular control file'):
            bootstrap.prepare(link, digest, self.root / 'output', self.policy, self.origin)
        output = self.root / 'output'; output.mkdir()
        (output / 'keep').write_bytes(b'preserve')
        with self.assertRaises(FileExistsError):
            bootstrap.prepare(path, digest, output, self.policy, self.origin)
        self.assertEqual((output / 'keep').read_bytes(), b'preserve')
        self.assertEqual(self.calls, [])

    def test_missing_changed_or_oversized_origin_file_never_writes_baseline(self):
        cases = [('missing', None), ('changed', b'changed'), ('oversized', b'long' * 100)]
        original = self.blobs['index.html']
        for name, body in cases:
            if body is None:
                self.blobs.pop('index.html', None)
            else:
                self.blobs['index.html'] = body
            path, digest = self.input_file(name=name + '.json')
            output = self.root / name
            with self.subTest(name=name), self.assertRaises((ValueError, HTTPError)):
                bootstrap.prepare(path, digest, output, self.policy, self.origin)
            self.assertFalse((output / 'baseline.json').exists())
        self.blobs['index.html'] = original

    def test_signature_and_signed_reference_failures_stop_preparation(self):
        path, digest = self.input_file()
        with patch.object(bootstrap.binary_repo, 'verify_indexes', side_effect=ValueError('bad signature')):
            with self.assertRaisesRegex(ValueError, 'bad signature'):
                bootstrap.prepare(path, digest, self.root / 'bad-signature', self.policy, self.origin)
        self.index['repo']['icon']['en-US']['name'] = '/icons/missing.png'
        with self.assertRaisesRegex(ValueError, 'signed index file reference'):
            bootstrap.prepare(path, digest, self.root / 'missing-reference', self.policy, self.origin)

    def test_wrong_signed_route_or_apk_signer_rejected(self):
        path, digest = self.input_file()
        self.index['repo']['address'] = channels.BETA.repository_url
        with self.assertRaisesRegex(ValueError, 'repository address'):
            bootstrap.prepare(path, digest, self.root / 'wrong-route', self.policy, self.origin)
        self.index['repo']['address'] = channels.STABLE.repository_url
        self.index['packages'][channels.STABLE.package]['versions']['apk']['manifest']['signer']['sha256'] = ['b' * 64]
        with self.assertRaisesRegex(ValueError, 'APK signer'):
            bootstrap.prepare(path, digest, self.root / 'wrong-signer', self.policy, self.origin)

    def test_candidate_extra_missing_changed_linked_and_special_files_rejected(self):
        _, baseline, site = self.prepare()
        extra = site / 'extra'; extra.write_bytes(b'extra')
        with self.assertRaisesRegex(ValueError, 'preserve exactly'):
            bootstrap.verify_candidate(baseline, site)
        extra.unlink()
        target = site / 'index.html'; original = target.read_bytes(); target.unlink()
        with self.assertRaisesRegex(ValueError, 'preserve exactly'):
            bootstrap.verify_candidate(baseline, site)
        target.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'preserve exactly'):
            bootstrap.verify_candidate(baseline, site)
        target.unlink(); target.symlink_to(site / 'CNAME')
        with self.assertRaisesRegex(ValueError, 'symlink'):
            bootstrap.verify_candidate(baseline, site)
        target.unlink(); target.write_bytes(original)
        os.link(target, extra)
        with self.assertRaisesRegex(ValueError, 'hardlink'):
            bootstrap.verify_candidate(baseline, site)
        extra.unlink()
        if hasattr(os, 'mkfifo'):
            os.mkfifo(extra)
            with self.assertRaisesRegex(ValueError, 'special file'):
                bootstrap.verify_candidate(baseline, site)

    def test_baseline_cannot_change_reviewed_json_or_candidate_manifest(self):
        _, baseline, site = self.prepare()
        original = json.loads(baseline.read_bytes())
        for field in ('reviewed_inventory_sha256', 'site_manifest_sha256'):
            value = {**original, field: 'b' * 64}
            baseline.write_text(json.dumps(value))
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'digest differs'):
                bootstrap.verify_candidate(baseline, site)
        value = {**original, 'reviewed_inventory_json': original['reviewed_inventory_json'] + ' '}
        baseline.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, 'digest differs'):
            bootstrap.verify_candidate(baseline, site)

    def test_deploy_recheck_rejects_changed_legacy_bytes_and_late_inventory_or_beta(self):
        _, baseline, site = self.prepare()
        original = self.blobs['index.html']; self.blobs['index.html'] = b'changed'
        with self.assertRaisesRegex(ValueError, 'reviewed inventory'):
            bootstrap.verify_deploy_baseline(baseline, site, self.origin)
        self.blobs['index.html'] = original
        for path in bootstrap.ABSENT:
            self.statuses = {path: 200}
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, 'requires absent'):
                bootstrap.verify_deploy_baseline(baseline, site, self.origin)

    def test_readback_rejects_missing_manifest_and_changed_preserved_bytes(self):
        _, baseline, site = self.prepare()
        with self.assertRaises(HTTPError):
            bootstrap.verify_readback(baseline, self.origin)
        self.blobs[feed.SITE_INVENTORY] = (site / feed.SITE_INVENTORY).read_bytes()
        self.blobs['index.html'] = b'changed'
        with self.assertRaisesRegex(ValueError, 'reviewed inventory'):
            bootstrap.verify_readback(baseline, self.origin)

    def test_origin_errors_never_become_absence_and_redirects_are_not_followed(self):
        path, digest = self.input_file()
        self.statuses[feed.SITE_INVENTORY] = 503
        with self.assertRaisesRegex(ValueError, 'absence check failed'):
            bootstrap.prepare(path, digest, self.root / 'http-error', self.policy, self.origin)
        with self.assertRaises(URLError):
            bootstrap.prepare(path, digest, self.root / 'transport-error', self.policy,
                              Mock(side_effect=URLError('unavailable')))
        redirect = bootstrap._NoRedirect()
        self.assertIsNone(redirect.redirect_request(None, None, 302, '', {}, 'https://other.invalid/x'))
        response = Response(b'x', 'https://other.invalid/x', [])
        with self.assertRaisesRegex(ValueError, 'canonical HTTPS URL'):
            bootstrap._download('index.html', record(b'x'), self.root / 'wrong-origin',
                                lambda *_a, **_k: response)

    def test_large_files_are_hashed_and_copied_in_bounded_chunks(self):
        data = b'x' * (bootstrap.CHUNK_BYTES * 2 + 17)
        response = Response(data, bootstrap.ORIGIN + 'large.apk', self.reads)
        target = self.root / 'large.apk'
        bootstrap._download('large.apk', record(data), target, lambda *_a, **_k: response)
        self.assertEqual(record(data)['sha256'], bootstrap.binary_repo.sha(target))
        self.assertGreater(len(self.reads), 2)
        self.assertTrue(all(0 < size <= bootstrap.CHUNK_BYTES for size in self.reads))


class BootstrapWorkflowTests(unittest.TestCase):
    def test_manual_main_only_workflow_shares_guard_without_signing_credentials(self):
        text = (Path(__file__).resolve().parents[1] / '.github/workflows/pages-bootstrap-inventory.yml').read_text()
        self.assertIn('workflow_dispatch:', text)
        for forbidden in ('schedule:', 'pull_request', 'workflow_run:', 'secrets.', 'fdroid update', 'keystore'):
            self.assertNotIn(forbidden, text)
        self.assertIn("github.ref == 'refs/heads/main' && vars.FEEDS_ENABLED == 'true'", text)
        self.assertIn('group: collie-pages-production\n  cancel-in-progress: false', text)
        self.assertIn('name: github-pages', text)
        self.assertIn('ref: ${{ github.sha }}', text)
        self.assertIn('test "$REVIEWED_POLICY" = "$RUN_POLICY"', text)
        self.assertIn('download_pages_input.py --kind legacy-inventory', text)
        self.assertIn('--sha256 "$INVENTORY_SHA256"', text)
        self.assertIn('--bootstrap-site "$RUNNER_TEMP/bootstrap/site"', text)
        self.assertIn('--bootstrap-baseline "$RUNNER_TEMP/bootstrap/baseline.json"', text)
        self.assertLess(text.index('check-candidate'), text.index('actions/upload-pages-artifact@'))
        self.assertLess(text.index('deploy_pages.py'), text.index('bootstrap_inventory.py readback'))
        self.assertIn('include-hidden-files: true', text)


if __name__ == '__main__':
    unittest.main()
