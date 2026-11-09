"""Synthetic, test-only Pages fixtures; never actual publication evidence."""
import copy
import hashlib
import io
import json
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / 'source'))
import native_acceptance as acceptance

POLICY = 'a' * 40
NATIVE = {
    'native-inputs/v1/maven/im/collie/native/libsignal-android/0.106.1-collie.1/libsignal-android-0.106.1-collie.1.aar': {'sha256': '4247031a753f73f12753d04f14d9316436bbf07af2097c1b897feedd360dbd9a', 'bytes': 215587114},
    'native-inputs/v1/maven/im/collie/native/libsignal-android/0.106.1-collie.1/libsignal-android-0.106.1-collie.1.pom': {'sha256': '94432b6fefcb0eeaae4622dc3d381b4ed1ae7c2a453967db6f416a636c25ad87', 'bytes': 1718},
    'native-inputs/v1/maven/im/collie/native/libsignal-client/0.106.1-collie.1/libsignal-client-0.106.1-collie.1.jar': {'sha256': 'c361b8e905c770b838535fdac5a0bdf0f995f8ed6ce0a6837cb4c7360e8c5a79', 'bytes': 1003356},
    'native-inputs/v1/maven/im/collie/native/libsignal-client/0.106.1-collie.1/libsignal-client-0.106.1-collie.1.pom': {'sha256': 'cb706c8229ba35967c285a4f28e0049769e904b9cc9fbbb6c97d37596690cd23', 'bytes': 1693},
    'native-inputs/v1/maven/im/collie/native/ringrtc-android/2.74.0-collie.1/ringrtc-android-2.74.0-collie.1.aar': {'sha256': '51f1f9b1f2335ba1b6557b5f0810227ac66a54ec8cbf0f2c789091b0ab5d245b', 'bytes': 94240994},
    'native-inputs/v1/maven/im/collie/native/ringrtc-android/2.74.0-collie.1/ringrtc-android-2.74.0-collie.1.pom': {'sha256': 'ab10045f321d1023e3c810625a209c0c8e007db7622fa4a0fdfcf1156e115b8b', 'bytes': 1572},
}
PATHS = sorted(acceptance.CANONICAL | set(NATIVE))
HEADERS_CACHED = {'Cache-Control': None, 'ETag': 'shared-404', 'Last-Modified': None, 'Age': '1468', 'X-Cache': 'HIT'}
HEADERS_FRESH = {'Cache-Control': None, 'ETag': 'fresh-negative', 'Last-Modified': None, 'Age': '0', 'X-Cache': 'MISS'}


def retained_dispatch_fixture():
    """Synthetic controller/API format fixture; this is never publication proof."""
    asset_id, run_id, size = 7501, 4001, 123
    bundle_digest = 'b' * 64
    tag = 'collie-pages-native-v1-' + POLICY[:7]
    template = ('gh workflow run pages-release.yml --repo 0cwa/collie-release-engineering --ref main '
                '-f preparation_mode=feed -f trusted_policy_revision="$POLICY" '
                '-f bundle_asset_id="$ASSET_ID" -f bundle_sha256="$BUNDLE_SHA"')
    controller = ('readonly POLICY="$(git -C "$WORKTREE" rev-parse HEAD)"\n'
                  'readonly BUNDLE_SHA="$APPROVED_BUNDLE_SHA256"\n'
                  '[[ "$actual_main" == "$POLICY" ]]\n'
                  'select(.tag_name==$tag and .target_commitish==$policy and .draft==false and .prerelease==true and (.assets|length)==1)\n'
                  f'select(.name=="input.tar" and .size=={size} and .digest==("sha256:"+$sha) and .state=="uploaded")\n'
                  'sha256sum "$INPUT"\n' + template + '\necho "DISPATCH_RUN_ID=$run_id"\n').encode()
    approval = b'{"synthetic":true}\n'
    log = (f'ASSET_ID: {asset_id}\nEXPECTED_SHA256: {bundle_digest}\n'
           f'APPROVED_POLICY: {POLICY}\nPREPARATION_MODE: feed\n'
           'python distribution/download_pages_input.py --asset-id "$ASSET_ID" --sha256 "$EXPECTED_SHA256"\n'
           'python distribution/pages.py unpack "$RUNNER_TEMP/input.tar" "$RUNNER_TEMP/inputs"\n'
           'python distribution/pages.py prepare "$RUNNER_TEMP/inputs" "$RUNNER_TEMP/pages" --policy "$APPROVED_POLICY"\n'
           'cp "$RUNNER_TEMP/pages/plan.json" "$RUNNER_TEMP/pages/journal.json" "$RUNNER_TEMP/control/"\n').encode()
    release = {'id': 7500, 'tag_name': tag, 'target_commitish': POLICY, 'draft': False,
               'prerelease': True, 'assets': [{'id': asset_id, 'name': 'input.tar', 'size': size,
               'digest': 'sha256:' + bundle_digest, 'state': 'uploaded'}]}
    dispatch = {
        'run_id': run_id, 'run_attempt': 2, 'run_created_at': '2026-11-09T10:00:00Z',
        'policy_revision': POLICY, 'repository': acceptance.REPO, 'workflow_ref': 'refs/heads/main',
        'dispatch_event': 'workflow_dispatch',
        'dispatch_inputs_as_passed_by_executed_controller': {
            'preparation_mode': 'feed', 'trusted_policy_revision': POLICY,
            'bundle_asset_id': str(asset_id), 'bundle_sha256': bundle_digest},
        'exact_command_template': template,
        'value_binding': ('POLICY was git rev-parse HEAD checked against remote main; ASSET_ID was selected '
                          'from the exact release tag only after matching asset name/size/API digest; '
                          'BUNDLE_SHA was the exact APPROVED_BUNDLE_SHA256 value passed to this controller invocation.'),
        'release': {'id': 7500, 'tag': tag, 'target_commitish': POLICY, 'asset_id': asset_id,
                    'asset_name': 'input.tar', 'asset_bytes': size,
                    'asset_digest': 'sha256:' + bundle_digest, 'asset_state': 'uploaded'},
        'candidate_approval_member': {'path': 'actual-approval.json', 'bytes': len(approval),
                                      'sha256': sha(approval)},
        'bundle_sha256': bundle_digest, 'bundle_bytes': size,
        'controller_sha256_at_dispatch': sha(controller),
        'provider_event_payload_exposes_inputs': False,
    }
    jobs = [{'id': i + 1, 'name': name, 'status': 'completed', 'conclusion': 'success',
             'startedAt': '2026-11-09T10:00:30Z', 'completedAt': '2026-11-09T10:01:00Z'}
            for i, name in enumerate(('prepare', 'phase1', 'phase1-readback', 'phase2', 'final-readback'))]
    plan = {'policy_revision': POLICY}
    return {
        'approval_raw': approval, 'dispatch_inputs': dispatch, 'release_metadata': release,
        'controller_raw': controller, 'prepare_log_raw': log,
        'run': {'id': run_id, 'status': 'completed', 'conclusion': 'success',
                'event': 'workflow_dispatch', 'head_sha': POLICY, 'run_attempt': 2,
                'created_at': '2026-11-09T10:00:00Z', 'run_started_at': '2026-11-09T10:00:00Z',
                'updated_at': '2026-11-09T10:10:00Z',
                'path': acceptance.WORKFLOW, 'workflow_id': 1},
        'jobs': jobs,
        'artifact_metadata': {'artifacts': [
            {'id': 9001, 'name': acceptance.ARTIFACT, 'expired': False, 'size_in_bytes': 1,
             'created_at': '2026-11-09T10:08:00Z',
             'workflow_run': {'id': run_id, 'head_sha': POLICY}},
            {'id': 9002, 'name': 'collie-pages-control', 'expired': False, 'size_in_bytes': 1}]},
        'pre_dispatch_inventory': {'captured_at': '2026-11-09T09:59:30Z', 'runs': [],
                                   'deployments': [], 'expected_run_ids': [1],
                                   'expected_deployment_ids': [1]},
        'plan': plan, 'evidence_index_sha256': 'd' * 64,
    }


