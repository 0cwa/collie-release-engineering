"""Beta release selection and CI-build checks without GitHub or APK downloads."""
import copy
import hashlib
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import apk_match
import beta_inputs as beta
import channel_profile as channels
import feed
import retained_artifacts as retention
from test_release_inputs import add_manifest, download_release

APK = b'published signed Beta APK fixture'
APK_SHA = hashlib.sha256(APK).hexdigest()
UNSIGNED = b'unsigned Beta APK fixture'
TAG = 'trailblazer-v8.36.3-1-beta1'
VERSION = '8.36.3-1-beta1'
COMMIT = 'c' * 40
TAG_OID = 'd' * 40
ROOT = f'repos/{beta.APP}/'
release_inputs_origin = beta.release_inputs.ORIGIN


def release(number=7, tag=TAG, **changes):
    version = tag[len(channels.BETA.tag_prefix):]
    value = {'id': number, 'tag_name': tag, 'name': 'Collie TrailBlazer Beta ' + version, 'draft': False,
             'prerelease': True, 'immutable': True, 'published_at': '2026-11-21T22:28:21Z',
             'assets': [{'id': number + 100, 'name': 'trailblazer-beta.apk', 'state': 'uploaded',
                         'size': len(APK), 'digest': 'sha256:' + APK_SHA,
                         'browser_download_url': f'https://github.com/{beta.APP}/releases/download/{tag}/trailblazer-beta.apk'}]}
    value.update(changes)
    return value


def artifact_zip(commit=COMMIT, apk=UNSIGNED, extra=None, sums=None):
    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w') as bundle:
        bundle.writestr('trailblazer-beta.apk', apk)
        bundle.writestr('source-commit.txt', commit + '\n')
        bundle.writestr('source-tree.txt', 'e' * 40 + '\n')
        bundle.writestr('SHA256SUMS', sums if sums is not None else
                        f'{hashlib.sha256(apk).hexdigest()}  trailblazer-beta.apk\n')
        for name in extra or ():
            bundle.writestr(name, b'x')
    return data.getvalue()


class GitHub:
    def __init__(self):
        self.release = release()
        self.pages = {1: [release(1, 'v8.36.3-1-rc2', prerelease=True, name='Collie 8.36.3-1-rc2'), self.release]}
        self.verified = True
        self.run = {'id': 44948699471, 'run_attempt': 1, 'head_sha': COMMIT, 'head_branch': 'main',
                    'display_title': 'Build TrailBlazer Beta',
                    'event': 'workflow_dispatch', 'path': '.github/workflows/build-release.yml',
                    'status': 'completed', 'conclusion': 'success', 'head_repository': {'full_name': beta.APP}}
        self.zip = artifact_zip()
        self.artifact = {'id': 14726064808, 'name': 'unsigned-release-apk', 'expired': False,
                         'size_in_bytes': len(self.zip), 'digest': 'sha256:' + hashlib.sha256(self.zip).hexdigest(),
                         'workflow_run': {'id': 44948699471, 'head_sha': COMMIT}}

    def __call__(self, endpoint):
        if endpoint == ROOT + 'releases/tags/' + TAG:
            return copy.deepcopy(self.release)
        if endpoint.startswith(ROOT + 'releases?'):
            return copy.deepcopy(self.pages.get(int(endpoint.rsplit('=', 1)[1]), []))
        if endpoint == ROOT + 'git/ref/tags/' + TAG:
            return {'ref': 'refs/tags/' + TAG, 'object': {'type': 'tag', 'sha': TAG_OID}}
        if endpoint == ROOT + 'git/tags/' + TAG_OID:
            return {'sha': TAG_OID, 'tag': TAG, 'object': {'type': 'commit', 'sha': COMMIT},
                    'verification': {'verified': self.verified}}
        if endpoint.startswith(ROOT + 'actions/workflows/build-release.yml/runs?head_sha=' + COMMIT):
            return {'workflow_runs': copy.deepcopy(getattr(self, 'runs', [self.run]))}
        if endpoint.startswith(ROOT + f"actions/runs/{self.run['id']}/artifacts"):
            return {'artifacts': [copy.deepcopy(self.artifact)]}
        raise AssertionError('unexpected API request: ' + endpoint)


def copy_download(data):
    def download(url, destination, size, digest, *, hosts):
        assert hosts == beta.ASSET_HOSTS and url.startswith('https://github.com/0cwa/collieim-android/')
        if len(data) != size or hashlib.sha256(data).hexdigest() != digest:
            raise ValueError('download differs from approved size or digest')
        Path(destination).parent.mkdir(parents=True, exist_ok=True)
        Path(destination).write_bytes(data)
    return download


