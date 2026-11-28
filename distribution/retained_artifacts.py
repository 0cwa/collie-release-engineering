"""Retain published immutable URLs from one verified, frozen Pages snapshot.

Old APKs are copied only after the new F-Droid index has been generated. They
remain downloadable by clients with a cached index without entering the new one.
Retention is bounded: per channel, the previous RETAINED_PREVIOUS_APKS APKs below
the indexed ones stay, and a native Maven file stays only while a current release
plan references it. See distribution/OPERATIONS.md ("Retention policy").
"""
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import shutil
import stat
import xml.etree.ElementTree as ET
from zipfile import BadZipFile, ZipFile

import binary_repo
import channel_profile as channels


ORIGIN = 'https://repo.collie.im/'
INVENTORY = 'site-manifest.json'
INVENTORY_LIMIT = 32 * 1024 * 1024
FILE_LIMIT = 40000
SITE_LIMIT = 950_000_000
STABLE_ROUTE = 'fdroid/repo/'
BETA_ROUTE = 'fdroid/beta/repo/'
# Per channel, old APK URLs kept next to the indexed ones. A client holding the cached index of the
# previous release can still download it; older versions stay on the GitHub release pages.
RETAINED_PREVIOUS_APKS = 1
ARCHIVE_INDEX_FILES = frozenset({'entry.jar', 'entry.json', 'index-v1.jar', 'index-v1.json',
                                 'index-v2.json', 'index.jar', 'index.xml'})

def route_profiles(route):
    return (channels.STABLE, channels.BETA) if route == STABLE_ROUTE else (channels.BETA,)


def route_public_path(name, route):
    return any(binary_repo.public_path(name, profile) for profile in route_profiles(route))


def verify_route(root, files, route):
    """Read the signed canonical feed or its pre-migration Stable/Beta indexes."""
    profiles = route_profiles(route)
    if route == STABLE_ROUTE and not any(
            name.startswith(route + channels.BETA.package + '_') and apk_code(name) is not None
            for name in files):
        profiles = (channels.STABLE,)
    primary, *additional = profiles
    kwargs = {'additional_profiles': additional} if additional else {}
    index = binary_repo.verify_indexes(root / route, primary.repository_certificate, primary, **kwargs)
    address = channels.LEGACY_BETA_URL if route == BETA_ROUTE else channels.REPOSITORY_URL
    if index.get('repo', {}).get('address', '').rstrip('/') != address.rstrip('/'):
        raise ValueError(f'served {primary.name} index address differs from fixed route')
    return index, profiles


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


def apk_code(name):
    """versionCode of a channel-route APK path, else None."""
    match = re.fullmatch(r'fdroid/(?:beta/)?repo/[A-Za-z0-9_.]+_([1-9][0-9]*)\.apk', name)
    return int(match[1]) if match else None


def generation_archive_path(name):
    match = re.fullmatch(r'_generations/[0-9a-f]{64}/([A-Za-z0-9_.-]+)', name)
    return match is not None and match[1] in ARCHIVE_INDEX_FILES | {'repository-receipt.json'}


def immutable_public_path(name):
    """Published versioned URLs shared by both whole-site publication paths."""
    if versioned_native_path(name) or generation_archive_path(name):
        return True
    for route in (STABLE_ROUTE, BETA_ROUTE):
        if not name.startswith(route):
            continue
        relative = name[len(route):]
        if not route_public_path(relative, route):
            return False
        return (relative.endswith('.apk') or relative.startswith('diff/') or
                (relative.startswith('icons') and not relative.endswith('/icon.png')) or
                # fdroid names listing artwork it content-addresses (icon_<b64 sha256>=.png);
                # Fastlane screenshots keep their source names and may change between releases.
                (any(relative.startswith(profile.package + '/') for profile in route_profiles(route)) and
                 re.fullmatch(r'[A-Za-z0-9_-]+=\.png', relative.rsplit('/', 1)[-1]) is not None))
    return False


