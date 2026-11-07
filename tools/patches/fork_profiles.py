"""Verify profile port prefixes and separately owned fork checkpoint layers."""
import hashlib
import json
import os
import subprocess
from pathlib import Path
import tempfile

import forest
import git_utils
import release
import release_profiles
import verify_source_bundles

# These identify the historical fork itself, not the Signal version being ported.
FORK_SIGNAL_BASE = '6e9dddc01f1b26e608ac683807c51e139c141ed2'
FORK_RETENTION_ANCESTOR = 'fc0daac37774d9c20238a34ac4786d9398adc07f'
FORK_SOURCE_HEAD = 'b13bff463547475bbe1ae55335add689f52532e0'
SIGNAL_URL = 'https://github.com/signalapp/Signal-Android.git'


def port_prefix(repo, config_path, selection, records):
    """Verify the complete port chain, then select an exact layer boundary."""
    verify_source_bundles.verify(repo, config_path)
    series = release.inside(repo, release_profiles.PORT_SERIES, 'port series')
    provenance = json.loads((series.parent / 'provenance.json').read_text())
    layers = provenance['layers']
    flattened = [path for layer in layers for path in layer['patches']]
    if flattened != forest.lines(series):
        raise forest.ForestError('port series and provenance order differ')
    if len(flattened) != len(records):
        raise forest.ForestError('port series record count differs')
    for path, (_, metadata) in zip(flattened, records):
        if metadata['sha256'] != provenance['mailbox_sha256'][path]:
            raise forest.ForestError('port series file hash differs: ' + path)
    endpoints = [i for i, layer in enumerate(layers) if layer['id'] == selection['base_layer']]
    if len(endpoints) != 1:
        raise forest.ForestError('selected port boundary missing or duplicated')
    end = endpoints[0]
    boundary = 'followups/network-initialization-cycle'
    common_fixes = []
    if provenance.get('signal') == 'a74583b01e260944f699c4da03ba4e8ce04de32b':
        common_fixes = ['followups/locked-startup-notification-restore', 'followups/molly-donation-label',
                        'followups/arabic-molly-donation', 'followups/persian-molly-donation', 'followups/native-catalog']
    additions = ['followups/registration-network-settings', 'followups/donation-completion-copy',
                 'followups/unifiedpush-registration-retry']
    if selection['base_layer'] == 'followups/view-once-open-guard':
        additions.append('followups/view-once-open-guard')
    minimal_boundary = common_fixes[-1] if common_fixes else boundary
    selected_boundaries = [layer['id'] for layer in layers[:end + 1] if layer['id'] in {boundary, *common_fixes, *additions}]
    expected_boundaries = [boundary] + common_fixes + ([] if selection['base_layer'] == minimal_boundary else additions)
    if selected_boundaries != expected_boundaries:
        raise forest.ForestError('selected port prefix has missing, repeated, or misordered additions')
    layer = layers[end]
    if (layer['source_commit'], layer['after_tree']) != (selection['base_checkpoint'], selection['base_tree']):
        raise forest.ForestError('selected port checkpoint or tree differs')
    count = sum(len(layer['patches']) for layer in layers[:end + 1])
    return records[:count]


