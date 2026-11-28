import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import binary_repo as b
from channel_profile import BETA

class BinaryRepositoryTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
    def test_immutable_retry_and_substitution(self):
        s=self.root/'source.apk';d=self.root/'repo/a.apk';s.write_bytes(b'approved')
        b.immutable_copy(s,d);b.immutable_copy(s,d);s.write_bytes(b'replacement')
        with self.assertRaisesRegex(ValueError,'immutable'): b.immutable_copy(s,d)
        self.assertEqual(d.read_bytes(),b'approved')
    def test_ingest_pins_approved_digest_after_apk_inspection(self):
        apk=self.root/'candidate.apk';apk.write_bytes(b'approved synthetic APK')
        manifest=self.root/'final-manifest.json';manifest.write_text('{}')
        a=b.load(Path(__file__).parent/'fixtures/legacy-release-approval.json');a.update(apk_sha256=b.sha(apk),release_manifest_sha256=b.sha(manifest));a['aliases']={k:a['apk_sha256'] for k in b.ALIASES}
        approval=self.root/'approval.json';approval.write_text(json.dumps(a))
        info={k:a[k] for k in ('package','version_code','version_name','apk_certificate_sha256')}
        def mutate(*args):apk.write_bytes(b'changed after inspection');return info
        with patch('binary_repo.inspect',side_effect=mutate):
            with self.assertRaisesRegex(ValueError,'copy digest mismatch'):b.ingest(approval,manifest,apk,self.root/'stage','unused','unused')
        self.assertFalse((self.root/'stage/repo'/f"{b.APP}_{a['version_code']}.apk").exists())
    def test_duplicate_approval_fields_rejected(self):
        p=self.root/'a.json';p.write_text('{"schema":1,"schema":1}')
        with self.assertRaisesRegex(ValueError,'duplicate'):b.load(p)
    def test_signature_failure_stops_metadata(self):
        import subprocess
        with patch('binary_repo.subprocess.check_output',side_effect=subprocess.CalledProcessError(1,'apksigner')):
            with self.assertRaises(subprocess.CalledProcessError): b.inspect('x','aapt','apksigner')
    def test_inspect_normalizes_real_colon_separated_apksigner_fingerprint(self):
        import subprocess
        fingerprint=':'.join(['ab']*32)
        with patch('binary_repo.subprocess.check_output',side_effect=[
                'Signer #1 certificate SHA-256 digest: '+fingerprint,
                "package: name='im.collie.app' versionCode='12' versionName='1.2.0'\n"]):
            result=b.inspect('candidate.apk','aapt','apksigner')
        self.assertEqual(result['apk_certificate_sha256'],'ab'*32)
    def test_partial_publish_resumes_same_generation(self):
        g=self.root/'generation';g.mkdir();o=self.root/'origin'
        for f in ['im.collie.app_1.apk','entry.jar','index-v1.jar','index-v2.json']: (g/f).write_bytes(f.encode())
        original=b.immutable_copy
        def stop(source,dest,expected=None):
            original(source,dest,expected)
            raise OSError('interrupted')
        with patch('binary_repo.approved_generation',return_value=(b.generation_digest(g),{})),patch('binary_repo.immutable_copy',side_effect=stop):
            with self.assertRaises(OSError):b.publish_local(g,o,'f'*64,b.generation_digest(g),[])
        self.assertIsNotNone(b.load(o.parent/(o.name+'.control')/'promotion.json')['active'])
        with patch('binary_repo.approved_generation',return_value=(b.generation_digest(g),{})),patch('binary_repo.verify_indexes'): first=b.publish_local(g,o,'f'*64,b.generation_digest(g),[]);second=b.publish_local(g,o,'f'*64,b.generation_digest(g),[])
        self.assertEqual(first,second);self.assertEqual(b.sha(g/'im.collie.app_1.apk'),b.sha(o/'im.collie.app_1.apk'))
    def test_changed_generation_cannot_replace_interrupted(self):
        g=self.root/'g';g.mkdir();o=self.root/'o';o.mkdir()
        for f in ['im.collie.app_1.apk','entry.jar','index-v1.jar','index-v2.json']:(g/f).write_bytes(b'x')
        (o.parent/(o.name+'.control')).mkdir();(o.parent/(o.name+'.control')/'promotion.json').write_text(json.dumps({'active':'other','completed':[]}))
        with patch('binary_repo.approved_generation',return_value=(b.generation_digest(g),{})):
            with self.assertRaisesRegex(ValueError,'resume'):b.publish_local(g,o,'f'*64,b.generation_digest(g),[])
    def test_symlink_not_exported(self):
        g=self.root/'g';g.mkdir();(g/'evil.apk').symlink_to('/etc/passwd')
        with patch('binary_repo.verify_indexes'):
            with self.assertRaisesRegex(ValueError,'symlink'):b.publish_local(g,self.root/'o','f'*64,'f'*64,[])
    def test_source_mutation_during_approved_copy_rejected(self):
        g=self.root/'g';g.mkdir();o=self.root/'o'
        for name in ['im.collie.app_1.apk','entry.jar','index-v1.jar','index-v2.json']:(g/name).write_bytes(b'approved')
        expected=b.generation_digest(g);original=b.immutable_copy
        def mutate(source,dest,pinned=None):
            source.write_bytes(b'replaced')
            return original(source,dest,pinned)
        with patch('binary_repo.approved_generation',return_value=(expected,{})),patch('binary_repo.immutable_copy',side_effect=mutate):
            with self.assertRaisesRegex(ValueError,'copy digest'):b.publish_local(g,o,'f'*64,expected,[])
        self.assertFalse((o/'im.collie.app_1.apk').exists())
    def indexes(self):
        row={'manifest':{'versionCode':1,'versionName':'1','signer':{'sha256':['c'*64]}},'file':{'name':'/im.collie.app_1.apk','sha256':'a'*64}}
        data={'packages':{b.APP:{'versions':{'a'*64:row}}}}
        legacy={'apps':[{'packageName':b.APP}],'packages':{b.APP:[{'versionCode':1,'versionName':'1','signer':'c'*64,'hash':'a'*64,'hashType':'sha256','apkName':'im.collie.app_1.apk'}]}}
        return data,legacy
    def test_v1_v2_parity_rejects_crossed_signed_release(self):
        data,legacy=self.indexes();b.validate_index_parity(data,legacy)
        for field,value in [('hash','b'*64),('versionCode',2),('signer','b'*64),('apkName','wrong.apk')]:
            import copy
            changed=copy.deepcopy(legacy);changed['packages'][b.APP][0][field]=value
            with self.assertRaisesRegex(ValueError,'v1/v2'):b.validate_index_parity(data,changed)
    def test_public_listing_accepts_fastlane_locales_with_numeric_regions(self):
        self.assertTrue(b.public_path(f'{BETA.package}/es-419/icon.png', BETA))

    def test_unified_index_rejects_crossed_apk_signers_and_nondefault_releases(self):
        generation = self.root / 'unified'
        generation.mkdir()
        packages, apps, legacy = {}, [], {}
        for profile in (b.STABLE, BETA):
            apk = generation / f'{profile.package}_1.apk'
            apk.write_text(profile.package)
            digest = b.sha(apk)
            packages[profile.package] = {'metadata': {'preferredSigner': profile.apk_certificate}, 'versions': {
                digest: {'manifest': {'versionCode': 1, 'versionName': '1.0.0',
                                      'signer': {'sha256': [profile.apk_certificate]}},
                         'file': {'name': '/' + apk.name, 'sha256': digest}}}}
            apps.append({'packageName': profile.package})
            legacy[profile.package] = [{'versionCode': 1, 'versionName': '1.0.0', 'hash': digest,
                                       'hashType': 'sha256', 'apkName': apk.name,
                                       'signer': profile.apk_certificate}]
        data = {'repo': {'timestamp': 123}, 'packages': packages}
        v1 = {'apps': apps, 'packages': legacy}

        def verify():
            v2 = generation / 'index-v2.json'
            v2.write_text(json.dumps(data))
            entry = {'timestamp': 123, 'index': {'name': '/index-v2.json', 'sha256': b.sha(v2),
                                                  'size': v2.stat().st_size}}
            (generation / 'entry.json').write_text(json.dumps(entry))
            jars = {'entry.jar': entry, 'index-v1.jar': v1}
            backend = SimpleNamespace(common=SimpleNamespace(), index=SimpleNamespace(
                get_index_from_jar=lambda path, _fingerprint, **_kwargs: (jars[Path(path).name], None, None)))
            with patch.dict(sys.modules, {'fdroidserver': backend}):
                return b.verify_indexes(generation, b.STABLE.repository_certificate, b.STABLE,
                                        additional_profiles=(BETA,))

        self.assertEqual(verify(), data)
        beta_version = next(iter(packages[BETA.package]['versions'].values()))
        beta_version['releaseChannels'] = ['Beta']
        with self.assertRaisesRegex(ValueError, 'releaseChannels'):
            verify()
        del beta_version['releaseChannels']
        beta_version['manifest']['signer']['sha256'] = [b.STABLE.apk_certificate]
        legacy[BETA.package][0]['signer'] = b.STABLE.apk_certificate
        with self.assertRaisesRegex(ValueError, 'Beta|trailblazer-beta.*APK signer'):
            verify()

    def test_expected_generation_cannot_be_substituted(self):
        g=self.root/'g';g.mkdir()
        for name in ['entry.jar','index-v1.jar','index-v2.json']:(g/name).write_bytes(b'x')
        with self.assertRaisesRegex(ValueError,'generation mismatch'):b.approved_generation(g,'f'*64,'0'*64,[])
    def test_signed_unapproved_release_rejected(self):
        g=self.root/'g';g.mkdir()
        for name in ['entry.jar','index-v1.jar','index-v2.json']:(g/name).write_bytes(b'x')
        data,_=self.indexes()
        with patch('binary_repo.verify_indexes',return_value=data):
            with self.assertRaisesRegex(ValueError,'unapproved indexed'):b.approved_generation(g,'f'*64,b.generation_digest(g),[])
    def test_manual_stable_approval_accepts_only_canonical_or_full_historical_aliases(self):
        fixtures=Path(__file__).parent/'fixtures'
        legacy=b.load(fixtures/'legacy-release-approval.json')
        current=b.load(fixtures/'v2-release-input.json')
        for approved,apk_bytes in ((legacy,b'legacy APK'),(current['approval'],current['apk_utf8'].encode())):
            with self.subTest(schema=approved['schema']):
                generation=self.root/f"generation-{approved['schema']}"
                generation.mkdir()
                for name in ('entry.jar','index-v1.jar','index-v2.json'):
                    (generation/name).write_bytes(b'synthetic signed index')
                filename=f"{b.APP}_{approved['version_code']}.apk"
                apk=generation/filename
                apk.write_bytes(apk_bytes)
                digest=b.sha(apk)
                expected=b.generation_digest(generation)
                signer=approved['apk_certificate_sha256']
                row={'manifest':{'versionCode':approved['version_code'],'versionName':approved['version_name'],
                                 'signer':{'sha256':[signer]}},'file':{'name':'/'+filename,'sha256':digest}}
                index={'packages':{b.APP:{'versions':{digest:row}}}}
                approval_path=self.root/'approved-release.json'
                canonical={b.STABLE.apk_asset:digest}
                historical=dict.fromkeys(b.ALIASES,digest)
                correct=dict(approved,apk_sha256=digest,aliases=canonical)
                fingerprint=b.STABLE.repository_certificate
                with patch('binary_repo.verify_indexes',return_value=index):
                    for name,aliases in (('canonical',canonical),('historical',historical)):
                        approval_path.write_text(json.dumps(dict(correct,aliases=aliases)))
                        origin=self.root/f"{approved['schema']}-{name}"
                        self.assertEqual(b.publish_local(generation,origin,fingerprint,expected,[approval_path]),expected)
                        self.assertEqual((origin/filename).read_bytes(),apk_bytes)
                    cases=(
                        ({'aliases':{}},'alias inequality'),
                        ({'aliases':{'collie-foss.apk':digest}},'alias inequality'),
                        ({'aliases':canonical|{'collie-foss.apk':digest}},'alias inequality'),
                        ({'aliases':historical|{'trailblazer-beta.apk':digest}},'alias inequality'),
                        ({'aliases':historical|{'molly-minimal.apk':'0'*64}},'alias inequality'),
                        ({'aliases':{b.STABLE.apk_asset:'0'*64}},'alias inequality'),
                        ({'apk_sha256':'0'*64,'aliases':{b.STABLE.apk_asset:'0'*64}},'unapproved indexed release'),
                        ({'apk_certificate_sha256':'0'*64},'signing certificate differs'),
                        ({'package':BETA.package},'package/version'),
                    )
                    for changes,error in cases:
                        with self.subTest(changes=changes):
                            approval_path.write_text(json.dumps(dict(correct,**changes)))
                            origin=self.root/'rejected'
                            with self.assertRaisesRegex(ValueError,error):
                                b.publish_local(generation,origin,fingerprint,expected,[approval_path])
                            self.assertFalse(origin.exists())
                    approval_path.write_text(json.dumps(correct))
                    with self.assertRaisesRegex(ValueError,'alias inequality'):
                        b.approved_generation(generation,fingerprint,expected,[approval_path])
    def test_beta_keeps_its_exact_asset_and_tag(self):
        approved=b.load(Path(__file__).parent/'fixtures/legacy-release-approval.json')
        digest=approved['apk_sha256']
        approved.update(package=BETA.package,apk_certificate_sha256=BETA.apk_certificate,
                        tag=BETA.tag_prefix+approved['version_name'],aliases={BETA.apk_asset:digest})
        path=self.root/'beta-approval.json'
        path.write_text(json.dumps(approved))
        self.assertEqual(b.approval(path,BETA),approved)
        with self.assertRaisesRegex(ValueError,'Beta publication requires approved Pages snapshots'):
            b.publish_local(self.root/'candidate',self.root/'origin',BETA.repository_certificate,'0'*64,[path],BETA)
        for changes in ({'aliases':{b.STABLE.apk_asset:digest}},{'tag':'v'+approved['version_name']}):
            path.write_text(json.dumps(dict(approved,**changes)))
            with self.assertRaisesRegex(ValueError,'Beta asset/tag identity differs'):
                b.approval(path,BETA)
    def test_private_file_rejected_from_public_inventory(self):
        g=self.root/'g';g.mkdir();(g/'keystore.p12').write_bytes(b'private')
        with self.assertRaisesRegex(ValueError,'unexpected public file'):b.public_files(g)
    def test_alternate_apk_certificate_cannot_be_approved(self):
        metadata=(Path(__file__).parent/'metadata/im.collie.app.yml').read_text()
        self.assertIn("AllowedAPKSigningKeys:\n  - '"+b.APK_CERTIFICATE+"'",metadata)
        self.assertNotIn('@APK_CERTIFICATE@',metadata)
        a=b.load(Path(__file__).parent/'fixtures/legacy-release-approval.json');a['apk_certificate_sha256']='0'*64
        path=self.root/'alternate-signer.json';path.write_text(json.dumps(a))
        with self.assertRaisesRegex(ValueError,'signing certificate differs'):b.approval(path)
    def test_boolean_schema_is_not_legacy_integer_one(self):
        a=b.load(Path(__file__).parent/'fixtures/legacy-release-approval.json');a['schema']=True
        path=self.root/'boolean.json';path.write_text(json.dumps(a))
        with self.assertRaisesRegex(ValueError,'fields/schema'):b.approval(path)
    def test_unapproved_unknown_field(self):
        p=self.root/'approval';p.write_text(json.dumps({'schema':1,'unexpected':True}))
        with self.assertRaisesRegex(ValueError,'fields'):b.approval(p)
if __name__=='__main__':unittest.main()