class RetainedDispatchAdapterTests(unittest.TestCase):
    def setUp(self):
        self.evidence = retained_dispatch_fixture()

    def test_real_field_shape_binds_direct_records_without_event_inputs(self):
        run, binding = acceptance.validate_retained_dispatch(
            self.evidence, POLICY, 'b' * 64, 123, self.evidence['plan'])
        self.assertEqual(run['run_id'], 4001)
        self.assertEqual(run['run_attempt'], 2)
        self.assertEqual(binding['input_asset_id'], 7501)
        self.assertFalse(self.evidence['dispatch_inputs']['provider_event_payload_exposes_inputs'])

    def test_controller_filter_matches_the_measured_bundle_size(self):
        acceptance.validate_retained_dispatch(self.evidence, POLICY, 'b' * 64, 123, self.evidence['plan'])
        evidence=copy.deepcopy(self.evidence)
        evidence['controller_raw']=evidence['controller_raw'].replace(b'.size==123 ',b'.size==310855680 ')
        evidence['dispatch_inputs']['controller_sha256_at_dispatch']=sha(evidence['controller_raw'])
        with self.assertRaisesRegex(ValueError,'controller source does not bind'):
            acceptance.validate_retained_dispatch(evidence, POLICY, 'b' * 64, 123, evidence['plan'])

    def test_rerun_identity_uses_current_attempt_start_not_initial_creation(self):
        evidence=copy.deepcopy(self.evidence)
        evidence['run']['created_at']='2026-11-09T09:00:00Z'
        evidence['dispatch_inputs']['run_created_at']='2026-11-09T09:00:00Z'
        identity,_=acceptance.validate_retained_dispatch(evidence, POLICY, 'b' * 64, 123, evidence['plan'])
        self.assertEqual(identity['attempt_started_at'],'2026-11-09T10:00:00Z')
        del evidence['run']['run_started_at']
        with self.assertRaisesRegex(ValueError,'workflow run record fields differ'):
            acceptance.validate_retained_dispatch(evidence, POLICY, 'b' * 64, 123, evidence['plan'])

    def test_receipt_artifact_has_its_own_run_identity_and_creation_time(self):
        mutations=(
            lambda item:item['workflow_run'].update(id=9999),
            lambda item:item['workflow_run'].update(id=True),
            lambda item:item['workflow_run'].update(head_sha='f'*40),
            lambda item:item.pop('workflow_run'),
            lambda item:item.pop('created_at'),
            lambda item:item.update(created_at=None),
        )
        for mutate in mutations:
            evidence=copy.deepcopy(self.evidence)
            mutate(evidence['artifact_metadata']['artifacts'][0])
            with self.subTest(mutate=mutate),self.assertRaises(ValueError):
                acceptance.validate_retained_dispatch(evidence, POLICY, 'b' * 64, 123, evidence['plan'])

    def test_current_controller_shape_needs_no_plan_hash_at_dispatch(self):
        evidence = copy.deepcopy(self.evidence)
        self.assertNotIn('plan_sha256', evidence['dispatch_inputs'])
        run, binding = acceptance.validate_retained_dispatch(
            evidence, POLICY, 'b' * 64, 123, evidence['plan'])
        self.assertEqual(run['run_id'], 4001)
        self.assertEqual(binding['input_bundle_sha256'], 'b' * 64)

    def test_rejects_dispatch_asset_approval_and_run_mismatches(self):
        mutations = (
            lambda e: e['dispatch_inputs']['dispatch_inputs_as_passed_by_executed_controller'].update(bundle_asset_id='9999'),
            lambda e: e['dispatch_inputs']['dispatch_inputs_as_passed_by_executed_controller'].update(bundle_sha256='f' * 64),
            lambda e: e['dispatch_inputs']['release'].update(asset_id=9999),
            lambda e: e['dispatch_inputs']['candidate_approval_member'].update(sha256='0' * 64),
            lambda e: e['dispatch_inputs'].update(controller_sha256_at_dispatch='0' * 64),
            lambda e: e['dispatch_inputs'].update(plan_sha256='0' * 64),
            lambda e: (e.update(controller_raw=e['controller_raw'].replace(b'bundle_asset_id="$ASSET_ID"', b'bundle_asset_id=wrong')),
                       e['dispatch_inputs'].update(controller_sha256_at_dispatch=sha(e['controller_raw']))),
            lambda e: e['dispatch_inputs'].update(value_binding='self-consistent but false'),
            lambda e: e['dispatch_inputs'].update(run_attempt=1),
            lambda e: e['run'].update(id=9999),
            lambda e: e['run'].update(run_attempt=1),
            lambda e: e.update(prepare_log_raw=b'wrong preparation record'),
            lambda e: e['jobs'][-1].update(conclusion='skipped'),
        )
        for mutate in mutations:
            evidence = copy.deepcopy(self.evidence)
            mutate(evidence)
            with self.subTest(mutate=mutate), self.assertRaises(ValueError):
                acceptance.validate_retained_dispatch(evidence, POLICY, 'b' * 64, 123, evidence['plan'])

    def test_actual_failed_run_is_not_promoted_and_missing_snapshot_is_not_reconstructed(self):
        evidence = copy.deepcopy(self.evidence)
        evidence['run'].update(conclusion='failure')
        with self.assertRaisesRegex(ValueError, 'dispatch run ID/attempt/policy'):
            acceptance.validate_retained_dispatch(evidence, POLICY, 'b' * 64, 123, evidence['plan'])
        evidence_path = Path('native-candidate/runtime-timeout/current/run-42559808017')
        actual = next((parent / evidence_path for parent in Path(__file__).resolve().parents
                       if (parent / evidence_path).is_dir()), None)
        if actual is None:
            self.skipTest('retained actual failed-run evidence is not available in this checkout')
        with self.assertRaisesRegex(ValueError, 'pre-dispatch-inventory.json'):
            acceptance.load_run_evidence(actual)

    def test_run_evidence_directory_must_match_dispatch_run_id(self):
        with tempfile.TemporaryDirectory(prefix='run-id-binding-') as parent:
            directory = Path(parent) / 'run-4001'
            directory.mkdir()
            self.assertIsNone(acceptance.verify_run_directory(directory, {'run_id': 4001}))
            with self.assertRaisesRegex(ValueError, 'directory does not match'):
                acceptance.verify_run_directory(directory, {'run_id': 4002})


