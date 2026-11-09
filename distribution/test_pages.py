import copy,hashlib,io,json,tarfile,tempfile,unittest
from email.message import Message
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
import binary_repo as b
import pages as p

# Approved phase metadata only: these sizes/names are exact, payloads stay tiny.
NATIVE_PHASE_HEADER_FIXTURE=(
    ('assets/collie-icon.svg',23983),
    ('assets/manrope-OFL.txt',4387),
    ('assets/manrope.ttf',164700),
    ('assets/site.css',4945),
    ('assets/site.js',1200),
    ('assets/sora-OFL.txt',4385),
    ('assets/sora.ttf',111400),
    ('index.html',2333),
    ('native-inputs/v1/maven/im/collie/native/libsignal-android/0.106.1-collie.1/libsignal-android-0.106.1-collie.1.aar',215587114),
    ('native-inputs/v1/maven/im/collie/native/libsignal-android/0.106.1-collie.1/libsignal-android-0.106.1-collie.1.pom',1718),
    ('native-inputs/v1/maven/im/collie/native/libsignal-client/0.106.1-collie.1/libsignal-client-0.106.1-collie.1.jar',1003356),
    ('native-inputs/v1/maven/im/collie/native/libsignal-client/0.106.1-collie.1/libsignal-client-0.106.1-collie.1.pom',1693),
    ('native-inputs/v1/maven/im/collie/native/ringrtc-android/2.74.0-collie.1/ringrtc-android-2.74.0-collie.1.aar',94240994),
    ('native-inputs/v1/maven/im/collie/native/ringrtc-android/2.74.0-collie.1/ringrtc-android-2.74.0-collie.1.pom',1572),
    ('site-manifest.json',2625),
)

class PagesArchiveHeaderTests(unittest.TestCase):
    def test_approved_native_phase_paths_fit_ustar_without_extensions(self):
        self.assertEqual(p.ARCHIVE_FORMAT,tarfile.USTAR_FORMAT)
        self.assertEqual(len(NATIVE_PHASE_HEADER_FIXTURE),15)
        self.assertEqual([name for name,_ in NATIVE_PHASE_HEADER_FIXTURE],
                         sorted(name for name,_ in NATIVE_PHASE_HEADER_FIXTURE))
        for name,size in NATIVE_PHASE_HEADER_FIXTURE:
            with self.subTest(name=name):
                info=tarfile.TarInfo(name);info.size=size;info.mode=0o644;info.mtime=0
                encoded=info.tobuf(p.ARCHIVE_FORMAT)
                self.assertEqual(len(encoded),512)
                self.assertEqual(encoded[156:157],tarfile.REGTYPE)
                stored_name=encoded[0:100].split(b'\0',1)[0].decode('ascii')
                prefix=encoded[345:500].split(b'\0',1)[0].decode('ascii')
                self.assertEqual((prefix+'/'+stored_name) if prefix else stored_name,name)
                size_field=encoded[124:136].split(b'\0',1)[0].strip()
                self.assertEqual(int(size_field,8),size)
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);site=root/'site';site.mkdir();manifest={}
            for name,_ in NATIVE_PHASE_HEADER_FIXTURE:
                target=site/name;target.parent.mkdir(parents=True,exist_ok=True)
                payload=('fixture:'+name).encode();target.write_bytes(payload)
                manifest[name]={'bytes':len(payload),'sha256':hashlib.sha256(payload).hexdigest()}
            output=root/'archive.tar';p.archive(site,output,manifest);raw=output.read_bytes();cursor=0
            for name,entry in sorted(manifest.items()):
                header=raw[cursor:cursor+512];cursor+=512
                self.assertEqual(len(header),512);self.assertEqual(header[156:157],tarfile.REGTYPE)
                stored_name=header[0:100].split(b'\0',1)[0].decode('ascii')
                prefix=header[345:500].split(b'\0',1)[0].decode('ascii')
                self.assertEqual((prefix+'/'+stored_name) if prefix else stored_name,name)
                size_field=header[124:136].split(b'\0',1)[0].strip()
                size=int(size_field,8);self.assertEqual(size,entry['bytes'])
                payload=raw[cursor:cursor+size];cursor+=size;cursor+=(-size)%512
                self.assertEqual(hashlib.sha256(payload).hexdigest(),entry['sha256'])
            trailer=raw[cursor:]
            self.assertGreaterEqual(len(trailer),1024)
            self.assertEqual(len(trailer)%512,0)
            self.assertFalse(any(trailer))

    def test_archive_rejects_public_path_that_cannot_fit_ustar(self):
        name='assets/'+'a'*236+'.svg'
        self.assertEqual(p.safe_name(name),name)
        self.assertTrue(p.public_site_name(name))
        oversized=tarfile.TarInfo('assets/too-large.svg');oversized.size=(1<<33)
        with self.assertRaises(ValueError):oversized.tobuf(p.ARCHIVE_FORMAT)
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);site=root/'site';site.mkdir();content=b'fixture'
            target=site/name;target.parent.mkdir(parents=True);target.write_bytes(content)
            manifest={name:{'bytes':len(content),'sha256':hashlib.sha256(content).hexdigest()}}
            with self.assertRaises(ValueError):p.archive(site,root/'unsupported.tar',manifest)

