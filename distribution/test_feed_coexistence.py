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

    def test_inventory_cannot_authorize_tampered_beta_bytes(self):
        blobs=dict(self.blobs);blobs['fdroid/beta/repo/entry.jar']=b'changed'
        with tempfile.TemporaryDirectory() as tmp, self.assertRaisesRegex(ValueError,'differ from inventory'):
            feed.capture_live_site(Path(tmp)/'capture',fetch=self.fetch_for(blobs=blobs))

    def test_valid_signature_with_canonical_address_is_rejected_on_legacy_beta_route(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(feed.binary_repo,'verify_indexes',return_value={'repo':{'address':channels.REPOSITORY_URL}}), \
                self.assertRaisesRegex(ValueError,'address differs from fixed route'):
            feed.capture_live_site(Path(tmp)/'capture',fetch=self.fetch_for())

    def test_compare_and_swap_rejects_changes_after_capture(self):
        expected = hashlib.sha256(self.inventory).hexdigest()
        feed.verify_inventory_is_current(expected, fetch=self.fetch_for())
        for fetch in (self.fetch_for(inventory=served_inventory({})),
                      self.fetch_for(statuses={'site-manifest.json': 404})):
            with self.assertRaisesRegex(ValueError, 'changed after capture'):
                feed.verify_inventory_is_current(expected, fetch=fetch)

    @staticmethod
    def signed_repo(_path, _cert, profile, **_kwargs):
        address = channels.LEGACY_BETA_URL if profile == channels.BETA else profile.repository_url
        return {'repo': {'address': address}}

    def test_complete_live_capture_keeps_stable_beta_and_native_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            destination=Path(tmp)/'site'
            with patch.object(feed.binary_repo,'verify_indexes',side_effect=self.signed_repo) as verify:
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
            parser=patch.object(feed.binary_repo,'verify_indexes',side_effect=self.signed_repo)
            with parser:
                feed.capture_deploy_baseline(baseline,fetch=self.fetch_for())
                self.assertTrue(feed.verify_deploy_baseline(baseline,fetch=self.fetch_for()))
            newer={**self.blobs,'fdroid/beta/repo/entry.jar':b'late older-deployment completion'}
            inventory=served_inventory({name:{'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data)}
                                        for name,data in newer.items()})
            with patch.object(feed.binary_repo,'verify_indexes',side_effect=self.signed_repo), \
                    self.assertRaisesRegex(ValueError,'changed after Stable candidate capture'):
                feed.verify_deploy_baseline(baseline,fetch=self.fetch_for(blobs=newer,inventory=inventory))
            with self.assertRaisesRegex(OSError,'temporary origin failure'):
                def unavailable(_path, *, max_bytes):raise OSError('temporary origin failure')
                feed.verify_deploy_baseline(baseline,fetch=unavailable)

    def test_live_inventory_rejects_wrong_stable_repository_address_too(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(feed.binary_repo,'verify_indexes',side_effect=lambda _path,_cert,profile:{
                    'repo':{'address':channels.LEGACY_BETA_URL}}), \
                self.assertRaisesRegex(ValueError,'served stable index address'):
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
            with patch.object(feed.binary_repo,'verify_indexes',side_effect=self.signed_repo):
                feed.verify_live_site(candidate,fetch=fetch)
            changed={**live_blobs,'fdroid/beta/repo/entry.jar':b'stale candidate overwritten'}
            changed_manifest=served_inventory({name:{'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data)}
                                               for name,data in changed.items()})
            with patch.object(feed.binary_repo,'verify_indexes',side_effect=self.signed_repo), \
                    self.assertRaisesRegex(ValueError,'inventory differs'):
                feed.verify_live_site(candidate,fetch=self.fetch_for(blobs=changed,inventory=changed_manifest))

    def test_scheduled_stable_workflow_uses_shared_noncanceling_deploy_lock(self):
        workflow=(Path(__file__).resolve().parents[1]/'.github/workflows/feeds.yml').read_text()
        self.assertIn('group: collie-pages-production',workflow)
        self.assertIn('cancel-in-progress: false',workflow)
        self.assertIn('permissions:\n  contents: read',workflow)
        self.assertIn('      deployments: write\n      pages: write\n      id-token: write',workflow)
        self.assertIn('publish_unified=true',workflow)
        self.assertIn('--verify-live-site',workflow)
        self.assertIn('feed.py capture-deploy-baseline "$RUNNER_TEMP/deploy-baseline.json"',workflow)
        self.assertIn('--feed-baseline "$RUNNER_TEMP/deploy-baseline.json"',workflow)
        self.assertIn('release_inputs.py metadata --receipt "$RUNNER_TEMP/release-inputs.json"',workflow)
        self.assertIn('--native-plan "$RUNNER_TEMP/app-metadata/build-inputs/native-artifacts.json"',workflow)
        # The deploy job and the secret-free Beta check each check out this repository, never the app.
        self.assertEqual(workflow.count('uses: actions/checkout@'),2)
        self.assertNotIn('repository:',workflow)


if __name__=='__main__':unittest.main()
