"""Synthetic complete new-v2 chain; never production release evidence."""
import contextlib,copy,io,json,runpy,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import binary_repo as b
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'/'patches'))
import migration_receipts as m

class ReceiptAdapterTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.fixture=b.load(Path(__file__).parent/'fixtures/v2-release-input.json')
        self.bundle=self.root/'bundle';self.bundle.mkdir();self.approved=self.root/'approval.json';self.manifest=self.root/'final.json';self.apk=self.root/'collie.apk'
        self.write()
    def write(self):
        for kind in ('source','build','release'):(self.bundle/(kind+'.json')).write_text(json.dumps(self.fixture[kind]))
        self.approved.write_text(json.dumps(self.fixture['approval']));self.manifest.write_text(self.fixture['manifest_utf8']);self.apk.write_text(self.fixture['apk_utf8'])
    def ingest(self):
        a=self.fixture['approval'];identity={k:a[k] for k in ('package','version_code','version_name','apk_certificate_sha256')}
        with patch('binary_repo.inspect',return_value=identity):
            return b.ingest(self.approved,self.manifest,self.apk,self.root/'workspace','fixture-aapt','fixture-apksigner',self.bundle)
    def test_complete_v2_chain_ingest_emit_repository_graph(self):
        installed=self.ingest();self.assertEqual(b.sha(installed),b.sha(self.apk));repo=installed.parent
        for name in ('entry.jar','index-v1.jar','index-v2.json'):(repo/name).write_bytes(b'synthetic index fixture')
        a=self.fixture['approval'];data={'packages':{b.APP:{'versions':{a['apk_sha256']:{'manifest':{'versionCode':a['version_code'],'versionName':a['version_name'],'signer':{'sha256':[a['apk_certificate_sha256']]}},'file':{'name':'/'+installed.name,'sha256':a['apk_sha256']}}}}}}
        argv=['receipt.py',str(repo),'--approval',str(self.approved),'--receipt-bundle',str(self.bundle),'--metadata-revision','a'*40,'--fingerprint','b'*64,'--generation',b.generation_digest(repo)]
        output=io.StringIO()
        with patch.object(sys,'argv',argv),patch('binary_repo.verify_indexes',return_value=data),contextlib.redirect_stdout(output):
            runpy.run_path(str(Path(__file__).with_name('receipt.py')),run_name='__main__')
        receipt=json.loads(output.getvalue())
        # Device/provider evidence stays NOT RUN, so graph coherence is tested without promotion.
        documents={k:self.fixture[k] for k in ('source','build','release')};documents['repository']=receipt
        m.verify_graph(**documents,approved={k:m.digest(v) for k,v in documents.items()})
        with self.assertRaisesRegex(ValueError,'passed evidence'):
            m.verify_graph(**documents,approved={k:m.digest(v) for k,v in documents.items()},promotion=True)
        synthetic=copy.deepcopy(documents);synthetic['repository']['evidence']=[dict(status='PASS',description='synthetic complete-graph test only; not production evidence',sha256=b.sha(repo/'entry.jar'))]
        m.verify_graph(**synthetic,approved={k:m.digest(v) for k,v in synthetic.items()},promotion=True)
        self.assertEqual(receipt['payload']['release_manifest_sha256'],b.sha(self.manifest))
        self.assertEqual(receipt['payload']['release_receipt_sha256'],m.digest(self.fixture['release']))
    def test_wrong_raw_manifest_rejected_by_canonical_chain(self):
        self.fixture['manifest_utf8']+='tampered';self.fixture['approval']['release_manifest_sha256']=b.hashlib.sha256(self.fixture['manifest_utf8'].encode()).hexdigest();self.write()
        with self.assertRaisesRegex(ValueError,'raw final manifest'):self.ingest()
    def test_wrong_canonical_receipt_rejected(self):
        self.fixture['approval']['release_receipt_sha256']='0'*64;self.fixture['approval']['receipt_approvals']['release']='0'*64;self.write()
        with self.assertRaisesRegex(ValueError,'unapproved release'):self.ingest()
    def test_unknown_source_evidence_does_not_pass_v2_ingestion(self):
        self.fixture['source']['evidence']=[dict(status='UNKNOWN',description='not executed',sha256=None)]
        self.fixture['approval']['receipt_approvals']['source']=m.digest(self.fixture['source']);self.write()
        with self.assertRaisesRegex(ValueError,'passed evidence'):self.ingest()
    def test_legacy_cannot_claim_complete_graph(self):
        a=b.load(Path(__file__).parent/'approval.fixture.json')
        self.assertIsNone(b.verify_release_approval(a,None))
        with self.assertRaisesRegex(ValueError,'legacy approval'):b.verify_release_approval(a,self.bundle)
        self.fixture['approval']['schema']=2;self.write()
        with self.assertRaisesRegex(ValueError,'requires externally'):b.verify_release_approval(b.approval(self.approved),None)
