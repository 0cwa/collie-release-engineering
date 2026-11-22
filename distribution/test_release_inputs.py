"""Release selection and input integrity tests without GitHub or APK downloads."""
import copy
from email.message import Message
import hashlib
import io
import json
import tempfile
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import patch

import release_inputs as inputs


APK = b'published signed APK fixture'
APK_SHA = hashlib.sha256(APK).hexdigest()
LATEST = 'v8.35.2-1-rc1'
OLDER = 'v8.35.1-3-rc2'


def release(number=10, tag=LATEST, **changes):
    value = {'id': number, 'tag_name': tag, 'draft': False, 'prerelease': False,
             'immutable': True, 'assets': [{'id': number + 100, 'name': 'collie.apk',
             'state': 'uploaded', 'size': len(APK), 'digest': 'sha256:' + APK_SHA,
             'browser_download_url': f'https://github.com/{inputs.APP_REPOSITORY}/releases/download/{tag}/collie.apk'}]}
    value.update(changes)
    return value


class GitHub:
    def __init__(self, latest=None, pages=None):
        self.latest = release() if latest is None else latest
        self.pages = {1: [self.latest]} if pages is None else pages
        self.calls = []
        self.commit = 'b' * 40
        self.tag_oid = 'a' * 40

    def __call__(self, endpoint):
        self.calls.append(endpoint)
        root = f'repos/{inputs.APP_REPOSITORY}/'
        if endpoint == root + 'releases/tags/' + LATEST:
            return copy.deepcopy(self.latest)
        if endpoint.startswith(root + 'releases?'):
            return copy.deepcopy(self.pages.get(int(endpoint.rsplit('=', 1)[1]), []))
        if endpoint == root + 'git/ref/tags/' + LATEST:
            return {'ref': 'refs/tags/' + LATEST, 'object': {'type': 'tag', 'sha': self.tag_oid}}
        if endpoint == root + 'git/tags/' + self.tag_oid:
            return {'sha': self.tag_oid, 'object': {'type': 'commit', 'sha': self.commit}}
        for value in [self.latest] + [r for page in self.pages.values() for r in page]:
            if endpoint == root + 'releases/' + str(value['id']):
                return copy.deepcopy(value)
        raise AssertionError('unexpected API request: ' + endpoint)


