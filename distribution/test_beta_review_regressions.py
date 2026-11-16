"""Exercise the real parsing and promotion seams identified in security review."""
import json
import hashlib
import io
import contextlib
import runpy
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
import zipfile
from unittest.mock import patch

import binary_repo as b
import channel_profile as channels
import feed
import pages
import test_pages as fixtures


class BetaReviewRegressions(unittest.TestCase):
    def test_beta_direct_publication_is_rejected_before_any_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            origin=Path(tmp)/'origin'
            with patch.object(b, 'approved_generation') as verify:
                with self.assertRaisesRegex(ValueError, 'Pages'):
                    b.publish_local(Path(tmp)/'generation', origin, 'a'*64,
                                    'b'*64, [], channels.BETA)
            verify.assert_not_called()
            self.assertFalse(origin.exists())

    def test_valid_apk_metadata_parses_version_name_for_both_channels(self):
        for profile in (channels.STABLE, channels.BETA):
            with self.subTest(channel=profile.name), patch.object(feed.subprocess, 'run', side_effect=[
                SimpleNamespace(stdout=f"package: name='{profile.package}' versionCode='177801' versionName='8.35.2-1-beta1'"),
                SimpleNamespace(stdout='Signer #1 certificate SHA-256 digest: '+profile.apk_certificate),
            ]):
                self.assertEqual(feed.apk_identity(Path('input.apk'), Path('/tools'), profile),
                                 (177801, '8.35.2-1-beta1'))

    def verify_beta_index(self, release_channels=None, signer=None):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); name=channels.BETA.package+'_1.apk'
            (root/name).write_bytes(b'fixture APK')
            package={'versions': {'fixture': {
                'manifest': {'versionCode': 1, 'versionName': '1.0',
                             'signer': {'sha256': [signer or channels.BETA.apk_certificate]}},
                'file': {'name': '/'+name, 'sha256': b.sha(root/name)}}}}
            if release_channels is not None: package['releaseChannels']=release_channels
            data={'packages': {channels.BETA.package: package}}
            (root/'index-v2.json').write_text(json.dumps(data))
            legacy={'apps': [{'packageName': channels.BETA.package}],
                    'packages': {channels.BETA.package: [{
                        'versionCode': 1, 'versionName': '1.0',
                        'hash': b.sha(root/name), 'hashType': 'sha256',
                        'apkName': name, 'signer': channels.BETA.apk_certificate}]}}
            entry={'index': {'name': '/index-v2.json', 'sha256': b.sha(root/'index-v2.json')}}
            fake=SimpleNamespace(index=SimpleNamespace(get_index_from_jar=unittest.mock.Mock(side_effect=[
                (entry, None, None), (legacy, None, None)])), common=SimpleNamespace())
            with patch.dict(sys.modules, {'fdroidserver': fake}):
                return b.verify_indexes(root, channels.BETA.repository_certificate, channels.BETA)

    def test_beta_signed_indexes_reach_channel_specific_parity(self):
        for release_channels in (None, []):
            self.assertIn(channels.BETA.package, self.verify_beta_index(release_channels)['packages'])

    def test_promotion_rejects_beta_release_channels_even_without_builder(self):
        with self.assertRaisesRegex(ValueError, 'releaseChannels'):
            self.verify_beta_index(['beta'])
    def test_beta_index_rejects_wrong_apk_signer_even_when_repo_index_is_signed(self):
        with self.assertRaisesRegex(ValueError,'indexed Beta APK signer'):
            self.verify_beta_index(signer=channels.STABLE.apk_certificate)

    def test_beta_diff_and_versioned_icons_are_immutable(self):
        for prefix in ('fdroid/repo/', 'fdroid/beta/repo/'):
            self.assertTrue(pages.immutable(prefix+'diff/123.json'))
            self.assertTrue(pages.immutable(prefix+'icons-640/im.collie.app.123.png'))
            self.assertFalse(pages.immutable(prefix+'icons/icon.png'))


