"""Add an inventory to an explicitly reviewed legacy Pages site without changing it.

The input is a complete, externally reviewed inventory, never a crawler result.
Every existing byte is checked against that inventory and copied unchanged. The
ordinary feed publisher continues to require an already published inventory.
"""
import argparse
import hashlib
import json
import re
import stat
import tempfile
from pathlib import Path, PurePosixPath
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener

import binary_repo
import channel_profile as channels
import feed
import site_page


ORIGIN = feed.SITE_ORIGIN
MAX_BYTES = feed.SITE_LIMIT
MAX_FILES = 40_000
MAX_INVENTORY_BYTES = 8 * 1024 * 1024
MAX_CONTROL_BYTES = 32 * 1024 * 1024
CHUNK_BYTES = 1024 * 1024
NATIVE_POLICY = Path(__file__).with_name('native-v1.json')
ASSET_NAME = 'collie-pages-legacy-inventory.json'
ABSENT = (feed.SITE_INVENTORY, 'fdroid/beta/repo/entry.jar',
          'fdroid/beta/repo/index-v1.jar', 'fdroid/beta/repo/index-v2.json')
INVENTORY_FIELDS = {'schema', 'kind', 'origin', 'policy_revision',
                    'previous_deployment_run_id', 'provenance', 'files'}
BASELINE_FIELDS = {'schema', 'kind', 'origin', 'reviewed_inventory_sha256',
                   'reviewed_inventory_json', 'site_manifest_sha256'}


def _json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate inventory field')
            result[key] = value
        return result
    try:
        return json.loads(raw, object_pairs_hook=unique)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError('invalid inventory JSON') from error


def _read_control(path, limit):
    path = Path(path)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit:
        raise ValueError('bounded regular control file required')
    with path.open('rb') as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError('control file exceeds byte limit')
    return raw


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode() + b'\n'


def _hash(raw):
    return hashlib.sha256(raw).hexdigest()


