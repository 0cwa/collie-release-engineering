"""Stage a regular Collie feed from exact published release identities.

GitHub's global release list also contains TrailBlazer and regular prereleases.
Resolve the selected full release directly, then paginate only for additional
history. Listing and native inputs are read from its immutable tagged source.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
from tempfile import TemporaryDirectory
import urllib.request
from urllib.parse import urlparse
import zipfile

import channel_profile as channels
import native_generations

APP_REPOSITORY = '0cwa/collieim-android'
ASSET_HOSTS = {'github.com', 'release-assets.githubusercontent.com'}
ORIGIN = 'https://repo.collie.im/'
NATIVE_PREFIX = 'native-inputs/v1/maven/'
MAX_BYTES = 950_000_000
MAX_PAGES = 100
TAG = re.compile(r'v[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9]+)?(?:-(?:alpha|beta|rc)[0-9]+)?')
SHA = re.compile(r'[0-9a-f]{40}')
DIGEST = re.compile(r'[0-9a-f]{64}')
METADATA_PREFIX = 'fastlane/metadata/android'
NATIVE_PLAN = 'build-inputs/native-artifacts.json'
MAX_METADATA_FILES = 256
MAX_METADATA_BYTES = 16 * 1024 * 1024
MAX_METADATA_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_METADATA_API_BYTES = 32 * 1024 * 1024
MAX_TREE_ENTRIES = 2048
MAX_TEXT_BYTES = 64 * 1024
MAX_PNG_BYTES = 2 * 1024 * 1024
LOCALE = re.compile(r'[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*')
# This full release predates Fastlane in the app tree. The exception reproduces
# the listing checkout recorded by Pages run 42957848111, job 130317140954.
# It never changes the APK source or native manifest revision.
LEGACY_LISTINGS = {
    ('v8.35.1-3-rc2', 'cc55c02bef64b2980c5f384a6a04f52ffb7bef92'):
        'af90bcda4ed7c83d4eee7582d6f3844b018500b0',
}


def github_read(endpoint):
    result = subprocess.run(['gh', 'api', '--hostname', 'github.com', '--method', 'GET', endpoint],
                            check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


def release_asset(assets, name, url, limit, *, optional=False):
    if not isinstance(assets, list):
        raise ValueError('release assets are unavailable')
    matches = [asset for asset in assets if isinstance(asset, dict) and asset.get('name') == name]
    if not matches and optional:
        return None
    if len(matches) != 1:
        raise ValueError(f'release must contain exactly one {name}')
    asset = matches[0]
    if (type(asset.get('id')) is not int or asset['id'] <= 0 or asset.get('state') != 'uploaded'
            or type(asset.get('size')) is not int or not 0 < asset['size'] <= limit
            or not re.fullmatch(r'sha256:[0-9a-f]{64}', str(asset.get('digest', '')))
            or asset.get('browser_download_url') != url):
        raise ValueError(f'{name} asset identity, digest or URL differs')
    return {'asset_id': asset['id'], 'bytes': asset['size'], 'sha256': asset['digest'][7:], 'url': url}


def manifest_asset(assets, tag):
    url = f'https://github.com/{APP_REPOSITORY}/releases/download/{tag}/release.json'
    identity = release_asset(assets, 'release.json', url, MAX_TEXT_BYTES, optional=True)
    return {'manifest_asset': identity} if identity is not None else {}


def regular_release(value):
    """Keep only the immutable identity needed to fetch one regular APK."""
    if (not isinstance(value, dict) or type(value.get('id')) is not int or value['id'] <= 0
            or value.get('draft') is not False or value.get('prerelease') is not False
            or value.get('immutable') is not True or not TAG.fullmatch(str(value.get('tag_name', '')))):
        raise ValueError('expected an immutable published regular Collie release')
    tag = value['tag_name']
    assets = value.get('assets')
    if not isinstance(assets, list):
        raise ValueError('release assets are unavailable')
    matches = [asset for asset in assets if isinstance(asset, dict)
               and asset.get('name') == channels.STABLE.apk_asset]
    if len(matches) != 1:
        raise ValueError('release must contain exactly one canonical Collie APK')
    asset = matches[0]
    expected_url = f'https://github.com/{APP_REPOSITORY}/releases/download/{tag}/collie.apk'
    if (type(asset.get('id')) is not int or asset['id'] <= 0 or asset.get('state') != 'uploaded'
            or type(asset.get('size')) is not int or not 0 < asset['size'] <= MAX_BYTES
            or not re.fullmatch(r'sha256:[0-9a-f]{64}', str(asset.get('digest', '')))
            or asset.get('browser_download_url') != expected_url):
        raise ValueError('canonical APK asset identity, digest or URL differs')
    return {'release_id': value['id'], 'tag': tag, 'asset_id': asset['id'],
            'bytes': asset['size'], 'sha256': asset['digest'][7:], 'url': expected_url,
            **manifest_asset(assets, tag)}


def select_releases(latest, keep, *, api=github_read):
    if not isinstance(latest, str) or not TAG.fullmatch(latest):
        raise ValueError('regular release tag required')
    if type(keep) is not int or not 1 <= keep <= 50:
        raise ValueError('keep must be between 1 and 50')
    selected = regular_release(api(f'repos/{APP_REPOSITORY}/releases/tags/{latest}'))
    if selected['tag'] != latest:
        raise ValueError('selected release tag differs from requested release')
    result = [selected]
    seen_ids = {selected['release_id']}
    seen_tags = {latest}
    if keep == 1:
        return result
    anchored = False
    for page in range(1, MAX_PAGES + 1):
        values = api(f'repos/{APP_REPOSITORY}/releases?per_page=100&page={page}')
        if not isinstance(values, list) or len(values) > 100:
            raise ValueError('invalid public release history page')
        for value in values:
            if (not isinstance(value, dict) or type(value.get('draft')) is not bool
                    or type(value.get('prerelease')) is not bool):
                raise ValueError('malformed public release history')
            tag = value.get('tag_name')
            if value['draft'] or value['prerelease'] or not isinstance(tag, str) or not TAG.fullmatch(tag):
                continue
            release = regular_release(value)
            if release['release_id'] == selected['release_id']:
                if release != selected or anchored:
                    raise ValueError('selected release changed during history lookup')
                anchored = True
                continue
            if release['tag'] == latest:
                raise ValueError('selected release tag has a different history identity')
            # A publication after the /latest lookup must not silently become
            # the recommended APK under the older selected release's listing.
            if not anchored:
                continue
            if release['release_id'] in seen_ids or release['tag'] in seen_tags:
                raise ValueError('duplicate or changing public release history; retry')
            seen_ids.add(release['release_id'])
            seen_tags.add(release['tag'])
            result.append(release)
            if len(result) == keep:
                return result
        if len(values) < 100:
            if not anchored:
                raise ValueError('selected release was not found in public history; retry')
            return result
    raise ValueError('public release history exceeds verification page limit')


def source_commit(tag, *, api=github_read):
    if not TAG.fullmatch(tag):
        raise ValueError('regular release tag required')
    ref = api(f'repos/{APP_REPOSITORY}/git/ref/tags/{tag}')
    if not isinstance(ref, dict) or ref.get('ref') != f'refs/tags/{tag}':
        raise ValueError('release source tag is unavailable')
    obj = ref.get('object')
    tag_oid = None
    for _ in range(5):
        if not isinstance(obj, dict) or not SHA.fullmatch(str(obj.get('sha', ''))):
            raise ValueError('invalid immutable source object')
        if obj.get('type') == 'commit' and tag_oid is not None:
            return obj['sha'], tag_oid
        if obj.get('type') != 'tag':
            raise ValueError('release source must have an annotated tag')
        if tag_oid is None:
            tag_oid = obj['sha']
        annotation = api(f"repos/{APP_REPOSITORY}/git/tags/{obj['sha']}")
        if not isinstance(annotation, dict) or annotation.get('sha') != obj['sha']:
            raise ValueError('release tag object differs')
        obj = annotation.get('object')
    raise ValueError('release source tag nesting exceeds limit')


def listing_commit(tag, commit):
    """Use the release source, except for one recorded pre-Fastlane release."""
    if not isinstance(tag, str) or not TAG.fullmatch(tag) or not SHA.fullmatch(commit):
        raise ValueError('immutable release source identity required')
    return LEGACY_LISTINGS.get((tag, commit), commit)


class FixedOriginRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, hosts):
        super().__init__()
        self.hosts = frozenset(hosts)

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urlparse(newurl)
        if target.scheme != 'https' or target.netloc not in self.hosts:
            raise ValueError('input redirect left its fixed HTTPS origins')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def write_verified(source, destination, size, digest):
    """Stream input bytes while enforcing their declared size and SHA-256."""
    hasher = hashlib.sha256()
    count = 0
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open('xb') as output:
        while True:
            block = source.read(min(1024 * 1024, size - count + 1))
            if not block:
                break
            count += len(block)
            if count > size:
                raise ValueError('download exceeds declared byte size')
            hasher.update(block)
            output.write(block)
    if count != size or hasher.hexdigest() != digest:
        raise ValueError('download differs from approved size or digest')


def download(url, destination, size, digest, *, hosts):
    """Bound downloads and stream APK/native bytes instead of buffering them."""
    parsed = urlparse(url)
    if parsed.scheme != 'https' or parsed.netloc not in hosts:
        raise ValueError('input URL left its fixed HTTPS origin')
    opener = urllib.request.build_opener(FixedOriginRedirect(hosts))
    with opener.open(url, timeout=60) as response:
        final = urlparse(response.geturl())
        if final.scheme != 'https' or final.netloc not in hosts or response.status != 200:
            raise ValueError('input redirect left its fixed HTTPS origins')
        write_verified(response, destination, size, digest)


def apk_identity(apk, build_tools):
    """Read release package metadata and the verified signer from the actual APK."""
    tools = Path(build_tools)
    certs = subprocess.run([str(tools / 'apksigner'), 'verify', '--print-certs', str(apk)],
                           check=True, capture_output=True, text=True).stdout
    digests = re.findall(r'certificate SHA-256 digest: ([0-9a-f]{64})', certs)
    badging = subprocess.run([str(tools / 'aapt2'), 'dump', 'badging', str(apk)],
                             check=True, capture_output=True, text=True).stdout
    match = re.search(r"package: name='([^']+)' versionCode='([0-9]+)' versionName='([^']+)'", badging)
    if len(digests) != 1 or match is None or 'application-debuggable' in badging:
        raise ValueError('expected one APK signer and release package metadata')
    return {'package': match[1], 'version_code': int(match[2]), 'version_name': match[3],
            'apk_certificate_sha256': digests[0]}


def verify_manifest(release, profile, commit, identity, *, downloader):
    asset = release['manifest_asset']
    with TemporaryDirectory() as work:
        path = Path(work) / 'release.json'
        downloader(asset['url'], path, asset['bytes'], asset['sha256'], hosts=ASSET_HOSTS)
        manifest = json.loads(path.read_bytes(), object_pairs_hook=unique_properties)
    version = release['tag'][len(profile.tag_prefix):]
    if (identity['package'] != profile.package or identity['apk_certificate_sha256'] != profile.apk_certificate
            or identity['version_name'] != version or identity['version_code'] <= 0):
        raise ValueError('release.json APK package, signer or version differs from the channel and tag')
    expected = {'schema': 1, 'channel': profile.name, 'tag': release['tag'], 'source_commit': commit,
                'version_name': version, 'version_code': identity['version_code'], 'package_id': profile.package,
                'apk_name': profile.apk_asset, 'apk_sha256': release['sha256'], 'native_artifacts_path': NATIVE_PLAN}
    if (manifest != expected or type(manifest['schema']) is not int
            or type(manifest['version_code']) is not int):
        raise ValueError('release.json differs from the verified tag, channel or APK')
    return manifest


def stage_releases(latest, keep, destination, *, build_tools=None, api=github_read, downloader=download):
    destination = Path(destination)
    if destination.exists():
        raise ValueError('new APK input directory required')
    releases = select_releases(latest, keep, api=api)
    if sum(value['bytes'] for value in releases) > MAX_BYTES:
        raise ValueError('selected release inputs exceed byte budget')
    commit, tag_oid = source_commit(latest, api=api)
    destination.mkdir(parents=True)
    for index, release in enumerate(releases):
        tag = release['tag']
        apk = destination / (tag + '.apk')
        downloader(release['url'], apk, release['bytes'], release['sha256'], hosts=ASSET_HOSTS)
        if 'manifest_asset' in release:
            if build_tools is None:
                raise ValueError('Android build tools are required to verify release.json against the APK')
            source = (commit, tag_oid) if tag == latest else source_commit(tag, api=api)
            manifest = verify_manifest(release, channels.STABLE, source[0], apk_identity(apk, build_tools),
                                       downloader=downloader)
            releases[index] = {**release, 'manifest': manifest}
            if tag != latest and source_commit(tag, api=api) != source:
                raise ValueError('historical source tag changed during staging')
        current = regular_release(api(f"repos/{APP_REPOSITORY}/releases/{release['release_id']}"))
        if current != release:
            raise ValueError('release changed while its APK was staged; retry')
    if source_commit(latest, api=api) != (commit, tag_oid):
        raise ValueError('selected source tag changed during staging')
    return {'schema': 1, 'kind': 'collie-feed-release-inputs', 'repository': APP_REPOSITORY,
            'latest': latest, 'source_commit': commit, 'listing_commit': listing_commit(latest, commit),
            'tag_object': tag_oid, 'releases': releases}


def unique_properties(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('duplicate metadata JSON property')
        value[key] = item
    return value


def github_metadata_read(endpoint, limit):
    """Read bounded Git data, never a checkout or a URL supplied by that data."""
    command = ['gh', 'api', '--hostname', 'github.com', '--method', 'GET',
               '-H', 'Accept: application/vnd.github+json', endpoint]
    with subprocess.Popen(command, stdout=subprocess.PIPE) as request:
        try:
            data = bytearray()
            while block := request.stdout.read(min(65536, limit - len(data) + 1)):
                if len(block) > limit - len(data):
                    raise ValueError('metadata API response exceeds byte budget')
                data.extend(block)
            if request.wait() != 0:
                raise ValueError('metadata Git API request failed')
            return bytes(data)
        except BaseException:
            request.kill()
            request.wait()
            raise


def git_digest(kind, data):
    return hashlib.sha1(kind.encode('ascii') + b' ' + str(len(data)).encode('ascii') + b'\0' + data).hexdigest()


def tree_digest(entries):
    """Git sorts tree names as though a directory's name ends with a slash."""
    ordered = sorted(entries, key=lambda x: x['path'].encode('utf-8') + (b'/' if x['type'] == 'tree' else b''))
    data = b''.join(entry['mode'].lstrip('0').encode('ascii') + b' ' + entry['path'].encode('utf-8') +
                    b'\0' + bytes.fromhex(entry['sha']) for entry in ordered)
    return git_digest('tree', data)


