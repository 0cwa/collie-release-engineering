#!/usr/bin/env python3
"""Validate completed Pages native-input evidence for a reviewed source transition.

This consumes operator-exported GitHub run/artifact metadata; it performs no API
calls, publishing, signing, or gate changes by itself. A PASS is useful only when
these inputs are the exact retained artifact and its run metadata from GitHub.
"""
import argparse
import hashlib
import json
import os
import re
import stat
import tarfile
from datetime import datetime
from pathlib import Path
from pathlib import PurePosixPath

ORIGIN = 'https://repo.collie.im/'
REPO = '0cwa/collie-release-engineering'
WORKFLOW = '.github/workflows/pages-release.yml'
ARTIFACT = 'collie-pages-completed-receipt'
NATIVE_PREFIX = 'native-inputs/v1/maven/'
NATIVE_REPOSITORY = ORIGIN + NATIVE_PREFIX
FIRST_PUBLICATION_RECOVERY = 'owner-authorized-first-publication-unverified-origin'
CACHED_NEGATIVE = 'cached_negative_unverified_origin'
NEGATIVE_UNVERIFIED = 'negative_unverified_origin'
EXACT_CONTENT_UNVERIFIED = 'exact_content_match_unverified_origin'
CACHE_HEADER_FIELDS = {'Cache-Control', 'ETag', 'Last-Modified', 'Age', 'X-Cache'}
MAX_OPERATION_INVENTORY_AGE_SECONDS = 300
MAX_BYTES = 950_000_000
STATIC = {
    'assets/collie-icon.svg', 'assets/manrope-OFL.txt', 'assets/manrope.ttf',
    'assets/site.css', 'assets/site.js', 'assets/sora-OFL.txt', 'assets/sora.ttf',
}
CANONICAL = STATIC | {'index.html', 'site-manifest.json'}
HEX40 = re.compile(r'[0-9a-f]{40}\Z')
HEX64 = re.compile(r'[0-9a-f]{64}\Z')


