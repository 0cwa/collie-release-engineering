"""Select immutable product build inputs without altering legacy profiles."""
import copy
import hashlib
import json
from pathlib import Path
import re

import fork_test_sources


NAMES = {'molly-minimal', 'molly-continued', 'molly-foss-continued',
         'collie', 'collie-foss'}
FOSS = {'molly-foss-continued', 'collie-foss'}
CONTINUED_ICON = {'molly-continued', 'molly-foss-continued'}
PORT_SERIES = 'patches/ports/signal-8.35.1/series'
FORK_ROOT = 'patches/forks/signal-foss'
FIREBASE = FORK_ROOT + '/firebase-free-build/series'
RETENTION = FORK_ROOT + '/retention-controls/series'
RETENTION_FIXES = FORK_ROOT + '/retention-controls-fixes/series'
COLLIE_ICON = FORK_ROOT + '/collie-icon/series'
COLLIE_BRANDING = FORK_ROOT + '/collie-branding/series'
COLLIE_VISUALS = FORK_ROOT + '/collie-visuals/series'
RESTORE_PICKER = FORK_ROOT + '/restore-picker/series'
NOTIFICATION_DEFAULT = FORK_ROOT + '/notification-default/series'
COLLIE_BRANDING_TRANSLATIONS = FORK_ROOT + '/collie-branding-translations/series'
# User-visible app title, backup file base name and shared package ID per profile.
APP = {
    'molly-minimal': ('Collie', 'Molly-Minimal', 'im.collie.app'),
    'molly-continued': ('Collie', 'Molly-Continued', 'im.collie.app'),
    'molly-foss-continued': ('Collie FOSS', 'Molly-FOSS-Continued', 'im.collie.app'),
    'collie': ('Collie', 'Collie', 'im.collie.app'),
    'collie-foss': ('Collie FOSS', 'Collie-FOSS', 'im.collie.app'),
}
POLICY = {
    name: ('followups/view-once-open-guard', [FIREBASE, COLLIE_ICON, COLLIE_BRANDING, COLLIE_VISUALS, RESTORE_PICKER, NOTIFICATION_DEFAULT, COLLIE_BRANDING_TRANSLATIONS], name in FOSS)
    for name in NAMES
}



class ProfileError(ValueError):
    pass


def layer_test_sources(repo, series):
    """Classify test sources a fork layer adds or changes in mailbox order."""
    root = Path(repo) / FORK_ROOT
    provenance = json.loads((root / 'provenance.json').read_text())
    layers = [x for x in provenance['layers'] if FORK_ROOT + '/' + x['series'] == series]
    if len(layers) != 1:
        raise ProfileError('fork series has no single provenance layer: ' + series)
    text = (root / layers[0]['mailbox']).read_text(errors='surrogateescape')
    found = []
    for block in re.split(r'^(?=diff --git )', text, flags=re.M)[1:]:
        header = re.match(r'diff --git a/(\S+) b/(\S+)\n', block)
        if header is None:
            raise ProfileError('unreadable fork mailbox header in ' + series)
        path = header.group(2)
        if re.search(r'^deleted file mode ', block.split('\n@@', 1)[0], flags=re.M):
            continue
        source = fork_test_sources.classify(path)
        if source and source[0] == 'unsupported':
            raise ProfileError('no unit test task rule for fork test source: ' + path)
        if source:
            found.append(source)
    return found


def layer_test_classes(repo, series):
    """JVM test classes a fork layer adds or changes, in mailbox order."""
    return [identity for kind, identity in layer_test_sources(repo, series) if kind == 'unit']


def instrumentation_sources(repo, fork_series):
    """Device test source paths covered by the selected profile's device gate."""
    return list(dict.fromkeys(identity for series in fork_series
                              for kind, identity in layer_test_sources(repo, series)
                              if kind == 'instrumentation'))


def derived_tests(repo, base, fork_series):
    """Base Gradle test arguments plus the test classes each selected fork layer adds or changes.

    A class joins the filters of its module's existing test task. A task that already runs
    unfiltered keeps running everything; a module without a test task is an error.
    """
    groups, i = [], 0
    while i < len(base):
        if base[i] == '--tests':
            if not groups or i + 1 >= len(base):
                raise ProfileError('test filters must follow a Gradle task')
            groups[-1][1].append(base[i + 1]); i += 2
        else:
            groups.append([base[i], []]); i += 1
    for series in fork_series:
        for module, name in layer_test_classes(repo, series):
            prefix = ':' + module.replace('/', ':') + ':'
            tasks = [g for g in groups if g[0].startswith(prefix)]
            if len(tasks) != 1:
                raise ProfileError(f'fork test {name} needs exactly one {prefix} test task in android.tests')
            if tasks[0][1] and name not in tasks[0][1]:
                tasks[0][1].append(name)
    return [arg for task, filters in groups for arg in [task] + [x for f in filters for x in ('--tests', f)]]


