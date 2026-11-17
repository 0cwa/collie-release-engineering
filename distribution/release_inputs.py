"""Stage a regular Collie feed from exact published release identities.

GitHub's global release list also contains TrailBlazer and regular prereleases.
Resolve the selected full release directly, then paginate only for additional
history. Listing and native inputs are read from its immutable tagged source.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import urllib.request
from urllib.parse import urlparse

import channel_profile as channels

APP_REPOSITORY = '0cwa/collieim-android'
ORIGIN = 'https://repo.collie.im/'
NATIVE_PREFIX = 'native-inputs/v1/maven/'
MAX_BYTES = 950_000_000
MAX_PAGES = 100
TAG = re.compile(r'v[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9]+)?(?:-(?:alpha|beta|rc)[0-9]+)?')
SHA = re.compile(r'[0-9a-f]{40}')
DIGEST = re.compile(r'[0-9a-f]{64}')
# This full release predates Fastlane in the app tree. The exception reproduces
# the listing checkout recorded by Pages run 42957848111, job 130317140954.
# It never changes the APK source or native manifest revision.
LEGACY_LISTINGS = {
    ('v8.35.1-3-rc2', 'cc55c02bef64b2980c5f384a6a04f52ffb7bef92'):
        'af90bcda4ed7c83d4eee7582d6f3844b018500b0',
}


def github_read(endpoint):
    result = subprocess.run(['gh', 'api', '--method', 'GET', endpoint],
                            check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


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
            'bytes': asset['size'], 'sha256': asset['digest'][7:], 'url': expected_url}


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


def download(url, destination, size, digest, *, hosts):
    """Bound downloads and stream APK/native bytes instead of buffering them."""
    parsed = urlparse(url)
    if parsed.scheme != 'https' or parsed.netloc not in hosts:
        raise ValueError('input URL left its fixed HTTPS origin')
    hasher = hashlib.sha256()
    count = 0
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    opener = urllib.request.build_opener(FixedOriginRedirect(hosts))
    with opener.open(url, timeout=60) as response, destination.open('xb') as output:
        final = urlparse(response.geturl())
        if final.scheme != 'https' or final.netloc not in hosts or response.status != 200:
            raise ValueError('input redirect left its fixed HTTPS origins')
        while True:
            block = response.read(min(1024 * 1024, size - count + 1))
            if not block:
                break
            count += len(block)
            if count > size:
                raise ValueError('download exceeds declared byte size')
            hasher.update(block)
            output.write(block)
    if count != size or hasher.hexdigest() != digest:
        raise ValueError('download differs from approved size or digest')


def stage_releases(latest, keep, destination, *, api=github_read, downloader=download):
    destination = Path(destination)
    if destination.exists():
        raise ValueError('new APK input directory required')
    releases = select_releases(latest, keep, api=api)
    if sum(value['bytes'] for value in releases) > MAX_BYTES:
        raise ValueError('selected release inputs exceed byte budget')
    commit, tag_oid = source_commit(latest, api=api)
    destination.mkdir(parents=True)
    for release in releases:
        downloader(release['url'], destination / (release['tag'] + '.apk'),
                   release['bytes'], release['sha256'],
                   hosts={'github.com', 'release-assets.githubusercontent.com'})
        current = regular_release(api(f"repos/{APP_REPOSITORY}/releases/{release['release_id']}"))
        if current != release:
            raise ValueError('release changed while its APK was staged; retry')
    if source_commit(latest, api=api) != (commit, tag_oid):
        raise ValueError('selected source tag changed during staging')
    return {'schema': 1, 'kind': 'collie-feed-release-inputs', 'repository': APP_REPOSITORY,
            'latest': latest, 'source_commit': commit, 'listing_commit': listing_commit(latest, commit),
            'tag_object': tag_oid, 'releases': releases}


def native_records(plan):
    values = plan.get('files') if isinstance(plan, dict) else None
    if not isinstance(values, list) or not 0 < len(values) <= 40000:
        raise ValueError('native plan must contain a bounded file inventory')
    records = {}
    for value in values:
        path = value.get('path') if isinstance(value, dict) else None
        if (not isinstance(path, str) or not re.fullmatch(r'[A-Za-z0-9_.+/-]+', path)
                or any(part in ('', '.', '..') for part in path.split('/'))
                or PurePosixPath(path).is_absolute() or path in records
                or type(value.get('bytes')) is not int or value['bytes'] <= 0
                or not DIGEST.fullmatch(str(value.get('sha256', '')))
                or value.get('url') != ORIGIN + NATIVE_PREFIX + path):
            raise ValueError('invalid, duplicate or noncanonical native input')
        records[path] = value
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    releases = sub.add_parser('releases')
    releases.add_argument('--latest', required=True)
    releases.add_argument('--keep', type=int, default=1)
    releases.add_argument('--apks', type=Path, required=True)
    releases.add_argument('--receipt', type=Path, required=True)
    native = sub.add_parser('native')
    native.add_argument('--plan', type=Path, required=True)
    native.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.action == 'releases':
        if args.receipt.exists():
            parser.error('release input receipt must not exist')
        value = stage_releases(args.latest, args.keep, args.apks)
        with args.receipt.open('x', encoding='utf-8') as output:
            output.write(json.dumps(value, sort_keys=True, indent=2) + '\n')
        print(json.dumps({key: value[key] for key in ('latest', 'source_commit', 'listing_commit')}))
    else:
        plan = json.loads(args.plan.read_text())
        print(json.dumps({'native_files': stage_native(plan, args.output)}))


if __name__ == '__main__':
    main()