def _digest(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None


def _native_files():
    return _json(_read_control(NATIVE_POLICY, MAX_INVENTORY_BYTES))['files']


def _safe_path(name):
    if (not isinstance(name, str) or not name or len(name) > 512 or
            not re.fullmatch(r'[A-Za-z0-9_./=-]+', name) or
            PurePosixPath(name).is_absolute() or len(name.split('/')) > 20 or
            any(part in ('', '.', '..') for part in name.split('/'))):
        raise ValueError('unsafe legacy inventory path')
    return name


def _allowed_path(name, native):
    if name in native or name in site_page.STATIC:
        return True
    if name in {'CNAME', 'feed-state.json', 'index.html', 'add-repository.png',
                'add-repository.svg', '.nojekyll'}:
        return True
    if name.startswith('fdroid/repo/'):
        return binary_repo.public_path(name[len('fdroid/repo/'):], channels.STABLE)
    return False


def _validate_inventory(value, policy=None):
    if (not isinstance(value, dict) or set(value) != INVENTORY_FIELDS or
            type(value.get('schema')) is not int or value['schema'] != 1 or
            value['kind'] != 'collie-pages-legacy-inventory' or value['origin'] != ORIGIN or
            not isinstance(value['policy_revision'], str) or
            not re.fullmatch('[0-9a-f]{40}', value['policy_revision']) or
            (policy is not None and value['policy_revision'] != policy) or
            type(value['previous_deployment_run_id']) is not int or
            value['previous_deployment_run_id'] <= 0 or
            not isinstance(value['provenance'], str) or
            not value['provenance'].strip() or len(value['provenance']) > 4096 or
            not isinstance(value['files'], dict)):
        raise ValueError('legacy inventory identity/schema or policy differs')
    files = value['files']
    if not files or len(files) > MAX_FILES:
        raise ValueError('legacy inventory file count exceeds limit')
    native = _native_files()
    total = 0
    for name, record in files.items():
        _safe_path(name)
        if not _allowed_path(name, native):
            raise ValueError('unexpected legacy inventory path: ' + name)
        if (not isinstance(record, dict) or set(record) != {'sha256', 'bytes'} or
                not _digest(record['sha256']) or type(record['bytes']) is not int or
                record['bytes'] < 0):
            raise ValueError('invalid legacy inventory file record')
        total += record['bytes']
        if total > MAX_BYTES:
            raise ValueError('legacy inventory exceeds site byte limit')
    # Validate the whole set before any origin requests or output writes.
    for name in files:
        if any('/'.join(name.split('/')[:i]) in files for i in range(1, len(name.split('/')))):
            raise ValueError('legacy inventory has a file/directory collision')
    required = set(native) | set(site_page.STATIC) | {
        'CNAME', 'feed-state.json', 'index.html', 'add-repository.png',
        'fdroid/repo/entry.jar', 'fdroid/repo/index-v1.jar', 'fdroid/repo/index-v2.json',
    }
    if not required.issubset(files):
        raise ValueError('legacy inventory omits required existing site files')
    if any(files[name] != record for name, record in native.items()):
        raise ValueError('legacy native inputs differ from fixed native-v1 policy')
    if files['CNAME'] != {'sha256': _hash(b'repo.collie.im\n'), 'bytes': len(b'repo.collie.im\n')}:
        raise ValueError('legacy inventory has the wrong Pages hostname')
    manifest = _site_manifest(files)
    if total + len(manifest) > MAX_BYTES:
        raise ValueError('inventory addition exceeds site byte limit')
    return value


def _site_manifest(files):
    return _canonical({'schema': 1, 'kind': 'collie-pages-public-inventory',
                       'origin': ORIGIN, 'files': files})


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _open(path, open_url=None):
    request = Request(ORIGIN + path, headers={
        'Cache-Control': 'no-cache, no-store', 'Pragma': 'no-cache',
        'User-Agent': 'Collie-Pages-inventory-bootstrap',
    })
    if open_url is None:
        open_url = build_opener(_NoRedirect()).open
    return open_url(request, timeout=60)


def _absent(open_url=None):
    for path in ABSENT:
        try:
            with _open(path, open_url) as response:
                # Any successful response, even an empty one, closes this migration.
                raise ValueError('bootstrap requires absent inventory and Beta indexes: ' + path)
        except HTTPError as error:
            try:
                if error.code != 404 or error.geturl() != ORIGIN + path:
                    raise ValueError('canonical bootstrap absence check failed: ' + path) from error
            finally:
                error.close()


def _download(path, record, destination, open_url=None):
    """Stream one exact object to disk; never buffer a native AAR or APK in memory."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    size = 0
    with _open(path, open_url) as response, destination.open('xb') as stream:
        if response.status != 200 or response.geturl() != ORIGIN + path:
            raise ValueError('legacy file left its canonical HTTPS URL: ' + path)
        while True:
            block = response.read(min(CHUNK_BYTES, record['bytes'] - size + 1))
            if not block:
                break
            size += len(block)
            if size > record['bytes']:
                raise ValueError('legacy file exceeds reviewed byte size: ' + path)
            digest.update(block)
            stream.write(block)
    if size != record['bytes'] or digest.hexdigest() != record['sha256']:
        raise ValueError('legacy file differs from reviewed inventory: ' + path)


def _local_files(root):
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise ValueError('regular site directory required')
    result = {}
    total = 0
    for path in root.rglob('*'):
        info = path.lstat()
        if stat.S_ISDIR(info.st_mode):
            continue
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError('site contains a symlink, hardlink or special file')
        name = _safe_path(path.relative_to(root).as_posix())
        total += info.st_size
        if len(result) >= MAX_FILES + 1 or total > MAX_BYTES:
            raise ValueError('site exceeds file or byte limit')
        result[name] = {'sha256': binary_repo.sha(path), 'bytes': info.st_size}
    return result


def _verify_stable(site, files):
    data = binary_repo.verify_indexes(site / 'fdroid/repo',
                                      channels.STABLE.repository_certificate, channels.STABLE)
    if data.get('repo', {}).get('address', '').rstrip('/') != channels.STABLE.repository_url.rstrip('/'):
        raise ValueError('legacy Stable index has the wrong repository address')
    releases = binary_repo.release_tuples(data, channels.STABLE)
    if not releases or any(row[4] != channels.STABLE.apk_certificate for row in releases):
        raise ValueError('legacy Stable index APK signer differs')
    indexed_apks = {row[3] for row in releases}
    if any('fdroid/repo/' + name not in files for name in indexed_apks):
        raise ValueError('legacy inventory omits an indexed APK')
    def check_references(item):
        if isinstance(item, dict):
            if {'name', 'sha256', 'size'}.issubset(item):
                name = item['name']
                if not isinstance(name, str):
                    raise ValueError('invalid signed index file reference')
                name = 'fdroid/repo/' + name.removeprefix('/')
                _safe_path(name)
                if files.get(name) != {'sha256': item['sha256'], 'bytes': item['size']}:
                    raise ValueError('legacy inventory omits or changes a signed index file reference')
            for child in item.values():
                check_references(child)
        elif isinstance(item, list):
            for child in item:
                check_references(child)
    check_references(data)
    state = _json(_read_control(site / 'feed-state.json', MAX_INVENTORY_BYTES))
    if (not isinstance(state, dict) or state.get('schema') != 1 or
            state.get('repository_fingerprint') != channels.STABLE.repository_certificate or
            state.get('native_files') != len(_native_files()) or
            not isinstance(state.get('apks'), list) or set(state['apks']) != indexed_apks or
            state.get('latest') not in {'v' + row[1] for row in releases} or
            state.get('preview') or 'beta' in state or 'channel' in state):
        raise ValueError('legacy feed state differs from its Stable indexes')
    if (site / 'CNAME').read_bytes() != b'repo.collie.im\n':
        raise ValueError('legacy site has the wrong Pages hostname')


def _capture(files, destination, open_url=None):
    destination.mkdir()
    for name, record in sorted(files.items()):
        _download(name, record, destination / name, open_url)
    if _local_files(destination) != files:
        raise ValueError('captured site has extra or missing files')
    _verify_stable(destination, files)


def make_inventory(snapshot, output, policy, previous_run, provenance):
    """Inventory an operator-supplied complete snapshot, without claiming approval."""
    value = {'schema': 1, 'kind': 'collie-pages-legacy-inventory', 'origin': ORIGIN,
             'policy_revision': policy, 'previous_deployment_run_id': previous_run,
             'provenance': provenance, 'files': _local_files(snapshot)}
    _validate_inventory(value, policy)
    _verify_stable(Path(snapshot), value['files'])
    raw = _canonical(value)
    if len(raw) > MAX_INVENTORY_BYTES:
        raise ValueError('review inventory exceeds byte limit')
    with Path(output).open('xb') as stream:
        stream.write(raw)
    return _hash(raw)


def prepare(inventory_path, expected_sha256, output, policy, open_url=None):
    raw = _read_control(inventory_path, MAX_INVENTORY_BYTES)
    if not _digest(expected_sha256) or _hash(raw) != expected_sha256:
        raise ValueError('reviewed raw inventory digest differs')
    value = _validate_inventory(_json(raw), policy)
    output = Path(output)
    # Exclusive creation never overwrites an earlier preparation or a dangling link.
    output.mkdir()
    _absent(open_url)
    site = output / 'site'
    _capture(value['files'], site, open_url)
    _absent(open_url)
    manifest = _site_manifest(value['files'])
    (site / feed.SITE_INVENTORY).write_bytes(manifest)
    baseline = {'schema': 1, 'kind': 'collie-pages-inventory-bootstrap-baseline',
                'origin': ORIGIN, 'reviewed_inventory_sha256': expected_sha256,
                'reviewed_inventory_json': raw.decode('utf-8'),
                'site_manifest_sha256': _hash(manifest)}
    (output / 'baseline.json').write_bytes(_canonical(baseline))
    verify_candidate(output / 'baseline.json', site)
    return baseline


def _baseline(path):
    baseline = _json(_read_control(path, MAX_CONTROL_BYTES))
    if (not isinstance(baseline, dict) or set(baseline) != BASELINE_FIELDS or
            type(baseline.get('schema')) is not int or baseline['schema'] != 1 or
            baseline['kind'] != 'collie-pages-inventory-bootstrap-baseline' or
            baseline['origin'] != ORIGIN or
            not _digest(baseline['reviewed_inventory_sha256']) or
            not isinstance(baseline['reviewed_inventory_json'], str) or
            not _digest(baseline['site_manifest_sha256'])):
        raise ValueError('invalid inventory-bootstrap baseline')
    raw = baseline['reviewed_inventory_json'].encode('utf-8')
    if len(raw) > MAX_INVENTORY_BYTES or _hash(raw) != baseline['reviewed_inventory_sha256']:
        raise ValueError('baseline reviewed inventory digest differs')
    value = _validate_inventory(_json(raw))
    if _hash(_site_manifest(value['files'])) != baseline['site_manifest_sha256']:
        raise ValueError('baseline candidate manifest digest differs')
    return baseline, value


def verify_candidate(baseline_path, site):
    baseline, value = _baseline(baseline_path)
    manifest = _site_manifest(value['files'])
    expected = {**value['files'], feed.SITE_INVENTORY: {
        'sha256': baseline['site_manifest_sha256'], 'bytes': len(manifest)}}
    if _local_files(site) != expected:
        raise ValueError('bootstrap candidate must preserve exactly the reviewed files plus inventory')
    return baseline, value


def verify_deploy_baseline(baseline_path, site, open_url=None):
    """Called after durable guard reconciliation, immediately before Pages POST."""
    _, value = verify_candidate(baseline_path, site)
    _absent(open_url)
    with tempfile.TemporaryDirectory(prefix='collie-bootstrap-recheck-') as temporary:
        _capture(value['files'], Path(temporary) / 'site', open_url)
    _absent(open_url)
    return True


def verify_readback(baseline_path, open_url=None):
    baseline, value = _baseline(baseline_path)
    manifest = _site_manifest(value['files'])
    with tempfile.TemporaryDirectory(prefix='collie-bootstrap-readback-') as temporary:
        site = Path(temporary) / 'site'
        _capture(value['files'], site, open_url)
        _download(feed.SITE_INVENTORY, {'sha256': baseline['site_manifest_sha256'],
                                      'bytes': len(manifest)}, site / feed.SITE_INVENTORY, open_url)
        verify_candidate(baseline_path, site)
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    create = commands.add_parser('inventory', help='describe an authoritative complete legacy snapshot')
    create.add_argument('--snapshot', type=Path, required=True)
    create.add_argument('--output', type=Path, required=True)
    create.add_argument('--policy', required=True)
    create.add_argument('--previous-deployment-run-id', type=int, required=True)
    create.add_argument('--provenance', required=True)
    prep = commands.add_parser('prepare', help='preserve the reviewed live site and add only its inventory')
    prep.add_argument('--inventory', type=Path, required=True)
    prep.add_argument('--sha256', required=True)
    prep.add_argument('--output', type=Path, required=True)
    prep.add_argument('--policy', required=True)
    check = commands.add_parser('check-candidate')
    check.add_argument('--baseline', type=Path, required=True)
    check.add_argument('--site', type=Path, required=True)
    readback = commands.add_parser('readback')
    readback.add_argument('--baseline', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'inventory':
        print(make_inventory(args.snapshot, args.output, args.policy,
                             args.previous_deployment_run_id, args.provenance))
    elif args.command == 'prepare':
        result = prepare(args.inventory, args.sha256, args.output, args.policy)
        print('Prepared unchanged legacy site; manifest SHA256 ' + result['site_manifest_sha256'])
    elif args.command == 'check-candidate':
        verify_candidate(args.baseline, args.site)
        print('Candidate contains exactly the reviewed legacy files and new inventory')
    else:
        verify_readback(args.baseline)
        print('Published legacy files and inventory match the frozen bootstrap candidate')


if __name__ == '__main__':
    main()
