"""Retain published immutable URLs from one verified, frozen Pages snapshot.

Old APKs are copied only after the new F-Droid index has been generated. They
remain downloadable by clients with a cached index without entering the new one.
"""
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import stat

import binary_repo
import channel_profile as channels


ORIGIN = 'https://repo.collie.im/'
INVENTORY = 'site-manifest.json'
INVENTORY_LIMIT = 32 * 1024 * 1024
FILE_LIMIT = 40000
SITE_LIMIT = 950_000_000
STABLE_ROUTE = 'fdroid/repo/'
BETA_ROUTE = 'fdroid/beta/repo/'
ARCHIVE_INDEX_FILES = frozenset({'entry.jar', 'entry.json', 'index-v1.jar', 'index-v1.json',
                                 'index-v2.json', 'index.jar', 'index.xml'})


def _safe_paths(files):
    for name in files:
        parts = name.split('/')
        if (not name or len(name) > 512 or len(parts) > 20 or
                any(part in ('', '.', '..') for part in parts) or
                re.fullmatch(r'[A-Za-z0-9_./=-]+', name) is None):
            raise ValueError('unsafe retained site path: ' + name)
        if any('/'.join(parts[:index]) in files for index in range(1, len(parts))):
            raise ValueError('retained site file/directory path conflict: ' + name)