def fork_layers(repo, selection, *, check_target=True):
    """Import and verify the selected fork prefix anchored to the port checkpoint."""
    root = release.inside(repo, release_profiles.FORK_ROOT, 'fork root')
    provenance_path = release.inside(repo, release_profiles.FORK_ROOT + '/provenance.json', 'fork provenance')
    data = json.loads(provenance_path.read_text())
    if data['schema'] != 1 or data['source_url'] != 'https://github.com/tw-hx/Signal-Android':
        raise forest.ForestError('fork origin or schema differs')
    release.require_sha(data['source_head'], 'fork source head')
    if data['source_head'] != FORK_SOURCE_HEAD:
        raise forest.ForestError('fork source head differs from pinned fork release')
    if (data['source_url'], data['source_head']) != (selection['fork_source']['url'], selection['fork_source']['head']):
        raise forest.ForestError('fork source differs from build configuration')
    if data['upstream_bundle_prerequisite'] != FORK_SIGNAL_BASE:
        raise forest.ForestError('upstream bundle prerequisite differs from pinned historical Signal source')
    if data['upstream_bundle_ref'] != 'refs/heads/upstream-fork/signal-foss-8.33.2.0-FOSS':
        raise forest.ForestError('upstream bundle ref differs')
    verify_source_bundles.import_bundle(repo, Path(repo), root, data['upstream_bundle'],
        data['upstream_bundle_sha256'], data['upstream_bundle_bytes'], data['upstream_bundle_ref'],
        data['source_head'], [FORK_SIGNAL_BASE])
    if (data['base_commit'], data['base_tree']) != (selection['base_checkpoint'], selection['base_tree']):
        raise forest.ForestError('fork base checkpoint or tree differs')
    layers = data['layers']
    if not isinstance(layers, list) or [x['id'] for x in layers] != ['firebase-free-build', 'collie-icon', 'collie-branding', 'collie-visuals', 'restore-picker', 'notification-default']:
        raise forest.ForestError('fork layer order differs')
    _, upstream_parents = verify_source_bundles.commit_info(repo, data['source_head'])
    if upstream_parents != [FORK_RETENTION_ANCESTOR]:
        raise forest.ForestError('fork upstream head parent differs from retention origin')
    if git_utils.git(repo, 'merge-base', '--is-ancestor', FORK_SIGNAL_BASE,
                     FORK_RETENTION_ANCESTOR, check=False).returncode:
        raise forest.ForestError('fork retention origin does not descend from pinned Signal source')
    selected = selection['fork_series']
    if [release_profiles.FORK_ROOT + '/' + x['series'] for x in layers[:len(selected)]] != selected:
        raise forest.ForestError('fork series differs from selected prefix')
    previous, tree = selection['base_checkpoint'], selection['base_tree']
    records = []
    for layer in layers[:len(selected)]:
        commit = release.require_sha(layer['source_commit'], 'fork source commit')
        credit = layer.get('upstream_credit')
        if credit not in ('adapted', 'inspired', 'ours'):
            raise forest.ForestError('fork upstream_credit must be adapted, inspired or ours')
        if not isinstance(layer.get('author'), str) or not layer['author']:
            raise forest.ForestError('fork layer author missing')
        if credit == 'ours':
            # Our fixes to an adapted layer: no upstream commit, never authored as the fork author.
            fixed = [x for x in layers if x['id'] == layer.get('fixes')]
            if layer['id'] in ('collie-icon', 'collie-branding', 'collie-visuals', 'restore-picker', 'notification-default'):
                if 'fixes' in layer or 'upstream_commit' in layer or 'upstream_author' in layer:
                    raise forest.ForestError('Collie-owned layer must be original work')
                fixed = None
            if fixed is not None and (not fixed or fixed[0]['upstream_credit'] != 'adapted' or layers.index(fixed[0]) != layers.index(layer) - 1
                    or 'upstream_commit' in layer or 'upstream_author' in layer
                    or layer['author'] == fixed[0]['upstream_author']):
                raise forest.ForestError('fork fix layer must directly follow the adapted layer it fixes and be ours')
            original = None
        else:
            original = release.require_sha(layer.get('upstream_commit'), 'fork upstream commit')
            if not isinstance(layer.get('upstream_author'), str) or not layer['upstream_author']:
                raise forest.ForestError('fork layer upstream_author missing')
        if layer['id'] == 'firebase-free-build' and original != data['source_head']:
            raise forest.ForestError('Firebase origin does not match fork source head')
        if (layer['source_bundle_prerequisite'], layer['before_tree']) != (previous, tree):
            raise forest.ForestError('fork parent checkpoint or tree differs')
        series_path = release.inside(repo, release_profiles.FORK_ROOT + '/' + layer['series'], 'fork series')
        entries = forest.lines(series_path)
        mailbox = release.inside(repo, release_profiles.FORK_ROOT + '/' + layer['mailbox'], 'fork mailbox')
        if len(entries) != 1 or release.inside(series_path.parent, entries[0], 'fork series mailbox') != mailbox:
            raise forest.ForestError('fork series must select its one declared mailbox')
        content = mailbox.read_bytes()
        if len(content) != layer['mailbox_bytes'] or hashlib.sha256(content).hexdigest() != layer['mailbox_sha256']:
            raise forest.ForestError('fork mailbox bytes or hash differ')
        metadata = forest.mailbox(mailbox, layer['mailbox'])
        if metadata['source_commit_shas'] != [commit] or metadata['mailbox_commit_sha'] != commit:
            raise forest.ForestError('fork mailbox source commit differs')
        ref = layer['source_bundle_ref']
        if not isinstance(ref, str) or not ref.startswith('refs/heads/source-checkpoint/'):
            raise forest.ForestError('fork checkpoint ref is invalid')
        verify_source_bundles.import_bundle(repo, Path(repo), root, layer['source_bundle'],
            layer['source_bundle_sha256'], layer['source_bundle_bytes'], ref, commit, [previous])
        actual_tree, parents = verify_source_bundles.commit_info(repo, commit)
        if parents != [previous] or actual_tree != layer['after_tree']:
            raise forest.ForestError('fork source commit parent or tree differs')
        # An adapted layer is mostly the fork's code and keeps its author. An inspired layer is
        # our own code: we are the author and the commit credits the fork commit it follows.
        author = git_utils.git(repo, 'show', '-s', '--format=%an <%ae>', commit).stdout.strip()
        if author != layer['author']:
            raise forest.ForestError('fork adapted commit author differs')
        if credit == 'adapted' and author != layer['upstream_author']:
            raise forest.ForestError('adapted fork layer must keep the upstream author')
        if credit == 'inspired':
            message = git_utils.git(repo, 'show', '-s', '--format=%B', commit).stdout
            if author == layer['upstream_author']:
                raise forest.ForestError('inspired fork layer must be authored by us')
            if ('Inspired-by: ' + layer['upstream_author']) not in message.splitlines() or original not in message:
                raise forest.ForestError('inspired fork layer must credit the upstream author and commit')
        if original is not None:
            author = git_utils.git(repo, 'show', '-s', '--format=%an <%ae>', original).stdout.strip()
            if author != layer['upstream_author']:
                raise forest.ForestError('fork upstream commit author differs')
        records.append((mailbox, metadata, layer['after_tree']))
        previous, tree = commit, actual_tree
    if check_target and tree != selection['target_tree']:
        raise forest.ForestError('fork final source tree differs from profile')
    return records


