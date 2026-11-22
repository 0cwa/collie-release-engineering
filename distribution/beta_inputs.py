"""Select, verify and stage the newest published TrailBlazer Beta for the feed.

A Beta enters the feed the way Stable does: from its published GitHub release.
The release must be an immutable prerelease (GitHub then freezes its tag and
assets). Its single trailblazer-beta.apk must carry the pinned Beta package and
signer, its tag must be a verified annotated tag, and the APK must equal a
successful build-release.yml run of that tagged commit on trailblazer-beta plus
its signature (see apk_match.py). No app code runs here.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import urllib.request
import zipfile

import channel_profile as channels
import release_inputs

APP = release_inputs.APP_REPOSITORY
PROFILE = channels.BETA
TAG = re.compile(re.escape(PROFILE.tag_prefix) +
                 r'([0-9]+\.[0-9]+\.[0-9]+(?:-[0-9]+)?(?:-(?:alpha|beta|rc)[0-9]+)?)')
SHA = release_inputs.SHA
DIGEST = release_inputs.DIGEST
METADATA_PREFIX = 'fastlane/metadata/' + PROFILE.name
BUILD_WORKFLOW = '.github/workflows/build-release.yml'
BUILD_ARTIFACT = 'unsigned-release-apk'
ARTIFACT_FILES = {'SHA256SUMS', 'source-tree.txt', 'source-commit.txt', PROFILE.apk_asset}
MAX_BYTES = release_inputs.MAX_BYTES
STATE_URL = release_inputs.ORIGIN + 'feed-state.json'
ASSET_HOSTS = {'github.com', 'release-assets.githubusercontent.com'}


def beta_release(value):
    """Keep the immutable identity of one published Beta prerelease."""
    if (not isinstance(value, dict) or type(value.get('id')) is not int or value['id'] <= 0
            or value.get('draft') is not False or value.get('prerelease') is not True
            or value.get('immutable') is not True):
        raise ValueError('expected an immutable published TrailBlazer prerelease')
    tag = value.get('tag_name')
    match = TAG.fullmatch(tag) if isinstance(tag, str) else None
    if match is None:
        raise ValueError('TrailBlazer release tag differs from the channel contract')
    version = match[1]
    if value.get('name') != f'{PROFILE.app_title} {version}':
        raise ValueError('TrailBlazer release title differs from its tag')
    assets = value.get('assets')
    if not isinstance(assets, list):
        raise ValueError('release assets are unavailable')
    matches = [asset for asset in assets if isinstance(asset, dict) and asset.get('name') == PROFILE.apk_asset]
    if len(matches) != 1:
        raise ValueError('release must contain exactly one ' + PROFILE.apk_asset)
    asset = matches[0]
    url = f'https://github.com/{APP}/releases/download/{tag}/{PROFILE.apk_asset}'
    if (type(asset.get('id')) is not int or asset['id'] <= 0 or asset.get('state') != 'uploaded'
            or type(asset.get('size')) is not int or not 0 < asset['size'] <= MAX_BYTES
            or not re.fullmatch(r'sha256:[0-9a-f]{64}', str(asset.get('digest', '')))
            or asset.get('browser_download_url') != url):
        raise ValueError('Beta APK asset identity, digest or URL differs')
    return {'release_id': value['id'], 'tag': tag, 'version_name': version, 'asset_id': asset['id'],
            'bytes': asset['size'], 'sha256': asset['digest'][7:], 'url': url}


def newest_tag(*, api=release_inputs.github_read):
    """The most recently published TrailBlazer release; drafts are ignored."""
    newest = None
    for page in range(1, release_inputs.MAX_PAGES + 1):
        values = api(f'repos/{APP}/releases?per_page=100&page={page}')
        if not isinstance(values, list) or len(values) > 100:
            raise ValueError('invalid public release history page')
        for value in values:
            if not isinstance(value, dict) or type(value.get('draft')) is not bool:
                raise ValueError('malformed public release history')
            tag = value.get('tag_name')
            if value['draft'] or not isinstance(tag, str) or not tag.startswith(PROFILE.tag_prefix):
                continue
            published = value.get('published_at')
            if not isinstance(published, str) or not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:]{8}Z', published):
                raise ValueError('published TrailBlazer release lacks a publication time')
            if newest is None or published > newest[0]:
                newest = (published, tag)
        if len(values) < 100:
            return None if newest is None else newest[1]
    raise ValueError('public release history exceeds verification page limit')


def served_version(*, fetch=None):
    """Beta versionName in the served feed-state.json, or None before the first Beta."""
    if fetch is None:
        def fetch():
            request = urllib.request.Request(STATE_URL, headers={'Cache-Control': 'no-cache'})
            with urllib.request.urlopen(request, timeout=60) as response:
                return response.read(65537)
    raw = fetch()
    if len(raw) > 65536:
        raise ValueError('served feed state exceeds byte limit')
    state = json.loads(raw)
    beta = state.get('beta') if isinstance(state, dict) else None
    if beta is None:
        return None
    if not isinstance(beta, dict) or not isinstance(beta.get('version_name'), str):
        raise ValueError('served feed state has a malformed Beta entry')
    return beta['version_name']


def tag_commit(tag, *, api=release_inputs.github_read):
    """A Beta tag must be one verified annotated tag pointing at a commit."""
    if not isinstance(tag, str) or not TAG.fullmatch(tag):
        raise ValueError('TrailBlazer tag required')
    ref = api(f'repos/{APP}/git/ref/tags/{tag}')
    ref = ref if isinstance(ref, dict) else {}
    obj = ref.get('object')
    if (ref.get('ref') != 'refs/tags/' + tag or not isinstance(obj, dict) or obj.get('type') != 'tag'
            or not SHA.fullmatch(str(obj.get('sha', '')))):
        raise ValueError('Beta release tag must be annotated')
    annotation = api(f"repos/{APP}/git/tags/{obj['sha']}")
    annotation = annotation if isinstance(annotation, dict) else {}
    target = annotation.get('object')
    if (annotation.get('sha') != obj['sha'] or annotation.get('tag') != tag or not isinstance(target, dict)
            or target.get('type') != 'commit' or not SHA.fullmatch(str(target.get('sha', '')))
            or not isinstance(annotation.get('verification'), dict)
            or annotation['verification'].get('verified') is not True):
        raise ValueError('Beta release tag is not a verified signed tag of a commit')
    return target['sha'], obj['sha']


def build_artifact(commit, *, api=release_inputs.github_read):
    """Newest successful Beta build of the commit that still has its unsigned APK artifact."""
    if not SHA.fullmatch(str(commit)):
        raise ValueError('source commit required')
    value = api(f'repos/{APP}/actions/workflows/build-release.yml/runs'
                f'?head_sha={commit}&status=success&per_page=100')
    runs = value.get('workflow_runs') if isinstance(value, dict) else None
    if not isinstance(runs, list):
        raise ValueError('build run list is unavailable')
    for run in sorted((r for r in runs if isinstance(r, dict) and type(r.get('id')) is int),
                      key=lambda r: r['id'], reverse=True):
        repository = run.get('head_repository')
        if (run.get('head_sha') != commit or run.get('head_branch') != PROFILE.source_branch
                or run.get('event') != 'workflow_dispatch' or run.get('path') != BUILD_WORKFLOW
                or run.get('status') != 'completed' or run.get('conclusion') != 'success'
                or not isinstance(repository, dict) or repository.get('full_name') != APP):
            continue
        listing = api(f"repos/{APP}/actions/runs/{run['id']}/artifacts?per_page=100")
        artifacts = listing.get('artifacts') if isinstance(listing, dict) else None
        if not isinstance(artifacts, list):
            raise ValueError('build artifact list is unavailable')
        named = [a for a in artifacts if isinstance(a, dict) and a.get('name') == BUILD_ARTIFACT]
        if len(named) != 1 or named[0].get('expired') is not False:
            continue
        artifact = named[0]
        source = artifact.get('workflow_run')
        if (type(artifact.get('id')) is not int or artifact['id'] <= 0
                or type(artifact.get('size_in_bytes')) is not int or not 0 < artifact['size_in_bytes'] <= MAX_BYTES
                or not re.fullmatch(r'sha256:[0-9a-f]{64}', str(artifact.get('digest', '')))
                or not isinstance(source, dict) or source.get('id') != run['id']
                or source.get('head_sha') != commit):
            raise ValueError('build artifact identity differs from its run')
        return {'run_id': run['id'], 'run_attempt': run.get('run_attempt'), 'artifact_id': artifact['id'],
                'bytes': artifact['size_in_bytes'], 'sha256': artifact['digest'][7:]}
    raise ValueError('no successful trailblazer-beta build of this commit with an unexpired APK artifact; '
                     'dispatch build-release.yml for the tag commit again')


def download_artifact(artifact, destination):
    """Artifact downloads need a token; the job's own GITHUB_TOKEN reads public repositories."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open('xb') as output:
        subprocess.run(['gh', 'api', '--hostname', 'github.com', '--method', 'GET',
                        f"repos/{APP}/actions/artifacts/{artifact['artifact_id']}/zip"],
                       check=True, stdout=output)
    if destination.stat().st_size != artifact['bytes'] or file_sha256(destination) != artifact['sha256']:
        raise ValueError('downloaded build artifact differs from its recorded size or digest')