def sha(data):
    return hashlib.sha256(data).hexdigest()


def receipt_fixture(bundle_path):
    """Construct synthetic data matching the committed plan/journal shape only."""
    entries = {p: {'sha256': sha(('fixture:' + p).encode()), 'bytes': len(p) + 1} for p in PATHS}
    entries.update(NATIVE)
    native_doc = {'schema': 1, 'kind': 'collie-pages-native-v1', 'origin': acceptance.ORIGIN, 'files': NATIVE}
    native_raw = (json.dumps(native_doc, indent=2) + '\n').encode()
    source_native = [{'path': p, 'url': acceptance.ORIGIN + p, **e} for p, e in NATIVE.items()]
    approval = {
        'schema': 1, 'kind': 'collie-pages-native-input', 'origin': acceptance.ORIGIN,
        'previous_site_sha256': acceptance.digest({}), 'previous_manifest_sha256': None,
        'native_manifest_sha256': sha(native_raw), 'maximum_bytes': 950000000,
        'policy_revision': POLICY,
    }
    approval_raw = (json.dumps(approval, indent=2) + '\n').encode()
    with tarfile.open(bundle_path, 'w', format=tarfile.PAX_FORMAT) as archive:
        info = tarfile.TarInfo('approval.json')
        info.size = len(approval_raw)
        archive.addfile(info, io.BytesIO(approval_raw))
    bundle_digest = sha(bundle_path.read_bytes())
    asset_id = 7501
    tag = 'collie-pages-native-v1-' + POLICY[:7]
    asset = {
        'release_id': 7500, 'tag_name': tag,
        'target_commitish': POLICY, 'draft': False, 'prerelease': True,
        'asset_id': asset_id, 'name': 'input.tar', 'size': bundle_path.stat().st_size,
        'digest': 'sha256:' + bundle_digest, 'state': 'uploaded',
        'browser_download_url': f'https://github.com/{acceptance.REPO}/releases/download/{tag}/input.tar',
    }
    plan = {
        'schema': 1, 'kind': 'collie-pages-promotion',
        'input_approval_sha256': acceptance.digest(approval),
        'origin': acceptance.ORIGIN, 'policy_revision': POLICY, 'mode': 'native-only',
        'native_manifest_sha256': sha(native_raw), 'repository_receipt_sha256': None,
        'repository_certificate_sha256': None, 'candidate_generation_sha256': None,
        'maximum_bytes': 950000000, 'previous_manifest_sha256': None,
        'previous_site_sha256': acceptance.digest({}), 'previous_files': {},
        'manifests': {'phase1': entries, 'phase2': copy.deepcopy(entries)},
        'artifacts': {'phase1': {'sha256': 'c' * 64, 'bytes': 100},
                      'phase2': {'sha256': 'c' * 64, 'bytes': 100}},
    }
    plan_digest = acceptance.digest(plan)
    operation_inventory = {
        'runs': [{'id': 4101, 'status': 'completed', 'conclusion': 'failure'},
                 {'id': 4102, 'status': 'completed', 'conclusion': 'failure'}],
        'deployments': [{'id': 5101, 'state': 'failure'}, {'id': 5102, 'state': 'failure'}],
        'expected_run_ids': [4101, 4102], 'expected_deployment_ids': [5101, 5102],
        'captured_at': '2026-11-09T09:59:30Z',
    }
    run = {
        'repository': acceptance.REPO, 'workflow_path': acceptance.WORKFLOW, 'run_id': 4001,
        'run_attempt': 2, 'head_sha': POLICY, 'event': 'workflow_dispatch', 'status': 'completed',
        'conclusion': 'success', 'attempt_started_at': '2026-11-09T10:00:00Z',
        'attempt_completed_at': '2026-11-09T10:10:00Z',
        'prepare_job': {'id': 6000, 'name': 'prepare', 'status': 'completed', 'conclusion': 'success',
                        'started_at': '2026-11-09T10:00:30Z', 'completed_at': '2026-11-09T10:03:00Z'},
        'final_readback_job': {'name': 'final-readback', 'status': 'completed', 'conclusion': 'success',
                               'started_at': '2026-11-09T10:07:00Z', 'completed_at': '2026-11-09T10:09:00Z'},
        'artifact': {'id': 5001, 'name': acceptance.ARTIFACT, 'expired': False,
                     'workflow_run_id': 4001, 'head_sha': POLICY, 'created_at': '2026-11-09T10:08:00Z'},
        'pre_dispatch_inventory': operation_inventory,
        'dispatch_binding': {
            'payload': {'ref': 'main', 'preparation_mode': 'feed',
                        'trusted_policy_revision': POLICY, 'bundle_asset_id': str(asset_id),
                        'bundle_sha256': bundle_digest},
            'release_asset': asset,
            'controller_transcript': (
                'PRECONDITION_RELEASE_ASSET=PASS\n'
                f'+ ASSET_ID={asset_id}\n'
                f'+ gh workflow run pages-release.yml --repo {acceptance.REPO} --ref main '
                f'-f preparation_mode=feed -f trusted_policy_revision={POLICY} '
                f'-f bundle_asset_id={asset_id} -f bundle_sha256={bundle_digest}\n'
                'DISPATCH_RUN_ID=4001\n'),
            'run_identity': {'id': 4001, 'attempt': 2, 'head_sha': POLICY},
        },
    }
    observations = {p: {'status': 200, **e, 'headers': dict(HEADERS_FRESH)} for p, e in entries.items()}
    previous_paths = {'site-manifest.json', 'fdroid/repo/entry.jar'} | set(NATIVE)
    previous = {p: {'status': 404, 'headers': dict(HEADERS_CACHED),
                    'classification': acceptance.CACHED_NEGATIVE} for p in previous_paths}
    journal = {
        'schema': 1, 'plan_sha256': plan_digest, 'completed': ['phase1', 'phase2'],
        'previous_verified': False,
        'readbacks': {'phase1': observations, 'phase2': copy.deepcopy(observations)},
        'previous_state_classification': acceptance.FIRST_PUBLICATION_RECOVERY,
        'previous_state': {'plan_sha256': plan_digest,
                           'input_approval_sha256': acceptance.digest(approval),
                           'observations': previous},
    }
    return plan, journal, native_raw, source_native, run, approval_raw, bundle_path