def continued_icon(repo, selection):
    """Verify the original icon checkpoint chain, then return only its mailbox.

    Its preceding fork layers establish provenance; they are not replayed.
    """
    if selection.get('icon_overlay') != release_profiles.COLLIE_ICON:
        raise forest.ForestError('invalid Continued icon overlay')
    original = dict(selection, fork_series=[release_profiles.FIREBASE, release_profiles.COLLIE_ICON])
    return fork_layers(repo, original, check_target=False)[-1]


def materialize_continued_icon(repo, selection):
    """Create and verify a Continued tree in the repository object database.

    The index is private; like source bundle imports, this requires writable Git objects.
    """
    mailbox, metadata, _ = continued_icon(repo, selection)
    records = fork_layers(repo, selection, check_target=False) if selection['fork_series'] else []
    base = records[-1][2] if records else selection['base_tree']
    if hashlib.sha256(mailbox.read_bytes()).hexdigest() != metadata['sha256']:
        raise forest.ForestError('icon mailbox changed after validation')
    with tempfile.TemporaryDirectory(prefix='continued-icon-index-') as directory:
        index = Path(directory) / 'index'
        git_utils.git(repo, 'read-tree', base, index_file=index)
        git_utils.git(repo, 'apply', '--cached', str(mailbox), index_file=index)
        tree = git_utils.git(repo, 'write-tree', index_file=index).stdout.strip()
    if tree != selection['target_tree']:
        raise forest.ForestError('Continued icon source tree differs from profile')
    return tree


