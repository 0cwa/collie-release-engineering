import unittest
import json
import os
import sys
import tempfile
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from distribution import channel_profile as channels
from distribution import feed


class ChannelProfileTest(unittest.TestCase):
    def run_feed_cli(self, root, fingerprint, preview=False, channel='stable'):
        arguments=['feed.py']
        for name in ('site','apks','native','native-plan','fastlane','keystore','build-tools'):
            arguments += ['--'+name,str(root/name)]
        arguments += ['--latest','v8.35.2','--channel',channel]
        if preview: arguments.append('--preview')
        with patch.object(sys,'argv',arguments), \
             patch.dict(os.environ,{'FDROID_KEYSTORE_PASS':'test-only-unused-value'}), \
             patch.object(feed,'copy_native',return_value=0), \
             patch.object(feed,'build_fdroid'), \
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

    def test_beta_cannot_bypass_repository_pin_with_preview(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError,'Stable only'):
                self.run_feed_cli(Path(tmp),'0'*64,preview=True,channel='trailblazer-beta')

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

    def test_beta_feed_build_uses_only_beta_package_path_and_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);apk_dir=root/'apks';apk_dir.mkdir()
            (apk_dir/'trailblazer-beta.apk').write_bytes(b'test apk bytes')
            listing=root/'listing';(listing/'en-US/images').mkdir(parents=True)
            (listing/'en-US/images/icon.png').write_bytes(b'icon')
            work=root/'work';site=root/'site'
            profile=channels.BETA
            def fake_update(command,cwd,check):
                repo=Path(cwd)/'repo'
                (repo/'entry.jar').write_bytes(b'index')
                (repo/'index-v2.json').write_text(json.dumps({'repo':{'address':profile.repository_url.rstrip('/')},'packages':{profile.package:{'versions':{}}}}))
                return None
            with patch.object(feed,'apk_identity',return_value=(200,'8.36.0')), \
                 patch.object(feed.subprocess,'run',side_effect=fake_update):
                feed.build_fdroid(apk_dir,listing,root/'repo.p12',Path('/android/build-tools/35'),work,site,
                                  profile,tag='trailblazer-v8.36.0',title='Collie TrailBlazer Beta 8.36.0',
                                  prior_version_code=199,prerelease=True)
            self.assertTrue((site/'fdroid/beta/repo/im.collie.app.trailblazer_200.apk').is_file())
            self.assertFalse((site/'fdroid/repo').exists())
            metadata=(work/'metadata/im.collie.app.trailblazer.yml').read_text()
            self.assertIn("AllowedAPKSigningKeys:\n  - '"+channels.BETA.apk_certificate+"'",metadata)
            self.assertIn("versionName: '8.36.0'",metadata)
            generated=json.loads((work/'repo/index-v2.json').read_text())['packages']['im.collie.app.trailblazer']
            self.assertNotIn('releaseChannels',generated)
            config=(work/'config.yml').read_text()
            self.assertIn('repo_url: "https://repo.collie.im/fdroid/beta/repo"',config)
            self.assertIn('repo_name: "Collie TrailBlazer Beta"',config)

    def test_beta_feed_rejects_stable_alias_even_if_identity_would_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);apks=root/'apks';apks.mkdir();(apks/'collie.apk').write_bytes(b'not beta')
            profile=channels.BETA
            with self.assertRaisesRegex(ValueError,'requires exactly trailblazer-beta.apk'):
                feed.build_fdroid(apks,root,root/'key',Path('/android/build-tools/35'),root/'work',root/'site',
                                  profile,tag='trailblazer-v8.36.0',title='Collie TrailBlazer Beta 8.36.0',prior_version_code=1)

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

    def test_stable_feed_builder_keeps_historical_tag_named_apk_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);apks=root/'apks';apks.mkdir()
            (apks/'v8.35.1.apk').write_bytes(b'old stable')
            (apks/'v8.35.2.apk').write_bytes(b'new stable')
            listing=root/'listing';(listing/'en-US/images').mkdir(parents=True)
            (listing/'en-US/images/icon.png').write_bytes(b'icon')
            work=root/'work';site=root/'site';sequence=iter([(177700,'8.35.1'),(177701,'8.35.2')])
            def fake_update(command,cwd,check):
                (Path(cwd)/'repo/entry.jar').write_bytes(b'index')
                (Path(cwd)/'repo/index-v2.json').write_text('{"packages":{"im.collie.app":{"versions":{}}}}')
            with patch.object(feed,'apk_identity',side_effect=lambda *args:next(sequence)), \
                 patch.object(feed.subprocess,'run',side_effect=fake_update):
                feed.build_fdroid(apks,listing,root/'repo.p12',Path('/android/build-tools/35'),work,site)
            self.assertEqual({p.name for p in (site/'fdroid/repo').glob('*.apk')},
                             {'im.collie.app_177700.apk','im.collie.app_177701.apk'})
            self.assertFalse((site/'fdroid/beta/repo').exists())
            self.assertEqual(feed.channel_repository_config((feed.HERE/'config.public.yml').read_text(),channels.STABLE),
                             (feed.HERE/'config.public.yml').read_text())

    def test_beta_generated_index_rejects_nonempty_release_channels(self):
        with tempfile.TemporaryDirectory() as tmp:
            index=Path(tmp)/'repo';index.mkdir()
            (index/'index-v2.json').write_text(json.dumps({'repo':{'address':channels.BETA.repository_url.rstrip('/')},'packages':{channels.BETA.package:{'releaseChannels':['beta']}}}))
            with self.assertRaisesRegex(ValueError,'absent or empty'):
                feed.validate_generated_index(tmp,channels.BETA)
if __name__ == '__main__':
    unittest.main()