class BetaSourceBindingRegression(unittest.TestCase):
    setUp=fixtures.PagesTest.setUp
    write=fixtures.PagesTest.write

    def test_schema_two_release_uses_outer_pages_source_identity(self):
        profile=channels.BETA
        self.approval.update(schema=2,channel='trailblazer-beta', source_branch=profile.source_branch,
            source_commit='d'*40, readiness_commit='d'*40,
            readiness_workflow=profile.readiness_workflow, readiness_run_id=123,
            readiness_attempt=1, readiness_artifact_id=456, readiness_artifact_sha256='e'*64,
            repository_certificate_sha256=profile.repository_certificate)
        b.landing(self.bundle/'landing', profile.repository_url, profile.repository_certificate,
                  profile.apk_certificate, 'https://github.com/0cwa/collieim-android/releases/download/trailblazer-v1.0/trailblazer-beta.apk')
        self.approval['landing_sha256']=pages.digest(pages.inventory(self.bundle/'landing'))
        self.write()
        release={'schema': 2, 'release_id': 1, 'tag': 'trailblazer-v1.0',
                 'version_name': '1.0', 'version_code': 1, 'package': profile.package}
        class ReachedGeneration(Exception): pass
        for source in ('d'*40, 'f'*40):
            receipt={'payload': {'source_commit': source}}
            with patch.object(pages, 'validate_beta_provenance',return_value=None), \
                 patch.object(b, 'approval', return_value=release), \
                 patch.object(b, 'verify_release_approval', return_value=receipt), \
                 patch.object(b, 'approved_generation', side_effect=ReachedGeneration):
                if source==self.approval['source_commit']:
                    with self.assertRaises(ReachedGeneration):
                        pages.prepare(self.bundle, self.output, 'c'*40)
                else:
                    with self.assertRaisesRegex(ValueError, 'source commit differs'):
                        pages.prepare(self.bundle, self.output, 'c'*40)


def readiness_archive(manifest):
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w',zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(pages.READINESS_MANIFEST,json.dumps(manifest,separators=(',',':')))
    return stream.getvalue()


