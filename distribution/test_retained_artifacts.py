"""Publication regressions: frozen inputs, immutable URLs, cached indexes and capacity."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

import channel_profile as channels
import feed
import retained_artifacts as retention


OLD_NATIVE = 'native-inputs/v1/maven/org/signal/libsignal-client/1.0/libsignal-client-1.0.jar'
NEW_NATIVE = 'native-inputs/v1/maven/org/signal/libsignal-client/2.0/libsignal-client-2.0.jar'


def records(blobs):
    return {name: {'sha256': hashlib.sha256(blob).hexdigest(), 'bytes': len(blob)}
            for name, blob in blobs.items()}


def repository(profile, code, version, apk):
    route = 'fdroid/repo/' if profile.name == 'stable' else 'fdroid/beta/repo/'
    filename = f'{profile.package}_{code}.apk'
    digest = hashlib.sha256(apk).hexdigest()
    index = {'repo': {'address': profile.repository_url}, 'packages': {profile.package: {'versions': {
        digest: {'manifest': {'versionCode': code, 'versionName': version,
                             'signer': {'sha256': [profile.apk_certificate]}},
                 'file': {'name': '/' + filename, 'sha256': digest, 'size': len(apk)}}}}}}
    return {route + 'entry.jar': b'fixture signed entry ' + str(code).encode(),
            route + 'index-v1.jar': b'fixture signed v1 ' + str(code).encode(),
            route + 'index-v2.json': json.dumps(index, sort_keys=True).encode(),
            route + filename: apk}


def write_files(root, blobs):
    root.mkdir(parents=True, exist_ok=True)
    for name, blob in blobs.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(blob)


class RetainedArtifactsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.old = self.root / 'previous'
        self.baseline = self.root / 'baseline.json'
        self.site = self.root / 'site'
        self.old_apk = 'fdroid/repo/im.collie.app_101.apk'
        self.new_apk = 'fdroid/repo/im.collie.app_102.apk'
        self.blobs = {**repository(channels.STABLE, 101, '8.35.1', b'old verified APK'),
                      OLD_NATIVE: b'old native', 'index.html': b'old landing',
                      'assets/old.css': b'old stylesheet', 'CNAME': b'repo.collie.im\n'}
        # The real shared verifier's signature/hash/parity checks are covered in
        # test_binary_repo. These tests exercise our binding and retention policy.
        self.verifier = patch.object(retention.binary_repo, 'verify_indexes', side_effect=
            lambda root, _cert, _profile: json.loads((root / 'index-v2.json').read_bytes())).start()
        self.addCleanup(patch.stopall)

    def snapshot(self, blobs=None):
        blobs = self.blobs if blobs is None else blobs
        raw = retention.inventory_bytes(records(blobs))
        write_files(self.old, {**blobs, retention.INVENTORY: raw})
        self.baseline.write_text(json.dumps({'schema': 1, 'kind': 'collie-pages-deploy-baseline',
            'origin': retention.ORIGIN, 'inventory_sha256': hashlib.sha256(raw).hexdigest()}))
        return raw

    def load(self, **kwargs):
        return retention.load_previous_site(self.old, self.baseline,
            parse_inventory=feed._inventory_files, **kwargs)

    def candidate(self, apk=b'new verified APK'):
        blobs = {**repository(channels.STABLE, 102, '8.35.2', apk),
                 NEW_NATIVE: b'new native', 'index.html': b'new landing',
                 'CNAME': b'repo.collie.im\n'}
        write_files(self.site, blobs)
        return blobs

    def test_load_binds_all_files_and_checks_each_present_signed_route(self):
        self.blobs.update(repository(channels.BETA, 201, '8.40.0', b'Beta APK'))
        self.blobs['fdroid/repo/im.collie.app/en-US/abc=.png'] = b'image'
        raw = self.snapshot()
        previous = self.load()
        self.assertEqual(previous.inventory_record['sha256'], hashlib.sha256(raw).hexdigest())
        self.assertEqual(previous.beta['release'], {'version_code': 201, 'version_name': '8.40.0'})
        self.assertEqual(self.verifier.call_count, 2)
        self.verifier.assert_any_call(self.old / 'fdroid/repo', channels.STABLE.repository_certificate, channels.STABLE)
        self.verifier.assert_any_call(self.old / 'fdroid/beta/repo', channels.BETA.repository_certificate, channels.BETA)

    def test_missing_changed_and_extra_snapshot_files_are_rejected(self):
        for mutation in ('missing', 'changed', 'extra'):
            with self.subTest(mutation=mutation):
                self.snapshot()
                target = self.old / OLD_NATIVE
                if mutation == 'missing': target.unlink()
                elif mutation == 'changed': target.write_bytes(b'tampered')
                else: (self.old / 'unlisted.txt').write_bytes(b'unlisted')
                with self.assertRaisesRegex(ValueError, 'missing, extra, or changed'):
                    self.load()
                if mutation == 'extra': (self.old / 'unlisted.txt').unlink()
        self.verifier.assert_not_called()

    def test_missing_snapshot_or_different_inventory_cannot_rebase_itself(self):
        self.snapshot()
        (self.old / retention.INVENTORY).write_bytes(retention.inventory_bytes({}))
        with self.assertRaisesRegex(ValueError, 'differs from captured deploy baseline'):
            self.load()
        self.old.rename(self.root / 'moved')
        with self.assertRaisesRegex(ValueError, 'directory is missing or linked'):
            self.load()

    def test_duplicate_baseline_field_and_unbounded_inventory_are_rejected(self):
        raw = self.snapshot()
        self.baseline.write_text(self.baseline.read_text()[:-1] + ',"schema":1}')
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            self.load()
        self.snapshot()
        with self.assertRaisesRegex(ValueError, 'file or byte budget'):
            self.load(byte_limit=len(raw))
        with patch.object(retention, 'FILE_LIMIT', 1), self.assertRaisesRegex(ValueError, 'file or byte budget'):
            self.load()
        self.verifier.assert_not_called()

    def test_links_and_special_files_cannot_enter_a_verified_snapshot(self):
        for kind in ('symlink', 'directory symlink', 'hardlink', 'fifo'):
            with self.subTest(kind=kind):
                self.snapshot()
                target = self.old / OLD_NATIVE
                target.unlink()
                if kind == 'symlink': target.symlink_to(self.old / self.old_apk)
                elif kind == 'directory symlink': target.symlink_to(self.old / 'fdroid', target_is_directory=True)
                elif kind == 'hardlink': os.link(self.old / self.old_apk, target)
                else: os.mkfifo(target)
                with self.assertRaisesRegex(ValueError, 'regular, unlinked files'):
                    self.load()
                target.unlink()

    def test_noncanonical_paths_are_rejected_before_any_file_hashing(self):
        for name in ('../escape', 'native-inputs//bad', './index.html', '/absolute', 'x/../x'):
            with self.subTest(name=name):
                self.snapshot()
                entries = records(self.blobs)
                entries[name] = {'sha256': 'a' * 64, 'bytes': 1}
                raw = retention.inventory_bytes(entries)
                (self.old / retention.INVENTORY).write_bytes(raw)
                value = json.loads(self.baseline.read_text())
                value['inventory_sha256'] = hashlib.sha256(raw).hexdigest()
                self.baseline.write_text(json.dumps(value))
                with patch.object(retention, '_tree_records') as read_tree, self.assertRaisesRegex(ValueError, 'unsafe'):
                    self.load()
                read_tree.assert_not_called()

    def test_unknown_native_or_apk_paths_are_rejected_instead_of_dropped(self):
        paths = ('fdroid/repo/other.package_101.apk', 'fdroid/repo/im.collie.app_0.apk',
                 'fdroid/repo/nested/im.collie.app_101.apk', 'unexpected.apk',
                 'native-inputs/current/maven/org/lib/1/lib-1.jar',
                 'native-inputs/v1/maven/org/lib/maven-metadata.xml',
                 'native-inputs/v1/maven/org/lib/1-SNAPSHOT/lib-1-SNAPSHOT.jar',
                 'native-inputs/v1/maven/org/lib/1/lib-2.jar')
        for name in paths:
            with self.subTest(name=name):
                self.snapshot({**self.blobs, name: b'unexpected'})
                with self.assertRaisesRegex(ValueError, 'unexpected APK|unsupported native'):
                    self.load()
                (self.old / name).unlink()

    def test_immutable_path_policy_admits_only_known_archives_and_canonical_route_artwork(self):
        import pages
        generation = '_generations/' + 'a' * 64 + '/'
        for name in (*[generation + filename for filename in retention.ARCHIVE_INDEX_FILES],
                     generation + 'repository-receipt.json', OLD_NATIVE):
            with self.subTest(name=name):
                self.assertTrue(retention.immutable_public_path(name))
                self.assertTrue(pages.immutable(name))
        for profile, route in ((channels.STABLE, 'fdroid/repo/'), (channels.BETA, 'fdroid/beta/repo/')):
            for relative in ('diff/123.json', f'icons-640/{profile.package}.101.png',
                             f'{profile.package}/en-US/phoneScreenshots/abc_def=.png'):
                with self.subTest(name=route + relative):
                    self.assertTrue(retention.immutable_public_path(route + relative))
                    self.assertTrue(pages.immutable(route + relative))
            for relative in ('entry.jar', 'index-v2.json', 'icons/icon.png', 'icons-640/icon.png',
                             'diff/latest.json', 'other.package/en-US/abc.png',
                             f'{profile.package}/en-US/phoneScreenshots/1.png',
                             f'{profile.package}/en-US/phoneScreenshots/settings-overview.png'):
                with self.subTest(name=route + relative):
                    self.assertFalse(retention.immutable_public_path(route + relative))
        for name in (generation + 'private.json', '_generations/short/entry.jar',
                     '_generations/' + 'A' * 64 + '/entry.jar', 'unreviewed.apk', 'assets/old.css'):
            self.assertFalse(retention.immutable_public_path(name))

    def test_unknown_generation_shapes_are_rejected_instead_of_silently_dropped(self):
        generation = '_generations/' + 'a' * 64 + '/'
        for name in ('_generations/not-a-digest/entry.jar', generation + 'unreviewed.json',
                     generation + 'nested/entry.jar', '_generations/' + 'A' * 64 + '/index-v2.json'):
            with self.subTest(name=name):
                self.snapshot({**self.blobs, name: b'unreviewed archive'})
                with self.assertRaisesRegex(ValueError, 'unexpected generation archive path'):
                    self.load()
                (self.old / name).unlink()

    def test_bad_signed_route_and_apk_identity_are_rejected(self):
        self.snapshot()
        with patch.object(retention.binary_repo, 'verify_indexes', side_effect=ValueError('bad signature')):
            with self.assertRaisesRegex(ValueError, 'bad signature'): self.load()
        for mutation in ('address', 'code', 'signer'):
            with self.subTest(mutation=mutation):
                index = json.loads(self.blobs['fdroid/repo/index-v2.json'])
                version = next(iter(index['packages'][channels.STABLE.package]['versions'].values()))
                if mutation == 'address': index['repo']['address'] = channels.BETA.repository_url
                elif mutation == 'code': version['manifest']['versionCode'] = 99
                else: version['manifest']['signer']['sha256'] = [channels.BETA.apk_certificate]
                changed = {**self.blobs, 'fdroid/repo/index-v2.json': json.dumps(index).encode()}
                self.snapshot(changed)
                with self.assertRaisesRegex(ValueError, 'address differs|APK identity differs'):
                    self.load()

    def test_retention_keeps_old_urls_without_rewriting_the_new_index_or_landing(self):
        self.snapshot()
        previous = self.load()
        current = self.candidate()
        result = retention.retain(self.site, previous, preserve_beta=False)
        self.assertEqual((self.site / self.old_apk).read_bytes(), self.blobs[self.old_apk])
        self.assertEqual((self.site / OLD_NATIVE).read_bytes(), self.blobs[OLD_NATIVE])
        self.assertEqual((self.site / 'fdroid/repo/index-v2.json').read_bytes(), current['fdroid/repo/index-v2.json'])
        versions = retention.binary_repo.release_tuples(json.loads(current['fdroid/repo/index-v2.json']))
        self.assertEqual({row[0] for row in versions}, {102})
        self.assertEqual((self.site / 'index.html').read_bytes(), b'new landing')
        self.assertFalse((self.site / 'assets/old.css').exists())
        self.assertEqual(result[self.old_apk], records(self.blobs)[self.old_apk])

    def test_already_retained_apk_survives_a_second_update_without_entering_indexes(self):
        self.blobs['fdroid/repo/im.collie.app_100.apk'] = b'previously retained APK'
        self.snapshot()
        previous = self.load()
        self.candidate()
        first = retention.retain(self.site, previous, preserve_beta=False)
        raw = retention.inventory_bytes(first)
        (self.site / retention.INVENTORY).write_bytes(raw)
        second_baseline = self.root / 'second-baseline.json'
        second_baseline.write_text(json.dumps({'schema': 1, 'kind': 'collie-pages-deploy-baseline',
            'origin': retention.ORIGIN, 'inventory_sha256': hashlib.sha256(raw).hexdigest()}))
        second = retention.load_previous_site(self.site, second_baseline, parse_inventory=feed._inventory_files)
        final = self.root / 'third'
        current = repository(channels.STABLE, 103, '8.35.3', b'latest APK')
        write_files(final, current)
        retention.retain(final, second, preserve_beta=False)
        self.assertEqual({p.name for p in (final / 'fdroid/repo').glob('*.apk')},
            {'im.collie.app_100.apk', 'im.collie.app_101.apk', 'im.collie.app_102.apk', 'im.collie.app_103.apk'})
        self.assertEqual((final / 'fdroid/repo/index-v2.json').read_bytes(), current['fdroid/repo/index-v2.json'])

    def test_archived_evidence_diffs_and_artwork_survive_consecutive_stable_updates_with_beta(self):
        generation = '_generations/' + 'a' * 64 + '/'
        immutable = {generation + 'entry.jar': b'archived signed entry',
                     generation + 'repository-receipt.json': b'{"fixture":"reviewed Beta receipt"}',
                     'fdroid/repo/diff/123.json': b'previous Stable diff',
                     'fdroid/repo/icons-640/im.collie.app.101.png': b'previous versioned Stable icon',
                     'fdroid/repo/im.collie.app/en-US/phoneScreenshots/abc_def=.png': b'previous Stable artwork',
                     'fdroid/beta/repo/diff/234.json': b'previous Beta diff',
                     'fdroid/beta/repo/icons-640/im.collie.app.trailblazer.201.png': b'previous Beta icon',
                     'fdroid/beta/repo/im.collie.app.trailblazer/en-US/abc_def=.png': b'previous Beta artwork'}
        beta = repository(channels.BETA, 201, '8.40.0', b'Beta APK')
        self.blobs.update({**beta, **immutable})
        self.snapshot()
        previous = self.load()
        self.candidate()
        first = retention.retain(self.site, previous, preserve_beta=True)
        raw = retention.inventory_bytes(first)
        (self.site / retention.INVENTORY).write_bytes(raw)
        second_baseline = self.root / 'second-baseline.json'
        second_baseline.write_text(json.dumps({'schema': 1, 'kind': 'collie-pages-deploy-baseline',
            'origin': retention.ORIGIN, 'inventory_sha256': hashlib.sha256(raw).hexdigest()}))
        second = retention.load_previous_site(self.site, second_baseline, parse_inventory=feed._inventory_files)
        final = self.root / 'third'
        current = repository(channels.STABLE, 103, '8.35.3', b'latest APK')
        write_files(final, current)
        retention.retain(final, second, preserve_beta=True)
        for site in (self.site, final):
            for name, blob in {**beta, **immutable}.items():
                self.assertEqual((site / name).read_bytes(), blob)
        self.assertEqual((final / 'fdroid/repo/index-v2.json').read_bytes(), current['fdroid/repo/index-v2.json'])

    def test_changed_archive_or_versioned_artwork_cannot_overwrite_a_published_url(self):
        protected = {'_generations/' + 'a' * 64 + '/entry.jar': b'archived signed entry',
                     'fdroid/repo/diff/123.json': b'published diff',
                     'fdroid/repo/icons-640/im.collie.app.101.png': b'published icon',
                     'fdroid/repo/im.collie.app/en-US/abc_def=.png': b'published artwork'}
        self.blobs.update(protected)
        self.snapshot()
        previous = self.load()
        self.candidate()
        for name in protected:
            with self.subTest(name=name):
                target = self.site / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b'replacement at an existing URL')
                with patch.object(retention, '_copy') as copy, self.assertRaisesRegex(ValueError, 'immutable published URL'):
                    retention.retain(self.site, previous, preserve_beta=False)
                copy.assert_not_called()
                target.unlink()

    def test_same_immutable_url_with_changed_native_or_apk_bytes_fails_before_copy(self):
        self.snapshot()
        previous = self.load()
        self.candidate()
        for name in (OLD_NATIVE, self.old_apk):
            with self.subTest(name=name):
                target = self.site / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b'changed published bytes')
                with patch.object(retention, '_copy') as copy, self.assertRaisesRegex(ValueError, 'immutable published URL'):
                    retention.retain(self.site, previous, preserve_beta=False)
                copy.assert_not_called()
                self.assertEqual(target.read_bytes(), b'changed published bytes')
                target.unlink()
        write_files(self.site, {OLD_NATIVE: self.blobs[OLD_NATIVE]})
        retention.retain(self.site, previous, preserve_beta=False)
        self.assertEqual((self.site / OLD_NATIVE).read_bytes(), self.blobs[OLD_NATIVE])

    def test_file_parent_conflict_is_rejected_before_copy(self):
        self.snapshot()
        previous = self.load()
        write_files(self.site, {'native-inputs/v1/maven/org': b'file instead of parent directory'})
        with patch.object(retention, '_copy') as copy, self.assertRaisesRegex(ValueError, 'file/directory path conflict'):
            retention.retain(self.site, previous, preserve_beta=False)
        copy.assert_not_called()

    def test_previous_snapshot_changed_during_build_is_rejected_before_copy(self):
        self.snapshot()
        previous = self.load()
        self.candidate()
        # Even nonretained files must still belong to the same frozen snapshot.
        (self.old / 'index.html').write_bytes(b'changed during signing')
        with patch.object(retention, '_copy') as copy, self.assertRaisesRegex(ValueError, 'missing, extra, or changed'):
            retention.retain(self.site, previous, preserve_beta=False)
        copy.assert_not_called()

    def test_budget_includes_manifest_and_refuses_before_copy_without_deleting_old_urls(self):
        archive = '_generations/' + 'a' * 64 + '/repository-receipt.json'
        self.blobs[archive] = b'previous immutable receipt' * 100
        self.snapshot()
        previous = self.load()
        current = self.candidate(apk=b'n' * 10000)
        planned = {**records(current), **previous.retained_files(False)}
        total = sum(record['bytes'] for record in planned.values()) + len(retention.inventory_bytes(planned))
        with patch.object(retention, '_copy') as copy, self.assertRaisesRegex(ValueError, 'separately reviewed retirement'):
            retention.retain(self.site, previous, preserve_beta=False, byte_limit=total - 1)
        copy.assert_not_called()
        self.assertFalse((self.site / self.old_apk).exists())
        self.assertEqual((self.old / self.old_apk).read_bytes(), self.blobs[self.old_apk])
        self.assertEqual(retention.retain(self.site, previous, preserve_beta=False, byte_limit=total), planned)
        self.assertEqual((self.site / archive).read_bytes(), self.blobs[archive])

    def test_beta_must_be_preserved_and_is_copied_from_the_frozen_snapshot(self):
        beta = repository(channels.BETA, 201, '8.40.0', b'Beta APK')
        self.blobs.update(beta)
        self.snapshot()
        previous = self.load()
        self.candidate()
        with self.assertRaisesRegex(ValueError, 'requires --preserve-beta'):
            retention.retain(self.site, previous, preserve_beta=False)
        retention.retain(self.site, previous, preserve_beta=True)
        for name, blob in beta.items(): self.assertEqual((self.site / name).read_bytes(), blob)

    def test_capture_baseline_keeps_exact_verified_snapshot_without_another_capture(self):
        self.blobs['fdroid/repo/im.collie.app/en-US/abc_def=.png'] = b'listing artwork'
        raw = retention.inventory_bytes(records(self.blobs))
        fetched = []
        def fetch(name, *, max_bytes):
            fetched.append(name)
            return 200, raw if name == feed.SITE_INVENTORY else self.blobs[name]
        value = feed.capture_deploy_baseline(self.baseline, retained_site=self.old, fetch=fetch)
        self.assertEqual(fetched.count(feed.SITE_INVENTORY), 1)
        self.assertEqual(set(fetched), {feed.SITE_INVENTORY, *self.blobs})
        self.assertEqual(value['inventory_sha256'], hashlib.sha256(raw).hexdigest())
        self.assertEqual((self.old / feed.SITE_INVENTORY).read_bytes(), raw)
        self.load()
        with self.assertRaisesRegex(ValueError, 'outside the retained site'):
            feed.capture_deploy_baseline(self.root / 'other/baseline.json', retained_site=self.root / 'other')

    def run_feed(self, raw):
        arguments = ['feed.py', '--site', str(self.site), '--latest', 'v8.35.2', '--preserve-beta',
                     '--previous-site', str(self.old), '--previous-baseline', str(self.baseline)]
        for name in ('apks', 'native', 'native-plan', 'fastlane', 'keystore', 'build-tools'):
            arguments += ['--' + name, str(self.root / name)]
        def build(_apks, _listing, _key, _tools, _work, site, _profile, **_kwargs):
            self.assertFalse((site / self.old_apk).exists())
            self.assertFalse((site / OLD_NATIVE).exists())
            self.assertFalse((site / 'fdroid/beta').exists())
            write_files(site, repository(channels.STABLE, 102, '8.35.2', b'new verified APK'))
        def copy_native(_native, _plan, site):
            write_files(site, {NEW_NATIVE: b'new native'})
            return 1
        def fetch(name, *, max_bytes):
            self.assertEqual(name, feed.SITE_INVENTORY)
            return 200, raw
        with patch.object(sys, 'argv', arguments), patch.dict(os.environ, {'FDROID_KEYSTORE_PASS': 'test'}), \
                patch.object(feed, 'build_fdroid', side_effect=build), \
                patch.object(feed, 'copy_native', side_effect=copy_native), \
                patch.object(feed, 'keystore_fingerprint', return_value=channels.STABLE.repository_certificate), \
                patch.object(feed.site_page, 'render', side_effect=lambda site, *_a, **_k: write_files(site, {'index.html': b'new landing'})), \
                patch.object(feed, '_read_origin', side_effect=fetch) as origin, \
                patch.object(feed, 'capture_beta_route', side_effect=AssertionError('second moving Beta capture')), \
                patch.object(feed, 'capture_live_site', side_effect=AssertionError('second moving site capture')), \
                contextlib.redirect_stdout(io.StringIO()):
            feed.main()
        origin.assert_called_once()

    def test_feed_cli_keeps_old_urls_out_of_index_and_uses_one_frozen_beta_capture(self):
        self.blobs.update(repository(channels.BETA, 201, '8.40.0', b'Beta APK'))
        raw = self.snapshot()
        self.run_feed(raw)
        state = json.loads((self.site / 'feed-state.json').read_bytes())
        self.assertEqual(state['apks'], ['im.collie.app_102.apk'])
        self.assertEqual(state['retained_apks'], ['im.collie.app_101.apk'])
        self.assertEqual(state['native_files'], 2)
        self.assertEqual(state['beta'], {'version_name': '8.40.0', 'version_code': 201})
        manifest = feed._inventory_files((self.site / feed.SITE_INVENTORY).read_bytes())
        self.assertIn(self.old_apk, manifest)
        self.assertIn(OLD_NATIVE, manifest)
        self.assertEqual({p.relative_to(self.site).as_posix() for p in self.site.rglob('*') if p.is_file()},
                         {feed.SITE_INVENTORY, *manifest})
        index = json.loads((self.site / 'fdroid/repo/index-v2.json').read_bytes())
        self.assertEqual({row[0] for row in retention.binary_repo.release_tuples(index)}, {102})

    def test_feed_cli_refuses_a_newer_live_site_instead_of_adopting_it(self):
        self.snapshot()
        with self.assertRaisesRegex(ValueError, 'served site changed after Beta capture'):
            self.run_feed(retention.inventory_bytes({}))

    def test_cli_requires_both_frozen_inputs_and_rejects_preview_retention(self):
        for option in ('--previous-site', '--previous-baseline'):
            with self.subTest(option=option), patch.object(sys, 'argv', ['feed.py', option, 'missing']), \
                    contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                feed.main()
            self.assertEqual(error.exception.code, 2)
        with patch.object(sys, 'argv', ['feed.py', '--previous-site', 'site', '--previous-baseline', 'baseline', '--preview']), \
                self.assertRaisesRegex(ValueError, 'production Stable'):
            feed.main()

    def test_production_workflow_passes_the_same_capture_to_build_and_deploy_guard(self):
        workflow = (Path(__file__).resolve().parents[1] / '.github/workflows/feeds.yml').read_text()
        self.assertIn('capture-deploy-baseline "$RUNNER_TEMP/deploy-baseline.json" --retain-site "$RUNNER_TEMP/previous-site"', workflow)
        self.assertIn('--previous-site "$RUNNER_TEMP/previous-site" --previous-baseline "$RUNNER_TEMP/deploy-baseline.json"', workflow)
        self.assertIn('--feed-baseline "$RUNNER_TEMP/deploy-baseline.json"', workflow)
        self.assertIn('--preserve-beta', workflow)
        self.assertIn('group: collie-pages-production', workflow)

    def test_archive_budget_measures_actual_tar_overhead_and_cli_refuses_oversize(self):
        tiny = self.root / 'tiny-site'
        write_files(tiny, {'payload.txt': b'x'})
        archive = self.root / 'artifact.tar'
        # Same Linux tar options as the exact pinned upload-pages-artifact action.
        subprocess.run(['tar', '--dereference', '--hard-dereference', '--directory', str(tiny),
                        '-cf', str(archive), '--exclude=.git', '--exclude=.github', '--exclude=.[^/]*', '.'], check=True)
        with tarfile.open(archive) as packed:
            self.assertEqual(sum(item.size for item in packed.getmembers() if item.isfile()), 1)
        actual_size = archive.stat().st_size
        self.assertGreater(actual_size, 1)
        with self.assertRaisesRegex(ValueError, 'Pages artifact.*separately reviewed retirement'):
            retention.verify_pages_archive(archive, byte_limit=actual_size - 1)
        self.assertEqual(retention.verify_pages_archive(archive, byte_limit=actual_size), actual_size)
        with patch.object(sys, 'argv', ['feed.py', 'verify-uploaded-archive', str(archive)]), \
                patch.object(feed, 'SITE_LIMIT', actual_size - 1), self.assertRaisesRegex(ValueError, 'Pages artifact'):
            feed.main()

    def test_archive_gate_rejects_missing_empty_linked_and_special_artifacts(self):
        artifact = self.root / 'artifact.tar'
        with self.assertRaises(OSError): retention.verify_pages_archive(artifact)
        artifact.write_bytes(b'')
        with self.assertRaisesRegex(ValueError, 'Pages artifact'): retention.verify_pages_archive(artifact)
        artifact.unlink()
        regular = self.root / 'original.tar'
        regular.write_bytes(b'archive fixture')
        for kind in ('symlink', 'hardlink', 'directory', 'fifo'):
            with self.subTest(kind=kind):
                if kind == 'symlink': artifact.symlink_to(regular)
                elif kind == 'hardlink': os.link(regular, artifact)
                elif kind == 'directory': artifact.mkdir()
                else: os.mkfifo(artifact)
                with self.assertRaisesRegex(ValueError, 'regular, unlinked files'):
                    retention.verify_pages_archive(artifact)
                if kind == 'directory': artifact.rmdir()
                else: artifact.unlink()

    def test_uploaded_archive_gate_precedes_deployment_and_cannot_ignore_failure(self):
        workflow = (Path(__file__).resolve().parents[1] / '.github/workflows/feeds.yml').read_text()
        upload = workflow.index('uses: actions/upload-pages-artifact@fc324d3547104276b827a68afc52ff2a11cc49c9')
        gate = workflow.index('- name: Enforce the complete Pages archive budget')
        deploy = workflow.index('- name: Deploy and poll until success or the deadline')
        self.assertLess(upload, gate)
        self.assertLess(gate, deploy)
        self.assertIn('feed.py verify-uploaded-archive "$RUNNER_TEMP/artifact.tar"', workflow[gate:deploy])
        self.assertNotIn('continue-on-error', workflow[gate:])
        self.assertNotIn('if: always()', workflow[gate:])


if __name__ == '__main__':
    unittest.main()
