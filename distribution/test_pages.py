import copy,io,json,tarfile,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import binary_repo as b
import pages as p

class PagesTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name);self.bundle=self.root/'bundle';self.bundle.mkdir();self.output=self.root/'out'
        for name in ('candidate','previous','landing','releases'):(self.bundle/name).mkdir()
        repo=self.bundle/'candidate'
        for name in ('entry.jar','entry.json','index-v1.jar','index-v2.json'):(repo/name).write_bytes(('signedfixture '+name).encode())
        apk=repo/'im.collie.app_1.apk';apk.write_bytes(b'fixtureAPK')
        a=b.load(Path(__file__).parent/'approval.fixture.json');a.update(release_id=1,tag='syntheticfixture',version_code=1,version_name='fixture',apk_sha256=b.sha(apk));a['aliases']={k:b.sha(apk) for k in b.ALIASES}
        (self.bundle/'releases/fixture.json').write_text(json.dumps(a))
        self.data={'repo':{'address':p.ORIGIN+'fdroid/repo'},'packages':{b.APP:{'versions':{a['apk_sha256']:{'manifest':{'versionCode':1,'versionName':'fixture','signer':{'sha256':[b.APK_CERTIFICATE]}},'file':{'name':'/'+apk.name,'sha256':b.sha(apk)}}}}}}
        receipt={'schema':2,'kind':'repository','payload':dict(release_manifest_sha256=a['release_manifest_sha256'],release_receipt_sha256=None,apk_sha256=a['apk_sha256'],apk_certificate_sha256=b.APK_CERTIFICATE,repository_certificate_sha256='a'*64,fdroidserver_revision='synthetic signedfixture transport mocked',metadata_revision='b'*40,indexes=[dict(file='entry.jar',sha256=b.sha(repo/'entry.jar'))],generation=b.generation_digest(repo)),'evidence':[dict(status='UNKNOWN',description='Synthetic Pages fixture; no production signing or deployment',sha256=None)]}
        (self.bundle/'repository-receipt.json').write_text(json.dumps(receipt))
        (self.bundle/'landing/index.html').write_text('synthetic landing '+('a'*64).upper()+' '+b.APK_CERTIFICATE.upper())
        (self.bundle/'landing/add-repository.svg').write_text('<svg/>')
        self.approval=dict(schema=1,kind='collie-pages-input',origin=p.ORIGIN,previous_site_sha256=p.digest({}),previous_manifest_sha256=None,candidate_generation_sha256=b.generation_digest(repo),repository_certificate_sha256='a'*64,repository_receipt_sha256=p.receipts.digest(receipt),release_approvals=['fixture.json'],landing_sha256=p.digest(p.inventory(self.bundle/'landing')),maximum_bytes=p.MAX_BYTES,policy_revision='c'*40)
        self.write()
    def write(self):(self.bundle/'approval.json').write_text(json.dumps(self.approval))
    def seal_previous(self):
        old=self.bundle/'previous'
        p.b.atomic_json(old/'site-manifest.json',dict(schema=1,kind='collie-pages-public-inventory',origin=p.ORIGIN,files=p.inventory(old)))
        self.approval['previous_site_sha256']=p.digest(p.inventory(old));self.approval['previous_manifest_sha256']=b.sha(old/'site-manifest.json');self.write()
    def start_preflight(self,plan):
        def fetch(url):
            if not plan['previous_files']:return {'status':404,'headers':{}}
            return dict(plan['previous_files'][url.removeprefix(plan['origin'])],status=200,headers={})
        return p.preflight(plan,self.output/'journal.json',fetch=fetch)
    def prepare(self):
        with patch('binary_repo.verify_indexes',return_value=self.data):return p.prepare(self.bundle,self.output,'c'*40)
    def fetch(self,plan,phase):
        def fetch(url):
            name=url.removeprefix(plan['origin']);return dict(plan['manifests'][phase][name],headers={'Cache-Control':'measuredfixture'})
        return fetch
    def test_two_phases_same_assets_old_indexes_then_new(self):
        old=self.bundle/'previous/fdroid/repo';old.mkdir(parents=True);(old/'entry.jar').write_bytes(b'old entry')
        self.seal_previous();plan=self.prepare()
        self.assertEqual((self.output/'phase1/site/fdroid/repo/entry.jar').read_bytes(),b'old entry')
        self.assertEqual((self.output/'phase2/site/fdroid/repo/entry.jar').read_bytes(),b'signedfixture entry.jar')
        for phase in ('phase1','phase2'):
            self.assertEqual((self.output/phase/'site/fdroid/repo/im.collie.app_1.apk').read_bytes(),b'fixtureAPK')
            with tarfile.open(self.output/phase/'artifact.tar') as tar:self.assertTrue(all(m.isfile() and not m.islnk() and not m.issym() for m in tar.getmembers()))
        self.assertEqual(plan['policy_revision'],'c'*40)
    def test_first_phase_has_no_active_feed_or_add_link(self):
        plan=self.prepare();self.assertFalse((self.output/'phase1/site/fdroid/repo/entry.jar').exists())
        self.assertIn('not active',(self.output/'phase1/site/index.html').read_text())
        preview=self.output/'phase1/site/_generations'/self.approval['candidate_generation_sha256']/'entry.jar';self.assertTrue(preview.is_file())
    def test_immutable_apk_conflict_rejected(self):
        old=self.bundle/'previous/fdroid/repo';old.mkdir(parents=True);(old/'im.collie.app_1.apk').write_bytes(b'otherAPK')
        self.seal_previous()
        with self.assertRaisesRegex(ValueError,'immutable URL'):self.prepare()
    def test_stale_approval_and_budget_fail(self):
        self.approval['candidate_generation_sha256']='0'*64;self.write()
        with self.assertRaisesRegex(ValueError,'approved generation'):self.prepare()
        self.approval['candidate_generation_sha256']=b.generation_digest(self.bundle/'candidate');self.approval['maximum_bytes']=1;self.write()
        with self.assertRaisesRegex(ValueError,'budget'):self.prepare()
        self.assertFalse(self.output.exists())
    def test_interruption_cached404_retry_and_journal_binding(self):
        plan=self.prepare();journal=self.output/'journal.json'
        self.start_preflight(plan)
        with self.assertRaisesRegex(ValueError,'phase1 readback'):p.readback(plan,journal,'phase2',fetch=self.fetch(plan,'phase2'))
        def notfound(url):raise OSError('cached404')
        with self.assertRaises(OSError):p.readback(plan,journal,'phase1',fetch=notfound)
        self.assertEqual(b.load(journal)['completed'],[])
        p.readback(plan,journal,'phase1',fetch=self.fetch(plan,'phase1'))
        p.readback(plan,journal,'phase1',fetch=self.fetch(plan,'phase1'))
        p.readback(plan,journal,'phase2',fetch=self.fetch(plan,'phase2'))
        self.assertEqual(b.load(journal)['completed'],['phase1','phase2'])
        changed=copy.deepcopy(plan);changed['policy_revision']='e'*40
        with self.assertRaisesRegex(ValueError,'another plan'):p.readback(changed,journal,'phase1',fetch=self.fetch(plan,'phase1'))
    def test_mixed_generation_readback_does_not_advance(self):
        plan=self.prepare();journal=self.output/'journal.json'
        self.start_preflight(plan)
        p.readback(plan,journal,'phase1',fetch=self.fetch(plan,'phase1'))
        def mixed(url):return {'sha256':'0'*64,'bytes':0,'headers':{}}
        with self.assertRaisesRegex(ValueError,'readback mismatch'):p.readback(plan,journal,'phase2',fetch=mixed)
        self.assertEqual(b.load(journal)['completed'],['phase1'])
        p.readback(plan,journal,'phase2',fetch=self.fetch(plan,'phase2'))
    def test_incomplete_previous_site_cannot_drop_retained_asset(self):
        old=self.bundle/'previous';(old/'assets').mkdir();(old/'assets/approved.svg').write_bytes(b'old approved asset');self.seal_previous()
        (old/'assets/approved.svg').unlink();self.approval['previous_site_sha256']=p.digest(p.inventory(old));self.write()
        with self.assertRaisesRegex(ValueError,'full retained site'):self.prepare()
    def test_live_previous_origin_and_preflight_required(self):
        plan=self.prepare();journal=self.output/'journal.json'
        with self.assertRaisesRegex(ValueError,'preflight'):p.readback(plan,journal,'phase1',fetch=self.fetch(plan,'phase1'))
        with self.assertRaisesRegex(ValueError,'empty feed'):p.preflight(plan,journal,fetch=lambda url:{'status':200})
        self.assertFalse(b.load(journal)['previous_verified'])
        self.start_preflight(plan)
        self.assertTrue(b.load(journal)['previous_verified'])
    def test_bundle_links_traversal_and_duplicates_fail(self):
        for kind in ('link','traversal','duplicate'):
            file=self.root/(kind+'.tar')
            with tarfile.open(file,'w') as tar:
                entry=tarfile.TarInfo('../outside' if kind=='traversal' else 'safe');entry.size=1
                if kind=='link':entry.type=tarfile.SYMTYPE;entry.linkname='/etc/passwd'
                tar.addfile(entry,io.BytesIO(b'x'))
                if kind=='duplicate':tar.addfile(entry,io.BytesIO(b'x'))
            with self.assertRaises(ValueError):p.unpack(file,self.root/(kind+'-output'))