@dataclass(frozen=True)
class PreviousSite:
    root: Path
    files: dict
    inventory_record: dict
    beta_release: dict | None

    def retained_files(self, current=frozenset(), native_keep=None):
        """Select immutable legacy files and move retained Beta APKs to the canonical path."""
        kept = {name: record for name, record in self.files.items()
                if immutable_public_path(name) and not name.startswith(BETA_ROUTE)}
        for name, record in self.files.items():
            if name.startswith(BETA_ROUTE) and apk_code(name) is not None:
                canonical = STABLE_ROUTE + name.removeprefix(BETA_ROUTE)
                if kept.setdefault(canonical, record) != record:
                    raise ValueError('conflicting Beta APK bytes across repository routes')
        prefixes = [STABLE_ROUTE + profile.package + '_' for profile in route_profiles(STABLE_ROUTE)]
        for prefix in prefixes:
            old = {name: apk_code(name) for name in kept
                   if name.startswith(prefix) and apk_code(name) is not None and name not in current}
            keep = sorted(old, key=old.get, reverse=True)[:RETAINED_PREVIOUS_APKS]
            for name in old:
                if name not in keep:
                    del kept[name]
        if native_keep is not None:
            kept = {name: record for name, record in kept.items()
                    if not name.startswith('native-inputs/') or name in native_keep}
        return kept


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
                for route in (STABLE_ROUTE, BETA_ROUTE) for profile in route_profiles(route)):
            raise ValueError('previous site has an unexpected APK path: ' + name)
    previous = PreviousSite(snapshot, files, {'sha256': expected, 'bytes': len(raw)}, None)
    verify_snapshot(previous, byte_limit=byte_limit)
    beta_release = None
    for route in (STABLE_ROUTE, BETA_ROUTE):
        names = {name[len(route):] for name in files if name.startswith(route)}
        if not names:
            continue
        if any(not route_public_path(name, route) for name in names):
            raise ValueError('previous site contains an unexpected repository route path')
        if not {'entry.jar', 'index-v1.jar', 'index-v2.json'}.issubset(names):
            raise ValueError('previous site lacks signed repository indexes')
        index, profiles = verify_route(snapshot, files, route)
        for profile in profiles:
            versions = binary_repo.release_tuples(index, profile)
            if not versions:
                raise ValueError('previous ' + profile.name + ' index has no APK version')
            for code, _version, digest, filename, signer in versions:
                if (type(code) is not int or code <= 0 or filename != f'{profile.package}_{code}.apk' or
                        signer != profile.apk_certificate or route + filename not in files or
                        files[route + filename]['sha256'] != digest):
                    raise ValueError('previous indexed APK identity differs from channel policy')
            if profile == channels.BETA:
                if beta_release is not None:
                    raise ValueError('Beta was indexed on both repository routes')
                code, version, digest, filename, _ = max(versions, key=lambda row: row[0])
                beta_release = {'version_name': version, 'version_code': code, 'sha256': digest,
                                'path': route + filename}
    return PreviousSite(snapshot, files, previous.inventory_record, beta_release)


def inventory_bytes(files):
    return (json.dumps({'schema': 1, 'kind': 'collie-pages-public-inventory',
                       'origin': ORIGIN, 'files': files}, sort_keys=True, separators=(',', ':')) + '\n').encode()


def verify_pages_archive(path, *, byte_limit=SITE_LIMIT):
    """Measure the exact tar left by the pinned upload-pages-artifact action."""
    info = _regular(Path(path))
    if not 0 < info.st_size < byte_limit:
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


def _verify_stable_index_mirrors(previous, data, indexed_names):
    files = previous.files
    for name in ('index-v1.json', 'index.xml', 'index.jar'):
        if STABLE_ROUTE + name in files and files[STABLE_ROUTE + name]['bytes'] > INVENTORY_LIMIT:
            raise ValueError('Stable index mirror exceeds the byte limit')
    route = previous.root / STABLE_ROUTE
    if STABLE_ROUTE + 'index-v1.json' in files:
        legacy = json.loads((route / 'index-v1.json').read_bytes(), object_pairs_hook=_unique)
        binary_repo.validate_index_parity(data, legacy, channels.STABLE)
    legacy_xml = []
    if STABLE_ROUTE + 'index.xml' in files:
        legacy_xml.append((route / 'index.xml').read_bytes())
    if STABLE_ROUTE + 'index.jar' in files:
        try:
            with ZipFile(route / 'index.jar') as archive:
                entries = [entry for entry in archive.infolist() if entry.filename == 'index.xml']
                if len(entries) != 1 or not 0 < entries[0].file_size <= INVENTORY_LIMIT:
                    raise ValueError('invalid legacy Stable index JAR')
                legacy_xml.append(archive.read(entries[0]))
        except BadZipFile as exc:
            raise ValueError('invalid legacy Stable index JAR') from exc
    for raw in legacy_xml:
        text = raw.decode('utf-8')
        if '<!DOCTYPE' in text:
            raise ValueError('legacy Stable index contains an XML document type')
        root = ET.fromstring(text)
        if root.tag != 'fdroid' or {entry.text for entry in root.findall('./application/package/apkname')} != indexed_names:
            raise ValueError('legacy Stable index differs from signed v1/v2 APK membership')


