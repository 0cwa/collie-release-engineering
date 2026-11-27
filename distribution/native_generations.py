"""Admit only reviewed, hash-pinned additions to immutable native inputs."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import stat


ORIGIN = 'https://repo.collie.im/'
REPOSITORY = '0cwa/collie-release-engineering'
MAX_BYTES = 950_000_000
BASELINE = Path(__file__).with_name('native-v1.json')
REGISTRY = Path(__file__).with_name('native-generations.json')
ARCHIVE_URL = re.compile(
    rf'https://github\.com/{REPOSITORY}/releases/download/'
    r'([A-Za-z0-9][A-Za-z0-9_.-]{0,127})/([A-Za-z0-9][A-Za-z0-9_.-]{0,127}\.zip)')


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate native admission field')
        result[key] = value
    return result


def _read(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 1024 * 1024:
        raise ValueError('native admission requires a bounded regular policy file')
    raw = path.read_bytes()
    return raw, json.loads(raw, object_pairs_hook=_unique)


def _validate_generation(manifest, baseline, generation):
    fields = {'schema', 'kind', 'origin', 'generation', 'files'}
    if (not isinstance(manifest, dict) or set(manifest) not in (fields, fields | {'archive'}) or
            type(manifest['schema']) is not int or manifest['schema'] != 1 or
            manifest['kind'] != 'collie-pages-native-generation' or manifest['origin'] != ORIGIN or
            type(manifest['generation']) is not int or manifest['generation'] != generation):
        raise ValueError('invalid registered native generation manifest')
    files = manifest['files']
    if not isinstance(files, dict) or not len(baseline) < len(files) <= 100:
        raise ValueError('native generation must add a bounded artifact set')
    if any(files.get(name) != entry for name, entry in baseline.items()):
        raise ValueError('native generation must preserve every pinned baseline byte')
    if 'archive' in manifest:
        archive = manifest['archive']
        if (not isinstance(archive, dict) or set(archive) != {'url', 'bytes', 'sha256'} or
                not isinstance(archive['url'], str) or not ARCHIVE_URL.fullmatch(archive['url']) or
                type(archive['bytes']) is not int or not 0 < archive['bytes'] <= MAX_BYTES or
                not isinstance(archive['sha256'], str) or not re.fullmatch('[0-9a-f]{64}', archive['sha256'])):
            raise ValueError('native archive requires a pinned CRE GitHub Release ZIP, byte count and digest')
    coordinates = {}
    for name, entry in files.items():
        if name in baseline:
            continue
        pattern = (rf'native-inputs/v{generation}/maven/im/collie/native/'
                   r'(libsignal-android|libsignal-client|ringrtc-android)/'
                   r'([0-9]+\.[0-9]+\.[0-9]+-collie\.[1-9][0-9]*)/([^/]+)')
        match = re.fullmatch(pattern, name)
        if not match:
            raise ValueError('new native URL must use the registered generation and Collie coordinates')
        module, version, filename = match.groups()
        extension = 'jar' if module == 'libsignal-client' else 'aar'
        allowed = {f'{module}-{version}.{extension}', f'{module}-{version}.pom'}
        if filename not in allowed:
            raise ValueError('new native file is not the versioned binary or POM')
        if (not isinstance(entry, dict) or set(entry) != {'sha256', 'bytes'} or
                not isinstance(entry['sha256'], str) or not re.fullmatch('[0-9a-f]{64}', entry['sha256']) or
                type(entry['bytes']) is not int or not 0 < entry['bytes'] <= MAX_BYTES):
            raise ValueError('new native file needs exact digest and positive byte count')
        coordinates.setdefault((module, version), set()).add(filename)
    for (module, version), filenames in coordinates.items():
        extension = 'jar' if module == 'libsignal-client' else 'aar'
        if filenames != {f'{module}-{version}.{extension}', f'{module}-{version}.pom'}:
            raise ValueError('each new native coordinate requires its binary and POM')
        if module.startswith('libsignal-'):
            counterpart = 'libsignal-client' if module == 'libsignal-android' else 'libsignal-android'
            if (counterpart, version) not in coordinates:
                raise ValueError('new libsignal Android and client versions must match')


def approved_generation(supplied, baseline_path, registry_path):
    """Return the approved manifest, explicit retained baseline and known pins."""
    _, baseline = _read(baseline_path)
    if supplied == baseline:
        # Keep existing scheduled feed bundles valid; the complete prior site
        # retains later generations independently of the supplied addition set.
        return baseline, None, None
    _, registry = _read(registry_path)
    if (not isinstance(registry, dict) or set(registry) != {'schema', 'manifests'} or
            type(registry['schema']) is not int or registry['schema'] != 1 or
            not isinstance(registry['manifests'], list) or len(registry['manifests']) > 100):
        raise ValueError('invalid native generation registry')
    seen = set()
    selected = None
    historical_files = dict(baseline['files'])
    for item in registry['manifests']:
        if (not isinstance(item, dict) or
                set(item) not in ({'path', 'sha256'}, {'path', 'sha256', 'baseline'}) or
                not isinstance(item['path'], str) or not isinstance(item['sha256'], str) or
                not re.fullmatch('[0-9a-f]{64}', item['sha256'])):
            raise ValueError('invalid native generation registry pin')
        match = re.fullmatch(r'native-generations/v([2-9]|[1-9][0-9]+)\.json', item['path'])
        if not match or item['path'] in seen:
            raise ValueError('unsafe or duplicate native generation policy path')
        seen.add(item['path'])
        path = registry_path.parent.joinpath(*PurePosixPath(item['path']).parts)
        if path.parent.is_symlink():
            raise ValueError('native generation policy directory is a symlink')
        raw, approved = _read(path)
        if hashlib.sha256(raw).hexdigest() != item['sha256']:
            raise ValueError('registered native generation digest differs')
        required = None
        if 'baseline' in item:
            names = item['baseline']
            if (not isinstance(names, list) or not 0 < len(names) <= 100 or
                    any(not isinstance(name, str) or name not in historical_files for name in names) or
                    len(set(names)) != len(names)):
                raise ValueError('native baseline must name distinct previously approved files')
            required = {name: historical_files[name] for name in names}
        _validate_generation(approved, baseline['files'] if required is None else required, int(match[1]))
        if supplied == approved:
            selected = approved, required
        historical_files.update(approved['files'])
    if selected is None:
        raise ValueError('native manifest is not a registered immutable generation')
    approved, required = selected
    return approved, required, historical_files


def approved_files(supplied, baseline_path, registry_path):
    generation, _, _ = approved_generation(supplied, baseline_path, registry_path)
    return generation['files']


def load_generation(path, *, baseline_path=BASELINE, registry_path=REGISTRY):
    _, supplied = _read(path)
    generation, _, _ = approved_generation(supplied, baseline_path, registry_path)
    return generation