def unique(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError('duplicate JSON field: ' + key)
        out[key] = value
    return out


def load(path):
    return json.loads(Path(path).read_bytes(), object_pairs_hook=unique)


def verify_run_directory(directory, dispatch_inputs):
    if (not isinstance(dispatch_inputs, dict) or type(dispatch_inputs.get('run_id')) is not int
            or dispatch_inputs['run_id'] <= 0
            or Path(directory).name != 'run-' + str(dispatch_inputs['run_id'])):
        raise ValueError('retained evidence directory does not match dispatched run ID')


def load_run_evidence(directory):
    """Load the retained per-run files using their actual controller/API names."""
    directory = Path(directory)
    if not directory.is_dir() or directory.is_symlink():
        raise ValueError('retained per-run evidence directory required')
    names = (
        'actual-approval.json', 'actual-dispatch-inputs.json', 'published-release-asset.json',
        'controller-at-dispatch.sh', 'prepare-safe-log.txt', 'run.json', 'jobs.json',
        'artifact-metadata.json', 'control/plan.json', 'control/journal.json',
        'pre-dispatch-inventory.json',
    )
    expected_hashes = {}
    for line in (directory / 'evidence-sha256.txt').read_text().splitlines():
        fields = line.split('  ', 1)
        if len(fields) != 2 or not HEX64.fullmatch(fields[0]):
            raise ValueError('retained evidence checksum index is malformed')
        relative = fields[1]
        if PurePosixPath(relative).is_absolute() or '..' in PurePosixPath(relative).parts or relative in expected_hashes:
            raise ValueError('retained evidence checksum path is invalid or duplicated')
        expected_hashes[relative] = fields[0]
    contents = {}
    for relative in names:
        path = directory / relative
        if not path.is_file() or path.is_symlink():
            raise ValueError('required retained run evidence is missing: ' + relative)
        raw = path.read_bytes()
        if expected_hashes.get(relative) != sha(raw):
            raise ValueError('retained run evidence digest differs: ' + relative)
        contents[relative] = raw
    dispatch_inputs = json.loads(contents['actual-dispatch-inputs.json'], object_pairs_hook=unique)
    verify_run_directory(directory, dispatch_inputs)
    return {
        'approval_raw': contents['actual-approval.json'],
        'dispatch_inputs': dispatch_inputs,
        'release_metadata': json.loads(contents['published-release-asset.json'], object_pairs_hook=unique),
        'controller_raw': contents['controller-at-dispatch.sh'],
        'prepare_log_raw': contents['prepare-safe-log.txt'],
        'run': json.loads(contents['run.json'], object_pairs_hook=unique),
        'jobs': json.loads(contents['jobs.json'], object_pairs_hook=unique),
        'artifact_metadata': json.loads(contents['artifact-metadata.json'], object_pairs_hook=unique),
        'plan': json.loads(contents['control/plan.json'], object_pairs_hook=unique),
        'journal': json.loads(contents['control/journal.json'], object_pairs_hook=unique),
        'pre_dispatch_inventory': json.loads(contents['pre-dispatch-inventory.json'], object_pairs_hook=unique),
        'evidence_index_sha256': sha((directory / 'evidence-sha256.txt').read_bytes()),
    }


def canonical_bytes(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def digest(value):
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def inspect_input_bundle(path):
    """Hash the retained archive and read its approval through the same open file."""
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, 'rb') as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= 2 * MAX_BYTES:
            raise ValueError('retained input bundle must be a regular file')
        h = hashlib.sha256()
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
        stream.seek(0)
        with tarfile.open(fileobj=stream, mode='r:*') as archive:
            matches = [member for member in archive.getmembers() if member.name == 'approval.json']
            if (len(matches) != 1 or not matches[0].isfile() or matches[0].issym()
                    or matches[0].islnk() or matches[0].size > 16 * 1024):
                raise ValueError('input bundle must contain one small regular approval.json')
            member_stream = archive.extractfile(matches[0])
            if member_stream is None:
                raise ValueError('input bundle approval.json is unreadable')
            approval_raw = member_stream.read(16 * 1024 + 1)
            if len(approval_raw) != matches[0].size or len(approval_raw) > 16 * 1024:
                raise ValueError('input bundle approval.json size differs')
        after = os.fstat(stream.fileno())
        if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
            raise ValueError('retained input bundle changed during inspection')
    return h.hexdigest(), before.st_size, approval_raw


def exact(value, fields, label):
    if not isinstance(value, dict) or set(value) != set(fields):
        raise ValueError(label + ' fields differ')


def valid_file_entry(value, label):
    exact(value, {'sha256', 'bytes'}, label)
    if not isinstance(value['sha256'], str) or not HEX64.fullmatch(value['sha256']):
        raise ValueError(label + ' sha256 invalid')
    if type(value['bytes']) is not int or value['bytes'] <= 0:
        raise ValueError(label + ' byte count invalid')


def native_map(native_manifest, source_native_artifacts):
    exact(native_manifest, {'schema', 'kind', 'origin', 'files'}, 'native manifest')
    if (type(native_manifest['schema']) is not int or native_manifest['schema'] != 1
            or native_manifest['kind'] != 'collie-pages-native-v1'
            or native_manifest['origin'] != ORIGIN or not isinstance(native_manifest['files'], dict)):
        raise ValueError('native manifest identity differs')
    expected = {}
    for row in source_native_artifacts:
        exact(row, {'path', 'url', 'sha256', 'bytes'}, 'source native artifact')
        source_path = row['path']
        if not isinstance(source_path, str) or not source_path:
            raise ValueError('source native path invalid or duplicated')
        relative = PurePosixPath(source_path)
        if (relative.is_absolute() or '..' in relative.parts or '.' in relative.parts
                or relative.as_posix() != source_path):
            raise ValueError('source native path invalid or duplicated')
        path = source_path if source_path.startswith(NATIVE_PREFIX) else NATIVE_PREFIX + source_path
        if path in expected:
            raise ValueError('source native path invalid or duplicated')
        if row['url'] != ORIGIN + path:
            raise ValueError('source native URL differs')
        valid_file_entry({'sha256': row['sha256'], 'bytes': row['bytes']}, 'source native artifact')
        expected[path] = {'sha256': row['sha256'], 'bytes': row['bytes']}
    if len(expected) != 6 or set(native_manifest['files']) != set(expected):
        raise ValueError('exact six source native files required')
    for path, entry in native_manifest['files'].items():
        valid_file_entry(entry, 'native manifest entry')
        if entry != expected[path]:
            raise ValueError('native manifest differs from reviewed source pins: ' + path)
    return expected


def cache_headers(value, label):
    exact(value, CACHE_HEADER_FIELDS, label)
    for key, item in value.items():
        if item is not None and not isinstance(item, str):
            raise ValueError(label + ' values must be strings or null')
    return value


def fresh_canonical_headers(headers):
    age = headers['Age']
    if age is not None:
        try:
            if int(age) != 0:
                return False
        except (TypeError, ValueError):
            return False
    return not re.search(r'\bhit\b', headers['X-Cache'] or '', re.IGNORECASE)


def verify_terminal_operation_inventory(runs, deployments, expected_run_ids, expected_deployment_ids):
    if not isinstance(runs, list) or not isinstance(deployments, list):
        raise ValueError('pre-dispatch operation inventory lists are required')
    if not isinstance(expected_run_ids, list) or not isinstance(expected_deployment_ids, list):
        raise ValueError('expected operation ID lists are required')
    if not expected_run_ids or not expected_deployment_ids:
        raise ValueError('known operation IDs are required')
    all_ids = (*expected_run_ids, *expected_deployment_ids)
    if any(type(value) is not int or value <= 0 for value in all_ids):
        raise ValueError('known operation IDs are invalid')
    if (len(set(expected_run_ids)) != len(expected_run_ids)
            or len(set(expected_deployment_ids)) != len(expected_deployment_ids)):
        raise ValueError('known operation IDs are duplicated')
    for run in runs:
        exact(run, {'id', 'status', 'conclusion'}, 'pre-dispatch workflow run')
    for deployment in deployments:
        exact(deployment, {'id', 'state'}, 'pre-dispatch deployment')
    run_ids = [row['id'] for row in runs]
    deployment_ids = [row['id'] for row in deployments]
    if (any(type(value) is not int or value <= 0 for value in (*run_ids, *deployment_ids))
            or len(set(run_ids)) != len(run_ids) or len(set(deployment_ids)) != len(deployment_ids)
            or set(run_ids) != set(expected_run_ids)
            or set(deployment_ids) != set(expected_deployment_ids)):
        raise ValueError('unexpected or missing workflow run/deployment')
    if any(row['status'] != 'completed' or row['conclusion'] != 'failure' for row in runs):
        raise ValueError('workflow run is active or not a known terminal failure')
    if any(row['state'] not in ('failure', 'success') for row in deployments):
        raise ValueError('deployment is active or not a known terminal state')
    return True


def validate_recovery_precondition(plan, journal, canonical_paths, native_paths):
    exact(journal, {'schema', 'plan_sha256', 'completed', 'previous_verified', 'readbacks',
                    'previous_state_classification', 'previous_state'}, 'recovery Pages journal')
    if (journal['previous_verified'] is not False
            or journal['previous_state_classification'] != FIRST_PUBLICATION_RECOVERY
            or 'previous_readback' in journal):
        raise ValueError('owner-authorized empty recovery classification required; do not claim previous verification')
    state = journal['previous_state']
    exact(state, {'plan_sha256', 'input_approval_sha256', 'observations'}, 'recovery previous state')
    if (state['plan_sha256'] != digest(plan)
            or state['input_approval_sha256'] != plan['input_approval_sha256']):
        raise ValueError('recovery plan/approval provenance differs')
    expected_observations = {'site-manifest.json', 'fdroid/repo/entry.jar'} | native_paths
    observations = state['observations']
    if not isinstance(observations, dict) or set(observations) != expected_observations:
        raise ValueError('recovery observations do not cover the exact two sentinels and six native paths')
    for path, actual in observations.items():
        if not isinstance(actual, dict) or type(actual.get('status')) is not int:
            raise ValueError('recovery observation status invalid: ' + path)
        status = actual['status']
        if status == 404:
            exact(actual, {'status', 'headers', 'classification'}, 'recovery 404 ' + path)
            headers = cache_headers(actual['headers'], 'recovery 404 headers ' + path)
            expected_class = CACHED_NEGATIVE if not fresh_canonical_headers(headers) else NEGATIVE_UNVERIFIED
            if actual['classification'] != expected_class:
                raise ValueError('recovery negative classification disagrees with recorded cache headers: ' + path)
        elif status == 200 and path in canonical_paths:
            exact(actual, {'status', 'sha256', 'bytes', 'headers', 'classification'}, 'recovery 200 ' + path)
            cache_headers(actual['headers'], 'recovery 200 headers ' + path)
            expected = plan['manifests']['phase1'][path]
            if (actual['classification'] != EXACT_CONTENT_UNVERIFIED
                    or actual['sha256'] != expected['sha256']
                    or type(actual['bytes']) is not int or actual['bytes'] != expected['bytes']):
                raise ValueError('recovery exact-content observation differs from the approved plan: ' + path)
        else:
            raise ValueError('recovery observation has an unapproved status or path: ' + path)





def validate_retained_dispatch(evidence, expected_policy, bundle_digest, bundle_size, plan):
    """Bind real controller/API records; GitHub's workflow-run API has no inputs."""
    run = evidence['run']
    exact(run, {'id', 'status', 'conclusion', 'event', 'head_sha', 'run_attempt',
                'created_at', 'updated_at', 'path', 'workflow_id'},
          'GitHub workflow run record')
    dispatch = evidence['dispatch_inputs']
    dispatch_fields = {'run_id', 'run_attempt', 'run_created_at', 'policy_revision',
                       'repository', 'workflow_ref', 'dispatch_event',
                       'dispatch_inputs_as_passed_by_executed_controller',
                       'exact_command_template', 'value_binding', 'release',
                       'candidate_approval_member', 'bundle_sha256', 'bundle_bytes',
                       'controller_sha256_at_dispatch', 'provider_event_payload_exposes_inputs'}
    if set(dispatch) not in (dispatch_fields, dispatch_fields | {'plan_sha256'}):
        raise ValueError('retained dispatch record fields differ')
    values = dispatch['dispatch_inputs_as_passed_by_executed_controller']
    exact(values, {'preparation_mode', 'trusted_policy_revision', 'bundle_asset_id', 'bundle_sha256'},
          'controller-recorded dispatch values')
    release_binding = dispatch['release']
    exact(release_binding, {'id', 'tag', 'target_commitish', 'asset_id', 'asset_name',
                            'asset_bytes', 'asset_digest', 'asset_state'}, 'dispatch release binding')
    approval_binding = dispatch['candidate_approval_member']
    exact(approval_binding, {'path', 'bytes', 'sha256'}, 'dispatch approval binding')
    release = evidence['release_metadata']
    if (not isinstance(release, dict) or not isinstance(release.get('assets'), list)
            or len(release['assets']) != 1):
        raise ValueError('single published release asset metadata required')
    asset = release['assets'][0]
    asset_id = asset.get('id')
    expected_tag = 'collie-pages-native-v1-' + expected_policy[:7]
    if (release.get('id') != release_binding['id'] or release.get('tag_name') != expected_tag
            or release.get('target_commitish') != expected_policy or release.get('draft') is not False
            or release.get('prerelease') is not True or asset_id != release_binding['asset_id']
            or asset.get('name') != 'input.tar' or type(asset.get('size')) is not int
            or asset.get('size') != bundle_size or asset.get('digest') != 'sha256:' + bundle_digest
            or asset.get('state') != 'uploaded'):
        raise ValueError('published release metadata differs from retained bundle')
    if (type(asset_id) is not int or asset_id <= 0
            or release_binding['asset_name'] != asset['name']
            or release_binding['asset_bytes'] != asset['size']
            or release_binding['asset_digest'] != asset['digest']
            or release_binding['asset_state'] != asset['state']):
        raise ValueError('controller asset binding differs from published release metadata')
    if (dispatch['repository'] != REPO or dispatch['workflow_ref'] != 'refs/heads/main'
            or dispatch['dispatch_event'] != 'workflow_dispatch'
            or dispatch['provider_event_payload_exposes_inputs'] is not False
            or values['preparation_mode'] != 'feed'
            or values['trusted_policy_revision'] != expected_policy
            or values['bundle_asset_id'] != str(asset_id)
            or values['bundle_sha256'] != bundle_digest
            or dispatch['bundle_sha256'] != bundle_digest or dispatch['bundle_bytes'] != bundle_size):
        raise ValueError('recorded dispatch values differ from policy, plan, or asset')
    if 'plan_sha256' in dispatch and dispatch['plan_sha256'] != digest(plan):
        raise ValueError('optional retained plan binding differs from the completed control artifact')
    if (type(dispatch['run_id']) is not int or dispatch['run_id'] != run['id']
            or type(dispatch['run_attempt']) is not int or dispatch['run_attempt'] != run['run_attempt']
            or dispatch['run_created_at'] != run['created_at']
            or dispatch['policy_revision'] != expected_policy or run['head_sha'] != expected_policy
            or run['event'] != 'workflow_dispatch' or run['path'] != WORKFLOW
            or run['status'] != 'completed' or run['conclusion'] != 'success'):
        raise ValueError('dispatch run ID/attempt/policy differs from GitHub run record')
    approval_raw = evidence['approval_raw']
    if (approval_binding['path'] != 'actual-approval.json'
            or type(approval_binding['bytes']) is not int or approval_binding['bytes'] != len(approval_raw)
            or approval_binding['sha256'] != sha(approval_raw)):
        raise ValueError('dispatch approval-member identity differs from retained approval JSON')
    controller = evidence['controller_raw']
    prepare_log = evidence['prepare_log_raw']
    if (len(controller) > 256 * 1024 or len(prepare_log) > 1024 * 1024
            or dispatch['controller_sha256_at_dispatch'] != sha(controller)):
        raise ValueError('controller source digest or retained prepare log is invalid')
    text = controller.decode('utf-8')
    template = ('gh workflow run pages-release.yml --repo 0cwa/collie-release-engineering --ref main '
                '-f preparation_mode=feed -f trusted_policy_revision="$POLICY" '
                '-f bundle_asset_id="$ASSET_ID" -f bundle_sha256="$BUNDLE_SHA"')
    required_controller = (
        'readonly POLICY="$(git -C "$WORKTREE" rev-parse HEAD)"',
        'readonly BUNDLE_SHA="$APPROVED_BUNDLE_SHA256"',
        '[[ "$actual_main" == "$POLICY" ]]',
        'select(.tag_name==$tag and .target_commitish==$policy and .draft==false and .prerelease==true and (.assets|length)==1)',
        'select(.name=="input.tar" and .size==310855680 and .digest==("sha256:"+$sha) and .state=="uploaded")',
        'sha256sum "$INPUT"', 'gh workflow run pages-release.yml', 'preparation_mode=feed',
        'trusted_policy_revision="$POLICY"', 'bundle_asset_id="$ASSET_ID"',
        'bundle_sha256="$BUNDLE_SHA"', 'echo "DISPATCH_RUN_ID=$run_id"',
    )
    expected_value_binding = ('POLICY was git rev-parse HEAD checked against remote main; ASSET_ID was selected '
                              'from the exact release tag only after matching asset name/size/API digest; '
                              'BUNDLE_SHA was the exact APPROVED_BUNDLE_SHA256 value passed to this controller invocation.')
    if (dispatch['exact_command_template'] != template
            or not ('--repo "$REPO"' in text or f'--repo {REPO}' in text)
            or not all(fragment in text for fragment in required_controller)
            or dispatch['value_binding'] != expected_value_binding):
        raise ValueError('retained controller source does not bind the recorded dispatch inputs')
    log_text = re.sub(rb'\x1b\[[0-9;]*m', b'', prepare_log).decode('utf-8').replace('\ufeff', '')
    for line in (f'ASSET_ID: {asset_id}', f'EXPECTED_SHA256: {bundle_digest}',
                 f'APPROVED_POLICY: {expected_policy}', 'PREPARATION_MODE: feed',
                 'python distribution/download_pages_input.py --asset-id "$ASSET_ID" --sha256 "$EXPECTED_SHA256"',
                 'python distribution/pages.py unpack "$RUNNER_TEMP/input.tar" "$RUNNER_TEMP/inputs"',
                 'python distribution/pages.py prepare "$RUNNER_TEMP/inputs" "$RUNNER_TEMP/pages" --policy "$APPROVED_POLICY"',
                 'cp "$RUNNER_TEMP/pages/plan.json" "$RUNNER_TEMP/pages/journal.json" "$RUNNER_TEMP/control/"'):
        if line not in log_text:
            raise ValueError('prepare log does not confirm dispatched input/command: ' + line.split(':')[0])

    jobs = evidence['jobs']
    expected_jobs = {'prepare', 'phase1', 'phase1-readback', 'phase2', 'final-readback'}
    if not isinstance(jobs, list):
        raise ValueError('run-scoped job records required')
    by_name = {}
    for job in jobs:
        exact(job, {'id', 'name', 'status', 'conclusion', 'startedAt', 'completedAt'}, 'run job record')
        if (job['name'] in by_name or job['name'] not in expected_jobs
                or type(job['id']) is not int or job['id'] <= 0
                or job['status'] != 'completed' or job['conclusion'] != 'success'):
            raise ValueError('all five exact-run phase/readback jobs must succeed')
        by_name[job['name']] = job
    if set(by_name) != expected_jobs:
        raise ValueError('complete five-job publication workflow evidence required')
    artifact_data = evidence['artifact_metadata']
    if not isinstance(artifact_data, dict) or set(artifact_data) != {'artifacts'} or not isinstance(artifact_data['artifacts'], list):
        raise ValueError('run-scoped artifact API metadata required')
    receipts = [a for a in artifact_data['artifacts'] if isinstance(a, dict) and a.get('name') == ARTIFACT]
    controls = [a for a in artifact_data['artifacts'] if isinstance(a, dict) and a.get('name') == 'collie-pages-control']
    if len(receipts) != 1 or len(controls) != 1:
        raise ValueError('successful completed-receipt and control artifacts required')
    receipt = receipts[0]
    control = controls[0]
    if (type(receipt.get('id')) is not int or receipt['id'] <= 0
            or receipt.get('expired') is not False or type(receipt.get('size_in_bytes')) is not int):
        raise ValueError('completed receipt artifact metadata invalid')
    if (type(control.get('id')) is not int or control['id'] <= 0
            or control.get('expired') is not False or type(control.get('size_in_bytes')) is not int
            or control['size_in_bytes'] <= 0):
        raise ValueError('retained control artifact metadata invalid')

    inventory = evidence['pre_dispatch_inventory']
    exact(inventory, {'captured_at', 'runs', 'deployments', 'expected_run_ids', 'expected_deployment_ids'},
          'pre-dispatch operation inventory')
    run_identity = {
        'repository': REPO, 'workflow_path': WORKFLOW, 'run_id': run['id'],
        'run_attempt': run['run_attempt'], 'head_sha': run['head_sha'], 'event': run['event'],
        'status': run['status'], 'conclusion': run['conclusion'],
        'attempt_started_at': run['created_at'], 'attempt_completed_at': run['updated_at'],
        'prepare_job': {'id': by_name['prepare']['id'], 'name': 'prepare',
                        'status': by_name['prepare']['status'], 'conclusion': by_name['prepare']['conclusion'],
                        'started_at': by_name['prepare']['startedAt'], 'completed_at': by_name['prepare']['completedAt']},
        'final_readback_job': {'name': 'final-readback', 'status': by_name['final-readback']['status'],
                               'conclusion': by_name['final-readback']['conclusion'],
                               'started_at': by_name['final-readback']['startedAt'],
                               'completed_at': by_name['final-readback']['completedAt']},
        'artifact': {'id': receipt['id'], 'name': ARTIFACT, 'expired': receipt['expired'],
                     'workflow_run_id': run['id'], 'head_sha': run['head_sha'],
                     'created_at': receipt['created_at']},
        'pre_dispatch_inventory': inventory,
    }
    binding = {
        'input_approval_sha256': digest(json.loads(approval_raw, object_pairs_hook=unique)),
        'input_asset_id': asset_id, 'input_bundle_sha256': bundle_digest,
        'dispatch_binding_sha256': digest({
            'dispatch_inputs_sha256': digest(dispatch), 'release_metadata_sha256': digest(release),
            'controller_sha256': sha(controller), 'prepare_log_sha256': sha(prepare_log),
            'evidence_index_sha256': evidence['evidence_index_sha256'],
        }),
        'workflow_metadata_sha256': digest({'run': run, 'jobs': jobs, 'artifacts': artifact_data}),
    }
    return run_identity, binding


def validate_retained_evidence(evidence, native_manifest_raw, source_native_artifacts,
                               expected_policy, input_bundle_path):
    if sha(evidence['approval_raw']) != evidence['dispatch_inputs']['candidate_approval_member'].get('sha256'):
        raise ValueError('retained approval member digest differs')
    bundle_sha256, bundle_size, embedded = inspect_input_bundle(input_bundle_path)
    if embedded != evidence['approval_raw']:
        raise ValueError('retained approval is not the input.tar approval.json member')
    run, binding = validate_retained_dispatch(
        evidence, expected_policy, bundle_sha256, bundle_size, evidence['plan'])
    run['retained_binding'] = binding
    return _validate_core(evidence['plan'], evidence['journal'], native_manifest_raw,
                    source_native_artifacts, run, expected_policy, evidence['approval_raw'],
                    input_bundle_path, binding)


def validate(plan, journal, native_manifest_raw, source_native_artifacts, proof, expected_policy):
    """Rebind a locally validated retained-run proof to final composition inputs.

    Full dispatch, API, artifact, snapshot, approval and bundle checks are done
    by ``validate_retained_evidence`` before this proof is produced. This
    composition step rechecks proof integrity and the exact plan, journal,
    native manifest and response-body inventory. It is local consistency, not
    a cryptographic GitHub attestation.
    """
    blocked_state = {
        'schema': 1, 'status': 'BLOCKED', 'repository': NATIVE_REPOSITORY,
        'publication_status': 'NOT PUBLISHED', 'reason': 'composition proof check',
        'lock': {}, 'production_version_floor': None,
    }
    transition_native_state(json.dumps(blocked_state), proof)
    if (not isinstance(expected_policy, str) or not HEX40.fullmatch(expected_policy)
            or proof['policy_revision'] != expected_policy):
        raise ValueError('composition proof does not match the exact reviewed policy revision')
    if (proof['plan_sha256'] != sha(canonical_bytes(plan))
            or proof['journal_sha256'] != sha(canonical_bytes(journal))
            or proof['native_manifest_sha256'] != sha(native_manifest_raw)
            or proof['input_approval_sha256'] != plan.get('input_approval_sha256')):
        raise ValueError('composition proof differs from the completed receipt or native manifest')
    exact(plan, {'schema', 'kind', 'input_approval_sha256', 'origin', 'policy_revision', 'mode',
                 'native_manifest_sha256', 'repository_receipt_sha256',
                 'repository_certificate_sha256', 'candidate_generation_sha256',
                 'maximum_bytes', 'previous_manifest_sha256', 'previous_site_sha256',
                 'previous_files', 'manifests', 'artifacts'}, 'Pages plan')
    if (type(plan['schema']) is not int or plan['schema'] != 1
            or plan['kind'] != 'collie-pages-promotion' or plan['origin'] != ORIGIN
            or plan['mode'] != 'native-only' or plan['policy_revision'] != expected_policy
            or type(plan['maximum_bytes']) is not int or plan['maximum_bytes'] != MAX_BYTES
            or plan['previous_manifest_sha256'] is not None or plan['previous_files'] != {}
            or plan['previous_site_sha256'] != digest({})
            or any(plan[key] is not None for key in ('repository_receipt_sha256',
                                                       'repository_certificate_sha256',
                                                       'candidate_generation_sha256'))):
        raise ValueError('completed native-only Pages plan differs from the reviewed policy')
    native_manifest = json.loads(native_manifest_raw, object_pairs_hook=unique)
    native = native_map(native_manifest, source_native_artifacts)
    if (plan['native_manifest_sha256'] != sha(native_manifest_raw)
            or not isinstance(plan['manifests'], dict)
            or set(plan['manifests']) != {'phase1', 'phase2'}):
        raise ValueError('composition native manifest or phase inventory differs')
    canonical_paths = CANONICAL | set(native)
    for phase in ('phase1', 'phase2'):
        inventory = plan['manifests'][phase]
        if not isinstance(inventory, dict) or set(inventory) != canonical_paths or len(inventory) != 15:
            raise ValueError(phase + ' must contain exactly fifteen canonical paths')
        for path, entry in native.items():
            if inventory[path] != entry:
                raise ValueError(phase + ' native hashes differ from source pins: ' + path)
    if plan['manifests']['phase1'] != plan['manifests']['phase2']:
        raise ValueError('native-only phase inventories differ')
    exact(journal, {'schema', 'plan_sha256', 'completed', 'previous_verified', 'readbacks',
                    'previous_state_classification', 'previous_state'}, 'Pages journal')
    if (type(journal['schema']) is not int or journal['schema'] != 1
            or journal['plan_sha256'] != digest(plan)
            or journal['completed'] != ['phase1', 'phase2']):
        raise ValueError('completed receipt journal does not bind the exact two-phase plan')
    validate_recovery_precondition(plan, journal, canonical_paths, set(native))
    if not isinstance(journal['readbacks'], dict) or set(journal['readbacks']) != {'phase1', 'phase2'}:
        raise ValueError('both completed readback phases are required')
    for phase in ('phase1', 'phase2'):
        observed = journal['readbacks'][phase]
        if not isinstance(observed, dict) or set(observed) != canonical_paths or len(observed) != 15:
            raise ValueError(phase + ' readback must cover all fifteen canonical paths')
        for path, entry in plan['manifests'][phase].items():
            actual = observed[path]
            exact(actual, {'status', 'sha256', 'bytes', 'headers'}, phase + ' readback ' + path)
            cache_headers(actual['headers'], phase + ' readback headers ' + path)
            if (type(actual['status']) is not int or actual['status'] != 200
                    or actual['sha256'] != entry['sha256']
                    or type(actual['bytes']) is not int or actual['bytes'] != entry['bytes']):
                raise ValueError(phase + ' readback differs from the approved body: ' + path)
    return proof

def _validate_core(plan, journal, native_manifest_raw, source_native_artifacts, run, expected_policy,
                   approval_raw, input_bundle_path, retained_binding):
    if not isinstance(expected_policy, str) or not HEX40.fullmatch(expected_policy):
        raise ValueError('expected policy revision must be a full Git SHA')
    exact(plan, {'schema', 'kind', 'input_approval_sha256', 'origin', 'policy_revision', 'mode',
                 'native_manifest_sha256', 'repository_receipt_sha256',
                 'repository_certificate_sha256', 'candidate_generation_sha256',
                 'maximum_bytes', 'previous_manifest_sha256', 'previous_site_sha256',
                 'previous_files', 'manifests', 'artifacts'}, 'Pages plan')
    if type(plan['schema']) is not int or plan['schema'] != 1 or plan['kind'] != 'collie-pages-promotion':
        raise ValueError('Pages plan schema/kind differs')
    if plan['origin'] != ORIGIN or plan['mode'] != 'native-only':
        raise ValueError('native-only Pages plan required')
    if plan['policy_revision'] != expected_policy:
        raise ValueError('Pages plan does not match the exact reviewed policy revision')
    if any(plan[k] is not None for k in ('repository_receipt_sha256', 'repository_certificate_sha256', 'candidate_generation_sha256')):
        raise ValueError('native-only plan contains unrelated feed evidence')
    if type(plan['maximum_bytes']) is not int or plan['maximum_bytes'] != MAX_BYTES:
        raise ValueError('native-only Pages budget differs from the reviewed policy')
    if (not isinstance(plan['input_approval_sha256'], str)
            or not HEX64.fullmatch(plan['input_approval_sha256'])):
        raise ValueError('Pages input approval digest invalid')
    if (plan['previous_manifest_sha256'] is not None or plan['previous_files'] != {}
            or plan['previous_site_sha256'] != digest({})):
        raise ValueError('native first-publication plan does not bind an empty prior site')
    native_manifest = json.loads(native_manifest_raw, object_pairs_hook=unique)
    native = native_map(native_manifest, source_native_artifacts)
    if plan['native_manifest_sha256'] != sha(native_manifest_raw):
        raise ValueError('Pages plan native manifest digest differs')
    input_bundle_sha256, input_bundle_size, embedded_approval = inspect_input_bundle(input_bundle_path)
    if embedded_approval != approval_raw:
        raise ValueError('approval input is not the approval.json member of the dispatched bundle')
    approval = json.loads(approval_raw, object_pairs_hook=unique)
    exact(approval, {'schema', 'kind', 'origin', 'previous_site_sha256',
                     'previous_manifest_sha256', 'native_manifest_sha256',
                     'maximum_bytes', 'policy_revision'}, 'native input approval')
    if (type(approval['schema']) is not int or approval['schema'] != 1
            or approval['kind'] != 'collie-pages-native-input'
            or approval['origin'] != ORIGIN or approval['policy_revision'] != expected_policy
            or approval['previous_site_sha256'] != digest({})
            or approval['previous_manifest_sha256'] is not None
            or approval['native_manifest_sha256'] != plan['native_manifest_sha256']
            or type(approval['maximum_bytes']) is not int or approval['maximum_bytes'] != MAX_BYTES
            or digest(approval) != plan['input_approval_sha256']):
        raise ValueError('native input approval semantics or canonical digest differs from the plan')
    if not isinstance(plan['manifests'], dict) or set(plan['manifests']) != {'phase1', 'phase2'}:
        raise ValueError('Pages phase manifests missing')
    canonical_paths = CANONICAL | set(native)
    if len(canonical_paths) != 15:
        raise ValueError('canonical native Pages inventory is not fifteen paths')
    for phase in ('phase1', 'phase2'):
        inventory = plan['manifests'][phase]
        if not isinstance(inventory, dict) or set(inventory) != canonical_paths or len(inventory) != 15:
            raise ValueError(phase + ' must contain the exact fifteen canonical paths')
        for path in canonical_paths:
            valid_file_entry(inventory[path], phase + ' entry ' + path)
        for path, entry in native.items():
            if inventory[path] != entry:
                raise ValueError(phase + ' native bytes differ from the six pinned files: ' + path)
    if plan['manifests']['phase1'] != plan['manifests']['phase2']:
        raise ValueError('native-only phase inventories differ')
    exact(plan['artifacts'], {'phase1', 'phase2'}, 'Pages archives')
    for phase, artifact in plan['artifacts'].items():
        exact(artifact, {'sha256', 'bytes'}, phase + ' archive')
        valid_file_entry(artifact, phase + ' archive')
    if plan['artifacts']['phase1'] != plan['artifacts']['phase2']:
        raise ValueError('native-only phase artifacts differ')

    if (not isinstance(retained_binding, dict)
            or set(retained_binding) != {'input_approval_sha256', 'input_asset_id', 'input_bundle_sha256',
                                         'dispatch_binding_sha256', 'workflow_metadata_sha256'}):
        raise ValueError('validated retained dispatch binding is required')
    if (retained_binding['input_bundle_sha256'] != input_bundle_sha256
            or retained_binding['input_approval_sha256'] != digest(approval)
            or type(retained_binding['input_asset_id']) is not int or retained_binding['input_asset_id'] <= 0
            or any(not isinstance(retained_binding[key], str) or not HEX64.fullmatch(retained_binding[key])
                   for key in ('dispatch_binding_sha256', 'workflow_metadata_sha256'))):
        raise ValueError('retained dispatch binding differs from approved bundle or approval')
    input_bundle_sha256 = retained_binding['input_bundle_sha256']
    input_asset_id = retained_binding['input_asset_id']
    dispatch_binding_sha256 = retained_binding['dispatch_binding_sha256']
    if (run['repository'] != REPO or run['workflow_path'] != WORKFLOW
            or run['event'] != 'workflow_dispatch' or run['status'] != 'completed'
            or run['conclusion'] != 'success' or run['head_sha'] != expected_policy):
        raise ValueError('completed successful exact-policy Pages run required')
    def timestamp(value, label):
        if not isinstance(value, str): raise ValueError(label + ' timestamp missing')
        try: result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError as error: raise ValueError(label + ' timestamp invalid') from error
        if result.tzinfo is None: raise ValueError(label + ' timestamp must include a timezone')
        return result
    attempt_start = timestamp(run['attempt_started_at'], 'attempt start')
    attempt_end = timestamp(run['attempt_completed_at'], 'attempt end')
    inventory = run['pre_dispatch_inventory']
    exact(inventory, {'captured_at', 'runs', 'deployments', 'expected_run_ids', 'expected_deployment_ids'},
          'pre-dispatch operation inventory')
    inventory_captured = timestamp(inventory['captured_at'], 'pre-dispatch operation inventory')
    inventory_age = (attempt_start - inventory_captured).total_seconds()
    if not 0 <= inventory_age <= MAX_OPERATION_INVENTORY_AGE_SECONDS:
        raise ValueError('pre-dispatch operation inventory is not fresh for this run attempt')
    verify_terminal_operation_inventory(inventory['runs'], inventory['deployments'],
                                        inventory['expected_run_ids'], inventory['expected_deployment_ids'])
    operation_inventory_sha256 = sha(canonical_bytes(inventory))

    exact(run['prepare_job'], {'id', 'name', 'status', 'conclusion', 'started_at', 'completed_at'},
          'input preparation job')
    prepare_job = run['prepare_job']
    if (type(prepare_job['id']) is not int or prepare_job['id'] <= 0
            or prepare_job['name'] != 'prepare' or prepare_job['status'] != 'completed'
            or prepare_job['conclusion'] != 'success'):
        raise ValueError('successful exact-run input preparation job required')
    prepare_start = timestamp(prepare_job['started_at'], 'input preparation start')
    prepare_end = timestamp(prepare_job['completed_at'], 'input preparation end')
    if not attempt_start <= prepare_start <= prepare_end <= attempt_end:
        raise ValueError('input preparation job does not belong to the dispatched run attempt')

    exact(run['final_readback_job'], {'name', 'status', 'conclusion', 'started_at', 'completed_at'}, 'final readback job')
    job = run['final_readback_job']
    if job['name'] != 'final-readback' or job['status'] != 'completed' or job['conclusion'] != 'success':
        raise ValueError('successful exact-attempt final readback job required')
    job_start = timestamp(job['started_at'], 'final readback start')
    job_end = timestamp(job['completed_at'], 'final readback end')
    if not attempt_start <= job_start <= job_end <= attempt_end:
        raise ValueError('final readback job does not belong to the successful attempt interval')
    if (type(run['run_id']) is not int or run['run_id'] <= 0
            or type(run['run_attempt']) is not int or run['run_attempt'] <= 0):
        raise ValueError('workflow run identity invalid')
    exact(run['artifact'], {'id', 'name', 'expired', 'workflow_run_id', 'head_sha', 'created_at'}, 'receipt artifact metadata')
    if (type(run['artifact']['id']) is not int or run['artifact']['id'] <= 0
            or run['artifact']['name'] != ARTIFACT or run['artifact']['expired'] is not False
            or run['artifact']['workflow_run_id'] != run['run_id']
            or run['artifact']['head_sha'] != expected_policy):
        raise ValueError('completed receipt artifact is not bound to the exact successful run')
    artifact_created = timestamp(run['artifact']['created_at'], 'receipt artifact creation')
    if not job_start <= artifact_created <= job_end:
        raise ValueError('receipt artifact was not uploaded by the exact successful final readback attempt')

    exact(journal, {'schema', 'plan_sha256', 'completed', 'previous_verified', 'readbacks',
                    'previous_state_classification', 'previous_state'}, 'Pages journal')
    if type(journal['schema']) is not int or journal['schema'] != 1 or journal['plan_sha256'] != digest(plan):
        raise ValueError('Pages journal schema or plan binding differs')
    if journal['completed'] != ['phase1', 'phase2']:
        raise ValueError('both ordered phase readbacks must be complete')
    validate_recovery_precondition(plan, journal, canonical_paths, set(native))
    if not isinstance(journal['readbacks'], dict) or set(journal['readbacks']) != {'phase1', 'phase2'}:
        raise ValueError('both phase readbacks required')
    for phase in ('phase1', 'phase2'):
        observed = journal['readbacks'][phase]
        expected = plan['manifests'][phase]
        if not isinstance(observed, dict) or set(observed) != canonical_paths or len(observed) != 15:
            raise ValueError(phase + ' readback must cover all fifteen canonical paths')
        for path, entry in expected.items():
            actual = observed[path]
            exact(actual, {'status', 'sha256', 'bytes', 'headers'}, phase + ' readback ' + path)
            headers = cache_headers(actual['headers'], phase + ' readback headers ' + path)
            if (type(actual['status']) is not int or actual['status'] != 200
                    or actual['sha256'] != entry['sha256'] or type(actual['bytes']) is not int
                    or actual['bytes'] != entry['bytes']):
                raise ValueError(phase + ' readback differs: ' + path)

    plan_raw = canonical_bytes(plan)
    journal_raw = canonical_bytes(journal)
    bundle = {
        'artifact_id': run['artifact']['id'], 'journal_sha256': sha(journal_raw),
        'native_manifest_sha256': sha(native_manifest_raw), 'operation_inventory_sha256': operation_inventory_sha256,
        'input_approval_sha256': digest(approval), 'input_asset_id': input_asset_id,
        'input_bundle_sha256': input_bundle_sha256, 'dispatch_binding_sha256': dispatch_binding_sha256,
        'plan_sha256': sha(plan_raw), 'policy_revision': expected_policy,
        'run_attempt': run['run_attempt'], 'run_id': run['run_id'],
        'workflow_metadata_sha256': retained_binding['workflow_metadata_sha256'],
    }
    evidence_sha256 = sha(canonical_bytes(bundle))
    return {
        'schema': 1, 'status': 'PASS', **bundle, 'evidence_sha256': evidence_sha256,
        'source_evidence': {
            'status': 'PASS',
            'description': (f"Pages native inputs read back under policy {expected_policy}; "
                            f"run {run['run_id']} attempt {run['run_attempt']}, artifact {run['artifact']['id']}; "
                            f"plan {bundle['plan_sha256']}, journal {bundle['journal_sha256']}, "
                            f"native manifest {bundle['native_manifest_sha256']}, "
                            f"pre-dispatch inventory {bundle['operation_inventory_sha256']}, "
                            f"approval {bundle['input_approval_sha256']}, input asset {input_asset_id}, "
                            f"bundle {input_bundle_sha256}"),
            'sha256': evidence_sha256,
        },
    }

def transition_native_state(raw_state, proof):
    state = json.loads(raw_state, object_pairs_hook=unique)
    required = {'schema', 'status', 'repository', 'publication_status', 'reason', 'lock', 'production_version_floor'}
    exact(state, required, 'blocked native publication state')
    if (state['schema'] != 1 or state['status'] != 'BLOCKED'
            or state['repository'] != NATIVE_REPOSITORY or state['publication_status'] != 'NOT PUBLISHED'):
        raise ValueError('transition requires current blocked state for the approved origin')
    exact(proof, {'schema', 'status', 'artifact_id', 'journal_sha256', 'native_manifest_sha256',
                  'operation_inventory_sha256', 'input_approval_sha256', 'input_asset_id',
                  'input_bundle_sha256', 'dispatch_binding_sha256',
                  'plan_sha256', 'policy_revision', 'run_attempt', 'run_id',
                  'workflow_metadata_sha256', 'evidence_sha256', 'source_evidence'}, 'validated native proof')
    if proof['schema'] != 1 or proof['status'] != 'PASS':
        raise ValueError('passing validated native proof required')
    if type(proof['run_id']) is not int or proof['run_id'] <= 0 or type(proof['run_attempt']) is not int or proof['run_attempt'] <= 0 or type(proof['artifact_id']) is not int or proof['artifact_id'] <= 0:
        raise ValueError('validated run/artifact identity invalid')
    for field in ('journal_sha256', 'native_manifest_sha256', 'operation_inventory_sha256',
                  'input_approval_sha256', 'input_bundle_sha256', 'dispatch_binding_sha256',
                  'plan_sha256', 'workflow_metadata_sha256', 'evidence_sha256'):
        if not isinstance(proof[field], str) or not HEX64.fullmatch(proof[field]):
            raise ValueError('validated proof digest invalid: ' + field)
    if not HEX40.fullmatch(proof['policy_revision']):
        raise ValueError('validated proof policy revision invalid')
    if type(proof['input_asset_id']) is not int or proof['input_asset_id'] <= 0:
        raise ValueError('validated input asset identity invalid')
    bundle_fields = ('artifact_id', 'journal_sha256', 'native_manifest_sha256',
                     'operation_inventory_sha256', 'input_approval_sha256', 'input_asset_id',
                     'input_bundle_sha256', 'dispatch_binding_sha256', 'plan_sha256',
                     'policy_revision', 'run_attempt', 'run_id', 'workflow_metadata_sha256')
    bundle = {key: proof[key] for key in bundle_fields}
    if sha(canonical_bytes(bundle)) != proof['evidence_sha256']:
        raise ValueError('validated evidence bundle digest differs')
    source_evidence = proof['source_evidence']
    exact(source_evidence, {'status', 'description', 'sha256'}, 'source receipt evidence')
    if (source_evidence['status'] != 'PASS' or source_evidence['sha256'] != proof['evidence_sha256']
            or not isinstance(source_evidence['description'], str) or not source_evidence['description'].strip()):
        raise ValueError('source receipt evidence differs from validated proof')
    updated = dict(state)
    updated.update(status='ACCEPTED', publication_status='PUBLISHED_AND_READ_BACK',
                   reason='Exact pinned native inputs were published and independently read back under the recorded reviewed Pages policy.',
                   acceptance={key: proof[key] for key in (
                       'artifact_id', 'evidence_sha256', 'journal_sha256', 'native_manifest_sha256',
                       'operation_inventory_sha256', 'input_approval_sha256', 'input_asset_id',
                       'input_bundle_sha256', 'dispatch_binding_sha256', 'plan_sha256',
                       'policy_revision', 'run_attempt', 'run_id', 'workflow_metadata_sha256')})
    return (json.dumps(updated, indent=2) + '\n').encode(), source_evidence


def transition_outputs(state_raw, gradle_raw, workflow_raw, build_raw, proof):
    """Return the exact app files changed by a validated proof."""
    state_raw, gradle_raw, workflow_raw, build_raw = map(bytes, (state_raw, gradle_raw, workflow_raw, build_raw))
    updated_state, source_evidence = transition_native_state(state_raw, proof)
    gradle = gradle_raw.decode()
    old_guard = 'throw GradleException("Direct Android builds BLOCKED: publish and verify locked native inputs first; see BUILD.md")'
    if gradle.count(old_guard) != 1:
        raise ValueError('expected exactly one unconditional direct Android build guard')
    evidence_sha = proof['evidence_sha256']
    status_literal = json.dumps('"status": "ACCEPTED"')
    digest_literal = json.dumps('"evidence_sha256": "' + evidence_sha + '"')
    new_guard = (
        'val nativeAcceptanceFile = rootProject.file("build-inputs/native-publication.json")\n'
        'val nativeAcceptanceText = if (nativeAcceptanceFile.isFile) nativeAcceptanceFile.readText() else ""\n'
        'val nativeInputsAccepted = nativeAcceptanceText.contains(' + status_literal + ') &&\n'
        '  nativeAcceptanceText.contains(' + digest_literal + ')\n'
        'if (!nativeInputsAccepted) {\n'
        '  throw GradleException("Direct Android builds BLOCKED: accepted native publication evidence is absent or differs; see BUILD.md")\n'
        '}')
    gradle = gradle.replace(old_guard, new_guard)

    workflow = workflow_raw.decode()
    old_check = '''run: python3 -c 'import json; x=json.load(open("build-inputs/native-publication.json")); assert x["status"] == "BLOCKED"; print("Direct Android build acceptance BLOCKED pending public immutable native inputs; this source check is not build approval")' '''.rstrip()
    new_check = '''run: python3 -c 'import json,re; x=json.load(open("build-inputs/native-publication.json")); assert x.get("status") in ("BLOCKED","ACCEPTED"); a=x.get("acceptance",{}); assert x["status"] != "ACCEPTED" or (set(a)=={"artifact_id","evidence_sha256","journal_sha256","native_manifest_sha256","operation_inventory_sha256","input_approval_sha256","input_asset_id","input_bundle_sha256","dispatch_binding_sha256","plan_sha256","policy_revision","run_attempt","run_id","workflow_metadata_sha256"} and re.fullmatch("[0-9a-f]{40}",a["policy_revision"]) and all(re.fullmatch("[0-9a-f]{64}",a[k]) for k in ("evidence_sha256","journal_sha256","native_manifest_sha256","operation_inventory_sha256","input_approval_sha256","input_bundle_sha256","dispatch_binding_sha256","plan_sha256","workflow_metadata_sha256")) and type(a["run_id"]) is int and type(a["run_attempt"]) is int and type(a["artifact_id"]) is int and type(a["input_asset_id"]) is int); print("Direct Android build gate state:",x["status"])' '''.rstrip()
    if workflow.count(old_check) != 1:
        raise ValueError('expected exact blocked-state source workflow assertion')
    workflow = workflow.replace(old_check, new_check)

    build = build_raw.decode()
    marker = '**Direct build readiness: BLOCKED.**'
    if build.count(marker) != 1:
        raise ValueError('expected exact blocked readiness marker in BUILD.md')
    build = build.replace(marker, '**Direct build readiness: ACCEPTED for the exact native publication evidence recorded in `build-inputs/native-publication.json`.**')
    old_paragraph = ("Every direct Android Gradle configuration deliberately fails until the required publication layer\n"
                     "and clean-checkout build acceptance are reviewed. This draft is not releasable.")
    new_paragraph = ("Direct Android Gradle configuration proceeds only when its committed acceptance digest matches the\n"
                     "validated Pages plan, journal, six pinned native files, and exact successful policy run. Native input\n"
                     "acceptance does not by itself establish a releasable build.")
    if build.count(old_paragraph) != 1:
        raise ValueError('expected exact blocked Gradle explanation in BUILD.md')
    build = build.replace(old_paragraph, new_paragraph)
    old_task = 'it is currently BLOCKED as described above.'
    if build.count(old_task) != 1:
        raise ValueError('expected exact blocked canonical-task explanation in BUILD.md')
    build = build.replace(old_task, 'native input acceptance is recorded above; source build and runtime acceptance remain required.')
    old_version = ("The direct-build gate remains closed until the owner refreshes\n"
                   "the inventory and confirms this proposal through the serialized reservation policy.")
    new_version = ("The direct-build gate remains bound to the recorded native acceptance digest. Version reservation\n"
                   "and release eligibility remain separate requirements.")
    if build.count(old_version) != 1:
        raise ValueError('expected exact blocked version proposal explanation in BUILD.md')
    build = build.replace(old_version, new_version)
    return {
        'build-inputs/native-publication.json': updated_state,
        'app/build.gradle.kts': gradle.encode(),
        '.github/workflows/source.yml': workflow.encode(),
        'BUILD.md': build.encode(),
    }, source_evidence



def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--native-manifest', type=Path, required=True)
    parser.add_argument('--source-native-artifacts', type=Path, required=True)
    parser.add_argument('--run-evidence', type=Path, required=True)
    parser.add_argument('--input-bundle', type=Path, required=True)
    parser.add_argument('--policy-revision', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    manifest_raw = args.native_manifest.read_bytes()
    evidence = load_run_evidence(args.run_evidence)
    proof = validate_retained_evidence(evidence, manifest_raw,
                                       load(args.source_native_artifacts)['files'],
                                       args.policy_revision, args.input_bundle)
    if args.output.exists() or args.output.is_symlink():
        raise ValueError('refusing to overwrite proof output')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, 'O_NOFOLLOW'):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(args.output, flags, 0o600)
    with os.fdopen(descriptor, 'w') as output:
        output.write(json.dumps(proof, indent=2) + '\n')


if __name__ == '__main__':
    main()