def file_sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as source:
        for block in iter(lambda: source.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def extract_unsigned(archive, destination, commit):
    """Take only the unsigned APK from the fixed artifact layout and check its records."""
    destination = Path(destination)
    with zipfile.ZipFile(archive) as bundle:
        members = bundle.infolist()
        names = [member.filename for member in members]
        if sorted(names) != sorted(ARTIFACT_FILES) or len(set(names)) != len(names):
            raise ValueError('build artifact must contain exactly ' + ', '.join(sorted(ARTIFACT_FILES)))
        if sum(member.file_size for member in members) > MAX_BYTES:
            raise ValueError('build artifact expands beyond byte budget')
        if bundle.read('source-commit.txt').decode('ascii').strip() != commit:
            raise ValueError('build artifact was made from a different commit')
        sums = bundle.read('SHA256SUMS').decode('ascii').splitlines()
        destination.mkdir(parents=True, exist_ok=False)
        apk = destination / PROFILE.apk_asset
        with bundle.open(PROFILE.apk_asset) as source, apk.open('xb') as output:
            for block in iter(lambda: source.read(1 << 20), b''):
                output.write(block)
    if sums != [f'{file_sha256(apk)}  {PROFILE.apk_asset}']:
        raise ValueError('build artifact SHA256SUMS differs from its APK')
    return apk


def apk_identity(apk, build_tools):
    """Same signer and badging reads as feed.apk_identity, which the index build applies again."""
    tools = Path(build_tools)
    certs = subprocess.run([str(tools / 'apksigner'), 'verify', '--print-certs', str(apk)],
                           check=True, capture_output=True, text=True).stdout
    digests = re.findall(r'certificate SHA-256 digest: ([0-9a-f]{64})', certs)
    badging = subprocess.run([str(tools / 'aapt2'), 'dump', 'badging', str(apk)],
                             check=True, capture_output=True, text=True).stdout
    match = re.search(r"package: name='([^']+)' versionCode='([0-9]+)' versionName='([^']+)'", badging)
    if len(digests) != 1 or match is None or 'application-debuggable' in badging:
        lines = [line for line in certs.splitlines() if 'SHA-256 digest' in line]
        raise ValueError('expected one APK signer and release package metadata: ' + '; '.join(lines))
    return {'package': match[1], 'version_code': int(match[2]), 'version_name': match[3],
            'apk_certificate_sha256': digests[0]}


def fetch_release(tag, *, api=release_inputs.github_read):
    selected = beta_release(api(f'repos/{APP}/releases/tags/{tag}'))
    if selected['tag'] != tag:
        raise ValueError('release response names a different tag')
    return selected


def verify(tag, work, build_tools, *, api=release_inputs.github_read,
           downloader=release_inputs.download, artifact_downloader=download_artifact):
    """Download the release APK and its CI build; check everything except the byte match."""
    work = Path(work)
    if work.exists():
        raise ValueError('new Beta verification directory required')
    release = fetch_release(tag, api=api)
    commit, tag_object = tag_commit(tag, api=api)
    build = build_artifact(commit, api=api)
    work.mkdir(parents=True)
    published = work / 'published' / PROFILE.apk_asset
    downloader(release['url'], published, release['bytes'], release['sha256'], hosts=ASSET_HOSTS)
    identity = apk_identity(published, build_tools)
    if (identity['package'] != PROFILE.package or identity['apk_certificate_sha256'] != PROFILE.apk_certificate
            or identity['version_name'] != release['version_name']):
        raise ValueError('Beta APK package, signer or version differs from the channel and tag')
    artifact_downloader(build, work / 'build-artifact.zip')
    unsigned = extract_unsigned(work / 'build-artifact.zip', work / 'unsigned', commit)
    if fetch_release(tag, api=api) != release or tag_commit(tag, api=api) != (commit, tag_object):
        raise ValueError('release or tag changed during verification; retry')
    return {'schema': 1, 'kind': 'collie-beta-feed-input', 'repository': APP, **release,
            'version_code': identity['version_code'], 'source_commit': commit, 'tag_object': tag_object,
            'build': build, 'published_apk': str(published), 'unsigned_apk': str(unsigned)}


def stage(tag, expected_sha256, source_commit, destination, *, api=release_inputs.github_read,
          downloader=release_inputs.download):
    """Stage the verified release APK again in the job that signs the index."""
    destination = Path(destination)
    if destination.exists():
        raise ValueError('new Beta APK directory required')
    if not DIGEST.fullmatch(str(expected_sha256)) or not SHA.fullmatch(str(source_commit)):
        raise ValueError('verified Beta APK digest and source commit required')
    release = fetch_release(tag, api=api)
    if release['sha256'] != expected_sha256 or tag_commit(tag, api=api)[0] != source_commit:
        raise ValueError('Beta release differs from the verified release')
    destination.mkdir(parents=True)
    downloader(release['url'], destination / PROFILE.apk_asset, release['bytes'], release['sha256'],
               hosts=ASSET_HOSTS)
    return {'schema': 1, 'kind': 'collie-beta-feed-input', 'repository': APP, **release,
            'source_commit': source_commit}


def export_metadata(receipt, destination, *, api=release_inputs.github_metadata_read):
    """Export the Beta listing from the tagged source through the Git data API."""
    if (not isinstance(receipt, dict) or receipt.get('kind') != 'collie-beta-feed-input'
            or receipt.get('repository') != APP or not TAG.fullmatch(str(receipt.get('tag', '')))
            or not SHA.fullmatch(str(receipt.get('source_commit', '')))):
        raise ValueError('verified Beta feed input receipt required')
    destination = Path(destination)
    if destination.exists() or destination.is_symlink():
        raise ValueError('new metadata directory required')
    commit = receipt['source_commit']
    reader = release_inputs.MetadataReader(api)
    tree = reader.directory(reader.root_tree(commit), METADATA_PREFIX)
    plan = []
    for path, entry in sorted(reader.tree(tree, recursive=True).items()):
        kind = release_inputs.listing_kind(path, entry)
        if kind is not None:
            plan.append((path, kind, entry))
    if not any(path == 'en-US/images/icon.png' for path, _, _ in plan):
        raise ValueError('Beta listing requires the en-US PNG icon')
    if (len(plan) > release_inputs.MAX_METADATA_FILES or
            sum(entry['size'] for _, _, entry in plan) > release_inputs.MAX_METADATA_BYTES):
        raise ValueError('metadata file count or aggregate byte budget exceeded')
    verified = []
    for path, kind, entry in plan:
        data = reader.blob(entry)
        if kind == 'png' and not data.startswith(b'\x89PNG\r\n\x1a\n'):
            raise ValueError('Fastlane image is not PNG data')
        if kind == 'text' and any(ord(c) < 32 and c not in '\n\r\t' for c in data.decode('utf-8')):
            raise ValueError('Fastlane text contains control characters')
        verified.append((path, data))
    destination.mkdir(mode=0o700)
    for path, data in verified:
        target = destination.joinpath(*path.split('/'))
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
        with target.open('xb') as output:
            output.write(data)
        target.chmod(0o644)
    return {'source_commit': commit, 'metadata_tree': tree,
            'files': [{'path': METADATA_PREFIX + '/' + path, 'sha256': hashlib.sha256(data).hexdigest()}
                      for path, data in verified]}


def write_new(path, value):
    with Path(path).open('x', encoding='utf-8') as output:
        output.write(json.dumps(value, sort_keys=True, indent=2) + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    select = sub.add_parser('select', help='print the newest Beta tag and whether the feed already serves it')
    select.add_argument('--force', action='store_true')
    check = sub.add_parser('verify')
    check.add_argument('--tag', required=True)
    check.add_argument('--work', type=Path, required=True)
    check.add_argument('--build-tools', type=Path, required=True)
    check.add_argument('--output', type=Path, required=True)
    staged = sub.add_parser('stage')
    staged.add_argument('--tag', required=True)
    staged.add_argument('--sha256', required=True)
    staged.add_argument('--source-commit', required=True)
    staged.add_argument('--apks', type=Path, required=True)
    staged.add_argument('--receipt', type=Path, required=True)
    metadata = sub.add_parser('metadata')
    metadata.add_argument('--receipt', type=Path, required=True)
    metadata.add_argument('--output', type=Path, required=True)
    metadata.add_argument('--evidence', type=Path, required=True)
    args = parser.parse_args()
    if args.action == 'select':
        tag = newest_tag()
        served = served_version()
        changed = tag is not None and (args.force or PROFILE.tag_prefix + str(served) != tag)
        print(json.dumps({'tag': tag or '', 'served': served, 'changed': changed}))
    elif args.action == 'verify':
        value = verify(args.tag, args.work, args.build_tools)
        write_new(args.output, value)
        print(json.dumps({k: value[k] for k in ('tag', 'version_code', 'sha256', 'source_commit')}))
    elif args.action == 'stage':
        write_new(args.receipt, stage(args.tag, args.sha256, args.source_commit, args.apks))
    else:
        receipt = json.loads(args.receipt.read_text(), object_pairs_hook=release_inputs.unique_properties)
        write_new(args.evidence, export_metadata(receipt, args.output))


if __name__ == '__main__':
    main()