class MetadataReader:
    def __init__(self, api):
        self.api = api
        self.api_bytes = 0
        self.trees = {}
        self.commits = {}

    def read(self, suffix):
        limit = min(MAX_METADATA_RESPONSE_BYTES, MAX_METADATA_API_BYTES - self.api_bytes)
        if limit <= 0:
            raise ValueError('metadata API aggregate byte budget exhausted')
        raw = self.api(f'repos/{APP_REPOSITORY}/git/{suffix}', limit)
        if not isinstance(raw, bytes) or len(raw) > limit:
            raise ValueError('metadata API response exceeds byte budget')
        self.api_bytes += len(raw)
        value = json.loads(raw, object_pairs_hook=unique_properties)
        if not isinstance(value, dict):
            raise ValueError('metadata Git API response must be an object')
        return value

    def root_tree(self, commit):
        if commit not in self.commits:
            value = self.read('commits/' + commit)
            tree = value.get('tree')
            if (value.get('sha') != commit or not isinstance(tree, dict)
                    or not isinstance(tree.get('sha'), str) or not SHA.fullmatch(tree['sha'])):
                raise ValueError('metadata commit/root tree identity differs')
            self.commits[commit] = tree['sha']
        return self.commits[commit]

    def tree(self, oid, *, recursive=False):
        key = (oid, recursive)
        if key in self.trees:
            return self.trees[key]
        value = self.read('trees/' + oid + ('?recursive=1' if recursive else ''))
        values = value.get('tree')
        if (value.get('sha') != oid or value.get('truncated') is not False
                or not isinstance(values, list) or len(values) > MAX_TREE_ENTRIES):
            raise ValueError('metadata tree is truncated, oversized or has a different identity')
        entries = {}
        modes = {'040000': 'tree', '100644': 'blob', '100755': 'blob', '120000': 'blob', '160000': 'commit'}
        for entry in values:
            path = entry.get('path') if isinstance(entry, dict) else None
            if (not isinstance(path, str) or not 0 < len(path) <= 512 or path in entries
                    or any(part in ('', '.', '..') for part in path.split('/'))
                    or any(ord(char) < 32 or ord(char) == 127 or char == '\\' for char in path)
                    or (not recursive and '/' in path)
                    or not isinstance(entry.get('sha'), str) or not SHA.fullmatch(entry['sha'])
                    or entry.get('mode') not in modes or entry.get('type') != modes[entry['mode']]
                    or (entry['type'] == 'blob' and (type(entry.get('size')) is not int or entry['size'] < 0))):
                raise ValueError('invalid or duplicate metadata tree entry')
            entries[path] = entry
        if recursive:
            parents = {'': []}
            for path, entry in entries.items():
                parent, _, name = path.rpartition('/')
                if parent and (parent not in entries or entries[parent]['type'] != 'tree'):
                    raise ValueError('metadata tree has a missing parent or file/directory collision')
                parents.setdefault(parent, []).append({**entry, 'path': name})
                if entry['type'] == 'tree':
                    parents.setdefault(path, [])
            for path, children in parents.items():
                expected = entries[path]['sha'] if path else oid
                if tree_digest(children) != expected:
                    raise ValueError('metadata tree bytes do not match their Git identity')
        elif tree_digest(values) != oid:
            raise ValueError('metadata tree bytes do not match their Git identity')
        self.trees[key] = entries
        return entries

    def directory(self, root, path):
        oid = root
        for part in path.split('/'):
            entry = self.tree(oid).get(part)
            if entry is None or entry['type'] != 'tree' or entry['mode'] != '040000':
                raise ValueError('required metadata directory is absent or is not a tree: ' + path)
            oid = entry['sha']
        return oid

    def blob(self, entry):
        value = self.read('blobs/' + entry['sha'])
        content = value.get('content')
        size = entry['size']
        # GitHub wraps base64 in newlines. Bound its encoded representation
        # before allocating the compact form or decoding the content.
        maximum_encoded = 4 * ((size + 2) // 3)
        if (value.get('sha') != entry['sha'] or type(value.get('size')) is not int
                or value['size'] != size or value.get('encoding') != 'base64'
                or not isinstance(content, str) or len(content) > maximum_encoded * 2 + 2):
            raise ValueError('metadata blob response identity, size or encoding differs')
        encoded = content.replace('\n', '')
        if len(encoded) != maximum_encoded:
            raise ValueError('metadata blob base64 size differs')
        try:
            data = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError('invalid metadata blob base64') from exc
        if len(data) != size or git_digest('blob', data) != entry['sha']:
            raise ValueError('metadata blob bytes do not match their Git identity')
        return data


def listing_kind(path, entry):
    parts = path.split('/')
    if not LOCALE.fullmatch(parts[0]):
        raise ValueError('unreviewed Fastlane locale or path')
    tail = '/'.join(parts[1:])
    if entry['type'] == 'tree' and entry['mode'] == '040000':
        if tail in ('', 'changelogs', 'images', 'images/phoneScreenshots'):
            return None
    elif entry['type'] == 'blob' and entry['mode'] == '100644':
        if tail in ('title.txt', 'short_description.txt', 'full_description.txt') or re.fullmatch(r'changelogs/[0-9]+\.txt', tail):
            if entry['size'] <= MAX_TEXT_BYTES:
                return 'text'
        if tail in ('images/icon.png', 'images/featureGraphic.png') or re.fullmatch(r'images/phoneScreenshots/[A-Za-z0-9][A-Za-z0-9_-]{0,63}\.png', tail):
            if 0 < entry['size'] <= MAX_PNG_BYTES:
                return 'png'
    raise ValueError('unreviewed Fastlane path, mode or file size: ' + path)


def metadata_sources(receipt):
    if (not isinstance(receipt, dict) or type(receipt.get('schema')) is not int or receipt['schema'] != 1
            or receipt.get('kind') != 'collie-feed-release-inputs' or receipt.get('repository') != APP_REPOSITORY
            or not isinstance(receipt.get('latest'), str) or not TAG.fullmatch(receipt['latest'])
            or any(not isinstance(receipt.get(key), str) or not SHA.fullmatch(receipt[key])
                   for key in ('source_commit', 'listing_commit', 'tag_object'))
            or not isinstance(receipt.get('releases'), list) or not 1 <= len(receipt['releases']) <= 50
            or not isinstance(receipt['releases'][0], dict) or receipt['releases'][0].get('tag') != receipt['latest']
            or receipt['listing_commit'] != listing_commit(receipt['latest'], receipt['source_commit'])):
        raise ValueError('validated immutable release input receipt required')
    return receipt['source_commit'], receipt['listing_commit']


def export_metadata(receipt, destination, *, api=github_metadata_read):
    """Export only reviewed data paths from the already accepted release receipt.

    The release's signing/publication approval is a prerequisite, not established
    here. No Git checkout, hooks, filters, app actions or app code are executed.
    """
    source, listing = metadata_sources(receipt)
    destination = Path(destination)
    if destination.exists() or destination.is_symlink() or any(parent.is_symlink() for parent in destination.parents):
        raise ValueError('new metadata directory with regular parents required')
    reader = MetadataReader(api)
    source_tree = reader.root_tree(source)
    listing_tree = reader.root_tree(listing)
    fastlane_tree = reader.directory(listing_tree, METADATA_PREFIX)
    entries = reader.tree(fastlane_tree, recursive=True)
    plan = []
    for path, entry in sorted(entries.items()):
        kind = listing_kind(path, entry)
        if kind is not None:
            plan.append((METADATA_PREFIX + '/' + path, listing, kind, entry))
    if not any(path == METADATA_PREFIX + '/en-US/images/icon.png' for path, *_ in plan):
        raise ValueError('Fastlane requires the en-US PNG repository icon')
    native_tree = reader.directory(source_tree, 'build-inputs')
    native = reader.tree(native_tree).get('native-artifacts.json')
    if (native is None or native['type'] != 'blob' or native['mode'] != '100644'
            or not 0 < native['size'] <= MAX_TEXT_BYTES):
        raise ValueError('exact source native plan must be a bounded regular JSON file')
    plan.append((NATIVE_PLAN, source, 'json', native))
    if len(plan) > MAX_METADATA_FILES or sum(entry['size'] for _, _, _, entry in plan) > MAX_METADATA_BYTES:
        raise ValueError('metadata file count or aggregate byte budget exceeded')
    verified = []
    for path, commit, kind, entry in plan:
        data = reader.blob(entry)
        if kind == 'png':
            if not data.startswith(b'\x89PNG\r\n\x1a\n'):
                raise ValueError('Fastlane image is not PNG data')
        elif kind == 'json':
            native_records(json.loads(data, object_pairs_hook=unique_properties))
        else:
            text = data.decode('utf-8')
            if any(ord(char) < 32 and char not in '\n\r\t' for char in text):
                raise ValueError('Fastlane text contains control characters')
        verified.append((path, data, {'path': path, 'source_commit': commit, 'git_blob': entry['sha'],
                                    'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}))
    # Every input is verified before any file is exported. The fresh directory
    # contains only ordinary non-executable files, never source-controlled modes.
    destination.mkdir(mode=0o700)
    for path, data, _ in verified:
        target = destination.joinpath(*path.split('/'))
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
        with target.open('xb') as output:
            output.write(data)
        target.chmod(0o644)
    return {'schema': 1, 'kind': 'collie-feed-metadata-inputs', 'repository': APP_REPOSITORY,
            'latest': receipt['latest'], 'source_commit': source, 'source_tree': source_tree,
            'listing_commit': listing, 'listing_tree': listing_tree, 'fastlane_tree': fastlane_tree,
            'files': [record for _, _, record in verified]}


NATIVE_ROUTE = re.compile(re.escape(ORIGIN) + r'(native-inputs/v[1-9][0-9]{0,2}/maven/)(.+)')


def native_site_path(value):
    """Site path of one native file from its URL; a plan may mix routes (v1 ringrtc, v2 libsignal)."""
    match = NATIVE_ROUTE.fullmatch(str(value.get('url'))) if isinstance(value, dict) else None
    if match is None or match[2] != value.get('path'):
        raise ValueError('invalid, duplicate or noncanonical native input')
    return match[1] + match[2]


def native_records(plan):
    values = plan.get('files') if isinstance(plan, dict) else None
    if not isinstance(values, list) or not 0 < len(values) <= 40000:
        raise ValueError('native plan must contain a bounded file inventory')
    records = {}
    for value in values:
        path = value.get('path') if isinstance(value, dict) else None
        if (not isinstance(path, str) or not re.fullmatch(r'[A-Za-z0-9_.+/-]+', path)
                or any(part in ('', '.', '..') for part in path.split('/'))
                or PurePosixPath(path).is_absolute()
                or type(value.get('bytes')) is not int or value['bytes'] <= 0
                or not DIGEST.fullmatch(str(value.get('sha256', '')))
                or native_site_path(value) in records):
            raise ValueError('invalid, duplicate or noncanonical native input')
        records[native_site_path(value)] = value
    if sum(value['bytes'] for value in records.values()) > MAX_BYTES:
        raise ValueError('native input plan exceeds byte budget')
    for path in records:
        if any('/'.join(path.split('/')[:i]) in records for i in range(1, len(path.split('/')))):
            raise ValueError('native input plan has a file/directory collision')
    return records


def stage_native(plan, destination, *, downloader=download):
    records = native_records(plan)
    destination = Path(destination)
    if destination.exists():
        raise ValueError('new native input directory required')
    destination.mkdir(parents=True)
    for path, value in sorted(records.items()):
        downloader(value['url'], destination / path, value['bytes'], value['sha256'],
                   hosts={'repo.collie.im'})
    return len(records)


def fetch_native_archive(archive, records, destination, *, api, downloader, work):
    tag, name = native_generations.ARCHIVE_URL.fullmatch(archive['url']).groups()
    release = api(f'repos/{native_generations.REPOSITORY}/releases/tags/{tag}')
    if (not isinstance(release, dict) or release.get('tag_name') != tag
            or release.get('immutable') is not True or release.get('draft') is not False):
        raise ValueError('native archive requires a published immutable CRE release')
    asset = release_asset(release.get('assets'), name, archive['url'], MAX_BYTES)
    if asset['bytes'] != archive['bytes'] or asset['sha256'] != archive['sha256']:
        raise ValueError('native archive asset differs from the registered pin')
    path = work / name
    downloader(archive['url'], path, archive['bytes'], archive['sha256'], hosts=ASSET_HOSTS)
    with zipfile.ZipFile(path) as bundle:
        members = bundle.infolist()
        if len(members) != len(records) or {member.orig_filename for member in members} != records.keys():
            raise ValueError('native archive must contain exactly the pinned generation files')
        for member in members:
            name = member.orig_filename
            entry = records[name]
            kind = stat.S_IFMT(member.external_attr >> 16)
            if kind not in (0, stat.S_IFREG) or member.file_size != entry['bytes']:
                raise ValueError('native archive member is not a pinned regular file of the expected size')
            with bundle.open(member) as source:
                write_verified(source, destination / name, entry['bytes'], entry['sha256'])


def stage_native_generation(manifest, destination, *, api=github_read, downloader=download,
                            baseline_path=native_generations.BASELINE, registry_path=native_generations.REGISTRY):
    generation = native_generations.load_generation(manifest, baseline_path=baseline_path, registry_path=registry_path)
    records = generation['files']
    if sum(value['bytes'] for value in records.values()) > MAX_BYTES:
        raise ValueError('native input generation exceeds byte budget')
    destination = Path(destination)
    if destination.exists() or destination.is_symlink():
        raise ValueError('new native input directory required')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=destination.parent) as temporary:
        work = Path(temporary)
        staged = work / 'native'
        staged.mkdir()
        archived = {}
        if 'archive' in generation:
            prefix = f"native-inputs/v{generation['generation']}/"
            archived = {path: entry for path, entry in records.items() if path.startswith(prefix)}
            fetch_native_archive(generation['archive'], archived, staged, api=api, downloader=downloader, work=work)
        for path, entry in sorted(records.items()):
            if path not in archived:
                downloader(ORIGIN + path, staged / path, entry['bytes'], entry['sha256'], hosts={'repo.collie.im'})
        staged.rename(destination)
    return len(records)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    releases = sub.add_parser('releases')
    releases.add_argument('--latest', required=True)
    releases.add_argument('--keep', type=int, default=1)
    releases.add_argument('--apks', type=Path, required=True)
    releases.add_argument('--receipt', type=Path, required=True)
    releases.add_argument('--build-tools', type=Path)
    native = sub.add_parser('native')
    source = native.add_mutually_exclusive_group(required=True)
    source.add_argument('--plan', type=Path)
    source.add_argument('--generation-manifest', type=Path, help='stage a registered CRE native generation for Pages')
    native.add_argument('--output', type=Path, required=True)
    metadata = sub.add_parser('metadata')
    metadata.add_argument('--receipt', type=Path, required=True)
    metadata.add_argument('--output', type=Path, required=True)
    metadata.add_argument('--evidence', type=Path, required=True)
    args = parser.parse_args()
    if args.action == 'releases':
        if args.receipt.exists():
            parser.error('release input receipt must not exist')
        value = stage_releases(args.latest, args.keep, args.apks, build_tools=args.build_tools)
        with args.receipt.open('x', encoding='utf-8') as output:
            output.write(json.dumps(value, sort_keys=True, indent=2) + '\n')
        print(json.dumps({key: value[key] for key in ('latest', 'source_commit', 'listing_commit')}))
    elif args.action == 'metadata':
        if args.evidence.exists() or args.evidence.is_symlink():
            parser.error('metadata evidence output must not exist')
        receipt = json.loads(args.receipt.read_text(), object_pairs_hook=unique_properties)
        value = export_metadata(receipt, args.output)
        with args.evidence.open('x', encoding='utf-8') as output:
            output.write(json.dumps(value, sort_keys=True, indent=2) + '\n')
        print(json.dumps({'metadata_files': len(value['files']), 'source_commit': value['source_commit'],
                          'listing_commit': value['listing_commit']}))
    else:
        count = (stage_native(json.loads(args.plan.read_text()), args.output) if args.plan
                 else stage_native_generation(args.generation_manifest, args.output))
        print(json.dumps({'native_files': count}))


if __name__ == '__main__':
    main()