def prepare_stable_previous_withdrawal(site, previous, *, inventory_sha256, apk_version_code,
                                     apk_sha256, apk_bytes, byte_limit=SITE_LIMIT):
    """Stage an otherwise identical site without the previous unindexed Stable APK."""
    if previous.inventory_record['sha256'] != inventory_sha256:
        raise ValueError('withdrawal inventory differs from captured deploy baseline')
    if type(apk_version_code) is not int or apk_version_code <= 0 or type(apk_bytes) is not int or apk_bytes <= 0:
        raise ValueError('withdrawal requires a positive Stable version code and APK size')
    verify_snapshot(previous, byte_limit=byte_limit)
    stable = channels.STABLE
    data = binary_repo.verify_indexes(previous.root / STABLE_ROUTE, stable.repository_certificate, stable)
    versions = binary_repo.release_tuples(data, stable)
    indexed_names = {version[3] for version in versions}
    indexed = {STABLE_ROUTE + name for name in indexed_names}
    name = f'{STABLE_ROUTE}{stable.package}_{apk_version_code}.apk'
    if name in indexed:
        raise ValueError('cannot withdraw an indexed Stable APK')
    _verify_stable_index_mirrors(previous, data, indexed_names)
    newest = max(version[0] for version in versions)
    prior = [code for path in previous.files
             if path.startswith(STABLE_ROUTE) and path not in indexed
             and (code := apk_code(path)) is not None and code < newest]
    if not prior or apk_version_code != max(prior):
        raise ValueError('withdrawal must name the previous unindexed Stable APK')
    if previous.files.get(name) != {'sha256': apk_sha256, 'bytes': apk_bytes}:
        raise ValueError('withdrawal APK differs from captured bytes')

    site = Path(site)
    if site.resolve().is_relative_to(previous.root.resolve()):
        raise ValueError('withdrawal output must be outside the captured previous site')
    state_name = 'feed-state.json'
    state_record = previous.files.get(state_name)
    if state_record is None or state_record['bytes'] > INVENTORY_LIMIT:
        raise ValueError('Stable withdrawal requires the captured feed state')
    state_blob = (previous.root / state_name).read_bytes()
    if hashlib.sha256(state_blob).hexdigest() != state_record['sha256']:
        raise ValueError('feed state changed while preparing Stable withdrawal')
    state = json.loads(state_blob, object_pairs_hook=_unique)
    if (not isinstance(state, dict) or type(state.get('schema')) is not int or
            state['schema'] != 1 or state.get('repository_fingerprint') != stable.repository_certificate or
            state.get('apks') != sorted(indexed_names) or state.get('retained_apks') != [Path(name).name]):
        raise ValueError('feed state does not name the signed Stable selection and the sole previous mirror')
    state['retained_apks'] = []
    new_state = (json.dumps(state, indent=1) + '\n').encode()
    state_sha256 = hashlib.sha256(new_state).hexdigest()
    planned = {path: entry for path, entry in previous.files.items() if path != name}
    planned[state_name] = {'sha256': state_sha256, 'bytes': len(new_state)}
    manifest = inventory_bytes(planned)
    total = sum(entry['bytes'] for entry in planned.values()) + len(manifest)
    if total > byte_limit:
        raise ValueError('withdrawal candidate exceeds the site byte limit')
    sha256 = hashlib.sha256(manifest).hexdigest()
    site.mkdir()
    try:
        for path, entry in planned.items():
            if path != state_name:
                _copy(previous.root / path, site / path, entry)
        (site / state_name).write_bytes(new_state)
        (site / INVENTORY).write_bytes(manifest)
        if _tree_records(site, byte_limit) != {**planned, INVENTORY: {'sha256': sha256, 'bytes': len(manifest)}}:
            raise ValueError('withdrawal candidate differs from the planned inventory')
    except Exception:
        shutil.rmtree(site)
        raise
    return {'withdrawn_path': name, 'withdrawn_sha256': apk_sha256, 'withdrawn_bytes': apk_bytes,
            'prior_inventory_sha256': inventory_sha256, 'feed_state_sha256': state_sha256,
            'inventory_sha256': sha256, 'site_bytes': total}


def retain(site, previous, *, byte_limit=SITE_LIMIT, native_keep=None):
    """Admit the complete candidate before copying; never overwrite an immutable URL."""
    site = Path(site)
    if site.resolve() == previous.root.resolve() or site.resolve().is_relative_to(previous.root.resolve()) or previous.root.resolve().is_relative_to(site.resolve()):
        raise ValueError('candidate and previous site directories must be separate')
    verify_snapshot(previous, byte_limit=byte_limit)
    current = _tree_records(site, byte_limit)
    if INVENTORY in current:
        raise ValueError('write the candidate inventory only after immutable artifact retention')
    selected = previous.retained_files(frozenset(current), native_keep)
    for name, record in selected.items():
        if name in current and current[name] != record:
            raise ValueError('immutable published URL has different candidate bytes: ' + name)
    planned = {**current, **selected}
    _safe_paths(planned)
    raw = inventory_bytes(planned)
    total = sum(record['bytes'] for record in planned.values()) + len(raw)
    if len(planned) > FILE_LIMIT or len(raw) > INVENTORY_LIMIT or total >= byte_limit:
        raise ValueError(f'retained candidate is {total} bytes, over a file or {byte_limit} byte limit; '
                         'a separately reviewed retirement or storage migration is required')
    for name, record in sorted(selected.items()):
        if name not in current:
            source = name
            if name not in previous.files:
                source = BETA_ROUTE + name.removeprefix(STABLE_ROUTE)
            _copy(previous.root / source, site / name, record)
    if _tree_records(site, byte_limit) != planned:
        raise ValueError('candidate changed while retaining immutable published URLs')
    return planned