class BetaReceiptGraphPrepareTest(unittest.TestCase):
    """Synthetic end-to-end candidate; only GitHub/Android and F-Droid crypto are seams."""
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.bundle=self.root/'bundle';self.output=self.root/'prepared'
        for name in ('candidate','previous','landing','releases','bundles','native'):(self.bundle/name).mkdir(parents=True)
        self.profile=channels.BETA;self.commit='d'*40;self.apk_bytes=b'synthetic APK payload for digest/parser fixtures'
        self.apk_sha=hashlib.sha256(self.apk_bytes).hexdigest();self.version='8.36.0';self.code=200
        self.release_id=887
        self.build_release_graph()
        self.write_candidate()
        self.make_approval_and_readiness()

    def passed(self,kind,payload):
        return {'schema':2,'kind':kind,'payload':payload,
                'evidence':[{'status':'PASS','description':'synthetic integration fixture only','sha256':'f'*64}]}

    def build_release_graph(self):
        source=self.passed('source',dict(upstream_commit=self.commit,engineering_revision='a'*40,
            generator_revision='b'*40,verifier_revision='c'*40,layers=[],reconstructed_tree='e'*40,
            overlays=[],published_commit=self.commit,published_tree='e'*40))
        source_hash=pages.receipts.digest(source,profile=self.profile.name)
        build=self.passed('build',dict(source_receipt_sha256=source_hash,source_commit=self.commit,
            source_tree='e'*40,recipe_revision='b'*40,toolchain_sha256='1'*64,
            dependencies_sha256='2'*64,native_inputs_sha256='3'*64,unsigned_apk_sha256='4'*64,
            trust_level='independent synthetic fixture'))
        build_hash=pages.receipts.digest(build,profile=self.profile.name)
        self.manifest_bytes=b'approved release manifest bytes'
        self.manifest_sha=hashlib.sha256(self.manifest_bytes).hexdigest()
        release=self.passed('release',dict(release_manifest_sha256=self.manifest_sha,
            source_receipt_sha256=source_hash,build_receipt_sha256=build_hash,source_commit=self.commit,
            engineering_revision='a'*40,workflow_revision='c'*40,package=self.profile.package,
            version_code=self.code,version_name=self.version,apk_sha256=self.apk_sha,
            apk_certificate_sha256=self.profile.apk_certificate,aliases=[self.profile.apk_asset]))
        self.release_hash=pages.receipts.digest(release,profile=self.profile.name)
        self.receipts={'source':source,'build':build,'release':release}
        self.receipt_approvals={'source':source_hash,'build':build_hash,'release':self.release_hash}

    def write_candidate(self):
        repo=self.bundle/'candidate';apk_name=f'{self.profile.package}_{self.code}.apk'
        (repo/apk_name).write_bytes(self.apk_bytes)
        (repo/'index-v1.jar').write_bytes(b'synthetic v1 signed container')
        (repo/'index-v2.json').write_text('{}')
        (repo/'entry.jar').write_bytes(b'synthetic entry signed container')
        data={'repo':{'address':self.profile.repository_url.rstrip('/')},'packages':{self.profile.package:{
            'versions':{self.apk_sha:{'manifest':{'versionCode':self.code,'versionName':self.version,
                'signer':{'sha256':[self.profile.apk_certificate]}},'file':{'name':'/'+apk_name,'sha256':self.apk_sha}}}}}}
        (repo/'index-v2.json').write_text(json.dumps(data))
        self.index_data=data
        self.legacy={'apps':[{'packageName':self.profile.package}], 'packages':{self.profile.package:[{
            'versionCode':self.code,'versionName':self.version,'hash':self.apk_sha,'hashType':'sha256',
            'apkName':apk_name,'signer':self.profile.apk_certificate}]}}

    def make_approval_and_readiness(self):
        repo=self.bundle/'candidate';generation=b.generation_digest(repo,self.profile)
        release_approval={'schema':2,'release_id':self.release_id,'tag':self.profile.tag_prefix+self.version,
            'release_manifest_sha256':self.manifest_sha,'apk_sha256':self.apk_sha,
            'apk_certificate_sha256':self.profile.apk_certificate,'package':self.profile.package,
            'version_code':self.code,'version_name':self.version,'aliases':{self.profile.apk_asset:self.apk_sha},
            'release_receipt_sha256':self.release_hash,'receipt_approvals':self.receipt_approvals}
        (self.bundle/'releases/release.json').write_text(json.dumps(release_approval))
        release_bundle=self.bundle/'bundles'/str(self.release_id);release_bundle.mkdir()
        for kind,document in self.receipts.items():(release_bundle/(kind+'.json')).write_text(json.dumps(document))
        client_evidence=self.root/'client-acceptance.json'
        client_evidence.write_text('{"result":"isolated client add-refresh-install-upgrade passed"}\n')
        self.repo_receipt=self.produce_repository_receipt(client_evidence)
        self.repo_hash=pages.receipts.digest(self.repo_receipt,profile=self.profile.name)
        (self.bundle/'repository-receipt.json').write_text(json.dumps(self.repo_receipt))
        b.landing(self.bundle/'landing',self.profile.repository_url,self.profile.repository_certificate,
            self.profile.apk_certificate,'https://github.com/0cwa/collieim-android/releases/download/'+
            release_approval['tag']+'/'+self.profile.apk_asset)
        manifest={'schema':1,'kind':'collie-trailblazer-readiness','repository':'0cwa/collieim-android',
            'branch':self.profile.source_branch,'workflow_path':self.profile.readiness_workflow,'run_id':123,
            'run_attempt':1,'head_sha':self.commit,'package':self.profile.package,'version_name':self.version,
            'version_code':self.code,'tag':release_approval['tag'],'title':self.profile.app_title+' '+self.version,
            'apk_asset':self.profile.apk_asset,'source_receipt_sha256':self.receipt_approvals['source'],
            'build_receipt_sha256':self.receipt_approvals['build'],'release_receipt_sha256':self.release_hash,
            'repository_receipt_sha256':self.repo_hash,'release_manifest_sha256':self.manifest_sha,
            'apk_sha256':self.apk_sha,'apk_certificate_sha256':self.profile.apk_certificate}
        self.artifact=readiness_archive(manifest);self.artifact_hash=hashlib.sha256(self.artifact).hexdigest()
        (self.bundle/'releases/manifest.json').write_bytes(self.manifest_bytes)
        self.approval={'schema':2,'kind':'collie-pages-input','channel':self.profile.name,'origin':pages.ORIGIN,
            'previous_site_sha256':pages.digest({}),'previous_manifest_sha256':None,
            'candidate_generation_sha256':generation,'repository_certificate_sha256':self.profile.repository_certificate,
            'repository_receipt_sha256':self.repo_hash,'release_approvals':['release.json'],
            'landing_sha256':pages.digest(pages.inventory(self.bundle/'landing')),'maximum_bytes':pages.MAX_BYTES,
            'policy_revision':'c'*40,'source_branch':self.profile.source_branch,'source_commit':self.commit,
            'readiness_workflow':self.profile.readiness_workflow,'readiness_run_id':123,
            'readiness_attempt':1,'readiness_artifact_id':456,'readiness_artifact_sha256':self.artifact_hash,
            'readiness_commit':self.commit}
        (self.bundle/'approval.json').write_text(json.dumps(self.approval))
        self.run={'id':123,'run_attempt':1,'head_repository':{'full_name':'0cwa/collieim-android'},
            'head_branch':self.profile.source_branch,'head_sha':self.commit,'path':self.profile.readiness_workflow,
            'status':'completed','conclusion':'success','event':'workflow_dispatch'}
        self.artifact_record={'id':456,'name':'trailblazer-beta-readiness-123-1','expired':False,
            'workflow_run':{'id':123},'digest':'sha256:'+self.artifact_hash,'size_in_bytes':len(self.artifact)}

    def produce_repository_receipt(self,client_evidence=None):
        repo=self.bundle/'candidate';approval=self.bundle/'releases/release.json'
        generation=b.generation_digest(repo,self.profile)
        args=['receipt.py',str(repo),'--approval',str(approval),'--receipt-bundle',
            str(self.bundle/'bundles'/str(self.release_id)),'--metadata-revision','a'*40,
            '--fingerprint',self.profile.repository_certificate,'--generation',generation,
            '--channel',self.profile.name,'--repository-stage','prepublication']
        if client_evidence is not None:args.extend(['--client-evidence',str(client_evidence)])
        parser=SimpleNamespace(index=SimpleNamespace(get_index_from_jar=unittest.mock.Mock(side_effect=[
            ({'index':{'name':'/index-v2.json','sha256':b.sha(repo/'index-v2.json')}},None,None),
            (self.legacy,None,None)])),common=SimpleNamespace())
        output=io.StringIO()
        with patch.dict(sys.modules,{'fdroidserver':parser}),patch.object(sys,'argv',args),contextlib.redirect_stdout(output):
            runpy.run_path(str(Path(__file__).with_name('receipt.py')),run_name='__main__')
        return json.loads(output.getvalue())

    def rebind_repository_receipt(self,receipt):
        self.repo_receipt=receipt;self.repo_hash=pages.receipts.digest(receipt,profile=self.profile.name)
        (self.bundle/'repository-receipt.json').write_text(json.dumps(receipt))
        manifest=json.loads(zipfile.ZipFile(io.BytesIO(self.artifact)).read(pages.READINESS_MANIFEST))
        manifest['repository_receipt_sha256']=self.repo_hash
        self.artifact=readiness_archive(manifest);self.artifact_hash=hashlib.sha256(self.artifact).hexdigest()
        self.artifact_record.update(digest='sha256:'+self.artifact_hash,size_in_bytes=len(self.artifact))
        self.approval.update(repository_receipt_sha256=self.repo_hash,readiness_artifact_sha256=self.artifact_hash)
        (self.bundle/'approval.json').write_text(json.dumps(self.approval))

    def authoritative(self,endpoint):
        if '/actions/runs/' in endpoint:return self.run
        if '/actions/artifacts/' in endpoint:return self.artifact_record
        raise AssertionError(endpoint)

    def test_beta_ingest_uses_real_selected_channel_receipt_graph(self):
        apk=self.bundle/'candidate'/f'{self.profile.package}_{self.code}.apk'
        info=dict(package=self.profile.package,version_code=self.code,
                  version_name=self.version,apk_certificate_sha256=self.profile.apk_certificate)
        with patch.object(b, 'inspect', return_value=info):
            target=b.ingest(self.bundle/'releases/release.json',
                self.bundle/'releases/manifest.json',apk,self.root/'ingested',
                'aapt','apksigner',self.bundle/'bundles'/str(self.release_id),self.profile)
        self.assertEqual(target.read_bytes(),self.apk_bytes)

    def prepare(self):
        fake=SimpleNamespace(index=SimpleNamespace(get_index_from_jar=unittest.mock.Mock(side_effect=[
            ({'index':{'name':'/index-v2.json','sha256':b.sha(self.bundle/'candidate/index-v2.json')}},None,None),
            (self.legacy,None,None)])),common=SimpleNamespace())
        with patch.dict(sys.modules,{'fdroidserver':fake}), \
             patch.object(pages,'github_read',side_effect=self.authoritative), \
             patch.object(pages,'github_artifact_bytes',return_value=self.artifact):
            return pages.prepare(self.bundle,self.output,'c'*40)

    def test_beta_pages_prepare_verifies_full_graph_and_real_index_parsers(self):
        stable=self.bundle/'previous/fdroid/repo';stable.mkdir(parents=True)
        for name,content in {'entry.jar':b'stable entry','index-v1.jar':b'stable v1',
                             'index-v2.json':b'stable v2','im.collie.app_90.apk':b'stable APK'}.items():
            (stable/name).write_bytes(content)
        (self.bundle/'previous/site-manifest.json').write_text(json.dumps({'schema':1,'kind':'collie-pages-public-inventory',
            'origin':pages.ORIGIN,'files':pages.inventory(self.bundle/'previous')}))
        self.approval['previous_site_sha256']=pages.digest(pages.inventory(self.bundle/'previous'))
        self.approval['previous_manifest_sha256']=b.sha(self.bundle/'previous/site-manifest.json')
        (self.bundle/'approval.json').write_text(json.dumps(self.approval))
        plan=self.prepare()
        self.assertEqual(plan['channel'],self.profile.name)
        self.assertTrue((self.output/'phase2/site/fdroid/beta/repo'/f'{self.profile.package}_{self.code}.apk').is_file())
        for phase in ('phase1','phase2'):
            self.assertEqual((self.output/phase/'site/fdroid/repo/entry.jar').read_bytes(),b'stable entry')
            self.assertEqual((self.output/phase/'site/fdroid/repo/im.collie.app_90.apk').read_bytes(),b'stable APK')

    def test_actual_beta_receipt_uses_prepublication_boundary_and_journal_gates_readback(self):
        evidence={item['description']:item for item in self.repo_receipt['evidence']}
        self.assertEqual(evidence[pages.receipts.PREPUBLICATION_REPOSITORY_EVIDENCE['candidate_index']]['status'],'PASS')
        self.assertEqual(evidence[pages.receipts.PREPUBLICATION_REPOSITORY_EVIDENCE['client_acceptance']]['status'],'PASS')
        production=evidence[pages.receipts.PREPUBLICATION_REPOSITORY_EVIDENCE['production_readback']]
        self.assertEqual((production['status'],production['sha256']),('NOT RUN',None))
        repo_digest=pages.receipts.digest(self.repo_receipt,profile=self.profile.name)
        self.assertEqual(self.approval['repository_receipt_sha256'],repo_digest)
        receipt_dir=self.bundle/'bundles'/str(self.release_id)
        graph={kind:pages.receipts.load(receipt_dir/(kind+'.json'),kind,profile=self.profile.name)
               for kind in ('source','build','release')}
        approved={**self.receipt_approvals,'repository':repo_digest}
        with self.assertRaisesRegex(ValueError,'requires explicit prepublication repository evidence stage'):
            pages.receipts.verify_graph(**graph,repository=self.repo_receipt,approved=approved,
                                        promotion=True,profile=self.profile.name)
        pages.receipts.verify_graph(**graph,repository=self.repo_receipt,approved=approved,
            promotion=True,profile=self.profile.name,repository_stage='prepublication')
        plan=self.prepare();journal=self.output/'journal.json'
        with self.assertRaisesRegex(ValueError,'live previous preflight'):
            pages.verify_completed_readbacks(plan,b.load(journal))
        pages.preflight(plan,journal,head=lambda _url:{'status':404,'headers':{'Age':'0','X-Cache':'MISS'}},
                        expected_channel=self.profile.name)
        with self.assertRaisesRegex(ValueError,'both canonical readbacks'):
            pages.verify_completed_readbacks(plan,b.load(journal))
        with self.assertRaisesRegex(ValueError,'phase1 readback required'):
            pages.readback(plan,journal,'phase2',fetch=lambda _url:None,expected_channel=self.profile.name)
        with self.assertRaisesRegex(ValueError,'both canonical readbacks'):
            pages.verify_completed_readbacks(plan,b.load(journal))
        for phase in ('phase1','phase2'):
            def exact(url):
                name=url.removeprefix(plan['origin']);entry=plan['manifests'][phase][name]
                return {'status':200,'sha256':entry['sha256'],'bytes':entry['bytes']}
            pages.readback(plan,journal,phase,fetch=exact,expected_channel=self.profile.name)
        self.assertTrue(pages.verify_completed_readbacks(plan,b.load(journal)))
        current=pages.receipts.load(self.bundle/'repository-receipt.json','repository',profile=self.profile.name)
        current_evidence={item['description']:item for item in current['evidence']}
        self.assertEqual((current_evidence[pages.receipts.PREPUBLICATION_REPOSITORY_EVIDENCE['production_readback']]['status'],
                          current_evidence[pages.receipts.PREPUBLICATION_REPOSITORY_EVIDENCE['production_readback']]['sha256']),
                         ('NOT RUN',None))

    def test_beta_pages_does_not_skip_unrun_client_acceptance(self):
        self.rebind_repository_receipt(self.produce_repository_receipt())
        with self.assertRaisesRegex(ValueError,'completed isolated client acceptance required'):
            self.prepare()

    def test_repository_receipt_cli_requires_and_uses_beta_profile(self):
        repo=self.bundle/'candidate';generation=b.generation_digest(repo,self.profile)
        approval=self.bundle/'releases/release.json'
        args=['receipt.py',str(repo),'--approval',str(approval),'--receipt-bundle',
            str(self.bundle/'bundles'/str(self.release_id)),'--metadata-revision','a'*40,
            '--fingerprint',self.profile.repository_certificate,'--generation',generation,
            '--channel',self.profile.name,'--repository-stage','prepublication',
            '--client-evidence',str(self.root/'client-acceptance.json')]
        parser=unittest.mock.Mock(side_effect=[
            ({'index':{'name':'/index-v2.json','sha256':b.sha(repo/'index-v2.json')}},None,None),
            (self.legacy,None,None)])
        fake=SimpleNamespace(index=SimpleNamespace(get_index_from_jar=parser),common=SimpleNamespace())
        output=io.StringIO()
        with patch.dict(sys.modules,{'fdroidserver':fake}),patch.object(sys,'argv',args),contextlib.redirect_stdout(output):
            runpy.run_path(str(Path(__file__).with_name('receipt.py')),run_name='__main__')
        receipt=json.loads(output.getvalue())
        self.assertEqual(receipt['payload']['apk_certificate_sha256'],self.profile.apk_certificate)
        evidence={item['description']:item for item in receipt['evidence']}
        self.assertEqual(evidence[pages.receipts.PREPUBLICATION_REPOSITORY_EVIDENCE['production_readback']]['status'],'NOT RUN')
        unstaged=args.copy();stage_pos=unstaged.index('--repository-stage');del unstaged[stage_pos:stage_pos+2]
        with patch.object(sys,'argv',unstaged):
            with self.assertRaisesRegex(ValueError,'requires --repository-stage prepublication'):
                runpy.run_path(str(Path(__file__).with_name('receipt.py')),run_name='__main__')
        beta_channel=args.index('--channel');stage=args.index('--repository-stage')
        stable_args=args[:beta_channel+1]+['stable']+args[beta_channel+2:stage]+args[stage+2:]
        with patch.object(sys,'argv',stable_args):
            with self.assertRaisesRegex(ValueError,'stable APK signing certificate differs'):
                runpy.run_path(str(Path(__file__).with_name('receipt.py')),run_name='__main__')

    def test_readiness_artifact_missing_or_mismatched_bytes_rejected(self):
        with patch.object(pages,'github_read',side_effect=self.authoritative):
            for contents in (b'',self.artifact+b'changed'):
                with patch.object(pages,'github_artifact_bytes',return_value=contents), self.subTest(size=len(contents)), \
                     self.assertRaises(ValueError):
                    pages.validate_beta_provenance(self.approval,download=lambda _id:contents)
        missing=io.BytesIO()
        with zipfile.ZipFile(missing,'w') as archive:archive.writestr('unexpected.json','{}')
        missing=missing.getvalue();approval=dict(self.approval,readiness_artifact_sha256=hashlib.sha256(missing).hexdigest())
        record=dict(self.artifact_record,digest=approval['readiness_artifact_sha256'],size_in_bytes=len(missing))
        with patch.object(pages,'github_read',side_effect=lambda ep:self.run if '/actions/runs/' in ep else record):
            with self.assertRaisesRegex(ValueError,'fixed manifest'):
                pages.validate_beta_provenance(approval,download=lambda _id:missing)

    def test_readiness_manifest_hash_or_release_mismatch_rejected(self):
        manifest=json.loads(zipfile.ZipFile(io.BytesIO(self.artifact)).read(pages.READINESS_MANIFEST))
        manifest['apk_sha256']='0'*64
        altered=readiness_archive(manifest)
        approval=dict(self.approval,readiness_artifact_sha256=hashlib.sha256(altered).hexdigest())
        artifact=dict(self.artifact_record,digest=approval['readiness_artifact_sha256'],size_in_bytes=len(altered))
        with patch.object(pages,'github_read',side_effect=lambda ep:self.run if '/actions/runs/' in ep else artifact):
            parsed=pages.validate_beta_provenance(approval,download=lambda _id:altered)
        self.assertNotEqual(parsed['apk_sha256'],self.apk_sha)
        self.approval['readiness_artifact_sha256']=approval['readiness_artifact_sha256']
        self.artifact_record=artifact;self.artifact=altered
        (self.bundle/'approval.json').write_text(json.dumps(self.approval))
        with self.assertRaisesRegex(ValueError,'readiness manifest release/receipt identity'):
            self.prepare()

    def test_beta_receipt_profile_rejects_stable_identity_and_signer(self):
        source=self.receipts['source'];build=self.receipts['build'];release=self.receipts['release']
        with self.assertRaisesRegex(ValueError,'wrong package for selected channel'):
            pages.receipts.validate(dict(release,payload=dict(release['payload'],package='im.collie.app')),
                                    'release',profile=self.profile.name)
        with self.assertRaisesRegex(ValueError,'signer differs'):
            bad=dict(release,payload=dict(release['payload'],apk_certificate_sha256=channels.STABLE.apk_certificate))
            pages.receipts.validate(bad,'release',profile=self.profile.name)
        with self.assertRaisesRegex(ValueError,'wrong aliases'):
            bad=dict(release,payload=dict(release['payload'],aliases=['collie.apk']))
            pages.receipts.validate(bad,'release',profile=self.profile.name)
        with self.assertRaisesRegex(ValueError,'Beta APK signer'):
            bad=dict(release,payload=dict(release['payload'],package=channels.STABLE.package,
                aliases=pages.receipts.PROFILES['stable']['aliases'],apk_certificate_sha256=channels.BETA.apk_certificate))
            pages.receipts.validate(bad,'release',profile='stable')

    def test_beta_candidate_rollback_rejected_against_verified_previous_index(self):
        # The previous index is parsed and its signature is verified by the same verifier seam.
        old=self.bundle/'previous/fdroid/beta/repo';old.mkdir(parents=True)
        for name in ('entry.jar','index-v1.jar','index-v2.json'):(old/name).write_bytes((name+' prior').encode())
        prior_apk=old/f'{self.profile.package}_199.apk';prior_apk.write_bytes(b'prior APK')
        previous_data={'repo':{'address':self.profile.repository_url.rstrip('/')},'packages':{self.profile.package:{'versions':{
            'old':{'manifest':{'versionCode':201,'versionName':'8.35.9','signer':{'sha256':[self.profile.apk_certificate]}},
                   'file':{'name':'/'+prior_apk.name,'sha256':b.sha(prior_apk)}}}}}}
        previous_legacy={'apps':[{'packageName':self.profile.package}], 'packages':{self.profile.package:[{
            'versionCode':201,'versionName':'8.35.9','hash':b.sha(prior_apk),'hashType':'sha256',
            'apkName':prior_apk.name,'signer':self.profile.apk_certificate}]}}
        entry={'index':{'name':'/index-v2.json','sha256':b.sha(old/'index-v2.json')}}
        candidate_entry={'index':{'name':'/index-v2.json','sha256':b.sha(self.bundle/'candidate/index-v2.json')}}
        (self.bundle/'previous/site-manifest.json').write_text(json.dumps({'schema':1,'kind':'collie-pages-public-inventory',
            'origin':pages.ORIGIN,'files':pages.inventory(self.bundle/'previous')}))
        self.approval['previous_site_sha256']=pages.digest(pages.inventory(self.bundle/'previous'))
        self.approval['previous_manifest_sha256']=b.sha(self.bundle/'previous/site-manifest.json')
        (self.bundle/'approval.json').write_text(json.dumps(self.approval))
        parser=unittest.mock.Mock(side_effect=[(candidate_entry,None,None),(self.legacy,None,None),
                                               (entry,None,None),(previous_legacy,None,None)])
        fake=SimpleNamespace(index=SimpleNamespace(get_index_from_jar=parser),common=SimpleNamespace())
        original_load=b.load
        def load_previous(path):
            path=Path(path)
            if path==old/'index-v2.json':return previous_data
            return original_load(path)
        with patch.dict(sys.modules,{'fdroidserver':fake}), \
             patch.object(pages,'github_read',side_effect=self.authoritative), \
             patch.object(pages,'github_artifact_bytes',return_value=self.artifact), \
             patch.object(b,'load',side_effect=load_previous):
            with self.assertRaisesRegex(ValueError,'must exceed verified previous signed index'):
                pages.prepare(self.bundle,self.root/'rollback','c'*40)

    def test_partial_previous_beta_route_cannot_skip_version_check(self):
        old=self.bundle/'previous/fdroid/beta/repo';old.mkdir(parents=True)
        (old/f'{self.profile.package}_999.apk').write_bytes(b'previous APK without signed index')
        (self.bundle/'previous/site-manifest.json').write_text(json.dumps({'schema':1,'kind':'collie-pages-public-inventory',
            'origin':pages.ORIGIN,'files':pages.inventory(self.bundle/'previous')}))
        self.approval['previous_site_sha256']=pages.digest(pages.inventory(self.bundle/'previous'))
        self.approval['previous_manifest_sha256']=b.sha(self.bundle/'previous/site-manifest.json')
        (self.bundle/'approval.json').write_text(json.dumps(self.approval))
        with patch.object(pages,'github_read',side_effect=self.authoritative), \
             patch.object(pages,'github_artifact_bytes',return_value=self.artifact):
            with self.assertRaisesRegex(ValueError,'previous Beta feed lacks its signed entry'):
                pages.prepare(self.bundle,self.root/'partial-previous','c'*40)