class PagesFreshInventoryTests(unittest.TestCase):
    def test_observe_requests_revalidation_and_rejects_stale_cache_responses(self):
        url='https://repo.collie.im/native-inputs/example'
        headers=Message();headers['Age']='0';headers['X-Cache']='MISS'
        error=HTTPError(url,404,'Not Found',headers,None)
        with patch('urllib.request.urlopen',side_effect=error) as opened:
            result=p.observe(url)
        request=opened.call_args.args[0]
        self.assertEqual(request.get_header('Cache-control'),'no-cache, no-store')
        self.assertEqual(request.get_header('Pragma'),'no-cache')
        self.assertEqual(result['status'],404)
        self.assertEqual(result['headers']['X-Cache'],'MISS')

        stale_headers=Message();stale_headers['Age']='1920';stale_headers['X-Cache']='MISS'
        stale=HTTPError(url,404,'Not Found',stale_headers,None)
        with patch('urllib.request.urlopen',side_effect=stale):
            with self.assertRaisesRegex(ValueError,'not a fresh canonical inventory'):
                p.observe(url)

        hit_headers=Message();hit_headers['Age']='0';hit_headers['X-Cache']='HIT'
        hit=HTTPError(url,404,'Not Found',hit_headers,None)
        with patch('urllib.request.urlopen',side_effect=hit):
            with self.assertRaisesRegex(ValueError,'not a fresh canonical inventory'):
                p.observe(url)

        downgraded=HTTPError('http://repo.collie.im/native-inputs/example',404,'Not Found',headers,None)
        with patch('urllib.request.urlopen',side_effect=downgraded):
            with self.assertRaisesRegex(ValueError,'HTTPS readback required'):
                p.observe(url)

    def test_observe_rejects_unknown_age(self):
        url='https://repo.collie.im/native-inputs/example'
        for age in ('unknown',''):
            headers=Message();headers['Age']=age;headers['X-Cache']='MISS'
            error=HTTPError(url,404,'Not Found',headers,None)
            with self.subTest(age=age),patch('urllib.request.urlopen',side_effect=error):
                with self.assertRaisesRegex(ValueError,'freshness is unknown'):
                    p.observe(url)

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
        b.landing(self.bundle/'landing',p.ORIGIN+'fdroid/repo/','a'*64,b.APK_CERTIFICATE,'https://github.com/0cwa/collieim-android/releases/download/v8.35.1-1-rc1/collie.apk')
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
    def test_existing_live_manifest_mismatch_blocks_preflight(self):
        old=self.bundle/'previous';(old/'assets').mkdir();(old/'assets/approved.svg').write_bytes(b'previous public asset');self.seal_previous()
        plan=self.prepare();journal=self.output/'journal.json'
        def changed(url):
            name=url.removeprefix(plan['origin']);actual=dict(plan['previous_files'][name],status=200,headers={})
            if name=='site-manifest.json':actual['sha256']='0'*64
            return actual
        with self.assertRaisesRegex(ValueError,'live previous site differs'):p.preflight(plan,journal,fetch=changed)
        self.assertFalse(b.load(journal)['previous_verified'])
        self.start_preflight(plan)
    def test_same_approved_inputs_produce_identical_artifacts(self):
        first=self.prepare()
        with patch('binary_repo.verify_indexes',return_value=self.data):second=p.prepare(self.bundle,self.root/'other-output','c'*40)
        self.assertEqual(first['artifacts'],second['artifacts'])
        self.assertEqual(p.digest(first),p.digest(second))
    def test_bundle_links_traversal_and_duplicates_fail(self):
        for kind in ('link','traversal','duplicate'):
            file=self.root/(kind+'.tar')
            with tarfile.open(file,'w') as tar:
                entry=tarfile.TarInfo('../outside' if kind=='traversal' else 'safe');entry.size=1
                if kind=='link':entry.type=tarfile.SYMTYPE;entry.linkname='/etc/passwd'
                tar.addfile(entry,io.BytesIO(b'x'))
                if kind=='duplicate':tar.addfile(entry,io.BytesIO(b'x'))
            with self.assertRaises(ValueError):p.unpack(file,self.root/(kind+'-output'))

