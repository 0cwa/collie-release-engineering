#!/usr/bin/env python3
"""Build and verify the immutable pre-readiness TrailBlazer evidence bundle."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile

import binary_repo as binary
import channel_profile as channels
import pages

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools' / 'patches'))
import migration_receipts as receipts
import release_artifacts as artifacts
import release_publish
import runtime_receipts as runtime

CHANNEL = channels.BETA
ENGINEERING_REPOSITORY = '0cwa/collie-release-engineering'
ASSET_NAME = 'trailblazer-beta-release-evidence.tar'
MANIFEST = 'bundle.json'
MAX_BUNDLE_BYTES = pages.MAX_BYTES
MAX_FILES = 40000
MAX_PATH = 512
MAX_MANIFEST_BYTES = 32 * 1024 * 1024
READINESS_NAME = 'trailblazer-beta-readiness.json'
READINESS_KIND = 'collie-trailblazer-readiness'
READINESS_FIELDS = {
    'schema', 'kind', 'repository', 'branch', 'workflow_path', 'run_id', 'run_attempt', 'head_sha',
    'package', 'version_name', 'version_code', 'tag', 'title', 'apk_asset', 'source_receipt_sha256',
    'build_receipt_sha256', 'release_receipt_sha256', 'repository_receipt_sha256',
    'release_manifest_sha256', 'apk_sha256', 'apk_certificate_sha256',
}
ROOT_FILES = {
    'repository-receipt.json', 'metadata/im.collie.app.trailblazer.yml',
    'manifests/unsigned-manifest.json', 'manifests/final-manifest.json',
    'apk/trailblazer-beta.apk',
}
REQUIRED_INDEXES = {'entry.jar', 'entry.json', 'index-v1.jar', 'index-v1.json', 'index-v2.json'}


def sha_file(path):
    digest = hashlib.sha256()
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as source:
        before = os.fstat(source.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise ValueError('release evidence input must be a regular file')
        for block in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(block)
        after = os.fstat(source.fileno())
        if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise ValueError('release evidence input changed while hashing')
    return digest.hexdigest()


def verify_metadata_revision(path, revision):
    if not re.fullmatch('[0-9a-f]{40}', str(revision)):
        raise ValueError('invalid immutable Beta metadata revision')
    repo = Path(__file__).resolve().parents[1]
    relative = f'distribution/beta-metadata/{CHANNEL.package}.yml'
    try:
        expected = subprocess.check_output(
            ['git', '-C', str(repo), 'show', f'{revision}:{relative}'], stderr=subprocess.PIPE)
    except subprocess.CalledProcessError as exc:
        raise ValueError('Beta metadata revision is not available in trusted engineering history') from exc
    if hashlib.sha256(expected).hexdigest() != sha_file(path):
        raise ValueError('bundled Beta metadata differs from its immutable engineering revision')


def strict_json(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate JSON field: ' + key)
            result[key] = value
        return result
    try:
        with open(path, encoding='utf-8') as stream:
            return json.load(stream, object_pairs_hook=unique)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError('invalid JSON input: ' + str(path)) from exc


def allowed_path(name):
    if (not isinstance(name, str) or len(name) > MAX_PATH or
            not re.fullmatch(r'[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*', name)):
        return False
    parts = PurePosixPath(name).parts
    if any(part in ('', '.', '..') for part in parts):
        return False
    if name in ROOT_FILES or name.startswith('releases/') and name.endswith('.json'):
        return len(parts) == (2 if name.startswith('releases/') else len(parts))
    if re.fullmatch(r'bundles/[1-9][0-9]*/(?:source|build|release)\.json', name):
        return True
    if name.startswith('candidate/'):
        relative = name[len('candidate/'):]
        return binary.public_path(relative, CHANNEL)
    return False


def inventory(root, *, permit_manifest=False):
    if root.is_symlink() or not root.is_dir():
        raise ValueError('bundle root must be a real directory')
    result = {}
    total = 0
    for path in sorted(root.rglob('*')):
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode):
            raise ValueError('symlink in bundle input')
        if stat.S_ISDIR(mode):
            continue
        if not stat.S_ISREG(mode):
            raise ValueError('non-regular file in bundle input')
        if path.stat().st_nlink != 1:
            raise ValueError('hardlinked bundle input')
        name = path.relative_to(root).as_posix()
        if name == MANIFEST and permit_manifest:
            continue
        if not allowed_path(name):
            raise ValueError('unexpected bundle input: ' + name)
        size = path.stat().st_size
        total += size
        if total > MAX_BUNDLE_BYTES or len(result) >= MAX_FILES:
            raise ValueError('release evidence bundle exceeds resource limit')
        result[name] = {'bytes': size, 'sha256': sha_file(path)}
    return result


def validate_release_set(root, entries, selected_id, tools, *, run_api=None, tag_verifier=None, release_api=None):
    approvals = sorted(name for name in entries if name.startswith('releases/'))
    if not approvals:
        raise ValueError('at least one approved Beta package release is required')
    release_ids = set()
    selected = None
    selected_graph = None
    for name in approvals:
        approval = binary.approval(root / name, CHANNEL)
        release_id = str(approval['release_id'])
        if release_id in release_ids or name != 'releases/' + release_id + '.json':
            raise ValueError('release approval filenames must bind unique release IDs')
        release_ids.add(release_id)
        bundle = root / 'bundles' / release_id
        graph = {kind: receipts.load(bundle / (kind + '.json'), kind, profile=CHANNEL.name)
                 for kind in ('source', 'build', 'release')}
        binary.verify_release_approval(approval, bundle, CHANNEL)
        if release_id == str(selected_id):
            selected, selected_graph = approval, graph
    if selected is None:
        raise ValueError('selected release is absent from approved package history')
    expected_bundle_paths = {
        f'bundles/{release_id}/{kind}.json'
        for release_id in release_ids for kind in ('source', 'build', 'release')
    }
    actual_bundle_paths = {name for name in entries if name.startswith('bundles/')}
    if actual_bundle_paths != expected_bundle_paths:
        raise ValueError('receipt bundle membership differs from release approvals')

    unsigned_path = root / 'manifests/unsigned-manifest.json'
    final_path = root / 'manifests/final-manifest.json'
    unsigned = strict_json(unsigned_path)
    final = strict_json(final_path)
    source_payload = selected_graph['source']['payload']
    release_payload = selected_graph['release']['payload']
    identities = {key: source_payload['engineering_revision'] if key == 'engineering_revision'
                  else source_payload['published_commit'] if key == 'source_commit'
                  else final.get('workflow_revision')
                  for key in ('source_commit', 'engineering_revision', 'workflow_revision')}
    manifest_rows = artifacts.validate_manifest(
        unsigned, source_payload['published_commit'], unsigned['run_id'],
        CHANNEL.apk_certificate, identities, CHANNEL.name)
    artifacts.validate_beta_signed_provenance(final, unsigned, source_payload['published_commit'], identities)
    if (selected['release_manifest_sha256'] != entries['manifests/final-manifest.json']['sha256'] or
            selected['apk_sha256'] != entries['apk/trailblazer-beta.apk']['sha256'] or
            selected['version_name'] != release_payload['version_name'] or
            selected['version_code'] != release_payload['version_code'] or
            selected['tag'] != CHANNEL.tag_prefix + selected['version_name'] or
            selected['aliases'] != {CHANNEL.apk_asset: selected['apk_sha256']}):
        raise ValueError('selected release approval differs from signed graph')
    if (release_payload['release_manifest_sha256'] != entries['manifests/final-manifest.json']['sha256'] or
            release_payload['apk_sha256'] != entries['apk/trailblazer-beta.apk']['sha256'] or
            release_payload['source_receipt_sha256'] != receipts.digest(selected_graph['source'], profile=CHANNEL.name) or
            release_payload['build_receipt_sha256'] != receipts.digest(selected_graph['build'], profile=CHANNEL.name)):
        raise ValueError('selected release receipt does not bind included manifest and APK')
    final_links = {
        'commit': source_payload['published_commit'], 'source_commit': source_payload['published_commit'],
        'package': release_payload['package'], 'version_code': release_payload['version_code'],
        'version_name': release_payload['version_name'], 'certificate_sha256': release_payload['apk_certificate_sha256'],
        'engineering_revision': release_payload['engineering_revision'],
        'workflow_revision': release_payload['workflow_revision'],
    }
    if any(final.get(key) != value for key, value in final_links.items()):
        raise ValueError('signed final manifest differs from release receipt')
    if len(manifest_rows) != 1 or manifest_rows[0]['file'] != CHANNEL.apk_asset:
        raise ValueError('unsigned manifest is not the single Beta APK')
    apk = root / 'apk/trailblazer-beta.apk'
    actual = binary.inspect(apk, tools['aapt'], tools['apksigner'], CHANNEL)
    expected = {'package': CHANNEL.package, 'version_code': selected['version_code'],
                'version_name': selected['version_name'], 'apk_certificate_sha256': CHANNEL.apk_certificate}
    if actual != expected or sha_file(apk) != selected['apk_sha256']:
        raise ValueError('actual signed APK differs from selected Beta release')

    repo_receipt = receipts.load(root / 'repository-receipt.json', 'repository', profile=CHANNEL.name)
    repo_payload = repo_receipt['payload']
    candidate = root / 'candidate'
    generation, index = binary.approved_generation(
        candidate, CHANNEL.repository_certificate, repo_payload['generation'],
        [root / name for name in approvals], CHANNEL)
    approval_hashes = selected['receipt_approvals']
    graph_approvals = {**approval_hashes, 'repository': receipts.digest(repo_receipt, profile=CHANNEL.name)}
    receipts.verify_graph(
        source=selected_graph['source'], build=selected_graph['build'], release=selected_graph['release'],
        repository=repo_receipt, approved=graph_approvals, promotion=True, profile=CHANNEL.name,
        repository_stage='prepublication')
    if (generation != repo_payload['generation'] or
            index['repo']['address'].rstrip('/') != CHANNEL.repository_url.rstrip('/') or
            repo_payload['repository_certificate_sha256'] != CHANNEL.repository_certificate or
            repo_payload['apk_sha256'] != selected['apk_sha256'] or
            repo_payload['release_receipt_sha256'] != receipts.digest(selected_graph['release'], profile=CHANNEL.name)):
        raise ValueError('signed Beta index or repository receipt differs from selected release')
    expected_indexes = [{'file': path.name, 'sha256': binary.sha(path)}
                        for path in sorted(candidate.iterdir())
                        if path.is_file() and path.suffix in {'.jar', '.json'}]
    if repo_payload['indexes'] != expected_indexes:
        raise ValueError('repository receipt index hashes differ from included signed indexes')
    indexed_names = {v['file']['name'].lstrip('/') for v in index['packages'][CHANNEL.package]['versions'].values()}
    indexed_codes = [v['manifest']['versionCode'] for v in index['packages'][CHANNEL.package]['versions'].values()]
    if not indexed_codes or selected['version_code'] != max(indexed_codes):
        raise ValueError('selected Beta release is not the highest indexed versionCode')
    if indexed_names != {p.name for p in candidate.glob(CHANNEL.package + '_*.apk')}:
        raise ValueError('indexed Beta APK bytes are incomplete')
    indexed_apk = candidate / f'{CHANNEL.package}_{selected["version_code"]}.apk'
    if (indexed_apk.stat().st_size != apk.stat().st_size or
            binary.sha(indexed_apk) != binary.sha(apk)):
        raise ValueError('selected indexed APK differs from signed APK asset')
    metadata_name = 'metadata/' + CHANNEL.package + '.yml'
    if metadata_name not in entries or not (root / metadata_name).is_file():
        raise ValueError('fixed Beta declarative metadata is missing')
    verify_metadata_revision(root / metadata_name, repo_payload['metadata_revision'])
    source_run_id = unsigned.get('run_id')
    source_attempt = unsigned.get('run_attempt')
    if (unsigned.get('commit') != source_payload['published_commit'] or
            final.get('commit') != source_payload['published_commit'] or
            str(final.get('run_id')) != str(source_run_id) or
            str(final.get('run_attempt')) != str(source_attempt)):
        raise ValueError('manifest source/run identities differ from receipt graph')
    if tag_verifier is None:
        tag_verifier = release_publish.verify_remote_tag
    tag_verifier('0cwa/collieim-android', selected['tag'], source_payload['published_commit'])
    if release_api is None:
        release_api = pages.github_read
    release_record = release_api(
        f"repos/0cwa/collieim-android/releases/{selected['release_id']}")
    if (not isinstance(release_record, dict) or type(release_record.get('id')) is not int or
            release_record['id'] != selected['release_id'] or
            release_record.get('tag_name') != selected['tag'] or
            release_record.get('name') != CHANNEL.app_title + ' ' + selected['version_name'] or
            release_record.get('draft') is not True or release_record.get('prerelease') is not True):
        raise ValueError('authoritative Beta draft release identity differs')
    apk_assets = [asset for asset in release_record.get('assets', [])
                  if isinstance(asset, dict) and str(asset.get('name', '')).endswith('.apk')]
    if (len(apk_assets) != 1 or apk_assets[0].get('name') != CHANNEL.apk_asset or
            apk_assets[0].get('state') != 'uploaded' or
            apk_assets[0].get('digest') != 'sha256:' + selected['apk_sha256']):
        raise ValueError('authoritative Beta draft APK asset differs from verified bytes')
    if run_api is None:
        runtime.verify_beta_ci_run(source_run_id, source_attempt, source_payload['published_commit'])
    else:
        runtime.verify_beta_ci_run(source_run_id, source_attempt, source_payload['published_commit'], transport=run_api)
    return {
        'approval': selected, 'graph': selected_graph, 'repo_receipt': repo_receipt,
        'unsigned': unsigned, 'final': final, 'generation': generation,
        'source_commit': source_payload['published_commit'], 'source_run_id': int(source_run_id),
        'source_run_attempt': int(source_attempt), 'repository_receipt_sha256': receipts.digest(repo_receipt, profile=CHANNEL.name),
        'release_receipt_sha256': receipts.digest(selected_graph['release'], profile=CHANNEL.name),
        'build_receipt_sha256': receipts.digest(selected_graph['build'], profile=CHANNEL.name),
        'source_receipt_sha256': receipts.digest(selected_graph['source'], profile=CHANNEL.name),
        'release_manifest_sha256': entries['manifests/final-manifest.json']['sha256'],
        'apk_sha256': selected['apk_sha256'], 'apk_certificate_sha256': CHANNEL.apk_certificate,
        'metadata_revision': repo_payload['metadata_revision'],
    }


def manifest_for(root, entries, selected_id):
    selected_name = f'releases/{selected_id}.json'
    if selected_name not in entries:
        raise ValueError('selected release approval is absent')
    approval = binary.approval(root / selected_name, CHANNEL)
    repository = receipts.load(root / 'repository-receipt.json', 'repository', profile=CHANNEL.name)
    source = receipts.load(root / 'bundles' / str(selected_id) / 'source.json', 'source', profile=CHANNEL.name)
    return {
        'schema': 1, 'kind': 'collie-trailblazer-release-evidence', 'channel': CHANNEL.name,
        'package': CHANNEL.package, 'version_name': approval['version_name'],
        'version_code': approval['version_code'], 'tag': approval['tag'],
        'title': CHANNEL.app_title + ' ' + approval['version_name'], 'apk_asset': CHANNEL.apk_asset,
        'source_commit': source['payload']['published_commit'],
        'source_run_id': int(strict_json(root / 'manifests/unsigned-manifest.json')['run_id']),
        'source_run_attempt': int(strict_json(root / 'manifests/unsigned-manifest.json')['run_attempt']),
        'repository_address': CHANNEL.repository_url,
        'repository_certificate_sha256': CHANNEL.repository_certificate,
        'apk_certificate_sha256': CHANNEL.apk_certificate,
        'release_id': int(selected_id), 'repository_receipt_sha256': receipts.digest(repository, profile=CHANNEL.name),
        'release_manifest_sha256': entries['manifests/final-manifest.json']['sha256'],
        'apk_sha256': entries['apk/trailblazer-beta.apk']['sha256'],
        'metadata_revision': repository['payload']['metadata_revision'],
        'generation': repository['payload']['generation'],
        'files': [{'path': name, **entries[name]} for name in sorted(entries)],
    }


def pack(input_dir, output, tools, *, run_api=None, tag_verifier=None, release_api=None):
    input_dir, output = Path(input_dir), Path(output)
    if output.resolve().is_relative_to(input_dir.resolve()):
        raise ValueError('evidence archive output must be outside its input directory')
    entries = inventory(input_dir)
    if ROOT_FILES - entries.keys():
        raise ValueError('required pre-readiness evidence files are missing')
    index_names = {name for name in entries if name.startswith('candidate/')}
    if not {f'candidate/{name}' for name in REQUIRED_INDEXES} <= index_names:
        raise ValueError('all canonical signed Beta index payloads are required')
    approvals = [name for name in entries if name.startswith('releases/')]
    if len(approvals) != len({name for name in approvals}):
        raise ValueError('duplicate release approval')
    # Current release is selected by the single included manifest/APK graph.
    selected_id = strict_json(input_dir / 'manifests/final-manifest.json').get('release_id')
    if type(selected_id) is not int or selected_id <= 0:
        # The signed manifest does not carry a repository release ID; select by the approved APK hash.
        final = strict_json(input_dir / 'manifests/final-manifest.json')
        matches = [binary.approval(input_dir / name, CHANNEL) for name in approvals
                   if binary.approval(input_dir / name, CHANNEL)['apk_sha256'] == final['apks'][0]['signed_sha256']]
        if len(matches) != 1:
            raise ValueError('cannot uniquely select release approval from signed APK bytes')
        selected_id = matches[0]['release_id']
    manifest = manifest_for(input_dir, entries, selected_id)
    if (input_dir / MANIFEST).exists() or (input_dir / MANIFEST).is_symlink():
        raise ValueError('bundle input must not provide its own manifest')
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() or output.is_symlink():
        raise ValueError('new evidence archive path required')
    with tempfile.TemporaryDirectory(prefix='beta-evidence-pack-', dir=output.parent) as temporary:
        verified = verify_directory(input_dir, manifest, tools, run_api=run_api,
                                    tag_verifier=tag_verifier, release_api=release_api)
        manifest_path = Path(temporary) / MANIFEST
        manifest_path.write_text(json.dumps(manifest, sort_keys=True, separators=(',', ':')) + '\n')
        temporary_archive = Path(temporary) / 'archive.tar'
        with tarfile.open(temporary_archive, 'w', format=tarfile.USTAR_FORMAT) as archive:
            for name in sorted([*entries, MANIFEST]):
                path = manifest_path if name == MANIFEST else input_dir / name
                info = tarfile.TarInfo(name)
                info.size = path.stat().st_size
                info.mode = 0o644
                info.mtime = 0
                with open(path, 'rb') as source:
                    archive.addfile(info, source)
        if temporary_archive.stat().st_size > MAX_BUNDLE_BYTES:
            raise ValueError('release evidence archive exceeds resource limit')
        archive_digest = sha_file(temporary_archive)
        extracted = Path(temporary) / 'verified-bundle'
        extract_archive(temporary_archive, extracted, expected_sha256=archive_digest)
        verified = verify_directory(extracted, strict_json(extracted / MANIFEST), tools,
                                    run_api=run_api, tag_verifier=tag_verifier,
                                    release_api=release_api)
        os.link(temporary_archive, output)
        return {'sha256': sha_file(output), 'bytes': output.stat().st_size, 'manifest': manifest,
                'verified': verified}


def verify_directory(root, manifest, tools, *, run_api=None, tag_verifier=None, release_api=None):
    if (not isinstance(manifest, dict) or type(manifest.get('schema')) is not int or
            manifest.get('schema') != 1 or manifest.get('kind') != 'collie-trailblazer-release-evidence'):
        raise ValueError('release evidence manifest schema')
    required = {'schema', 'kind', 'channel', 'package', 'version_name', 'version_code', 'tag', 'title',
                'apk_asset', 'source_commit', 'source_run_id', 'source_run_attempt', 'repository_address',
                'repository_certificate_sha256', 'apk_certificate_sha256', 'release_id',
                'repository_receipt_sha256', 'release_manifest_sha256', 'apk_sha256', 'metadata_revision',
                'generation', 'files'}
    if set(manifest) != required or manifest['channel'] != CHANNEL.name or manifest['package'] != CHANNEL.package:
        raise ValueError('release evidence manifest channel/schema differs')
    if (type(manifest['version_code']) is not int or not 1 <= manifest['version_code'] <= 2100000000 or
            type(manifest['release_id']) is not int or manifest['release_id'] <= 0 or
            type(manifest['source_run_id']) is not int or manifest['source_run_id'] <= 0 or
            type(manifest['source_run_attempt']) is not int or manifest['source_run_attempt'] <= 0 or
            not re.fullmatch('[0-9a-f]{40}', str(manifest['source_commit'])) or
            not re.fullmatch('[0-9a-f]{40}', str(manifest['metadata_revision'])) or
            not re.fullmatch('[0-9a-f]{64}', str(manifest['generation']))):
        raise ValueError('release evidence manifest contains invalid typed identities')
    artifacts.validate_beta_identity(manifest['version_name'], manifest['version_code'],
                                     manifest['tag'], manifest['title'], True)
    if (manifest['apk_asset'] != CHANNEL.apk_asset or manifest['repository_address'].rstrip('/') != CHANNEL.repository_url.rstrip('/') or
            manifest['repository_certificate_sha256'] != CHANNEL.repository_certificate or
            manifest['apk_certificate_sha256'] != CHANNEL.apk_certificate or
            manifest['tag'] != CHANNEL.tag_prefix + manifest['version_name'] or
            manifest['title'] != CHANNEL.app_title + ' ' + manifest['version_name']):
        raise ValueError('release evidence manifest channel identity differs')
    files = manifest['files']
    if not isinstance(files, list) or not files or len(files) > MAX_FILES:
        raise ValueError('release evidence file inventory missing')
    expected = {}
    for row in files:
        if not isinstance(row, dict) or set(row) != {'path', 'bytes', 'sha256'} or not allowed_path(row['path']):
            raise ValueError('unsafe release evidence file inventory')
        if row['path'] in expected or type(row['bytes']) is not int or row['bytes'] < 0 or not re.fullmatch('[0-9a-f]{64}', row['sha256']):
            raise ValueError('duplicate or invalid release evidence file inventory row')
        expected[row['path']] = {'bytes': row['bytes'], 'sha256': row['sha256']}
    actual = inventory(root, permit_manifest=True)
    if actual != expected:
        raise ValueError('release evidence archive membership or file hashes differ')
    selected = validate_release_set(root, expected, manifest['release_id'], tools, run_api=run_api,
                                    tag_verifier=tag_verifier, release_api=release_api)
    for field, value in (('version_name', selected['approval']['version_name']),
                         ('version_code', selected['approval']['version_code']),
                         ('source_commit', selected['source_commit']),
                         ('source_run_id', selected['source_run_id']),
                         ('source_run_attempt', selected['source_run_attempt']),
                         ('generation', selected['generation']),
                         ('repository_receipt_sha256', selected['repository_receipt_sha256']),
                         ('release_manifest_sha256', selected['release_manifest_sha256']),
                         ('apk_sha256', selected['apk_sha256']),
                         ('metadata_revision', selected['metadata_revision'])):
        if manifest[field] != value:
            raise ValueError('release evidence manifest differs from verified ' + field)
    return selected


class RegularTarInfo(tarfile.TarInfo):
    """Reject extension headers before tarfile reads or decodes their payload."""

    def _proc_member(self, archive):
        if self.type != tarfile.REGTYPE:
            raise ValueError('release evidence archive permits only regular USTAR members')
        return super()._proc_member(archive)


def extract_archive(archive_path, destination, *, expected_sha256=None):
    if destination.exists() or destination.is_symlink():
        raise ValueError('new extraction directory required')
    archive_path = Path(archive_path)
    try:
        descriptor = os.open(archive_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as exc:
        raise ValueError('release evidence archive must be a regular non-symlink file') from exc
    stream = os.fdopen(descriptor, 'rb')
    archive_stat = os.fstat(stream.fileno())
    if not stat.S_ISREG(archive_stat.st_mode) or archive_stat.st_size > MAX_BUNDLE_BYTES:
        stream.close()
        raise ValueError('release evidence archive exceeds resource limit')
    seen, total = set(), 0
    try:
        destination.mkdir(parents=True)
        with stream:
            with tarfile.open(fileobj=stream, mode='r:', tarinfo=RegularTarInfo) as archive:
                for member in archive:
                    name = member.name
                    if (not allowed_path(name) and name != MANIFEST) or name in seen or not member.isfile():
                        raise ValueError('unsafe, duplicate, or non-regular archive member')
                    if member.mode != 0o644 or member.uid != 0 or member.gid != 0:
                        raise ValueError('release evidence archive member metadata is not canonical')
                    if member.size < 0 or member.size > MAX_BUNDLE_BYTES:
                        raise ValueError('invalid archive member size')
                    if name == MANIFEST and member.size > MAX_MANIFEST_BYTES:
                        raise ValueError('release evidence manifest exceeds resource limit')
                    total += member.size
                    if total > MAX_BUNDLE_BYTES or len(seen) >= MAX_FILES:
                        raise ValueError('release evidence archive expands beyond resource limit')
                    seen.add(name)
                    target = destination / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    source = archive.extractfile(member)
                    if source is None:
                        raise ValueError('archive member has no data')
                    with source, open(target, 'xb') as sink:
                        shutil.copyfileobj(source, sink, 1024 * 1024)
                    if target.stat().st_size != member.size:
                        raise ValueError('truncated archive member')
                # Keep the independently opened, no-follow descriptor available for
                # hashing the exact bytes after tar parsing completes.
                archive.fileobj = None
            if MANIFEST not in seen:
                raise ValueError('release evidence bundle manifest missing')
            if expected_sha256 is not None:
                digest = hashlib.sha256()
                stream.seek(0)
                for block in iter(lambda: stream.read(1024 * 1024), b''):
                    digest.update(block)
                after = os.fstat(stream.fileno())
                if ((archive_stat.st_size, archive_stat.st_mtime_ns, archive_stat.st_ctime_ns) !=
                        (after.st_size, after.st_mtime_ns, after.st_ctime_ns) or digest.hexdigest() != expected_sha256):
                    raise ValueError('release evidence archive changed during extraction or differs from approved SHA256')
    except Exception as exc:
        shutil.rmtree(destination, ignore_errors=True)
        if isinstance(exc, ValueError):
            raise
        raise ValueError('invalid release evidence archive') from exc


def verify_asset_metadata(asset_id, expected_sha256, *, api=None):
    if type(asset_id) is not int or asset_id <= 0 or not re.fullmatch('[0-9a-f]{64}', str(expected_sha256)):
        raise ValueError('approved engineering asset ID and complete bundle SHA256 are required')
    if api is None:
        api = pages.github_read
    asset = api(f'repos/{ENGINEERING_REPOSITORY}/releases/assets/{asset_id}')
    if (not isinstance(asset, dict) or asset.get('id') != asset_id or asset.get('name') != ASSET_NAME or asset.get('state') != 'uploaded' or
            type(asset.get('size')) is not int or not 0 < asset['size'] <= MAX_BUNDLE_BYTES or
            asset.get('digest') != 'sha256:' + expected_sha256):
        raise ValueError('fixed engineering release asset metadata differs from approved bundle')
    return asset


def require_private_scratch(scratch):
    scratch = Path(scratch)
    try:
        scratch_stat = scratch.lstat()
    except OSError as exc:
        raise ValueError('existing private scratch directory required') from exc
    if (stat.S_ISLNK(scratch_stat.st_mode) or not stat.S_ISDIR(scratch_stat.st_mode) or
            scratch_stat.st_uid != os.geteuid() or stat.S_IMODE(scratch_stat.st_mode) & 0o077):
        raise ValueError('owned private scratch directory with mode 0700 or stricter required')
    return scratch


def verify_archive(archive_path, *, bundle_sha256, asset_id, scratch, tools,
                   api=None, run_api=None, tag_verifier=None, release_api=None):
    """Verify a local archive against its approved fixed GitHub asset and full graph."""
    asset = verify_asset_metadata(asset_id, bundle_sha256, api=api)
    archive_path = Path(archive_path)
    if (archive_path.is_symlink() or not archive_path.is_file() or
            archive_path.stat().st_size != asset['size'] or sha_file(archive_path) != bundle_sha256):
        raise ValueError('local fixed engineering asset bytes differ from approved complete bundle SHA256')
    scratch = require_private_scratch(scratch)
    with tempfile.TemporaryDirectory(prefix='beta-evidence-verify-', dir=scratch) as temporary:
        unpacked = Path(temporary) / 'contents'
        extract_archive(archive_path, unpacked, expected_sha256=bundle_sha256)
        manifest = strict_json(unpacked / MANIFEST)
        verified = verify_directory(unpacked, manifest, tools, run_api=run_api or api,
                                    tag_verifier=tag_verifier, release_api=release_api or api)
        return {
            'bundle_sha256': bundle_sha256, 'bytes': asset['size'],
            'package': verified['approval']['package'],
            'version_name': verified['approval']['version_name'],
            'version_code': verified['approval']['version_code'],
            'tag': verified['approval']['tag'], 'title': CHANNEL.app_title + ' ' + verified['approval']['version_name'],
            'source_commit': verified['source_commit'],
            'repository_receipt_sha256': verified['repository_receipt_sha256'],
            'apk_sha256': verified['apk_sha256'],
        }


def download_asset(asset_id, destination):
    endpoint = f'repos/{ENGINEERING_REPOSITORY}/releases/assets/{asset_id}'
    with open(destination, 'xb') as output:
        subprocess.run(['gh', 'api', '--hostname', 'github.com', '--method', 'GET',
                        '-H', 'Accept: application/octet-stream', endpoint],
                       check=True, stdout=output, stderr=subprocess.PIPE, timeout=600)


def readiness_run(run_id, attempt, source_commit, *, api=None):
    """Bind this running emitter job without depending on its future completion."""
    if type(run_id) is not int or run_id <= 0 or type(attempt) is not int or attempt <= 0:
        raise ValueError('positive authoritative readiness run and attempt required')
    if api is None:
        api = pages.github_read
    endpoint = f'repos/0cwa/collieim-android/actions/runs/{run_id}/attempts/{attempt}'
    run = api(endpoint)
    head_repository = run.get('head_repository') if isinstance(run, dict) else None
    if (not isinstance(run, dict) or run.get('id') != run_id or run.get('run_attempt') != attempt or
            not isinstance(head_repository, dict) or head_repository.get('full_name') != '0cwa/collieim-android' or
            run.get('head_branch') != CHANNEL.source_branch or run.get('head_sha') != source_commit or
            run.get('path') != CHANNEL.readiness_workflow or run.get('status') != 'in_progress' or
            run.get('event') not in ('push', 'workflow_dispatch')):
        raise ValueError('authoritative in-progress Beta readiness run differs from verified source')
    return run


def readiness_document(verified, run):
    approval, graph = verified['approval'], verified['graph']
    value = {
        'schema': 1, 'kind': READINESS_KIND, 'repository': '0cwa/collieim-android',
        'branch': CHANNEL.source_branch, 'workflow_path': CHANNEL.readiness_workflow,
        'run_id': run['id'], 'run_attempt': run['run_attempt'], 'head_sha': verified['source_commit'],
        'package': CHANNEL.package, 'version_name': approval['version_name'],
        'version_code': approval['version_code'], 'tag': approval['tag'],
        'title': CHANNEL.app_title + ' ' + approval['version_name'], 'apk_asset': CHANNEL.apk_asset,
        'source_receipt_sha256': receipts.digest(graph['source'], profile=CHANNEL.name),
        'build_receipt_sha256': receipts.digest(graph['build'], profile=CHANNEL.name),
        'release_receipt_sha256': receipts.digest(graph['release'], profile=CHANNEL.name),
        'repository_receipt_sha256': verified['repository_receipt_sha256'],
        'release_manifest_sha256': verified['release_manifest_sha256'], 'apk_sha256': verified['apk_sha256'],
        'apk_certificate_sha256': CHANNEL.apk_certificate,
    }
    if set(value) != READINESS_FIELDS:
        raise ValueError('internal readiness schema differs from Pages contract')
    return value


def emit_readiness(*, bundle_sha256, asset_id, run_id, attempt, output, scratch, tools,
                   api=None, downloader=None, source_run_api=None, tag_verifier=None):
    asset = verify_asset_metadata(asset_id, bundle_sha256, api=api)
    scratch = require_private_scratch(scratch)
    with tempfile.TemporaryDirectory(prefix='beta-evidence-', dir=scratch) as temp:
        temp = Path(temp)
        archive = temp / ASSET_NAME
        if downloader is None:
            download_asset(asset_id, archive)
        else:
            downloader(asset_id, archive)
        if archive.is_symlink() or not archive.is_file() or archive.stat().st_size != asset['size'] or sha_file(archive) != bundle_sha256:
            raise ValueError('downloaded fixed engineering asset bytes differ from approved complete bundle SHA256')
        unpacked = temp / 'contents'
        extract_archive(archive, unpacked, expected_sha256=bundle_sha256)
        manifest = strict_json(unpacked / MANIFEST)
        verified = verify_directory(unpacked, manifest, tools, run_api=source_run_api or api,
                                    tag_verifier=tag_verifier, release_api=api)
        run = readiness_run(run_id, attempt, verified['source_commit'], api=api)
        document = readiness_document(verified, run)
        output = Path(output)
        if output.exists() or output.is_symlink():
            raise ValueError('new readiness output path required')
        with output.open('x', encoding='utf-8') as stream:
            stream.write(json.dumps(document, sort_keys=True, separators=(',', ':')) + '\n')
        return document


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    packer = sub.add_parser('pack', help='validate and create the deterministic pre-readiness bundle')
    packer.add_argument('input', type=Path)
    packer.add_argument('output', type=Path)
    packer.add_argument('--aapt', required=True, type=Path)
    packer.add_argument('--apksigner', required=True, type=Path)
    verifier = sub.add_parser('verify', help='verify an approved fixed engineering archive and its complete release graph')
    verifier.add_argument('archive', type=Path)
    verifier.add_argument('--bundle-sha256', required=True)
    verifier.add_argument('--engineering-asset-id', required=True, type=int)
    verifier.add_argument('--scratch', required=True, type=Path)
    verifier.add_argument('--aapt', required=True, type=Path)
    verifier.add_argument('--apksigner', required=True, type=Path)
    emitter = sub.add_parser('emit-readiness', help='download the approved fixed engineering asset, verify it, and emit Pages readiness JSON')
    emitter.add_argument('--bundle-sha256', required=True)
    emitter.add_argument('--engineering-asset-id', required=True, type=int)
    emitter.add_argument('--readiness-run-id', required=True, type=int)
    emitter.add_argument('--readiness-run-attempt', required=True, type=int)
    emitter.add_argument('--output', required=True, type=Path)
    emitter.add_argument('--scratch', required=True, type=Path)
    emitter.add_argument('--aapt', required=True, type=Path)
    emitter.add_argument('--apksigner', required=True, type=Path)
    args = parser.parse_args()
    try:
        if args.command == 'pack':
            result = pack(args.input, args.output, {'aapt': args.aapt, 'apksigner': args.apksigner})
            print(json.dumps({'sha256': result['sha256'], 'bytes': result['bytes']}, sort_keys=True))
        elif args.command == 'verify':
            result = verify_archive(args.archive, bundle_sha256=args.bundle_sha256,
                asset_id=args.engineering_asset_id, scratch=args.scratch,
                tools={'aapt': args.aapt, 'apksigner': args.apksigner})
            print(json.dumps(result, sort_keys=True))
        else:
            result = emit_readiness(bundle_sha256=args.bundle_sha256, asset_id=args.engineering_asset_id,
                run_id=args.readiness_run_id, attempt=args.readiness_run_attempt, output=args.output,
                scratch=args.scratch, tools={'aapt': args.aapt, 'apksigner': args.apksigner})
            print(json.dumps(result, sort_keys=True))
    except (ValueError, OSError, KeyError, TypeError, IndexError, AttributeError,
            subprocess.SubprocessError, json.JSONDecodeError) as exc:
        parser.exit(1, 'Beta release evidence failed: ' + str(exc) + '\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
