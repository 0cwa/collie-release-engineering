"""Behavior tests for verified whole-site feed retention and readback."""
import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import channel_profile as channels
import feed


def served_inventory(files):
    return json.dumps({'schema':1,'kind':'collie-pages-public-inventory',
        'origin':feed.SITE_ORIGIN,'files':files},sort_keys=True,separators=(',',':')).encode()+b'\n'


class FeedCoexistenceTests(unittest.TestCase):
    def setUp(self):
        self.beta={'fdroid/beta/repo/entry.jar':b'beta-index','fdroid/beta/repo/index-v2.json':b'{"beta":true}'}
        self.stable={'fdroid/repo/entry.jar':b'stable-index','fdroid/repo/index-v2.json':b'{"stable":true}',
                     'native-inputs/v1/maven/module.pom':b'native'}
        self.blobs={**self.beta,**self.stable}
        self.inventory=served_inventory({name:{'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data)}
                                         for name,data in self.blobs.items()})

    def fetch_for(self, blobs=None, inventory=None, statuses=None):
        blobs=self.blobs if blobs is None else blobs
        inventory=self.inventory if inventory is None else inventory
        statuses={} if statuses is None else statuses
        def fetch(path, *, max_bytes):
            if path=='site-manifest.json':return statuses.get(path,200),inventory
            if path in statuses:return statuses[path],b''
            return (200,blobs[path]) if path in blobs else (404,b'')
        return fetch

    def test_beta_capture_checks_served_hashes_and_signed_index_parser(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot=Path(tmp)/'capture'
            with patch.object(feed.binary_repo,'verify_indexes',return_value={'repo':{'address':channels.BETA.repository_url}}) as verify, \
                    patch.object(feed.binary_repo,'release_tuples',return_value=[(220,'8.40.0')]):
                result=feed.capture_beta_route(snapshot,fetch=self.fetch_for())
            self.assertEqual(result['release'],{'version_name':'8.40.0','version_code':220})
            self.assertEqual((snapshot/'fdroid/beta/repo/entry.jar').read_bytes(),self.beta['fdroid/beta/repo/entry.jar'])
            verify.assert_called_once_with(snapshot/'fdroid/beta/repo',channels.BETA.repository_certificate,channels.BETA)

    def test_inventory_cannot_authorize_tampered_beta_bytes(self):
        blobs=dict(self.blobs);blobs['fdroid/beta/repo/entry.jar']=b'changed'
        with tempfile.TemporaryDirectory() as tmp, self.assertRaisesRegex(ValueError,'differ from canonical inventory'):
            feed.capture_beta_route(Path(tmp)/'capture',fetch=self.fetch_for(blobs=blobs))

    def test_valid_signature_with_stable_address_is_rejected_on_beta_route(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(feed.binary_repo,'verify_indexes',return_value={'repo':{'address':channels.STABLE.repository_url}}), \
                self.assertRaisesRegex(ValueError,'address differs from fixed route'):
            feed.capture_beta_route(Path(tmp)/'capture',fetch=self.fetch_for())

    def test_beta_inventory_cannot_force_unbounded_retention_download(self):
        files={'fdroid/beta/repo/entry.jar':{'sha256':'a'*64,'bytes':feed.SITE_LIMIT},
               'fdroid/beta/repo/index-v2.json':{'sha256':'b'*64,'bytes':1}}
        inventory=served_inventory(files)
        with tempfile.TemporaryDirectory() as tmp, self.assertRaisesRegex(ValueError,'byte budget'):
            feed.capture_beta_route(Path(tmp)/'capture',fetch=self.fetch_for(inventory=inventory))

    def test_inventory_404_is_not_treated_as_empty_when_beta_bytes_exist(self):
        with tempfile.TemporaryDirectory() as tmp, self.assertRaisesRegex(ValueError,'without the canonical site inventory'):
            feed.capture_beta_route(Path(tmp)/'capture',fetch=self.fetch_for(inventory=b'',statuses={'site-manifest.json':404}))

    def test_non404_inventory_failure_does_not_erase_beta(self):
        with tempfile.TemporaryDirectory() as tmp, self.assertRaisesRegex(OSError,'temporary origin failure'):
            def failing(_path, *, max_bytes):raise OSError('temporary origin failure')
            feed.capture_beta_route(Path(tmp)/'capture',fetch=failing)

    def test_compare_and_swap_rejects_beta_update_after_stable_capture(self):
        with tempfile.TemporaryDirectory() as tmp:
            info={'inventory_sha256':hashlib.sha256(self.inventory).hexdigest()}
            newer=dict(self.blobs);newer['fdroid/beta/repo/entry.jar']=b'newer signed beta index'
            new_inventory=served_inventory({name:{'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data)}
                                            for name,data in newer.items()})
            with self.assertRaisesRegex(ValueError,'changed after Beta capture'):
                feed.verify_beta_snapshot_is_current(info,fetch=self.fetch_for(blobs=newer,inventory=new_inventory))

    def test_compare_and_swap_rejects_beta_appearing_after_empty_capture(self):
        with tempfile.TemporaryDirectory() as tmp:
            empty=served_inventory({})
            info={'inventory_sha256':None}
            with self.assertRaisesRegex(ValueError,'Beta route appeared'):
                feed.verify_beta_snapshot_is_current(info,fetch=self.fetch_for(inventory=empty))

    def test_complete_live_capture_keeps_stable_beta_and_native_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            destination=Path(tmp)/'site'
            with patch.object(feed.binary_repo,'verify_indexes',side_effect=lambda _path,_cert,profile:{'repo':{'address':profile.repository_url}}) as verify:
                result=feed.capture_live_site(destination,fetch=self.fetch_for())
            self.assertEqual(set(result['files']),set(self.blobs))
            self.assertEqual((destination/'fdroid/beta/repo/entry.jar').read_bytes(),self.beta['fdroid/beta/repo/entry.jar'])
            self.assertEqual((destination/'fdroid/repo/entry.jar').read_bytes(),self.stable['fdroid/repo/entry.jar'])
            self.assertEqual((destination/'native-inputs/v1/maven/module.pom').read_bytes(),b'native')
            self.assertEqual(verify.call_count,2)

    def test_stable_deploy_baseline_is_captured_and_compared_not_recaptured_as_new_expectation(self):
        with tempfile.TemporaryDirectory() as tmp:
            baseline=Path(tmp)/'baseline.json'
            initial={'inventory_sha256':hashlib.sha256(self.inventory).hexdigest(),'files':{}}
            with patch.object(feed,'capture_live_site',return_value=initial):
                value=feed.capture_deploy_baseline(baseline,fetch=lambda *_a,**_k:None)
            self.assertEqual(value['inventory_sha256'],initial['inventory_sha256'])
            with patch.object(feed,'capture_live_site',return_value=initial):
                self.assertTrue(feed.verify_deploy_baseline(baseline,fetch=lambda *_a,**_k:None))
            newer={**initial,'inventory_sha256':'f'*64}
            with patch.object(feed,'capture_live_site',return_value=newer), \
                    self.assertRaisesRegex(ValueError,'changed after Stable candidate capture'):
                feed.verify_deploy_baseline(baseline,fetch=lambda *_a,**_k:None)
            malformed=json.loads(baseline.read_text());malformed['inventory_sha256']=None
            baseline.write_text(json.dumps(malformed))
            with self.assertRaisesRegex(ValueError,'invalid captured Stable deploy baseline'):
                feed.verify_deploy_baseline(baseline,fetch=lambda *_a,**_k:None)

    def test_deploy_baseline_roundtrip_validates_live_route_bytes_and_rejects_late_beta(self):
        with tempfile.TemporaryDirectory() as tmp:
            baseline=Path(tmp)/'baseline.json'
            parser=patch.object(feed.binary_repo,'verify_indexes',side_effect=lambda _path,_cert,profile:
                {'repo':{'address':profile.repository_url}})
            with parser:
                feed.capture_deploy_baseline(baseline,fetch=self.fetch_for())
                self.assertTrue(feed.verify_deploy_baseline(baseline,fetch=self.fetch_for()))
            newer={**self.blobs,'fdroid/beta/repo/entry.jar':b'late older-deployment completion'}
            inventory=served_inventory({name:{'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data)}
                                        for name,data in newer.items()})
            with patch.object(feed.binary_repo,'verify_indexes',side_effect=lambda _path,_cert,profile:
                    {'repo':{'address':profile.repository_url}}), \
                    self.assertRaisesRegex(ValueError,'changed after Stable candidate capture'):
                feed.verify_deploy_baseline(baseline,fetch=self.fetch_for(blobs=newer,inventory=inventory))
            with self.assertRaisesRegex(OSError,'temporary origin failure'):
                def unavailable(_path, *, max_bytes):raise OSError('temporary origin failure')
                feed.verify_deploy_baseline(baseline,fetch=unavailable)

    def test_live_inventory_rejects_wrong_stable_repository_address_too(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(feed.binary_repo,'verify_indexes',side_effect=lambda _path,_cert,profile:{
                    'repo':{'address':channels.BETA.repository_url if profile.name=='stable' else channels.BETA.repository_url}}), \
                self.assertRaisesRegex(ValueError,'stable served index address'):
            feed.capture_live_site(Path(tmp)/'site',fetch=self.fetch_for())

    def test_candidate_readback_rejects_a_different_served_site(self):
        with tempfile.TemporaryDirectory() as tmp:
            candidate=Path(tmp)/'candidate';candidate.mkdir()
            for name,data in self.blobs.items():
                target=candidate/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
            state={'schema':1,'latest':'v8.35.2','beta':{'version_name':'8.40.0','version_code':220}}
            (candidate/'feed-state.json').write_text(json.dumps(state))
            records={name:{'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data)} for name,data in self.blobs.items()}
            records['feed-state.json']={'sha256':hashlib.sha256((candidate/'feed-state.json').read_bytes()).hexdigest(),
                                        'bytes':(candidate/'feed-state.json').stat().st_size}
            (candidate/feed.SITE_INVENTORY).write_bytes(served_inventory(records))
            live_blobs={**self.blobs,'feed-state.json':(candidate/'feed-state.json').read_bytes()}
            fetch=self.fetch_for(blobs=live_blobs,inventory=(candidate/feed.SITE_INVENTORY).read_bytes())
            with patch.object(feed.binary_repo,'verify_indexes',side_effect=lambda _path,_cert,profile:{'repo':{'address':profile.repository_url}}):
                feed.verify_live_site(candidate,fetch=fetch)
            changed={**live_blobs,'fdroid/beta/repo/entry.jar':b'stale candidate overwritten'}
            changed_manifest=served_inventory({name:{'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data)}
                                               for name,data in changed.items()})
            with patch.object(feed.binary_repo,'verify_indexes',side_effect=lambda _path,_cert,profile:{'repo':{'address':profile.repository_url}}), \
                    self.assertRaisesRegex(ValueError,'inventory differs'):
                feed.verify_live_site(candidate,fetch=self.fetch_for(blobs=changed,inventory=changed_manifest))

    def test_scheduled_stable_workflow_uses_shared_noncanceling_deploy_lock(self):
        workflow=(Path(__file__).resolve().parents[1]/'.github/workflows/feeds.yml').read_text()
        self.assertIn('group: collie-pages-production',workflow)
        self.assertIn('cancel-in-progress: false',workflow)
        self.assertIn('permissions:\n  contents: read',workflow)
        self.assertIn('      deployments: write\n      pages: write\n      id-token: write',workflow)
        self.assertIn('--preserve-beta',workflow)
        self.assertIn('--verify-live-site',workflow)
        self.assertIn('feed.py capture-deploy-baseline "$RUNNER_TEMP/deploy-baseline.json"',workflow)
        self.assertIn('--feed-baseline "$RUNNER_TEMP/deploy-baseline.json"',workflow)
        self.assertIn('release_inputs.py metadata --receipt "$RUNNER_TEMP/release-inputs.json"',workflow)
        self.assertIn('--native-plan "$RUNNER_TEMP/app-metadata/build-inputs/native-artifacts.json"',workflow)
        self.assertEqual(workflow.count('uses: actions/checkout@'),1)

    def test_beta_listing_must_be_from_exact_receipt_commit_and_artwork(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo=Path(tmp);metadata=repo/'fastlane/metadata/trailblazer-beta'
            (metadata/'en-US/images').mkdir(parents=True)
            (metadata/'en-US/images/icon.png').write_bytes(b'fixture icon')
            (metadata/'en-US/title.txt').write_text('Collie TrailBlazer\n')
            (metadata/'en-US/full_description.txt').write_text('Approved description\n')
            (metadata/'.gitignore').write_text('ignored.txt\n')
            subprocess.run(['git','init','-q',str(repo)],check=True)
            subprocess.run(['git','-C',str(repo),'config','user.name','Test'],check=True)
            subprocess.run(['git','-C',str(repo),'config','user.email','test@example.invalid'],check=True)
            subprocess.run(['git','-C',str(repo),'add','.'],check=True)
            subprocess.run(['git','-C',str(repo),'commit','-qm','fixture'],check=True)
            commit=subprocess.check_output(['git','-C',str(repo),'rev-parse','HEAD'],text=True).strip()
            with patch.object(feed,'sha256',return_value=feed.BETA_ICON_SHA256):
                self.assertEqual(feed.validate_beta_source_input(repo,metadata,commit,
                    required_source_commits=(commit,)),metadata.resolve())
                with self.assertRaisesRegex(ValueError,'immutable approved source commit'):
                    feed.validate_beta_source_input(repo,metadata,'0'*40,required_source_commits=(commit,))
                with self.assertRaisesRegex(ValueError,'omits required branding'):
                    feed.validate_beta_source_input(repo,metadata,commit,required_source_commits=('0'*40,))
                (metadata/'en-US/full_description.txt').write_text('Unreviewed description\n')
                (metadata/'extra.txt').write_text('Untracked\n')
                (metadata/'ignored.txt').write_text('Ignored\n')
                staged=feed.stage_beta_source_metadata(repo,commit,repo/'staged-metadata')
                self.assertEqual((staged/'en-US/full_description.txt').read_text(),'Approved description\n')
                self.assertFalse((staged/'extra.txt').exists())
                self.assertFalse((staged/'ignored.txt').exists())
                blob=subprocess.check_output(['git','-C',str(repo),'rev-parse',
                    commit+':fastlane/metadata/trailblazer-beta/en-US/full_description.txt'],text=True).strip()
                replacement=subprocess.check_output(['git','-C',str(repo),'hash-object','-w','--stdin'],
                    input=b'Replacement description\n').decode().strip()
                subprocess.run(['git','-C',str(repo),'replace',blob,replacement],check=True)
                replaced=feed.stage_beta_source_metadata(repo,commit,repo/'replacement-stage')
                self.assertEqual((replaced/'en-US/full_description.txt').read_text(),'Approved description\n')
                subprocess.run(['git','-C',str(repo),'replace','-d',blob],check=True,capture_output=True)
                (metadata/'link').symlink_to('en-US/title.txt')
                subprocess.run(['git','-C',str(repo),'add','fastlane'],check=True)
                subprocess.run(['git','-C',str(repo),'commit','-qm','symlink fixture'],check=True)
                linked=subprocess.check_output(['git','-C',str(repo),'rev-parse','HEAD'],text=True).strip()
                with self.assertRaisesRegex(ValueError,'regular Git blobs'):
                    feed.stage_beta_source_metadata(repo,linked,repo/'symlink-stage')
                (metadata/'link').unlink()
            (metadata/'en-US/title.txt').write_text('Collie\n')
            with patch.object(feed,'sha256',return_value=feed.BETA_ICON_SHA256), \
                    self.assertRaisesRegex(ValueError,'listing title'):
                feed.validate_beta_source_input(repo,metadata,linked,required_source_commits=(commit,))

    def test_first_feed_floor_uses_authoritative_published_release_history(self):
        release={'id':17,'tag_name':'trailblazer-v8.40.0','name':'Collie TrailBlazer Beta 8.40.0',
                 'draft':False,'prerelease':True,'assets':[{'id':18,'name':'trailblazer-beta.apk','state':'uploaded','size':8,
                 'digest':'sha256:'+'a'*64,'browser_download_url':'https://github.com/0cwa/collieim-android/releases/download/trailblazer-v8.40.0/trailblazer-beta.apk'}]}
        calls=[]
        def api(endpoint):
            calls.append(endpoint)
            return [release] if 'page=1' in endpoint else []
        def download(_url,path,_size):Path(path).write_bytes(b'test APK');return 'a'*64
        with tempfile.TemporaryDirectory() as tmp, patch.object(feed.binary_repo,'inspect',return_value={'package':channels.BETA.package,
                'version_name':'8.40.0','version_code':220,'apk_certificate_sha256':channels.BETA.apk_certificate}):
            self.assertEqual(feed.verified_public_beta_history('/tools',tmp,api=api,download=download),220)
            selected={'release_id':17,'tag':release['tag_name'],'version_name':'8.40.0',
                      'version_code':220,'apk_sha256':'a'*64}
            self.assertEqual(feed.verified_public_beta_history('/tools',tmp,api=api,download=download,
                selected_release=selected),0)
            other={**release,'id':19,'tag_name':'trailblazer-v8.40.1',
                'name':'Collie TrailBlazer Beta 8.40.1','assets':[{**release['assets'][0],'id':20,
                    'browser_download_url':release['assets'][0]['browser_download_url'].replace('8.40.0','8.40.1')}]}
            identities=[{'package':channels.BETA.package,'version_name':version,'version_code':220,
                'apk_certificate_sha256':channels.BETA.apk_certificate} for version in ('8.40.0','8.40.1')]
            with patch.object(feed.binary_repo,'inspect',side_effect=identities):
                self.assertEqual(feed.verified_public_beta_history('/tools',tmp,
                    api=lambda _endpoint:[release,other],download=download,selected_release=selected),220)
            with self.assertRaisesRegex(ValueError,'selected public Beta release differs'):
                feed.verified_public_beta_history('/tools',tmp,api=api,download=download,
                    selected_release={**selected,'apk_sha256':'b'*64})
        self.assertEqual(len(calls),3)

    def test_first_feed_history_rejects_wrong_apk_package_or_signer(self):
        release={'id':17,'tag_name':'trailblazer-v8.40.0','name':'Collie TrailBlazer Beta 8.40.0',
                 'draft':False,'prerelease':True,'assets':[{'id':18,'name':'trailblazer-beta.apk','state':'uploaded','size':3,
                 'digest':'sha256:'+'a'*64,'browser_download_url':'https://github.com/0cwa/collieim-android/releases/download/x/trailblazer-beta.apk'}]}
        release['assets'][0]['browser_download_url']='https://github.com/0cwa/collieim-android/releases/download/trailblazer-v8.40.0/trailblazer-beta.apk'
        def download(_url,path,_size):Path(path).write_bytes(b'bad');return 'a'*64
        with tempfile.TemporaryDirectory() as tmp, patch.object(feed.binary_repo,'inspect',return_value={'package':'im.collie.app',
                'version_name':'8.40.0','version_code':220,'apk_certificate_sha256':channels.STABLE.apk_certificate}), \
                self.assertRaisesRegex(ValueError,'package or signing certificate'):
            feed.verified_public_beta_history('/tools',tmp,api=lambda _endpoint:[release] if 'page=1' in _endpoint else [],download=download)


if __name__=='__main__':unittest.main()
