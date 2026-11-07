"""Synthetic metadata fixtures only; no real production acceptance is claimed."""
import copy,json,shutil,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import binary_repo as b
import pages as p
import pages_activation as a
import test_pages as fixtures
class ActivationTest(unittest.TestCase):
    write=fixtures.PagesTest.write
    def setUp(self):
        fixtures.PagesTest.setUp(self)
        # Use actual trusted inactive HTML/fonts, with mocked index signatures.
        shutil.rmtree(self.bundle/'landing');b.landing(self.bundle/'landing',p.ORIGIN+'fdroid/repo/','a'*64,b.APK_CERTIFICATE,'https://github.com/0cwa/collieim-android/releases/download/v8.35.1-1-rc1/collie.apk')
        self.approval['landing_sha256']=p.digest(p.inventory(self.bundle/'landing'));fixtures.PagesTest.write(self)
        with patch('binary_repo.verify_indexes',return_value=self.data):feed=p.prepare(self.bundle,self.root/'feed-output','c'*40)
        shutil.rmtree(self.bundle/'previous');shutil.copytree(self.root/'feed-output/phase2/site',self.bundle/'previous')
        journal=dict(schema=1,plan_sha256=p.digest(feed),previous_verified=True,completed=['phase1','phase2'],readbacks={'phase2':{k:dict(v,status=200) for k,v in feed['manifests']['phase2'].items()}})
        clients=dict(schema=1,environment='production',origin=p.ORIGIN,repository_certificate_sha256='a'*64,apk_sha256=next(iter(self.data['packages'][b.APP]['versions'])),clients={name:dict(version='synthetic fixture',evidence_sha256='d'*64,tests={x:'PASS' for x in ('add','refresh','install','update')}) for name in a.CLIENTS})
        for name,value in [('feed-plan.json',feed),('feed-journal.json',journal),('client-acceptance.json',clients)]:b.atomic_json(self.bundle/name,value)
        self.activation=dict(schema=1,kind='collie-pages-link-activation',environment='production',origin=p.ORIGIN,policy_revision='e'*40,feed_policy_revision='c'*40,feed_plan_sha256=p.digest(feed),feed_journal_sha256=p.digest(journal),repository_receipt_sha256=feed['repository_receipt_sha256'],repository_certificate_sha256='a'*64,client_acceptance_sha256=p.digest(clients),previous_site_sha256=p.digest(p.inventory(self.bundle/'previous')),previous_manifest_sha256=b.sha(self.bundle/'previous/site-manifest.json'),maximum_bytes=p.MAX_BYTES,download_url='https://github.com/0cwa/collieim-android/releases/download/v8.35.1-1-rc1/collie.apk');b.atomic_json(self.bundle/'approval.json',self.activation)
    def prepare(self):
        with patch('binary_repo.verify_indexes',return_value=self.data):return a.prepare(self.bundle,self.root/'activation-output','e'*40)
    def test_first_active_snapshot_changes_only_links_not_apk_index_native_or_assets(self):
        plan=self.prepare();old=plan['previous_files'];new=plan['manifests']['phase2'];self.assertTrue(p.site_page.has_add_link((self.root/'activation-output/phase2/site/index.html').read_text()))
        for name,entry in old.items():
            if name not in ('index.html','site-manifest.json'):self.assertEqual(new[name],entry,name)
        self.assertEqual(set(new)-set(old),{'add-repository.png'})
    def test_staging_client_partial_readback_and_substituted_receipt_fail(self):
        for mutation in ('staging','missingclient','readback','receipt'):
            with self.subTest(mutation=mutation):
                original={name:(self.bundle/name).read_bytes() for name in ('approval.json','client-acceptance.json','feed-journal.json')}
                approval=copy.deepcopy(self.activation)
                if mutation in ('staging','missingclient'):
                    value=b.load(self.bundle/'client-acceptance.json')
                    if mutation=='staging':value['environment']='staging'
                    else:value['clients'].pop('com.looker.droidify')
                    b.atomic_json(self.bundle/'client-acceptance.json',value);approval['client_acceptance_sha256']=p.digest(value)
                elif mutation=='readback':
                    value=b.load(self.bundle/'feed-journal.json');value['completed']=['phase1'];b.atomic_json(self.bundle/'feed-journal.json',value);approval['feed_journal_sha256']=p.digest(value)
                else:approval['repository_receipt_sha256']='0'*64
                b.atomic_json(self.bundle/'approval.json',approval)
                with self.assertRaises(ValueError):self.prepare()
                self.assertFalse((self.root/'activation-output').exists())
                for name,data in original.items():(self.bundle/name).write_bytes(data)
    def test_record_requires_post_activation_complete_readback(self):
        plan=self.prepare();out=self.root/'activation-output'
        with self.assertRaisesRegex(ValueError,'completed'):a.record(self.bundle,out/'plan.json',out/'journal.json')
        def fetch(url):return dict(plan['previous_files'][url.removeprefix(p.ORIGIN)],status=200)
        p.preflight(plan,out/'journal.json',fetch=fetch);p.readback(plan,out/'journal.json','phase1',fetch=fetch)
        p.readback(plan,out/'journal.json','phase2',fetch=lambda url:dict(plan['manifests']['phase2'][url.removeprefix(p.ORIGIN)],status=200))
        self.assertEqual(a.record(self.bundle,out/'plan.json',out/'journal.json')['checks']['canonical_https_readback'],'PASS')
    def test_normal_feed_prepare_rejects_first_live_or_preview_add_link(self):
        self.approval.update(previous_site_sha256=p.digest(p.inventory(self.bundle/'previous')),previous_manifest_sha256=b.sha(self.bundle/'previous/site-manifest.json'))
        for state in ('preview','active'):
            b.landing(self.bundle/'landing',p.ORIGIN+'fdroid/repo/','a'*64,b.APK_CERTIFICATE,self.activation['download_url'],state=state);self.approval['landing_sha256']=p.digest(p.inventory(self.bundle/'landing'));b.atomic_json(self.bundle/'approval.json',self.approval)
            with patch('binary_repo.verify_indexes',return_value=self.data),self.assertRaisesRegex(ValueError,'preview|activation'):p.prepare(self.bundle,self.root/('forbidden-'+state),'c'*40)
    def test_already_active_updates_keep_links_and_cannot_unpublish(self):
        plan=self.prepare();shutil.rmtree(self.bundle/'previous');shutil.copytree(self.root/'activation-output/phase2/site',self.bundle/'previous')
        self.approval.update(previous_site_sha256=p.digest(p.inventory(self.bundle/'previous')),previous_manifest_sha256=b.sha(self.bundle/'previous/site-manifest.json'))
        for state in ('active','inactive'):
            if state=='inactive':shutil.rmtree(self.bundle/'landing')
            b.landing(self.bundle/'landing',p.ORIGIN+'fdroid/repo/','a'*64,b.APK_CERTIFICATE,self.activation['download_url'],state=state);self.approval['landing_sha256']=p.digest(p.inventory(self.bundle/'landing'));b.atomic_json(self.bundle/'approval.json',self.approval)
            with patch('binary_repo.verify_indexes',return_value=self.data):
                if state=='active':p.prepare(self.bundle,self.root/'active-update','c'*40)
                else:
                    with self.assertRaisesRegex(ValueError,'remain active'):p.prepare(self.bundle,self.root/'bad-update','c'*40)

    def test_ordinary_prepare_rejects_relative_first_links_and_stale_new_qr(self):
        self.approval.update(previous_site_sha256=p.digest(p.inventory(self.bundle/'previous')),previous_manifest_sha256=b.sha(self.bundle/'previous/site-manifest.json'))
        original=(self.bundle/'landing/index.html').read_text()
        for href in ('/fdroid/repo/?fingerprint='+'A'*64,'//repo.collie.im/fdroid/repo/?fingerprint='+'A'*64):
            (self.bundle/'landing/index.html').write_text(original+'<a href="'+href+'">Add</a>');self.approval['landing_sha256']=p.digest(p.inventory(self.bundle/'landing'));b.atomic_json(self.bundle/'approval.json',self.approval)
            with patch('binary_repo.verify_indexes',return_value=self.data),self.assertRaisesRegex(ValueError,'activation'):p.prepare(self.bundle,self.root/'forbidden-relative','c'*40)
        for href in (r'/fdroid\repo/?fingerprint=A',r'https://repo.collie.im\fdroid\repo/?fingerprint=A','/fdroid/\nrepo/?fingerprint=A'):
            (self.bundle/'landing/index.html').write_text(original+'<a href="'+href+'">Add</a>');self.approval['landing_sha256']=p.digest(p.inventory(self.bundle/'landing'));b.atomic_json(self.bundle/'approval.json',self.approval)
            with patch('binary_repo.verify_indexes',return_value=self.data),self.assertRaisesRegex(ValueError,'noncanonical'):p.prepare(self.bundle,self.root/'forbidden-noncanonical','c'*40)
        (self.bundle/'landing/index.html').write_text(original+'<base href="https://repo.collie.im/"><a href="fdroid/repo/">Add</a>');self.approval['landing_sha256']=p.digest(p.inventory(self.bundle/'landing'));b.atomic_json(self.bundle/'approval.json',self.approval)
        with patch('binary_repo.verify_indexes',return_value=self.data),self.assertRaisesRegex(ValueError,'approved base'):p.prepare(self.bundle,self.root/'forbidden-base','c'*40)
        (self.bundle/'landing/index.html').write_text(original);(self.bundle/'landing/add-repository.png').write_bytes(b'opaque stale fixture QR')
        self.approval['landing_sha256']=p.digest(p.inventory(self.bundle/'landing'));b.atomic_json(self.bundle/'approval.json',self.approval)
        with patch('binary_repo.verify_indexes',return_value=self.data),self.assertRaisesRegex(ValueError,'new repository QR'):p.prepare(self.bundle,self.root/'forbidden-stale','c'*40)

        # Changed bytes at an existing inactive QR URL are new QR content too.
        previous=self.bundle/'previous';(previous/'add-repository.png').write_bytes(b'old unactivated placeholder')
        b.atomic_json(previous/'site-manifest.json',dict(schema=1,kind='collie-pages-public-inventory',origin=p.ORIGIN,files={k:v for k,v in p.inventory(previous).items() if k!='site-manifest.json'}))
        self.approval.update(previous_site_sha256=p.digest(p.inventory(previous)),previous_manifest_sha256=b.sha(previous/'site-manifest.json'));b.atomic_json(self.bundle/'approval.json',self.approval)
        with patch('binary_repo.verify_indexes',return_value=self.data),self.assertRaisesRegex(ValueError,'new repository QR'):p.prepare(self.bundle,self.root/'forbidden-replaced-qr','c'*40)
