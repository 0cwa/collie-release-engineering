import unittest
import json
import os
import sys
import tempfile
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from distribution import feed

channels = feed.channels


class ChannelProfileTest(unittest.TestCase):
    def run_feed_cli(self, root, fingerprint, preview=False):
        arguments=['feed.py']
        for name in ('site','apks','native','native-plan','fastlane','keystore','build-tools'):
            arguments += ['--'+name,str(root/name)]
        arguments += ['--latest','v8.35.2']
        if preview: arguments.append('--preview')
        with patch.object(sys,'argv',arguments), \
             patch.dict(os.environ,{'FDROID_KEYSTORE_PASS':'test-only-unused-value'}), \
             patch.object(feed,'copy_native',return_value=0), \
             patch.object(feed,'build_fdroid',return_value=None), \
             patch.object(feed,'keystore_fingerprint',return_value=fingerprint), \
             patch.object(feed.site_page,'render') as render:
            feed.main()
        return render

    def test_disposable_index_key_requires_explicit_preview(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            with self.assertRaisesRegex(ValueError,'repository signer'):
                self.run_feed_cli(root/'production','0'*64)
            rendered=self.run_feed_cli(root/'preview','0'*64,preview=True)
            self.assertEqual(rendered.call_args.kwargs['state'],'preview')
            state=json.loads((root/'preview/site/feed-state.json').read_text())
            self.assertTrue(state['preview'])
            self.assertEqual(state['repository_fingerprint'],'0'*64)

    def test_default_feed_cli_keeps_production_pin_and_active_render(self):
        with tempfile.TemporaryDirectory() as tmp:
            rendered=self.run_feed_cli(Path(tmp),channels.STABLE.repository_certificate)
            self.assertEqual(rendered.call_args.kwargs['state'],'active')
            state=json.loads((Path(tmp)/'site/feed-state.json').read_text())
            self.assertNotIn('preview',state)

    def test_stable_identity_and_trust_tuple_remain_unchanged(self):
        self.assertEqual(channels.STABLE.package, 'im.collie.app')
        self.assertEqual(channels.STABLE.apk_asset, 'collie.apk')
        self.assertEqual(channels.STABLE.repository_url, 'https://repo.collie.im/fdroid/repo/')
        self.assertEqual(channels.STABLE.repository_certificate,
                         'b0c5dcbac2b65ada4e8f641910097fb0bf86a5f7255319584d453c2932f5233c')
        self.assertEqual(channels.STABLE.apk_certificate,
                         'f8cc0c0c052e5635c26fcd293c2b0af3e87d61ff7f2df3602a9af6e91f73c826')

    def test_beta_uses_distinct_channel_identity_and_never_stable_asset(self):
        self.assertEqual(channels.BETA.package, 'im.collie.app.trailblazer')
        self.assertEqual(channels.BETA.app_title, 'Collie TrailBlazer Beta')
        self.assertEqual(channels.BETA.apk_asset, 'trailblazer-beta.apk')
        self.assertNotEqual(channels.BETA.apk_asset, channels.STABLE.apk_asset)
        self.assertNotIn(channels.BETA.apk_asset, {'collie-foss.apk', 'molly-continued.apk',
                                                   'molly-foss-continued.apk', 'molly-minimal.apk'})

    def test_beta_feed_fails_closed_without_signer_pins(self):
        unconfigured=replace(channels.BETA,apk_certificate=None)
        with self.assertRaisesRegex(ValueError, 'APK signer pin is not configured'):
            channels.require_apk_trusted(unconfigured)
        channels.require_trusted(channels.STABLE)

    def test_beta_release_identity_binds_title_tag_version_and_monotonic_code(self):
        profile=channels.BETA
        feed.release_identity(profile,'trailblazer-v8.36.0','Collie TrailBlazer Beta 8.36.0','8.36.0',200,199)
        for values, message in [
            (('trailblazer-v8.36.0','Collie TrailBlazer Beta 8.36.1','8.36.0',200,199),'title'),
            (('v8.36.0','Collie TrailBlazer Beta 8.36.0','8.36.0',200,199),'tag'),
            (('trailblazer-v8.36.0-dev','Collie TrailBlazer Beta 8.36.0-dev','8.36.0-dev',200,199),'development'),
            (('trailblazer-v8.36.0','Collie TrailBlazer Beta 8.36.0','8.36.0',199,199),'increase'),
        ]:
            with self.subTest(message=message), self.assertRaisesRegex(ValueError,message):
                feed.release_identity(profile,*values)

    def test_both_packages_are_built_together_with_distinct_signers_and_retained_beta(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            apks = root / 'apks'
            apks.mkdir()
            stable_apk = apks / 'v8.35.2.apk'
            stable_apk.write_bytes(b'stable')
            beta_apk = root / 'trailblazer-beta.apk'
            beta_apk.write_bytes(b'beta')
            listing = root / 'listing'
            (listing / 'en-US/images').mkdir(parents=True)
            (listing / 'en-US/images/icon.png').write_bytes(b'icon')
            work, site = root / 'work', root / 'site'
            metadata = {'categories': ['Internet'], **{key: {'en-US': 'listing'}
                        for key in ('name', 'summary', 'description', 'icon')}}
            packages = {}
            versions = {}
            for profile, code, version, apk in ((channels.STABLE, 102, '8.35.2', stable_apk),
                                                 (channels.BETA, 205, '8.36.0', beta_apk)):
                packages[profile.package] = {'metadata': metadata}
                versions[profile.package] = {(code, version, feed.sha256(apk),
                                              f'{profile.package}_{code}.apk', profile.apk_certificate)}
            index = {'repo': {'address': channels.REPOSITORY_URL}, 'packages': packages}
            prior = {'version_code': 205, 'version_name': '8.36.0', 'sha256': feed.sha256(beta_apk)}
            kwargs = dict(tag='v8.35.2', fingerprint=channels.REPOSITORY_CERTIFICATE,
                          beta_apk=beta_apk, beta_fastlane=listing, beta_tag='trailblazer-v8.36.0',
                          previous_beta=prior)

            with patch.object(feed, 'apk_identity', side_effect=[(102, '8.35.2'), (205, '8.36.0')]), \
                    patch.object(feed.subprocess, 'run') as update, \
                    patch.object(feed.binary_repo, 'verify_indexes', return_value=index) as verify, \
                    patch.object(feed.binary_repo, 'release_tuples', side_effect=lambda _, p: versions[p.package]):
                result = feed.build_fdroid(apks, listing, root/'key', Path('/tools/bin'), work, site, **kwargs)
            self.assertEqual(result, {'version_code': 205, 'version_name': '8.36.0'})
            update.assert_called_once()
            verify.assert_called_once_with(work/'repo', channels.REPOSITORY_CERTIFICATE, channels.STABLE,
                                           additional_profiles=(channels.BETA,))
            self.assertEqual({apk.name for apk in (site/'fdroid/repo').glob('*.apk')},
                             {'im.collie.app_102.apk', 'im.collie.app.trailblazer_205.apk'})
            self.assertFalse((site/'fdroid/beta').exists())
            for profile in (channels.STABLE, channels.BETA):
                metadata_text = (work/'metadata'/f'{profile.package}.yml').read_text()
                self.assertIn("AllowedAPKSigningKeys:\n  - '" + profile.apk_certificate + "'", metadata_text)
            beta_apk.write_bytes(b'different bytes for already served version')
            with patch.object(feed, 'apk_identity', side_effect=[(102, '8.35.2'), (205, '8.36.0')]), \
                    patch.object(feed.subprocess, 'run') as update, \
                    self.assertRaisesRegex(ValueError, 'versionCode must increase'):
                feed.build_fdroid(apks, listing, root/'key', Path('/tools/bin'), root/'other', site, **kwargs)
            update.assert_not_called()

    def test_beta_apk_package_and_signer_must_match_channel_profile(self):
        profile=replace(channels.BETA,apk_certificate='a'*64,repository_certificate='b'*64)
        with patch.object(feed.subprocess,'run',side_effect=[
            type('R',(),{'stdout':"package: name='im.collie.app' versionCode='200' versionName='8.36.0'"})(),
            type('R',(),{'stdout':'Signer #1 certificate SHA-256 digest: '+'a'*64})(),
        ]):
            with self.assertRaisesRegex(SystemExit,'package or signing certificate'):
                feed.apk_identity(Path('trailblazer-beta.apk'),Path('/tools'),profile)
        with patch.object(feed.subprocess,'run',side_effect=[
            type('R',(),{'stdout':"package: name='im.collie.app.trailblazer' versionCode='200' versionName='8.36.0'"})(),
            type('R',(),{'stdout':'Signer #1 certificate SHA-256 digest: '+'c'*64})(),
        ]):
            with self.assertRaisesRegex(SystemExit,'package or signing certificate'):
                feed.apk_identity(Path('trailblazer-beta.apk'),Path('/tools'),profile)

    def test_stable_feed_rejects_tag_mismatch_and_duplicate_version_codes_before_signing(self):
        for identities in ([(101,'8.35.9'),(102,'8.35.2')], [(101,'8.35.1'),(101,'8.35.2')]):
            with self.subTest(identities=identities), tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);apks=root/'apks';apks.mkdir()
                (apks/'v8.35.1.apk').write_bytes(b'first')
                (apks/'v8.35.2.apk').write_bytes(b'second')
                listing=root/'listing';(listing/'en-US/images').mkdir(parents=True)
                (listing/'en-US/images/icon.png').write_bytes(b'icon')
                with patch.object(feed,'apk_identity',side_effect=identities), \
                     patch.object(feed.subprocess,'run') as signer:
                    with self.assertRaises(ValueError):
                        feed.build_fdroid(apks,listing,root/'repo.p12',Path('/android/build-tools/35'),root/'work',root/'site',tag='v8.35.2',fingerprint=channels.REPOSITORY_CERTIFICATE)
                    signer.assert_not_called()

    def test_stable_feed_does_not_advertise_a_newer_apk_under_an_older_selected_release(self):
        for selected in ('v8.35.1', 'v8.35.9'):
            with self.subTest(selected=selected), tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);apks=root/'apks';apks.mkdir()
                (apks/'v8.35.1.apk').write_bytes(b'first')
                (apks/'v8.35.2.apk').write_bytes(b'second')
                listing=root/'listing';(listing/'en-US/images').mkdir(parents=True)
                (listing/'en-US/images/icon.png').write_bytes(b'icon')
                with patch.object(feed,'apk_identity',side_effect=[(101,'8.35.1'),(102,'8.35.2')]), \
                     patch.object(feed.subprocess,'run') as signer:
                    with self.assertRaisesRegex(ValueError,'highest indexed versionCode'):
                        feed.build_fdroid(apks,listing,root/'repo.p12',Path('/android/build-tools/35'),root/'work',root/'site', tag=selected,
                                          fingerprint=channels.REPOSITORY_CERTIFICATE)
                    signer.assert_not_called()

if __name__ == '__main__':
    unittest.main()