def ensure_fork_signal_base(repo):
    """Ensure the pinned prerequisite and its full source tree are local for offline attribution."""
    env = dict(os.environ, GIT_NO_LAZY_FETCH='1')
    def complete():
        if git_utils.git(repo, 'cat-file', '-e', FORK_SIGNAL_BASE + '^{commit}', check=False, env=env).returncode:
            return False
        objects = git_utils.git(repo, 'rev-list', '--objects', '--missing=print',
                                FORK_SIGNAL_BASE + '^{tree}', check=False, env=env)
        return not objects.returncode and not any(line.startswith('?') for line in objects.stdout.splitlines())
    present = not git_utils.git(repo, 'cat-file', '-e', FORK_SIGNAL_BASE + '^{commit}', check=False, env=env).returncode
    if not present:
        # Fetch full ancestry for a missing commit; never add a shallow boundary to the caller.
        git_utils.git(repo, 'fetch', '--no-tags', '--no-write-fetch-head', '--no-filter', SIGNAL_URL, FORK_SIGNAL_BASE)
    if not complete():
        # A partial clone may have the commit but lack blobs. Fetch the snapshot in a
        # disposable shallow repository, then import only its tree objects as a pack.
        # The disposable repository's shallow metadata never enters the caller.
        with tempfile.TemporaryDirectory(prefix='fork-source-repair-') as directory:
            git_utils.git(directory, 'init', '--bare', '--quiet')
            git_utils.git(directory, 'fetch', '--no-tags', '--no-write-fetch-head', '--depth=1', '--no-filter', SIGNAL_URL, FORK_SIGNAL_BASE)
            tree = git_utils.git(directory, 'rev-parse', FORK_SIGNAL_BASE + '^{tree}').stdout.strip()
            clean_env = forest.clean_git_env(env)
            producer = subprocess.Popen(['git', '-C', directory, 'pack-objects', '--stdout', '--revs'],
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=clean_env)
            assert producer.stdin is not None and producer.stdout is not None
            producer.stdin.write((tree + '\n').encode())
            producer.stdin.close()
            consumer = subprocess.run(['git', '-C', str(repo), 'index-pack', '--stdin'],
                                      stdin=producer.stdout, stdout=subprocess.PIPE, env=clean_env)
            producer.stdout.close()
            if producer.wait() or consumer.returncode:
                raise forest.ForestError('fork Signal prerequisite object import failed')
    if not complete():
        raise forest.ForestError('fork Signal prerequisite source tree is incomplete')


def prepare_profile_trees(repo, config, config_path=None):
    """Verify and materialize profile trees in a writable repository for reports."""
    profiles = config.get('profiles') or {}
    forked = [name for name, entry in profiles.items() if entry.get('fork_series')]
    continued = [name for name, entry in profiles.items() if entry.get('icon_overlay')]
    if not forked and not continued:
        return
    root = Path(repo).resolve()
    verify_source_bundles.verify(root, config_path)
    if forked:
        ensure_fork_signal_base(root)
        # The longest profile contains every preceding fork checkpoint.
        name = max(forked, key=lambda item: len(profiles[item]['fork_series']))
        _, selection = release_profiles.select(config, name, root)
        fork_layers(root, selection, check_target=not selection.get('icon_overlay'))
    for name in continued:
        _, selection = release_profiles.select(config, name, root)
        materialize_continued_icon(root, selection)