class ReleaseInputTests(unittest.TestCase):
    def test_github_read_pins_host_despite_alternate_default(self):
        endpoint = f'repos/{inputs.APP_REPOSITORY}/releases'
        result = inputs.subprocess.CompletedProcess([], 0, '{}', '')
        with (patch.dict('os.environ', {'GH_HOST': 'example.invalid'}),
              patch.object(inputs.subprocess, 'run', return_value=result) as run):
            self.assertEqual(inputs.github_read(endpoint), {})
        self.assertEqual(run.call_args.args[0],
                         ['gh', 'api', '--hostname', 'github.com', '--method', 'GET', endpoint])
        self.assertNotIn('shell', run.call_args.kwargs)

    def test_exact_selected_release_does_not_depend_on_history_position(self):
        api = GitHub(pages={})
        self.assertEqual(inputs.select_releases(LATEST, 1, api=api)[0]['tag'], LATEST)
        self.assertEqual(api.calls, [f'repos/{inputs.APP_REPOSITORY}/releases/tags/{LATEST}'])

    def test_regular_history_is_paginated_past_one_hundred_beta_releases(self):
        betas = [release(1000 + n, f'trailblazer-v8.40.{n}', prerelease=True) for n in range(100)]
        api = GitHub(pages={1: betas, 2: [release(), release(11, OLDER)]})
        selected = inputs.select_releases(LATEST, 2, api=api)
        self.assertEqual([value['tag'] for value in selected], [LATEST, OLDER])
        self.assertTrue(any(endpoint.endswith('page=2') for endpoint in api.calls))

    def test_regular_candidates_and_other_products_are_not_retained(self):
        history = [release(), release(11, 'v8.36.0-1-rc1', prerelease=True),
                   release(12, 'trailblazer-v8.40.0'), release(13, OLDER)]
        self.assertEqual([r['tag'] for r in inputs.select_releases(LATEST, 3, api=GitHub(pages={1: history}))],
                         [LATEST, OLDER])

    def test_selected_release_must_remain_full_immutable_and_regular(self):
        for changed in ({'draft': True}, {'prerelease': True}, {'immutable': False},
                        {'tag_name': 'trailblazer-v8.40.0'}, {'id': True}):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                inputs.select_releases(LATEST, 1, api=GitHub(latest=release(**changed)))

    def test_release_response_cannot_substitute_another_regular_tag(self):
        with self.assertRaisesRegex(ValueError, 'tag differs'):
            inputs.select_releases(LATEST, 1, api=GitHub(latest=release(tag=OLDER)))

    def test_asset_digest_origin_size_and_unique_name_are_required(self):
        mutations = [{'digest': None}, {'size': True}, {'state': 'new'}, {'id': 0},
                     {'browser_download_url': 'https://example.invalid/collie.apk'}]
        for changed in mutations:
            value = release()
            value['assets'][0].update(changed)
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                inputs.regular_release(value)
        value = release()
        value['assets'].append(dict(value['assets'][0]))
        with self.assertRaisesRegex(ValueError, 'exactly one'):
            inputs.regular_release(value)

    def test_history_change_or_duplicate_fails_instead_of_silently_selecting_it(self):
        changed = release()
        changed['assets'][0]['digest'] = 'sha256:' + 'c' * 64
        with self.assertRaisesRegex(ValueError, 'changed during'):
            inputs.select_releases(LATEST, 2, api=GitHub(pages={1: [changed]}))
        older = release(11, OLDER)
        with self.assertRaisesRegex(ValueError, 'duplicate or changing'):
            inputs.select_releases(LATEST, 3, api=GitHub(pages={1: [release(), older, older]}))

    def test_newer_release_before_selected_history_anchor_is_not_staged(self):
        newer = release(20, 'v8.99.0-1')
        history = [newer, release(), release(11, OLDER)]
        self.assertEqual([r['tag'] for r in inputs.select_releases(LATEST, 2, api=GitHub(pages={1: history}))],
                         [LATEST, OLDER])

    def test_absent_history_anchor_fails_instead_of_returning_unrelated_versions(self):
        with self.assertRaisesRegex(ValueError, 'not found in public history'):
            inputs.select_releases(LATEST, 2, api=GitHub(pages={1: [release(11, OLDER)]}))

    def test_unbounded_keep_or_nonregular_tag_fails_before_network(self):
        api = GitHub()
        for tag, keep in [('trailblazer-v8.40.0', 1), ('../main', 1), (LATEST, 0), (LATEST, 51), (LATEST, True)]:
            with self.subTest(tag=tag, keep=keep), self.assertRaises(ValueError):
                inputs.select_releases(tag, keep, api=api)
        self.assertEqual(api.calls, [])

    def test_staging_binds_apk_and_tag_commit_and_never_uses_main(self):
        api = GitHub()
        calls = []
        def download(url, target, size, digest, *, hosts):
            calls.append((url, size, digest, hosts))
            target.write_bytes(APK)
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / 'apks'
            record = inputs.stage_releases(LATEST, 1, destination, api=api, downloader=download)
            self.assertEqual(record['source_commit'], 'b' * 40)
            self.assertEqual(record['listing_commit'], record['source_commit'])
            self.assertEqual(record['tag_object'], 'a' * 40)
            self.assertEqual((destination / (LATEST + '.apk')).read_bytes(), APK)
            self.assertEqual(record['releases'][0]['sha256'], APK_SHA)
            self.assertEqual(calls[0][1:3], (len(APK), APK_SHA))
            self.assertFalse(any('main' in endpoint for endpoint in api.calls))

    def test_legacy_listing_exception_requires_both_exact_tag_and_source(self):
        tag, commit = next(iter(inputs.LEGACY_LISTINGS))
        expected = 'af90bcda4ed7c83d4eee7582d6f3844b018500b0'
        self.assertEqual(inputs.listing_commit(tag, commit), expected)
        self.assertEqual(inputs.listing_commit(tag, 'b' * 40), 'b' * 40)
        self.assertEqual(inputs.listing_commit(LATEST, commit), commit)

    def test_workflows_bind_data_only_metadata_export_to_the_verified_receipt(self):
        root = Path(__file__).resolve().parents[1]
        for name in ('feeds.yml', 'feeds-dry-run.yml'):
            workflow = (root / '.github/workflows' / name).read_text()
            with self.subTest(workflow=name):
                self.assertEqual(workflow.count('uses: actions/checkout@'), 2 if name == 'feeds.yml' else 1)
                self.assertNotIn('repository:', workflow)
                self.assertNotIn('steps.inputs.outputs.', workflow)
                self.assertIn('release_inputs.py metadata --receipt "$RUNNER_TEMP/release-inputs.json"', workflow)
                self.assertIn('--output "$RUNNER_TEMP/app-metadata" --evidence "$RUNNER_TEMP/metadata-inputs.json"', workflow)
                self.assertIn('--native-plan "$RUNNER_TEMP/app-metadata/build-inputs/native-artifacts.json"', workflow)
                self.assertIn('--fastlane "$RUNNER_TEMP/app-metadata/fastlane/metadata/android"', workflow)
                self.assertIn('${{ runner.temp }}/metadata-inputs.json', workflow)
                self.assertNotIn('/main/build-inputs/', workflow)
                self.assertNotIn('ref: main', workflow)

    def test_changed_release_after_download_is_rejected(self):
        api = GitHub()
        def change(_url, target, *_args, **_kwargs):
            target.write_bytes(APK)
            api.latest['assets'][0]['id'] += 1
        with tempfile.TemporaryDirectory() as temporary, self.assertRaisesRegex(ValueError, 'changed while'):
            inputs.stage_releases(LATEST, 1, Path(temporary) / 'apks', api=api, downloader=change)

    def test_changed_source_tag_after_download_is_rejected(self):
        api = GitHub()
        def change(_url, target, *_args, **_kwargs):
            target.write_bytes(APK)
            api.commit = 'c' * 40
        with tempfile.TemporaryDirectory() as temporary, self.assertRaisesRegex(ValueError, 'source tag changed'):
            inputs.stage_releases(LATEST, 1, Path(temporary) / 'apks', api=api, downloader=change)

    def test_lightweight_or_wrong_source_ref_is_rejected(self):
        for response in ({'ref': 'refs/tags/' + LATEST, 'object': {'type': 'commit', 'sha': 'b' * 40}},
                         {'ref': 'refs/heads/main', 'object': {'type': 'tag', 'sha': 'a' * 40}}):
            with self.subTest(response=response), self.assertRaises(ValueError):
                inputs.source_commit(LATEST, api=lambda _: response)

    def test_tag_cycle_is_bounded(self):
        def api(endpoint):
            if '/git/ref/' in endpoint:
                return {'ref': 'refs/tags/' + LATEST, 'object': {'type': 'tag', 'sha': 'a' * 40}}
            return {'sha': 'a' * 40, 'object': {'type': 'tag', 'sha': 'a' * 40}}
        with self.assertRaisesRegex(ValueError, 'nesting'):
            inputs.source_commit(LATEST, api=api)

    def test_streamed_download_checks_digest_size_and_final_origin(self):
        class Response(io.BytesIO):
            status = 200
            def geturl(self):
                return final_url
        url = 'https://github.com/0cwa/collieim-android/releases/download/' + LATEST + '/collie.apk'
        for final_url, body, size, digest in [
                ('https://release-assets.githubusercontent.com/asset', APK, len(APK), APK_SHA),
                ('https://example.invalid/asset', APK, len(APK), APK_SHA),
                (url, APK + b'x', len(APK), APK_SHA),
                (url, APK[:-1], len(APK), APK_SHA),
                (url, APK, len(APK), 'f' * 64)]:
            with self.subTest(final_url=final_url, size=len(body), digest=digest), tempfile.TemporaryDirectory() as temporary:
                target = Path(temporary) / 'collie.apk'
                valid = final_url.endswith('/asset') and 'release-assets' in final_url
                with patch.object(inputs.urllib.request.OpenerDirector, 'open', return_value=Response(body)):
                    if valid:
                        inputs.download(url, target, size, digest, hosts={'github.com', 'release-assets.githubusercontent.com'})
                        self.assertEqual(target.read_bytes(), APK)
                    else:
                        with self.assertRaises(ValueError):
                            inputs.download(url, target, size, digest, hosts={'github.com', 'release-assets.githubusercontent.com'})

    def test_redirect_handler_blocks_unapproved_hops_before_they_are_contacted(self):
        contacted = []
        class Origin(urllib.request.BaseHandler):
            handler_order = 100
            def https_open(self, request):
                contacted.append(request.full_url)
                headers = Message()
                headers['Location'] = redirect
                response = urllib.request.addinfourl(io.BytesIO(b''), headers, request.full_url,
                                                    302 if len(contacted) == 1 else 200)
                response.msg = 'Found' if len(contacted) == 1 else 'OK'
                return response
        for redirect in ('https://example.invalid/asset', 'http://github.com/asset'):
            contacted.clear()
            opener = urllib.request.build_opener(Origin(), inputs.FixedOriginRedirect({'github.com'}))
            with self.subTest(redirect=redirect), self.assertRaisesRegex(ValueError, 'redirect left'):
                opener.open('https://github.com/asset')
            self.assertEqual(contacted, ['https://github.com/asset'])
        redirect = 'https://github.com/redirected'
        contacted.clear()
        opener = urllib.request.build_opener(Origin(), inputs.FixedOriginRedirect({'github.com'}))
        with opener.open('https://github.com/asset') as response:
            self.assertEqual(response.status, 200)
        self.assertEqual(contacted, ['https://github.com/asset', redirect])

    def test_native_records_are_validated_before_any_download(self):
        good = {'path': 'im/collie/native/library/1/library.pom', 'bytes': 4,
                'sha256': hashlib.sha256(b'data').hexdigest()}
        good['url'] = inputs.ORIGIN + inputs.NATIVE_PREFIX + good['path']
        for changed in ({'path': '../private'}, {'path': '/absolute'}, {'path': 'a//b'},
                        {'url': 'https://example.invalid/library.pom'}, {'bytes': True},
                        {'sha256': 'invalid'}, {'bytes': inputs.MAX_BYTES + 1}):
            with self.subTest(changed=changed), tempfile.TemporaryDirectory() as temporary:
                with self.assertRaises(ValueError), patch.object(inputs, 'download') as download:
                    inputs.stage_native({'files': [{**good, **changed}]}, Path(temporary) / 'native', downloader=download)
                download.assert_not_called()
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            inputs.native_records({'files': [good, good]})
        child = {**good, 'path': good['path'] + '/child', 'url': good['url'] + '/child'}
        with tempfile.TemporaryDirectory() as temporary, patch.object(inputs, 'download') as downloader:
            with self.assertRaisesRegex(ValueError, 'file/directory collision'):
                inputs.stage_native({'files': [good, child]}, Path(temporary) / 'native', downloader=downloader)
            downloader.assert_not_called()

    def test_each_native_file_keeps_its_own_versioned_route(self):
        # Collie 8.36.3-1's plan serves libsignal from native-inputs/v2 and ringrtc from v1.
        files = []
        for route, path in (('native-inputs/v2/maven/', 'im/collie/native/libsignal/1/libsignal.pom'),
                            ('native-inputs/v1/maven/', 'im/collie/native/ringrtc/1/ringrtc.pom')):
            files.append({'path': path, 'url': inputs.ORIGIN + route + path, 'bytes': 4,
                          'sha256': hashlib.sha256(b'data').hexdigest()})
        plan = {'repository': inputs.ORIGIN + 'native-inputs/v2/maven/', 'files': files}
        self.assertEqual(set(inputs.native_records(plan)),
                         {'native-inputs/v2/maven/im/collie/native/libsignal/1/libsignal.pom',
                          'native-inputs/v1/maven/im/collie/native/ringrtc/1/ringrtc.pom'})
        for url in ('https://example.invalid/native-inputs/v2/maven/' + files[0]['path'],
                    inputs.ORIGIN + 'native-inputs/v0/maven/' + files[0]['path'],
                    inputs.ORIGIN + 'fdroid/repo/' + files[0]['path'],
                    inputs.ORIGIN + 'native-inputs/v2/maven/other/' + files[0]['path']):
            with self.subTest(url=url), self.assertRaisesRegex(ValueError, 'noncanonical'):
                inputs.native_records({'files': [{**files[0], 'url': url}]})
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            def download(url, target, size, digest, *, hosts):
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b'data')
            self.assertEqual(inputs.stage_native(plan, root / 'native', downloader=download), 2)
            (root / 'plan.json').write_text(json.dumps(plan))
            import feed
            self.assertEqual(feed.copy_native(root / 'native', root / 'plan.json', root / 'site'), 2)
            for name in inputs.native_records(plan):
                self.assertEqual((root / 'site' / name).read_bytes(), b'data')

    def test_native_inputs_use_the_exact_declared_fixed_origin_bytes(self):
        path = 'im/collie/native/library/1/library.pom'
        value = {'path': path, 'url': inputs.ORIGIN + inputs.NATIVE_PREFIX + path,
                 'bytes': 4, 'sha256': hashlib.sha256(b'data').hexdigest()}
        calls = []
        def download(url, target, size, digest, *, hosts):
            calls.append((url, size, digest, hosts))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b'data')
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / 'native'
            self.assertEqual(inputs.stage_native({'files': [value]}, destination, downloader=download), 1)
            self.assertEqual((destination / inputs.NATIVE_PREFIX / path).read_bytes(), b'data')
            self.assertEqual(calls, [(value['url'], 4, value['sha256'], {'repo.collie.im'})])
            with self.assertRaisesRegex(ValueError, 'new native'):
                inputs.stage_native({'files': [value]}, destination, downloader=download)


if __name__ == '__main__':
    unittest.main()