class NativePagesTest(unittest.TestCase):
    setUp=PagesTest.setUp
    write=PagesTest.write
    prepare=PagesTest.prepare
    seal_previous=PagesTest.seal_previous
    def add_native(self,only=False):
        # Small transport fixtures use the exact six production coordinate names;
        # only content hashes/sizes are mocked. Actual inputs have separate evidence.
        policy=b.load(p.NATIVE_POLICY)
        for name,entry in policy['files'].items():
            path=self.bundle/'native'/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(('fixture '+name).encode())
            entry.update(sha256=b.sha(path),bytes=path.stat().st_size)
        self.native_policy=self.root/'native-policy.json';b.atomic_json(self.native_policy,policy)
        b.atomic_json(self.bundle/'native-manifest.json',policy)
        self.approval.update(schema=2,native_manifest_sha256=b.sha(self.bundle/'native-manifest.json'))
        if only:
            self.approval={k:v for k,v in self.approval.items() if k in {'schema','origin','previous_site_sha256','previous_manifest_sha256','native_manifest_sha256','maximum_bytes','policy_revision'}}
            self.approval.update(schema=1,kind='collie-pages-native-input')
        self.write();self.policy_patch=patch.object(p,'NATIVE_POLICY',self.native_policy);self.policy_patch.start();self.addCleanup(self.policy_patch.stop)
        return policy['files']
    def test_native_only_needs_no_apk_index_key_or_landing(self):
        files=self.add_native(only=True)
        for name in ('candidate','releases','landing'):__import__('shutil').rmtree(self.bundle/name)
        with patch('binary_repo.verify_indexes',side_effect=AssertionError('no index verifier needed')):plan=p.prepare(self.bundle,self.output,'c'*40)
        self.assertEqual(plan['mode'],'native-only');self.assertIsNone(plan['repository_certificate_sha256'])
        for phase in ('phase1','phase2'):
            site=self.output/phase/'site';text=(site/'index.html').read_text();self.assertIn('not active',text);self.assertIn('native inputs',text);self.assertNotIn('signed index',text);self.assertNotIn('fdroidrepos:',text);self.assertFalse((site/'fdroid').exists());self.assertFalse((site/'add-repository.svg').exists())
            self.assertEqual({n:e for n,e in plan['manifests'][phase].items() if n.startswith('native-inputs/')},files)
        self.assertEqual(plan['artifacts']['phase1'],plan['artifacts']['phase2'])
    def test_native_feed_both_phases_same_six_files(self):
        files=self.add_native();plan=self.prepare()
        for phase in ('phase1','phase2'):self.assertEqual({n:e for n,e in plan['manifests'][phase].items() if n.startswith('native-inputs/')},files)
        self.assertFalse(p.public_site_name('native-inputs/v2/maven/extra.jar'))
    def test_native_missing_extra_tampered_and_unapproved_fail(self):
        files=self.add_native();name=next(iter(files));path=self.bundle/'native'/name;original=path.read_bytes()
        for change in ('missing','tampered','extra','manifest','approval'):
            with self.subTest(change=change):
                if change=='missing':path.unlink()
                elif change=='tampered':path.write_bytes(b'changed')
                elif change=='extra':(self.bundle/'native/extra.jar').write_bytes(b'private')
                elif change=='manifest':(self.bundle/'native-manifest.json').write_text('{}')
                else:self.approval['native_manifest_sha256']=None;self.write()
                with self.assertRaises(ValueError):self.prepare()
                self.assertFalse(self.output.exists());path.write_bytes(original)
                extra=self.bundle/'native/extra.jar'
                if extra.exists():extra.unlink()
                __import__('shutil').copyfile(self.native_policy,self.bundle/'native-manifest.json');self.approval['native_manifest_sha256']=b.sha(self.bundle/'native-manifest.json');self.write()
    def test_native_previous_cannot_be_omitted_or_rewritten(self):
        files=self.add_native();old=self.bundle/'previous'
        for name in files:
            dest=old/name;dest.parent.mkdir(parents=True,exist_ok=True);__import__('shutil').copyfile(self.bundle/'native'/name,dest)
        self.seal_previous();self.approval['native_manifest_sha256']=None;self.write()
        __import__('shutil').rmtree(self.bundle/'native')
        with self.assertRaisesRegex(ValueError,'must remain complete'):self.prepare()
    def test_native_previous_old_indexes_preserved_and_budget_enforced(self):
        self.add_native(only=True);old=self.bundle/'previous/fdroid/repo';old.mkdir(parents=True);(old/'entry.jar').write_bytes(b'previous signed entry');self.seal_previous()
        plan=self.prepare()
        for phase in ('phase1','phase2'):self.assertEqual((self.output/phase/'site/fdroid/repo/entry.jar').read_bytes(),b'previous signed entry')
        __import__('shutil').rmtree(self.output);self.approval['maximum_bytes']=100;self.write()
        with self.assertRaisesRegex(ValueError,'budget'):self.prepare()

    def test_initial_native_url_conflict_blocks_preflight(self):
        self.add_native(only=True);plan=self.prepare();journal=self.output/'journal.json'
        def conflict(url):
            if '/native-inputs/' in url:return {'status':200,'sha256':'0'*64,'bytes':0}
            return {'status':404}
        with self.assertRaisesRegex(ValueError,'native URL differs'):p.preflight(plan,journal,fetch=conflict)
        self.assertFalse(b.load(journal)['previous_verified'])
        p.preflight(plan,journal,fetch=lambda url:dict(plan['manifests']['phase1'][url.removeprefix(p.ORIGIN)],status=200) if '/native-inputs/' in url else {'status':404})
        self.assertTrue(b.load(journal)['previous_verified'])

    def test_existing_feed_new_native_url_conflict_blocks_preflight(self):
        self.add_native(only=True);old=self.bundle/'previous/fdroid/repo';old.mkdir(parents=True);(old/'entry.jar').write_bytes(b'previous signed entry');self.seal_previous()
        plan=self.prepare();journal=self.output/'journal.json';visited=[]
        def fetch(url):
            name=url.removeprefix(p.ORIGIN);visited.append(name)
            if name in plan['previous_files']:return dict(plan['previous_files'][name],status=200)
            return {'status':200,'sha256':'0'*64,'bytes':0}
        with self.assertRaisesRegex(ValueError,'native URL differs'):p.preflight(plan,journal,fetch=fetch)
        self.assertTrue(any(name.startswith('native-inputs/') for name in visited));self.assertFalse(b.load(journal)['previous_verified'])
        def absent(url):
            name=url.removeprefix(p.ORIGIN)
            return dict(plan['previous_files'][name],status=200) if name in plan['previous_files'] else {'status':404}
        p.preflight(plan,journal,fetch=absent);self.assertTrue(b.load(journal)['previous_verified'])
