import copy
import json
from pathlib import Path
import tempfile
import unittest
import migration_receipts as receipts
import version_policy


class Receipts(unittest.TestCase):
    def example(self,kind):
        values={'git':'a'*40,'sha':'b'*64,'text':'explicit trusted-local; not reproduced','code':177703,'package':'im.collie.app','digests':[], 'aliases':receipts.ALIASES,'indexes':[{'file':'index-v2.json','sha256':'c'*64}]}
        return {'schema':2,'kind':kind,'payload':{k:copy.deepcopy(values[t]) for k,t in receipts.FIELDS[kind].items()},'evidence':[{'status':'UNKNOWN','description':'independent reproduction unexecuted','sha256':None}]}

    def test_all_and_tampering(self):
        for kind in receipts.FIELDS:
            original=self.example(kind); receipts.validate(original,kind)
            for field in original['payload']:
                tampered=copy.deepcopy(original); del tampered['payload'][field]
                with self.assertRaises(ValueError): receipts.validate(tampered)
            tampered=copy.deepcopy(original); tampered['payload']['unapproved']='x'
            with self.assertRaises(ValueError): receipts.validate(tampered)
            tampered=copy.deepcopy(original); tampered['evidence'][0]['status']='PASS'
            with self.assertRaises(ValueError): receipts.validate(tampered)
            self.assertEqual(receipts.digest(original),receipts.digest(copy.deepcopy(original)))

    def test_duplicate_json(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'receipt.json'; path.write_text('{"schema":2,"schema":2}')
            with self.assertRaises(ValueError): receipts.load(path)

    def test_actual_rc1(self):
        root=Path(__file__).resolve().parents[2]/'docs/migration/fixtures'
        report=receipts.import_legacy(json.loads((root/'rc1-final-manifest.json').read_text()),json.loads((root/'rc1-provenance.json').read_text()))
        self.assertEqual(report['source_receipt'],'UNKNOWN')
        self.assertEqual(report['instrumentation']['status'],'not run')

    def test_reservations_and_redistribution(self):
        published=[{'release':'v8.35.1-1-rc1','code':177702,'sha256':'a'*64}]
        with tempfile.TemporaryDirectory() as root:
            ledger=Path(root)/'versions.json'
            version_policy.reserve(ledger,'v8.35.1-1-rc1',177702,'a'*64,published)
            version_policy.reserve(ledger,'next',177703,'b'*64,published)
            version_policy.reserve(ledger,'next',177703,'b'*64,published)
            for release,code,sha in [('reused',177703,'c'*64),('next',177704,'b'*64),('old',177701,'d'*64)]:
                with self.assertRaises(ValueError): version_policy.reserve(ledger,release,code,sha,published)

    def test_separate_manifest_identities(self):
        import release_artifacts
        root=Path(__file__).resolve().parents[2]/'docs/migration/fixtures'
        m=json.loads((root/'rc1-provenance.json').read_text())
        m.update(schema=2,source_commit=m['commit'],engineering_revision='a'*40,workflow_revision='b'*40)
        ids={k:m[k] for k in ('source_commit','engineering_revision','workflow_revision')}
        release_artifacts.validate_manifest(m,m['commit'],m['run_id'],m['certificate_sha256'],ids)
        with self.assertRaises(ValueError): release_artifacts.validate_manifest(m,m['commit'],m['run_id'],m['certificate_sha256'])
        for field in ids:
            wrong=dict(ids); wrong[field]='c'*40
            with self.assertRaises(ValueError): release_artifacts.validate_manifest(m,m['commit'],m['run_id'],m['certificate_sha256'],wrong)

    def test_graph_approvals_and_substitution(self):
        root=Path(__file__).resolve().parents[2]/'docs/migration/fixtures'
        docs={k:receipts.load(root/(k+'-v2.json'),k) for k in receipts.FIELDS}
        approved=json.loads((root/'graph-approvals.json').read_text())
        receipts.verify_graph(**docs,approved=approved)
        # Shape-valid replacement must fail both external approval and graph links.
        for kind,field in [('source','published_commit'),('build','source_commit'),('release','engineering_revision'),('repository','apk_sha256')]:
            wrong=copy.deepcopy(docs); wrong[kind]['payload'][field]='d'*(64 if field=='apk_sha256' else 40)
            with self.assertRaises(ValueError): receipts.verify_graph(**wrong,approved=approved)
            current={k:receipts.digest(v) for k,v in wrong.items()}
            with self.assertRaises(ValueError): receipts.verify_graph(**wrong,approved=current)
        with self.assertRaises(ValueError): receipts.verify_graph(**docs,approved=approved,promotion=True)
        for doc in docs.values(): doc['evidence']=[{'status':'PASS','description':'synthetic passed fixture; no real attestation','sha256':'c'*64}]
        # Recompute every digest link after evidence change.
        docs['build']['payload']['source_receipt_sha256']=receipts.digest(docs['source'])
        docs['release']['payload'].update(source_receipt_sha256=receipts.digest(docs['source']),build_receipt_sha256=receipts.digest(docs['build']))
        docs['repository']['payload']['release_manifest_sha256']=receipts.digest(docs['release'])
        current={k:receipts.digest(v) for k,v in docs.items()}
        receipts.verify_graph(**docs,approved=current,promotion=True)

    def test_current_policy_preserves_legacy_parser(self):
        import release_artifacts as a
        for manifest in ({},{'artifact_mode':'five-profiles'},{'artifact_mode':'single-collie','source_profile':'collie-foss'}):
            with self.assertRaises(ValueError): a.require_canonical_release(manifest)
        a.require_canonical_release({'artifact_mode':'single-collie','source_profile':'collie'})

    def test_production_gate(self):
        import hashlib
        with tempfile.TemporaryDirectory() as root:
            root=Path(root); inventory=root/'published.json'; inventory.write_text(json.dumps([{'release':'old','code':177702,'sha256':'a'*64}]))
            digest=hashlib.sha256(inventory.read_bytes()).hexdigest()
            m={'version_name':'next','version_code':177703,'unsigned_manifest_sha256':'b'*64,'apks':[{'signed_sha256':'c'*64}]*5}
            with self.assertRaises(ValueError): version_policy.enforce(m,None,None,None)
            with self.assertRaises(ValueError): version_policy.enforce(m,root/'ledger',inventory,'d'*64)
            version_policy.enforce(m,root/'ledger',inventory,digest)
            version_policy.bind_signed(root/'ledger',m)
            version_policy.enforce(m,root/'ledger',inventory,digest)
            m['apks']=[{'signed_sha256':'d'*64}]*5
            with self.assertRaises(ValueError): version_policy.bind_signed(root/'ledger',m)
            m['version_code']=177702
            with self.assertRaises(ValueError): version_policy.enforce(m,root/'ledger',inventory,digest)

if __name__=='__main__': unittest.main()