class BetaInputTests(unittest.TestCase):
    def setUp(self):
        self.api = GitHub()
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        identity = {'package': channels.BETA.package, 'version_code': 178201, 'version_name': VERSION,
                    'apk_certificate_sha256': channels.BETA.apk_certificate}
        patcher = patch.object(beta, 'apk_identity', return_value=identity)
        self.identity = patcher.start()
        self.addCleanup(patcher.stop)

    def artifact_download(self, artifact, destination):
        Path(destination).write_bytes(self.api.zip)
        if beta.file_sha256(destination) != artifact['sha256']:
            raise ValueError('downloaded build artifact differs')

    def verify(self):
        return beta.verify(TAG, self.root / 'work', self.root / 'tools', api=self.api,
                           downloader=copy_download(APK), artifact_downloader=self.artifact_download)

    def test_newest_published_trailblazer_release_is_selected_and_drafts_are_ignored(self):
        newer = release(9, 'trailblazer-v8.37.0-1-beta1', published_at='2026-12-01T00:00:00Z')
        draft = release(10, 'trailblazer-v8.38.0-1-beta1', draft=True, published_at=None)
        self.api.pages = {1: [draft, newer, self.api.release]}
        self.assertEqual(beta.newest_tag(api=self.api), 'trailblazer-v8.37.0-1-beta1')
        self.api.pages = {1: [release(1, 'v8.36.3-1-rc2')]}
        self.assertIsNone(beta.newest_tag(api=self.api))

    def test_published_release_must_be_an_immutable_prerelease_with_one_beta_apk(self):
        self.assertEqual(beta.beta_release(release())['version_name'], VERSION)
        for change in ({'prerelease': False}, {'immutable': False}, {'draft': True}, {'name': 'Collie ' + VERSION},
                       {'tag_name': 'v8.36.3-1-beta1'}, {'assets': []},
                       {'assets': release()['assets'] * 2}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                beta.beta_release(release(**change))
        asset = release()['assets'][0]
        for key, value in (('browser_download_url', 'https://example.invalid/trailblazer-beta.apk'),
                           ('digest', 'sha1:' + 'a' * 40), ('state', 'starter'), ('size', 0)):
            with self.subTest(asset=key), self.assertRaises(ValueError):
                beta.beta_release(release(assets=[{**asset, key: value}]))

    def test_draft_release_is_no_longer_required(self):
        result = self.verify()
        self.assertEqual((result['release_id'], result['sha256'], result['source_commit'], result['version_code']),
                         (7, APK_SHA, COMMIT, 178201))
        self.assertEqual(result['build']['run_id'], 44948699471)
        self.assertEqual(Path(result['unsigned_apk']).read_bytes(), UNSIGNED)
        self.assertEqual(Path(result['published_apk']).read_bytes(), APK)
        self.assertNotIn('manifest', result)

    def test_beta_manifest_is_verified_in_both_feed_jobs(self):
        manifest, raw = add_manifest(self.api.release, channels.BETA, COMMIT, 178201)
        downloader = download_release(APK, raw)
        verified = beta.verify(TAG, self.root / 'work', self.root / 'tools', api=self.api,
                               downloader=downloader, artifact_downloader=self.artifact_download)
        staged = beta.stage(TAG, APK_SHA, COMMIT, self.root / 'apks', build_tools=self.root / 'tools',
                            api=self.api, downloader=downloader)
        self.assertEqual(verified['manifest'], manifest)
        self.assertEqual(staged['manifest'], manifest)

    def test_beta_manifest_cannot_claim_a_different_commit(self):
        _, raw = add_manifest(self.api.release, channels.BETA, 'f' * 40, 178201)
        downloader = download_release(APK, raw)
        with self.assertRaisesRegex(ValueError, 'release.json differs'):
            beta.verify(TAG, self.root / 'work', self.root / 'tools', api=self.api,
                        downloader=downloader, artifact_downloader=self.artifact_download)
        with self.assertRaisesRegex(ValueError, 'release.json differs'):
            beta.stage(TAG, APK_SHA, COMMIT, self.root / 'apks', build_tools=self.root / 'tools',
                       api=self.api, downloader=downloader)

    def test_tag_must_be_a_verified_annotated_tag(self):
        self.api.verified = False
        with self.assertRaisesRegex(ValueError, 'verified signed tag'):
            self.verify()

    def test_wrong_package_signer_or_version_is_rejected(self):
        for key, value in (('package', 'im.collie.app'), ('apk_certificate_sha256', channels.STABLE.apk_certificate),
                           ('version_name', '8.36.3-1-beta2')):
            with self.subTest(key=key), tempfile.TemporaryDirectory() as tmp:
                self.identity.return_value = {**self.identity.return_value, key: value}
                with self.assertRaisesRegex(ValueError, 'package, signer or version'):
                    beta.verify(TAG, Path(tmp) / 'work', self.root, api=self.api, downloader=copy_download(APK),
                                artifact_downloader=self.artifact_download)
            self.identity.return_value = {'package': channels.BETA.package, 'version_code': 178201,
                                          'version_name': VERSION, 'apk_certificate_sha256': channels.BETA.apk_certificate}

    def test_build_must_be_a_successful_main_dispatch_of_the_tag_commit(self):
        # Main carries the reviewed candidate; the TrailBlazer branch is no longer a feed source.
        self.assertEqual(beta.build_artifact(COMMIT, api=self.api)['run_id'], 44948699471)
        for key, value in (('head_branch', 'trailblazer-beta'), ('event', 'pull_request'), ('conclusion', 'failure'),
                           ('path', '.github/workflows/other.yml'), ('head_sha', 'f' * 40),
                           ('head_repository', {'full_name': 'someone/fork'})):
            with self.subTest(key=key):
                original = copy.deepcopy(self.api.run)
                self.api.run[key] = value
                with self.assertRaisesRegex(ValueError, 'no successful main build'):
                    beta.build_artifact(COMMIT, api=self.api)
                self.api.run = original
        self.api.artifact['expired'] = True
        with self.assertRaisesRegex(ValueError, 'unexpired APK artifact'):
            beta.build_artifact(COMMIT, api=self.api)

    def test_newer_stable_run_cannot_shadow_older_beta_and_edition_evidence_is_required(self):
        beta_run = copy.deepcopy(self.api.run)
        stable_run = {**copy.deepcopy(beta_run), 'id': beta_run['id'] + 1,
                      'display_title': 'Build Stable'}
        self.api.runs = [stable_run, beta_run]
        self.assertEqual(beta.build_artifact(COMMIT, api=self.api)['run_id'], beta_run['id'])
        for title in (None, 'Build Stable', 'Build TrailBlazer Beta '):
            with self.subTest(title=title):
                self.api.runs = [{**beta_run, 'display_title': title}]
                with self.assertRaisesRegex(ValueError, 'no successful main build'):
                    beta.build_artifact(COMMIT, api=self.api)

    def test_artifact_must_be_from_the_same_commit_with_matching_checksums(self):
        for archive, pattern in ((artifact_zip(commit='f' * 40), 'different commit'),
                                 (artifact_zip(sums='0' * 64 + '  trailblazer-beta.apk\n'), 'SHA256SUMS'),
                                 (artifact_zip(extra=['other.apk']), 'exactly')):
            with self.subTest(pattern=pattern), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / 'artifact.zip'
                path.write_bytes(archive)
                with self.assertRaisesRegex(ValueError, pattern):
                    beta.extract_unsigned(path, Path(tmp) / 'out', COMMIT)

    def test_release_change_during_verification_is_rejected(self):
        def changing(url, destination, size, digest, *, hosts):
            copy_download(APK)(url, destination, size, digest, hosts=hosts)
            self.api.release = release(assets=[{**release()['assets'][0], 'id': 999}])
        with self.assertRaisesRegex(ValueError, 'changed during verification'):
            beta.verify(TAG, self.root / 'work', self.root, api=self.api, downloader=changing,
                        artifact_downloader=self.artifact_download)

    def test_stage_requires_the_verified_digest_and_source(self):
        receipt = beta.stage(TAG, APK_SHA, COMMIT, self.root / 'apks', api=self.api, downloader=copy_download(APK))
        self.assertEqual((self.root / 'apks/trailblazer-beta.apk').read_bytes(), APK)
        self.assertEqual(receipt['source_commit'], COMMIT)
        for sha, commit in (('0' * 64, COMMIT), (APK_SHA, 'f' * 40)):
            with self.subTest(sha=sha, commit=commit), self.assertRaisesRegex(ValueError, 'differs from the verified'):
                beta.stage(TAG, sha, commit, self.root / ('other-' + commit[:1] + sha[:1]), api=self.api,
                           downloader=copy_download(APK))

    def test_served_state_comparison_uses_the_beta_version(self):
        self.assertIsNone(beta.served_version(fetch=lambda: b'{"schema":1,"latest":"v8.35.1-3-rc2"}'))
        state = json.dumps({'beta': {'version_name': VERSION, 'version_code': 178201}}).encode()
        self.assertEqual(beta.served_version(fetch=lambda: state), VERSION)
        with self.assertRaises(ValueError):
            beta.served_version(fetch=lambda: b'{"beta": "x"}')

    def test_served_beta_is_not_new_and_a_forced_rebuild_cannot_republish_it(self):
        # Regression: a forced whole-site run used to report the served Beta as changed, and
        # feed.py then rejected its versionCode against the same served floor.
        self.assertFalse(beta.is_new_beta(TAG, VERSION))
        self.assertTrue(beta.is_new_beta(TAG, None))
        self.assertTrue(beta.is_new_beta(TAG, '8.36.3-1-beta0'))
        self.assertFalse(beta.is_new_beta(None, VERSION))
        with patch.object(beta, 'newest_tag', return_value=TAG), patch.object(beta, 'served_version', return_value=VERSION), \
                patch('sys.argv', ['beta_inputs.py', 'select']), patch('sys.stdout', io.StringIO()) as out:
            beta.main()
        self.assertEqual(json.loads(out.getvalue()), {'tag': TAG, 'served': VERSION, 'changed': False})
        # The dry-run workflow still asks to verify the newest Beta whatever is served.
        with patch.object(beta, 'newest_tag', return_value=TAG), patch.object(beta, 'served_version', return_value=VERSION), \
                patch('sys.argv', ['beta_inputs.py', 'select', '--force']), patch('sys.stdout', io.StringIO()) as out:
            beta.main()
        self.assertTrue(json.loads(out.getvalue())['changed'])
        with self.assertRaisesRegex(ValueError, 'must increase'):
            feed.release_identity(channels.BETA, TAG, 'Collie TrailBlazer Beta ' + VERSION, VERSION, 178201, 178201)

    def test_native_plan_of_the_beta_tag_is_exported_from_its_commit_only(self):
        plan = json.dumps({'files': [{'path': 'im/collie/x/1/x-1.pom', 'bytes': 4, 'sha256': 'a' * 64,
            'url': release_inputs_origin + 'native-inputs/v2/maven/im/collie/x/1/x-1.pom'}]}).encode()
        entry = {'type': 'blob', 'mode': '100644', 'size': len(plan), 'sha': 'e' * 40}
        class Reader:
            def __init__(self, api): pass
            def root_tree(self, commit):
                assert commit == COMMIT
                return 'f' * 40
            def directory(self, root, path):
                assert (root, path) == ('f' * 40, 'build-inputs')
                return 'd' * 40
            def tree(self, oid): return {'native-artifacts.json': entry}
            def blob(self, found): return plan
        output = self.root / 'plan.json'
        with patch.object(beta.release_inputs, 'MetadataReader', Reader):
            result = beta.export_native_plan(TAG, output, api=self.api, metadata_api=None)
        self.assertEqual(output.read_bytes(), plan)
        self.assertEqual(result, {'tag': TAG, 'source_commit': COMMIT, 'sha256': hashlib.sha256(plan).hexdigest()})
        entry['mode'] = '100755'
        with patch.object(beta.release_inputs, 'MetadataReader', Reader), self.assertRaisesRegex(ValueError, 'regular JSON'):
            beta.export_native_plan(TAG, self.root / 'other.json', api=self.api, metadata_api=None)
        self.api.verified = False
        with self.assertRaisesRegex(ValueError, 'verified signed tag'):
            beta.export_native_plan(TAG, self.root / 'third.json', api=self.api, metadata_api=None)

    def test_native_plan_command_uses_the_served_beta_and_does_nothing_without_one(self):
        out = self.root / 'plan.json'
        run = lambda served: (patch.object(beta, 'served_version', return_value=served),
                              patch('sys.argv', ['beta_inputs.py', 'native-plan', '--output', str(out)]),
                              patch('sys.stdout', io.StringIO()))
        with patch.object(beta, 'export_native_plan', return_value={'tag': TAG}) as export:
            a, b, c = run(VERSION)
            with a, b, c:
                beta.main()
            export.assert_called_once_with(TAG, out)
            export.reset_mock()
            a, b, c = run(None)
            with a, b, c as printed:
                beta.main()
            export.assert_not_called()
            self.assertEqual(json.loads(printed.getvalue()), {'tag': ''})

    def test_apk_match_refuses_changed_pinned_helpers(self):
        tools = self.root / 'tools'
        tools.mkdir()
        for name in apk_match.TOOLS:
            (tools / name).write_text('changed helper')
        with self.assertRaisesRegex(ValueError, 'pinned APK comparison helper differs'):
            apk_match.same_build(self.root / 'unsigned.apk', self.root / 'published.apk', tools)
        self.assertTrue(all(url.startswith('https://raw.githubusercontent.com/obfusk/') and len(digest) == 64
                            for url, _size, digest in apk_match.TOOLS.values()))


class BetaRouteRebuildTests(unittest.TestCase):
    def test_rebuilt_beta_route_keeps_only_previous_immutable_urls(self):
        record = {'sha256': 'a' * 64, 'bytes': 1}
        files = {'fdroid/beta/repo/entry.jar': record,
                 'fdroid/beta/repo/im.collie.app.trailblazer_178201.apk': record,
                 'fdroid/repo/entry.jar': record}
        previous = retention.PreviousSite(Path('previous'), files, record, {'version_name': VERSION,
                                                                            'version_code': 178201})
        self.assertEqual(set(previous.retained_files(False, True)),
                         {'fdroid/beta/repo/im.collie.app.trailblazer_178201.apk'})
        with self.assertRaisesRegex(ValueError, 'preserve-beta or a rebuilt'):
            previous.retained_files(False)
        with self.assertRaisesRegex(ValueError, 'either preserved or rebuilt'):
            previous.retained_files(True, True)

    def test_beta_route_build_uses_served_version_floor_and_checks_the_index(self):
        calls = []
        def build(*args, **kwargs):
            calls.append(kwargs)
        tuples = {(178202, '8.36.3-1-beta2', 'b' * 64, 'im.collie.app.trailblazer_178202.apk',
                   channels.BETA.apk_certificate)}
        with patch.object(feed, 'build_fdroid', side_effect=build), \
                patch.object(feed.binary_repo, 'verify_indexes', return_value={}) as verify, \
                patch.object(feed.binary_repo, 'release_tuples', return_value=tuples):
            result = feed.build_beta_route('apks', 'fastlane', 'trailblazer-v8.36.3-1-beta2', 'key', 'bt',
                                           'work', 'site', {'version_name': VERSION, 'version_code': 178201})
        self.assertEqual(result, {'version_name': '8.36.3-1-beta2', 'version_code': 178202})
        self.assertEqual(calls[0]['prior_version_code'], 178201)
        self.assertEqual(calls[0]['title'], 'Collie TrailBlazer Beta 8.36.3-1-beta2')
        self.assertTrue(calls[0]['prerelease'])
        verify.assert_called_once_with(Path('site/fdroid/beta/repo'), channels.BETA.repository_certificate,
                                       channels.BETA)
        with patch.object(feed, 'build_fdroid'), patch.object(feed.binary_repo, 'verify_indexes', return_value={}), \
                patch.object(feed.binary_repo, 'release_tuples', return_value=tuples), \
                self.assertRaisesRegex(ValueError, 'exactly the selected release'):
            feed.build_beta_route('apks', 'fastlane', TAG, 'key', 'bt', 'work', 'site', None)

    def test_scheduled_workflow_keeps_beta_behind_its_variable_and_out_of_the_signing_job(self):
        workflow = (Path(__file__).resolve().parents[1] / '.github/workflows/feeds.yml').read_text()
        beta_job = workflow[workflow.index('\n  beta:\n'):workflow.index('\n  deploy:\n')]
        self.assertIn("vars.BETA_FEED_ENABLED == 'true'", beta_job)
        self.assertNotIn('secrets.', beta_job)
        self.assertNotIn('environment:', beta_job)
        self.assertIn('apk_match.py', beta_job)
        self.assertIn('beta_inputs.py select >', beta_job)
        self.assertNotIn('--force', beta_job)
        self.assertNotIn('inputs.force', beta_job)
        self.assertIn('inputs.force', workflow[:workflow.index('\n  beta:\n')])
        deploy_job = workflow[workflow.index('\n  deploy:\n'):]
        self.assertIn('beta_inputs.py stage --tag "$BETA_TAG" --sha256 "$BETA_SHA256"', deploy_job)
        self.assertIn('${BETA_ARGS:---preserve-beta}', deploy_job)
        self.assertIn('beta_inputs.py native-plan --tag "$BETA_TAG"', deploy_job)
        self.assertIn('${KEEP_NATIVE}', deploy_job)


if __name__ == '__main__':
    unittest.main()