def retained_receipt_directory_fixture(directory, bundle_path):
    """Write a complete synthetic retained-run directory for the real loader chain."""
    plan, journal, native_raw, source_native, _, approval_raw, _ = receipt_fixture(bundle_path)
    evidence = retained_dispatch_fixture()
    evidence['approval_raw'] = approval_raw
    bundle_digest, bundle_size, _ = acceptance.inspect_input_bundle(bundle_path)
    dispatch = evidence['dispatch_inputs']
    dispatch['bundle_sha256'] = bundle_digest
    dispatch['bundle_bytes'] = bundle_size
    dispatch['candidate_approval_member'] = {
        'path': 'actual-approval.json', 'bytes': len(approval_raw), 'sha256': sha(approval_raw)}
    dispatch['dispatch_inputs_as_passed_by_executed_controller']['bundle_sha256'] = bundle_digest
    dispatch['release'].update(asset_bytes=bundle_size, asset_digest='sha256:' + bundle_digest)
    dispatch['controller_sha256_at_dispatch'] = sha(
        evidence['controller_raw'].replace(b'.size==123 ', f'.size=={bundle_size} '.encode()))
    evidence['controller_raw'] = evidence['controller_raw'].replace(
        b'.size==123 ', f'.size=={bundle_size} '.encode())
    evidence['prepare_log_raw'] = evidence['prepare_log_raw'].replace(
        b'EXPECTED_SHA256: ' + b'b' * 64, b'EXPECTED_SHA256: ' + bundle_digest.encode())
    evidence['release_metadata']['assets'][0].update(
        size=bundle_size, digest='sha256:' + bundle_digest)
    evidence['pre_dispatch_inventory'] = {
        'captured_at': '2026-11-09T09:59:30Z',
        'runs': [{'id': 1, 'status': 'completed', 'conclusion': 'failure'}],
        'deployments': [{'id': 2, 'state': 'success'}],
        'expected_run_ids': [1], 'expected_deployment_ids': [2],
    }
    for job in evidence['jobs']:
        if job['name'] == 'prepare':
            job.update(startedAt='2026-11-09T10:00:30Z', completedAt='2026-11-09T10:03:00Z')
        elif job['name'] == 'final-readback':
            job.update(startedAt='2026-11-09T10:07:00Z', completedAt='2026-11-09T10:09:00Z')
    evidence['artifact_metadata']['artifacts'][0]['workflow_run'] = {
        'id': evidence['run']['id'], 'head_sha': POLICY}
    evidence['plan'] = plan

    directory.mkdir(parents=True)
    values = {
        'actual-approval.json': approval_raw,
        'actual-dispatch-inputs.json': (json.dumps(dispatch, indent=2) + '\n').encode(),
        'published-release-asset.json': (json.dumps(evidence['release_metadata'], indent=2) + '\n').encode(),
        'controller-at-dispatch.sh': evidence['controller_raw'],
        'prepare-safe-log.txt': evidence['prepare_log_raw'],
        'run.json': (json.dumps(evidence['run'], indent=2) + '\n').encode(),
        'jobs.json': (json.dumps(evidence['jobs'], indent=2) + '\n').encode(),
        'artifact-metadata.json': (json.dumps(evidence['artifact_metadata'], indent=2) + '\n').encode(),
        'control/plan.json': (json.dumps(plan, indent=2) + '\n').encode(),
        'control/journal.json': (json.dumps(journal, indent=2) + '\n').encode(),
        'pre-dispatch-inventory.json': (json.dumps(evidence['pre_dispatch_inventory'], indent=2) + '\n').encode(),
    }
    for relative, raw in values.items():
        path = directory / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    index = ''.join(f'{sha(raw)}  {relative}\n' for relative, raw in sorted(values.items()))
    (directory / 'evidence-sha256.txt').write_text(index)
    return native_raw, source_native


class NativeAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='native-approval-fixture-')
        self.addCleanup(self.temp.cleanup)
        self.fixture = receipt_fixture(Path(self.temp.name) / 'input.tar')

    def validate(self, fixture=None, policy=POLICY):
        plan, journal, native_raw, native_files, run, approval_raw, bundle_path = fixture or self.fixture
        bundle_digest, _, _ = acceptance.inspect_input_bundle(bundle_path)
        binding = {'input_approval_sha256': acceptance.digest(json.loads(approval_raw)),
                   'input_asset_id': run['dispatch_binding']['release_asset']['asset_id'],
                   'input_bundle_sha256': bundle_digest, 'dispatch_binding_sha256': 'a' * 64,
                   'workflow_metadata_sha256': 'b' * 64}
        return acceptance._validate_core(plan, journal, native_raw, native_files, run, policy,
                                         approval_raw, bundle_path, binding)

    def approval_variant(self, mutate):
        fixture = copy.deepcopy(self.fixture)
        plan, journal, _, _, run, _, _ = fixture
        approval = json.loads(self.fixture[5])
        mutate(approval)
        approval_raw = (json.dumps(approval, indent=2) + '\n').encode()
        bundle_path = Path(self.temp.name) / ('variant-' + str(len(list(Path(self.temp.name).glob('variant-*')))) + '.tar')
        with tarfile.open(bundle_path, 'w', format=tarfile.PAX_FORMAT) as archive:
            info = tarfile.TarInfo('approval.json')
            info.size = len(approval_raw)
            archive.addfile(info, io.BytesIO(approval_raw))
        bundle_digest = sha(bundle_path.read_bytes())
        binding = run['dispatch_binding']
        old_digest = binding['payload']['bundle_sha256']
        binding['payload']['bundle_sha256'] = bundle_digest
        binding['release_asset']['size'] = bundle_path.stat().st_size
        binding['release_asset']['digest'] = 'sha256:' + bundle_digest
        binding['controller_transcript'] = binding['controller_transcript'].replace(old_digest, bundle_digest)
        plan['input_approval_sha256'] = acceptance.digest(approval)
        journal['plan_sha256'] = acceptance.digest(plan)
        journal['previous_state']['plan_sha256'] = acceptance.digest(plan)
        journal['previous_state']['input_approval_sha256'] = acceptance.digest(approval)
        return (plan, journal, fixture[2], fixture[3], run, approval_raw, bundle_path)

    def test_synthetic_complete_recovery_keeps_previous_unverified_and_binds_source_evidence(self):
        proof = self.validate()
        self.assertEqual(proof['status'], 'PASS')
        self.assertEqual(proof['source_evidence']['status'], 'PASS')
        self.assertEqual(proof['source_evidence']['sha256'], proof['evidence_sha256'])
        self.assertFalse(self.fixture[1]['previous_verified'])
        self.assertEqual(self.fixture[1]['previous_state_classification'], acceptance.FIRST_PUBLICATION_RECOVERY)
        self.assertIn('pre-dispatch inventory', proof['source_evidence']['description'])
        self.assertEqual(proof['operation_inventory_sha256'], acceptance.sha(acceptance.canonical_bytes(self.fixture[4]['pre_dispatch_inventory'])))
        self.assertEqual(proof['input_approval_sha256'], self.fixture[0]['input_approval_sha256'])
        self.assertEqual(proof['input_asset_id'], self.fixture[4]['dispatch_binding']['release_asset']['asset_id'])
        self.assertEqual(proof['input_bundle_sha256'], self.fixture[4]['dispatch_binding']['payload']['bundle_sha256'])

    def test_composer_rebinds_validated_proof_to_receipt_and_native_sources(self):
        plan, journal, native_raw, native_files, _, _, _ = self.fixture
        proof = self.validate()
        self.assertEqual(
            acceptance.validate(plan, journal, native_raw, native_files, proof, POLICY), proof)
        changed = copy.deepcopy(journal)
        changed['readbacks']['phase2'].pop(next(iter(changed['readbacks']['phase2'])))
        with self.assertRaisesRegex(ValueError, 'composition proof differs|readback must cover'):
            acceptance.validate(plan, changed, native_raw, native_files, proof, POLICY)
        with self.assertRaisesRegex(ValueError, 'composition proof does not match'):
            acceptance.validate(plan, journal, native_raw, native_files, proof, 'd' * 40)

    def test_retained_directory_to_composition_and_source_transition_end_to_end(self):
        directory = Path(self.temp.name) / 'run-4001'
        native_raw, source_native = retained_receipt_directory_fixture(directory, self.fixture[6])
        evidence = acceptance.load_run_evidence(directory)
        proof = acceptance.validate_retained_evidence(
            evidence, native_raw, source_native, POLICY, self.fixture[6])
        self.assertEqual(acceptance.validate(
            evidence['plan'], evidence['journal'], native_raw, source_native, proof, POLICY), proof)

        state = {'schema': 1, 'status': 'BLOCKED', 'repository': acceptance.NATIVE_REPOSITORY,
                 'publication_status': 'NOT PUBLISHED', 'reason': 'synthetic fixture only',
                 'lock': 'native-lock.json', 'production_version_floor': 177702}
        gradle = ('before\nthrow GradleException("Direct Android builds BLOCKED: publish and verify locked native inputs first; see docs/BUILD.md")\n')
        workflow_line = """run: python3 -c 'import json; x=json.load(open("build-inputs/native-publication.json")); assert x["status"] == "BLOCKED"; print("Direct Android build acceptance BLOCKED pending public immutable native inputs; this source check is not build approval")'"""
        build = ("**Direct build readiness: BLOCKED.**\n"
                 "Every direct Android Gradle configuration deliberately fails until the required publication layer\n"
                 "and clean-checkout build acceptance are reviewed. This draft is not releasable.\n"
                 "The expected command; it is currently BLOCKED as described above.\n"
                 "The direct-build gate remains closed until the owner refreshes the\n"
                 "inventory and confirms this proposal through the serialized reservation policy.\n"
                 "accepted native bytes. Their public immutable repository has not been supplied\n"
                 "or independently downloaded. `native-publication.json` records this gap. Do not\n"
                 "substitute upstream native libraries or disable strict dependency verification.\n"
                 "The retained local operator route is in collie-release-engineering and still\n"
                 "requires its explicit trusted-local native acceptance. That is not independent\n"
                 "native reproduction. The three locked binaries and transformed POMs now have tracked exclusive repository, catalog, substitutions and strict verification entries. The proposed origin in build-inputs/native-artifacts.json is NOT PUBLISHED. Obtain operator publication approval, independently read back all six exact files, review a separate acceptance/gate layer, then run a fresh controlled Gradle build. No native init script, local Maven lookup or external native source includeBuild is part of this direct-source recipe.\n"
                 "**DRAFT candidate proposal:** 8.35.1-2-rc1 / 177703. This value is not allocated\n"
                 "or reserved. The direct-build gate remains bound to the recorded native acceptance digest. Version reservation\n"
                 "and release eligibility remain separate requirements.")
        properties = ('# DRAFT proposal only; not reserved. Direct Android builds remain blocked.\n'
                      'releaseVersionCode=177703\nreleaseVersionName=8.35.1-2-rc1\n')
        outputs, _ = acceptance.transition_outputs(
            json.dumps(state).encode(), gradle.encode(), workflow_line.encode(), build.encode(),
            properties.encode(), proof)
        self.assertEqual(json.loads(outputs['build-inputs/native-publication.json'])['status'], 'ACCEPTED')
        self.assertIn(b'Preserve build outputs before source receipt collection', outputs['docs/BUILD.md'])

        journal_path = directory / 'control/journal.json'
        journal = json.loads(journal_path.read_bytes())
        path = next(iter(journal['readbacks']['phase2']))
        journal['readbacks']['phase2'][path]['sha256'] = '0' * 64
        journal_raw = (json.dumps(journal, indent=2) + '\n').encode()
        journal_path.write_bytes(journal_raw)
        index_path = directory / 'evidence-sha256.txt'
        rows = {line.split('  ', 1)[1]: line.split('  ', 1)[0]
                for line in index_path.read_text().splitlines()}
        rows['control/journal.json'] = sha(journal_raw)
        index_path.write_text(''.join(f'{rows[name]}  {name}\n' for name in sorted(rows)))
        tampered = acceptance.load_run_evidence(directory)
        with self.assertRaisesRegex(ValueError, 'readback differs'):
            acceptance.validate_retained_evidence(
                tampered, native_raw, source_native, POLICY, self.fixture[6])

    def test_n1_requires_actual_approval_member_and_canonical_digest(self):
        with self.assertRaisesRegex(ValueError, 'approval input is not'):
            plan, journal, native_raw, source_native, run, _, bundle_path = self.fixture
            _, _, _, _, run, _, _ = self.fixture
            bundle_digest, _, _ = acceptance.inspect_input_bundle(bundle_path)
            binding = {'input_approval_sha256': '0' * 64, 'input_asset_id': 7501,
                       'input_bundle_sha256': bundle_digest, 'dispatch_binding_sha256': 'a' * 64,
                       'workflow_metadata_sha256': 'b' * 64}
            acceptance._validate_core(plan, journal, native_raw, source_native, run, POLICY, b'{}', bundle_path, binding)
        fixture = copy.deepcopy(self.fixture)
        fixture[0]['input_approval_sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'approval semantics or canonical digest'):
            self.validate(fixture)

    def test_n1_rejects_semantically_wrong_approval_even_when_digest_is_rebound(self):
        wrong_values = (
            lambda a: a.update(origin='https://wrong.example/'),
            lambda a: a.update(policy_revision='c' * 40),
            lambda a: a.update(maximum_bytes=949999999),
            lambda a: a.update(previous_site_sha256='0' * 64),
            lambda a: a.update(previous_manifest_sha256='d' * 64),
            lambda a: a.update(native_manifest_sha256='e' * 64),
            lambda a: a.update(kind='collie-pages-input'),
            lambda a: a.update(extra='unapproved'),
        )
        for mutate in wrong_values:
            fixture = self.approval_variant(mutate)
            with self.subTest(mutate=mutate), self.assertRaises(ValueError):
                self.validate(fixture)

    def test_recovery_provenance_and_cache_classification_fail_closed(self):
        mutations = (
            lambda p, j, r: j.pop('previous_state_classification'),
            lambda p, j, r: j['previous_state'].update(plan_sha256='0' * 64),
            lambda p, j, r: j['previous_state'].update(input_approval_sha256='0' * 64),
            lambda p, j, r: j['previous_state']['observations'].pop(next(iter(NATIVE))),
            lambda p, j, r: j['previous_state']['observations'][next(iter(NATIVE))].update(classification=acceptance.NEGATIVE_UNVERIFIED),
            lambda p, j, r: j['previous_state']['observations'][next(iter(NATIVE))]['headers'].update(Age='0', **{'X-Cache': 'MISS'}),
            lambda p, j, r: j['previous_state']['observations'][next(iter(NATIVE))].update(status=True),
            lambda p, j, r: j['previous_state']['observations'].__setitem__(next(iter(NATIVE)), 'malformed'),
            lambda p, j, r: j.update(previous_verified=True),
            lambda p, j, r: j.update(previous_readback={}),
        )
        for mutate in mutations:
            fixture = copy.deepcopy(self.fixture)
            mutate(*fixture[:2], fixture[4])
            with self.subTest(mutate=mutate), self.assertRaises(ValueError):
                self.validate(fixture)

    def test_fresh_negative_classification_is_distinct_and_not_origin_proof(self):
        fixture = copy.deepcopy(self.fixture)
        item = next(iter(fixture[1]['previous_state']['observations'].values()))
        item['headers'] = dict(HEADERS_FRESH)
        item['classification'] = acceptance.NEGATIVE_UNVERIFIED
        proof = self.validate(fixture)
        self.assertEqual(proof['status'], 'PASS')
        self.assertFalse(fixture[1]['previous_verified'])
        item['classification'] = acceptance.CACHED_NEGATIVE
        with self.assertRaisesRegex(ValueError, 'disagrees with recorded cache headers'):
            self.validate(fixture)

    def test_plan_journal_native_hashes_and_exact_fifteen_path_sets_are_bound(self):
        mutations = (
            lambda p, j: p.update(policy_revision='e' * 40),
            lambda p, j: p.update(previous_site_sha256='0' * 64),
            lambda p, j: p['manifests']['phase1'].pop(PATHS[-1]),
            lambda p, j: p['manifests']['phase2'][next(iter(NATIVE))].update(sha256='f' * 64),
            lambda p, j: j.update(plan_sha256='0' * 64),
            lambda p, j: j['readbacks']['phase2'][next(iter(NATIVE))].update(bytes=1),
        )
        for mutate in mutations:
            fixture = copy.deepcopy(self.fixture)
            mutate(fixture[0], fixture[1])
            with self.subTest(mutate=mutate), self.assertRaises(ValueError):
                self.validate(fixture)

    def test_source_native_artifacts_accept_exact_repository_relative_producer_paths(self):
        native_raw = self.fixture[2]
        source_rows = self.fixture[3]
        relative_rows = [
            {**row, 'path': row['path'][len(acceptance.NATIVE_PREFIX):]}
            for row in source_rows
        ]
        self.assertEqual(acceptance.native_map(json.loads(native_raw), relative_rows), NATIVE)
        wrong_url = copy.deepcopy(relative_rows)
        wrong_url[0]['url'] = acceptance.ORIGIN + wrong_url[0]['path']
        with self.assertRaisesRegex(ValueError, 'source native URL differs'):
            acceptance.native_map(json.loads(native_raw), wrong_url)
        traversal = copy.deepcopy(relative_rows)
        traversal[0]['path'] = '../' + traversal[0]['path']
        with self.assertRaisesRegex(ValueError, 'source native path invalid'):
            acceptance.native_map(json.loads(native_raw), traversal)

    def test_both_ordered_readbacks_require_all_fifteen_exact_200_hash_size(self):
        for mutate in (
            lambda p, j: j.update(completed=['phase1']),
            lambda p, j: j['readbacks']['phase2'].pop(PATHS[-1]),
            lambda p, j: j['readbacks']['phase1'][PATHS[0]].update(status=404),
            lambda p, j: j['readbacks']['phase2'][PATHS[0]]['headers'].pop('X-Cache'),
        ):
            fixture = copy.deepcopy(self.fixture)
            mutate(fixture[0], fixture[1])
            with self.subTest(mutate=mutate), self.assertRaises(ValueError):
                self.validate(fixture)

    def test_exact_200_body_hashes_allow_cached_response_headers(self):
        fixture = copy.deepcopy(self.fixture)
        for phase in ('phase1', 'phase2'):
            for item in fixture[1]['readbacks'][phase].values():
                item['headers'] = dict(HEADERS_CACHED)
        self.assertEqual(self.validate(fixture)['status'], 'PASS')

    def test_terminal_inventory_must_be_recent_and_precede_run_dispatch(self):
        for captured_at in ('2026-11-09T09:54:59Z', '2026-11-09T10:00:01Z'):
            fixture = copy.deepcopy(self.fixture)
            fixture[4]['pre_dispatch_inventory']['captured_at'] = captured_at
            with self.subTest(captured_at=captured_at), self.assertRaisesRegex(ValueError, 'inventory is not fresh'):
                self.validate(fixture)

    def test_terminal_inventory_rejects_missing_active_or_unexpected_operations(self):
        for mutate in (
            lambda i: i['runs'].pop(),
            lambda i: i['runs'][0].update(status='in_progress'),
            lambda i: i['deployments'][0].update(state='in_progress'),
            lambda i: i['runs'].append({'id': 4103, 'status': 'completed', 'conclusion': 'failure'}),
            lambda i: i['expected_run_ids'].__setitem__(1, 4101),
        ):
            fixture = copy.deepcopy(self.fixture)
            mutate(fixture[4]['pre_dispatch_inventory'])
            with self.subTest(mutate=mutate), self.assertRaises(ValueError):
                self.validate(fixture)

    def test_terminal_inventory_allows_known_successful_deployments(self):
        fixture = copy.deepcopy(self.fixture)
        fixture[4]['pre_dispatch_inventory']['deployments'][0]['state'] = 'success'
        proof = self.validate(fixture)
        self.assertEqual(proof['status'], 'PASS')
        self.assertFalse(fixture[1]['previous_verified'])

    def test_run_workflow_head_and_artifact_identity_must_match_plan(self):
        for mutate in (
            lambda run: run.update(head_sha='e' * 40),
            lambda run: run.update(conclusion='failure'),
            lambda run: run['artifact'].update(workflow_run_id=9999),
            lambda run: run['artifact'].update(name='unrelated-artifact'),
            lambda run: run['artifact'].update(expired=True),
            lambda run: run['artifact'].update(created_at='2026-11-09T09:00:00Z'),
        ):
            fixture = copy.deepcopy(self.fixture)
            mutate(fixture[4])
            with self.subTest(mutate=mutate), self.assertRaises(ValueError):
                self.validate(fixture)

    def test_transition_changes_only_blocked_synthetic_state_after_validation(self):
        proof = self.validate()
        blocked = {'schema': 1, 'status': 'BLOCKED', 'repository': acceptance.NATIVE_REPOSITORY,
                   'publication_status': 'NOT PUBLISHED', 'reason': 'test only', 'lock': 'native-lock.json',
                   'production_version_floor': 177702}
        updated, evidence = acceptance.transition_native_state(json.dumps(blocked).encode(), proof)
        actual = json.loads(updated)
        self.assertEqual(actual['status'], 'ACCEPTED')
        self.assertEqual(actual['acceptance']['policy_revision'], POLICY)
        self.assertIn('operation_inventory_sha256', actual['acceptance'])
        self.assertIn('input_approval_sha256', actual['acceptance'])
        self.assertIn('input_asset_id', actual['acceptance'])
        self.assertIn('input_bundle_sha256', actual['acceptance'])
        self.assertEqual(evidence, proof['source_evidence'])
        with self.assertRaises(ValueError):
            acceptance.transition_native_state(updated, proof)

    def test_pinned_policy_revision_mismatch_fails(self):
        with self.assertRaisesRegex(ValueError, 'exact reviewed policy'):
            self.validate(policy='f' * 40)

    def test_accepted_transition_updates_gate_and_workflow_as_one_source_layer(self):
        proof = self.validate()
        state = {'schema': 1, 'status': 'BLOCKED', 'repository': acceptance.NATIVE_REPOSITORY,
                 'publication_status': 'NOT PUBLISHED', 'reason': 'test only',
                 'lock': 'native-lock.json', 'production_version_floor': 177702}
        gradle = 'before\nthrow GradleException("Direct Android builds BLOCKED: publish and verify locked native inputs first; see docs/BUILD.md")\n'
        workflow_line = """      - name: Report direct-build gate
        run: python3 -c 'import json; x=json.load(open("build-inputs/native-publication.json")); assert x["status"] == "BLOCKED"; print("Direct Android build acceptance BLOCKED pending public immutable native inputs; this source check is not build approval")'
"""
        workflow = 'name: Direct source checks\n' + workflow_line
        build = ("**Direct build readiness: BLOCKED.**\n"
                 "Every direct Android Gradle configuration deliberately fails until the required publication layer\n"
                 "and clean-checkout build acceptance are reviewed. This draft is not releasable.\n"
                 "The expected command; it is currently BLOCKED as described above.\n"
                 "The direct-build gate remains closed until the owner refreshes the\n"
                 "inventory and confirms this proposal through the serialized reservation policy.\n"
                 "accepted native bytes. Their public immutable repository has not been supplied\n"
                 "or independently downloaded. `native-publication.json` records this gap. Do not\n"
                 "substitute upstream native libraries or disable strict dependency verification.\n"
                 "The retained local operator route is in collie-release-engineering and still\n"
                 "requires its explicit trusted-local native acceptance. That is not independent\n"
                 "native reproduction. The three locked binaries and transformed POMs now have tracked exclusive repository, catalog, substitutions and strict verification entries. The proposed origin in build-inputs/native-artifacts.json is NOT PUBLISHED. Obtain operator publication approval, independently read back all six exact files, review a separate acceptance/gate layer, then run a fresh controlled Gradle build. No native init script, local Maven lookup or external native source includeBuild is part of this direct-source recipe.\n"
                 "**DRAFT candidate proposal:** 8.35.1-2-rc1 / 177703. This value is not allocated\n"
                 "or reserved. The direct-build gate remains bound to the recorded native acceptance digest. Version reservation\n"
                 "and release eligibility remain separate requirements.")
        properties = '# DRAFT proposal only; not reserved. Direct Android builds remain blocked.\nreleaseVersionCode=177703\nreleaseVersionName=8.35.1-2-rc1\n'
        output, evidence = acceptance.transition_outputs(json.dumps(state).encode(), gradle.encode(),
                                                         workflow.encode(), build.encode(), properties.encode(), proof)
        result = json.loads(output['build-inputs/native-publication.json'])
        self.assertEqual(result['status'], 'ACCEPTED')
        gradle_result = output['app/build.gradle.kts'].decode()
        self.assertIn('rootProject.file("build-inputs/native-publication.json")', gradle_result)
        self.assertIn(proof['evidence_sha256'], gradle_result)
        self.assertNotIn('throw GradleException("Direct Android builds BLOCKED: publish and verify locked native inputs first;', gradle_result)
        workflow_result = output['.github/workflows/source.yml'].decode()
        self.assertIn('x.get("status") in ("BLOCKED","ACCEPTED")', workflow_result)
        self.assertIn('operation_inventory_sha256', workflow_result)
        accepted_build = output['docs/BUILD.md'].decode()
        self.assertIn('Direct build readiness: ACCEPTED', accepted_build)
        self.assertIn('both readback phases verified all 15 canonical HTTPS paths', accepted_build)
        self.assertIn('this does not claim origin verification or cryptographic GitHub attestation', accepted_build)
        self.assertIn('Version code 177703 is held', accepted_build)
        self.assertNotIn('public immutable repository has not been supplied', accepted_build)
        self.assertNotIn('proposed origin in build-inputs/native-artifacts.json is NOT PUBLISHED', accepted_build)
        self.assertIn('177703 is held', output['gradle.properties'].decode())
        self.assertNotIn('not reserved', output['gradle.properties'].decode())
        accepted_build = output['docs/BUILD.md'].decode()
        self.assertIn('app/build', accepted_build)
        self.assertIn('`core/*`, `core/gms/*`, `lib/*`, and `feature/*`', accepted_build)
        self.assertIn('`.gradle/`, `.kotlin/`, `.cxx/`, root `build/`', accepted_build)
        self.assertIn('Do not remove `local.properties`', accepted_build)
        self.assertIn('Do not remove `local.properties`, source files, pre-existing paths, or anything with `git clean`', accepted_build)
        self.assertEqual(evidence, proof['source_evidence'])

    def test_tampered_proof_or_inventory_digest_cannot_transition_blocked_state(self):
        blocked = {'schema': 1, 'status': 'BLOCKED', 'repository': acceptance.NATIVE_REPOSITORY,
                   'publication_status': 'NOT PUBLISHED', 'reason': 'test only', 'lock': 'native-lock.json',
                   'production_version_floor': 177702}
        for field in ('plan_sha256', 'operation_inventory_sha256', 'input_approval_sha256',
                      'input_bundle_sha256', 'dispatch_binding_sha256'):
            proof = self.validate()
            proof[field] = '0' * 64
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'bundle digest'):
                acceptance.transition_native_state(json.dumps(blocked).encode(), proof)


if __name__ == '__main__':
    unittest.main()