def select(config, name, repo='.'):
    if name is None:
        return config, None
    if name not in NAMES:
        raise ProfileError('unknown release profile: ' + name)
    entry = config.get('profiles', {}).get(name)
    if not isinstance(entry, dict):
        raise ProfileError('release profile is not configured: ' + name)
    required = {'base_series', 'base_checkpoint', 'base_tree', 'fork_series', 'target_tree', 'firebase_free'}
    if set(entry) not in (required, required | {'icon_overlay'}):
        raise ProfileError('profile must specify exactly: ' + ', '.join(sorted(required)))
    for field in ('base_checkpoint', 'base_tree', 'target_tree'):
        if not isinstance(entry[field], str) or not re.fullmatch(r'[0-9a-f]{40}', entry[field]):
            raise ProfileError(field + ' must be a full lowercase tree ID')
    overlay = entry.get('icon_overlay')
    if 'icon_overlay' in entry and (name not in CONTINUED_ICON or overlay != COLLIE_ICON):
        raise ProfileError('invalid Continued icon overlay')
    boundary, expected_fork, expected_free = POLICY[name]
    if entry['base_series'] != PORT_SERIES:
        raise ProfileError('base_series does not match the port series')
    fork = entry['fork_series']
    if not isinstance(fork, list) or any(not isinstance(x, str) or not x for x in fork) or len(set(fork)) != len(fork):
        raise ProfileError('fork_series must be an ordered list of distinct paths')
    if fork != expected_fork:
        raise ProfileError('fork_series does not match the product composition')
    if type(entry['firebase_free']) is not bool or entry['firebase_free'] != expected_free:
        raise ProfileError('firebase_free does not match the product')
    if not config.get('port') or not isinstance(config.get('android'), dict):
        raise ProfileError('named profiles require a port and Android build settings')
    base_tests = config['android'].get('tests')
    if not isinstance(base_tests, list) or not base_tests or any(not isinstance(x, str) or not x for x in base_tests):
        raise ProfileError('android.tests must be a nonempty list of Gradle arguments')
    if fork or overlay:
        fork_source = config.get('fork_source')
        if not isinstance(fork_source, dict) or set(fork_source) != {'url', 'head'}:
            raise ProfileError('fork profiles require a fork_source URL and head pin')
        if fork_source['url'] != 'https://github.com/tw-hx/Signal-Android' or not isinstance(fork_source['head'], str) or not re.fullmatch(r'[0-9a-f]{40}', fork_source['head']):
            raise ProfileError('invalid fork_source URL or head')
        if not isinstance(config.get('signal', {}).get('commit'), str) or not re.fullmatch(r'[0-9a-f]{40}', config['signal']['commit']):
            raise ProfileError('fork profiles require a pinned Signal commit')
    selected = copy.deepcopy(config)
    selected.pop('profiles', None)
    selected['port']['series'] = entry['base_series']
    selected['target_tree'] = entry['target_tree']
    test_layers = fork + ([COLLIE_ICON] if overlay else [])
    selected['android']['tests'] = derived_tests(repo, base_tests, test_layers)
    device_sources = instrumentation_sources(repo, test_layers)
    selected['_profile'] = {'name': name, 'base_checkpoint': entry['base_checkpoint'],
                            'base_layer': boundary, 'base_tree': entry['base_tree'], 'target_tree': entry['target_tree'],
                            'fork_series': fork, 'firebase_free': entry['firebase_free'],
                            'app': dict(zip(('title', 'file_name', 'package_id'), APP[name]))}
    if device_sources:
        selected['_profile']['instrumentation_sources'] = device_sources
    if overlay:
        selected['_profile']['icon_overlay'] = COLLIE_ICON
    if fork or overlay:
        selected['_profile']['fork_source'] = copy.deepcopy(fork_source)
        selected['_profile']['signal_commit'] = config['signal']['commit']
    fingerprint = hashlib.sha256(json.dumps(selected, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    selected['_profile']['fingerprint'] = fingerprint
    return selected, selected['_profile']