class StableReceiptCompatibility(unittest.TestCase):
    def test_default_cli_keeps_stable_receipt_evidence_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);repo=root/'repo';repo.mkdir()
            for name in ('entry.jar','index-v1.jar','index-v2.json'):(repo/name).write_bytes(name.encode())
            apk_sha='a'*64
            approval={'schema':1,'release_id':1,'tag':'v8.35.2','release_manifest_sha256':'b'*64,
                'apk_sha256':apk_sha,'apk_certificate_sha256':channels.STABLE.apk_certificate,
                'package':channels.STABLE.package,'version_code':177800,'version_name':'8.35.2',
                'aliases':{name:apk_sha for name in b.ALIASES}}
            approval_path=root/'approval.json';approval_path.write_text(json.dumps(approval))
            data={'repo':{'address':channels.STABLE.repository_url.rstrip('/')},'packages':{
                channels.STABLE.package:{'versions':{'one':{'file':{'sha256':apk_sha}}}}}}
            args=['receipt.py',str(repo),'--approval',str(approval_path),'--metadata-revision','c'*40,
                '--fingerprint',channels.STABLE.repository_certificate,'--generation','d'*64]
            output=io.StringIO()
            with patch.object(b,'approved_generation',return_value=('d'*64,data)),patch.object(sys,'argv',args),contextlib.redirect_stdout(output):
                runpy.run_path(str(Path(__file__).with_name('receipt.py')),run_name='__main__')
            receipt=json.loads(output.getvalue())
            self.assertEqual([item['description'] for item in receipt['evidence']], [
                'Pinned fdroidserver verifies repository v1/v2 signatures, entry digest and indexed local APK hashes',
                'Official F-Droid and maintained compatible client device install/update acceptance',
                'Production HTTPS read-back and provider cache headers'])
            self.assertEqual([item['status'] for item in receipt['evidence']],['PASS','NOT RUN','NOT RUN'])