def _regular(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError('retained site requires regular, unlinked files: ' + str(path))
    return info


def _record(path):
    info = _regular(path)
    digest = hashlib.sha256()
    size = 0
    with path.open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            size += len(block)
            if size > info.st_size:
                raise ValueError('retained site file grew during verification: ' + str(path))
            digest.update(block)
    if size != info.st_size:
        raise ValueError('retained site file changed during verification: ' + str(path))
    return {'sha256': digest.hexdigest(), 'bytes': size}


def _tree_records(root, byte_limit):
    if root.is_symlink() or not root.is_dir():
        raise ValueError('retained site requires an existing real directory')
    paths = {}
    size = 0
    for path in sorted(root.rglob('*')):
        info = path.lstat()
        if stat.S_ISDIR(info.st_mode):
            continue
        _regular(path)
        paths[path.relative_to(root).as_posix()] = path
        size += info.st_size
        if len(paths) > FILE_LIMIT + 1 or size > byte_limit:
            raise ValueError('retained site exceeds file or byte budget')
    _safe_paths(paths)
    return {name: _record(path) for name, path in paths.items()}


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate captured deploy baseline field')
        result[key] = value
    return result


def _baseline_digest(path):
    if _regular(path).st_size > 4096:
        raise ValueError('captured deploy baseline exceeds byte limit')
    value = json.loads(path.read_bytes(), object_pairs_hook=_unique)
    if (not isinstance(value, dict) or set(value) != {'schema', 'kind', 'origin', 'inventory_sha256'} or
            type(value.get('schema')) is not int or value['schema'] != 1 or
            value['kind'] != 'collie-pages-deploy-baseline' or value['origin'] != ORIGIN or
            not re.fullmatch('[0-9a-f]{64}', str(value.get('inventory_sha256')))):
        raise ValueError('invalid captured Stable deploy baseline')
    return value['inventory_sha256']


def versioned_native_path(name):
    """Only version-addressed Maven artifacts; mutable repository metadata is not retained."""
    parts = name.split('/')
    if (len(parts) < 7 or parts[0] != 'native-inputs' or
            re.fullmatch(r'v[1-9][0-9]*', parts[1]) is None or parts[2] != 'maven' or
            any(re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]*', part) is None for part in parts[3:])):
        return False
    artifact, version, filename = parts[-3:]
    if not re.fullmatch(r'[0-9][A-Za-z0-9_.-]*', version) or version.upper().endswith('-SNAPSHOT'):
        return False
    return re.fullmatch(re.escape(artifact + '-' + version) +
        r'(?:-[A-Za-z0-9_.-]+)?\.(?:aar|jar|pom)(?:\.(?:asc|sha1|sha256|sha512))?', filename) is not None


def generation_archive_path(name):
    match = re.fullmatch(r'_generations/[0-9a-f]{64}/([A-Za-z0-9_.-]+)', name)
    return match is not None and match[1] in ARCHIVE_INDEX_FILES | {'repository-receipt.json'}


def immutable_public_path(name):
    """Published versioned URLs shared by both whole-site publication paths."""
    if versioned_native_path(name) or generation_archive_path(name):
        return True
    for profile, route in ((channels.STABLE, STABLE_ROUTE), (channels.BETA, BETA_ROUTE)):
        if not name.startswith(route):
            continue
        relative = name[len(route):]
        if not binary_repo.public_path(relative, profile):
            return False
        return (relative.endswith('.apk') or relative.startswith('diff/') or
                (relative.startswith('icons') and not relative.endswith('/icon.png')) or
                # fdroid names listing artwork it content-addresses (icon_<b64 sha256>=.png);
                # Fastlane screenshots keep their source names and may change between releases.
                (relative.startswith(profile.package + '/') and
                 re.fullmatch(r'[A-Za-z0-9_-]+=\.png', relative.rsplit('/', 1)[-1]) is not None))
    return False


@dataclass(frozen=True)
class PreviousSite:
    root: Path
    files: dict
    inventory_record: dict
    beta_release: dict | None

    @property
    def beta(self):
        return {'inventory_sha256': self.inventory_record['sha256'],
                'files': {name[len(BETA_ROUTE):]: record for name, record in self.files.items()
                          if name.startswith(BETA_ROUTE)},
                'release': self.beta_release}

    def retained_files(self, preserve_beta, rebuild_beta=False):
        # A rebuilt Beta route keeps only the previous route's immutable URLs (APKs, listing files).
        if preserve_beta and rebuild_beta:
            raise ValueError('the Beta route is either preserved or rebuilt')
        if self.beta['files'] and not (preserve_beta or rebuild_beta):
            raise ValueError('a previous Beta route requires --preserve-beta or a rebuilt Beta route')
        return {name: record for name, record in self.files.items()
                if immutable_public_path(name) or
                (preserve_beta and name.startswith(BETA_ROUTE))}


def verify_snapshot(previous, *, byte_limit=SITE_LIMIT):
    actual = _tree_records(previous.root, byte_limit)
    if actual != {**previous.files, INVENTORY: previous.inventory_record}:
        raise ValueError('previous site snapshot is missing, extra, or changed since baseline capture')


def load_previous_site(snapshot, baseline, *, parse_inventory, byte_limit=SITE_LIMIT):
    """Bind every local byte to the unchanged deploy baseline and verify both signed routes."""
    snapshot, baseline = Path(snapshot), Path(baseline)
    expected = _baseline_digest(baseline)
    manifest = snapshot / INVENTORY
    if snapshot.is_symlink() or not snapshot.is_dir():
        raise ValueError('previous site snapshot directory is missing or linked')
    if _regular(manifest).st_size > INVENTORY_LIMIT:
        raise ValueError('previous site inventory exceeds byte limit')
    raw = manifest.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError('previous site inventory differs from captured deploy baseline')
    files = parse_inventory(raw)
    _safe_paths(files)
    if INVENTORY in files:
        raise ValueError('previous site inventory must not inventory itself')
    if len(files) > FILE_LIMIT or sum(record['bytes'] for record in files.values()) + len(raw) > byte_limit:
        raise ValueError('previous site inventory exceeds file or byte budget')
    for name in files:
        if (name == '_generations' or name.startswith('_generations/')) and not generation_archive_path(name):
            raise ValueError('previous site has an unexpected generation archive path: ' + name)
        if name.startswith('native-inputs/') and not versioned_native_path(name):
            raise ValueError('previous site has a non-versioned or unsupported native artifact: ' + name)
        if name.lower().endswith('.apk') and not any(
                re.fullmatch(re.escape(route + profile.package) + r'_[1-9][0-9]*\.apk', name)
                for profile, route in ((channels.STABLE, STABLE_ROUTE), (channels.BETA, BETA_ROUTE))):
            raise ValueError('previous site has an unexpected APK path: ' + name)
    previous = PreviousSite(snapshot, files, {'sha256': expected, 'bytes': len(raw)}, None)
    verify_snapshot(previous, byte_limit=byte_limit)
    beta_release = None
    for profile, route in ((channels.STABLE, STABLE_ROUTE), (channels.BETA, BETA_ROUTE)):
        names = {name[len(route):] for name in files if name.startswith(route)}
        if not names:
            continue
        if any(not binary_repo.public_path(name, profile) for name in names):
            raise ValueError('previous site contains an unexpected ' + profile.name + ' route path')
        if not {'entry.jar', 'index-v1.jar', 'index-v2.json'}.issubset(names):
            raise ValueError('previous site lacks signed ' + profile.name + ' indexes')
        index = binary_repo.verify_indexes(snapshot / route, profile.repository_certificate, profile)
        if index.get('repo', {}).get('address', '').rstrip('/') != profile.repository_url.rstrip('/'):
            raise ValueError('previous ' + profile.name + ' index address differs from fixed route')
        versions = binary_repo.release_tuples(index, profile)
        if not versions:
            raise ValueError('previous ' + profile.name + ' index has no APK version')
        for code, _version, digest, filename, signer in versions:
            if (type(code) is not int or code <= 0 or filename != f'{profile.package}_{code}.apk' or
                    signer != profile.apk_certificate or route + filename not in files or
                    files[route + filename]['sha256'] != digest):
                raise ValueError('previous indexed APK identity differs from channel policy')
        if profile.name == channels.BETA.name:
            newest = max(versions, key=lambda row: row[0])
            beta_release = {'version_name': newest[1], 'version_code': newest[0]}
    return PreviousSite(snapshot, files, previous.inventory_record, beta_release)


def inventory_bytes(files):
    return (json.dumps({'schema': 1, 'kind': 'collie-pages-public-inventory',
                       'origin': ORIGIN, 'files': files}, sort_keys=True, separators=(',', ':')) + '\n').encode()


def verify_pages_archive(path, *, byte_limit=SITE_LIMIT):
    """Measure the exact tar left by the pinned upload-pages-artifact action."""
    info = _regular(Path(path))
    if not 0 < info.st_size <= byte_limit:
        raise ValueError(f'Pages artifact is {info.st_size} bytes, outside the {byte_limit} byte limit; '
                         'a separately reviewed retirement or storage migration is required')
    return info.st_size


def _copy(source, target, expected):
    _regular(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    size = 0
    with source.open('rb') as incoming, target.open('xb') as outgoing:
        for block in iter(lambda: incoming.read(1024 * 1024), b''):
            size += len(block)
            if size > expected['bytes']:
                raise ValueError('retained artifact grew during copy: ' + str(source))
            digest.update(block)
            outgoing.write(block)
    if size != expected['bytes'] or digest.hexdigest() != expected['sha256']:
        raise ValueError('retained artifact changed during copy: ' + str(source))


def retain(site, previous, *, preserve_beta, rebuild_beta=False, byte_limit=SITE_LIMIT):
    """Admit the complete candidate before copying; never overwrite an immutable URL."""
    site = Path(site)
    if site.resolve() == previous.root.resolve() or site.resolve().is_relative_to(previous.root.resolve()) or previous.root.resolve().is_relative_to(site.resolve()):
        raise ValueError('candidate and previous site directories must be separate')
    verify_snapshot(previous, byte_limit=byte_limit)
    current = _tree_records(site, byte_limit)
    if INVENTORY in current:
        raise ValueError('write the candidate inventory only after immutable artifact retention')
    selected = previous.retained_files(preserve_beta, rebuild_beta)
    for name, record in selected.items():
        if name in current and current[name] != record:
            raise ValueError('immutable published URL has different candidate bytes: ' + name)
    planned = {**current, **selected}
    _safe_paths(planned)
    raw = inventory_bytes(planned)
    total = sum(record['bytes'] for record in planned.values()) + len(raw)
    if len(planned) > FILE_LIMIT or len(raw) > INVENTORY_LIMIT or total > byte_limit:
        raise ValueError(f'retained candidate is {total} bytes, over a file or {byte_limit} byte limit; '
                         'a separately reviewed retirement or storage migration is required')
    for name, record in sorted(selected.items()):
        if name not in current:
            _copy(previous.root / name, site / name, record)
    if _tree_records(site, byte_limit) != planned:
        raise ValueError('candidate changed while retaining immutable published URLs')
    return planned
