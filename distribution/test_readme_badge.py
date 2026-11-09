import json,tempfile,unittest
from pathlib import Path
import binary_repo as b
import readme_badge as r
class BadgeTest(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.root=Path(t.name)
        self.readme=self.root/'README.md';self.original='''<p align="center"><a href="https://apps.obtainium.imranr.dev/redirect?r=fixture"><img src="docs/assets/badge_obtainium.png" alt="Get it on Obtainium" height="80"></a> <a href="https://liberapay.com/Collie/">Donate</a></p>
<p><img height="50" src="docs/assets/collie-matrix-invite.svg"></p>
*F-droid-compatible repo coming soon.*
''';self.readme.write_text(self.original)
        self.value=dict(schema=1,kind='collie-repository-link-activation',environment='production',origin='https://repo.collie.im/',policy_revision='a'*40,plan_sha256='a'*64,journal_sha256='b'*64,repository_receipt_sha256='c'*64,repository_certificate_sha256='d'*64,client_acceptance_sha256='e'*64,apk_sha256='f'*64,checks={k:'PASS' for k in ('canonical_https_readback','official_fdroid','compatible_client')});self.record=self.root/'record.json';b.atomic_json(self.record,self.value)
    def test_patch_only_places_unchanged_badge_next_to_obtainium_same_height(self):
        patch=r.prepare(self.readme,self.record,b.sha(self.record),self.root/'out');text=patch.read_text()
        self.assertEqual(self.readme.read_text(),self.original);self.assertIn('height="80"></a> <a href="https://repo.collie.im/"',text);self.assertIn('Our F-Droid repository',text);self.assertNotIn('f-droid.org/packages',text);self.assertIn('collie-matrix-invite.svg',text)
        self.assertEqual(b.sha(self.root/'out/docs/assets/badge_fdroid.svg'),b.sha(Path(r.__file__).with_name('site')/'publication/get-it-on-fdroid.svg'))
    def test_staging_partial_client_and_wrong_approval_fail(self):
        with self.assertRaisesRegex(ValueError,'approved'):r.prepare(self.readme,self.record,'0'*64,self.root/'out')
        for mutation in ('staging','client'):
            value=dict(self.value)
            if mutation=='staging':value['environment']='staging'
            else:value['checks']=dict(value['checks'],compatible_client='NOT RUN')
            b.atomic_json(self.record,value)
            with self.assertRaises(ValueError):r.prepare(self.readme,self.record,b.sha(self.record),self.root/'out')
            self.assertFalse((self.root/'out').exists())
    def test_missing_or_already_active_badges_fail(self):
        for text in ('No Obtainium badge',self.original+'badge_fdroid.svg'):
            self.readme.write_text(text)
            with self.assertRaisesRegex(ValueError,'one existing'):r.prepare(self.readme,self.record,b.sha(self.record),self.root/'out')
