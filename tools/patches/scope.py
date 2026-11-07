#!/usr/bin/env python3
"""Check a stack boundary against its explicit base and immutable source trees."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import re
import sys

MOLLY = '0e202dda90785771ff8c87526eaaf30c655f54f3'
OLD = 'c47a74ba0961903eeaeda6d22347a990056c3e83'
NEW = '6e9dddc01f1b26e608ac683807c51e139c141ed2'
FOREST = '7a280ac23b61cef2c09009a6dbed02773fff3dfa'
REVIEWED_PROFILES = '89bd7d6368a509470c4612d079746f66dfac8b55'
ICON_OVERLAY = 'patches/forks/signal-foss/collie-icon/series'
WORKFLOWS = {'.github/workflows/android.yml', '.github/workflows/diffuse.yml', '.github/workflows/molly-patch-release.yml', '.github/workflows/molly-unsigned-release.yml'}
RELEASE_PATHS = frozenset(['patches/ports/signal-8.33.2/source-checkpoint.bundle', 'patches/ports/signal-8.33.2/.gitattributes', 'patches/ports/signal-8.33.2/followups/cdn-transport-test-fixture/0001-cdn-transport-test-fixture.patch', 'patches/ports/signal-8.33.2/followups/cdn-transport-test-fixture/series', 'patches/ports/signal-8.33.2/followups/integration-and-regression-tests/0001-integration-and-regression-tests.patch', 'patches/ports/signal-8.33.2/followups/integration-and-regression-tests/series', 'patches/ports/signal-8.33.2/followups/netcipher-sdk-dsl/0001-netcipher-sdk-dsl.patch', 'patches/ports/signal-8.33.2/followups/netcipher-sdk-dsl/series', 'patches/ports/signal-8.33.2/followups/restore-preference-import/0001-restore-preference-import.patch', 'patches/ports/signal-8.33.2/followups/restore-preference-import/series', 'patches/ports/signal-8.33.2/initial/app-updates/0001-resolve-app-updates.patch', 'patches/ports/signal-8.33.2/initial/app-updates/series', 'patches/ports/signal-8.33.2/initial/appearance/0001-resolve-appearance.patch', 'patches/ports/signal-8.33.2/initial/appearance/series', 'patches/ports/signal-8.33.2/initial/backup-and-restore/0001-resolve-backup-and-restore.patch', 'patches/ports/signal-8.33.2/initial/backup-and-restore/series', 'patches/ports/signal-8.33.2/initial/build-and-release/0001-resolve-build-and-release.patch', 'patches/ports/signal-8.33.2/initial/build-and-release/series', 'patches/ports/signal-8.33.2/initial/integration/0001-resolve-integration.patch', 'patches/ports/signal-8.33.2/initial/integration/0002-resolve-integration.patch', 'patches/ports/signal-8.33.2/initial/integration/0003-resolve-integration.patch', 'patches/ports/signal-8.33.2/initial/integration/series', 'patches/ports/signal-8.33.2/initial/maps/0001-resolve-maps.patch', 'patches/ports/signal-8.33.2/initial/maps/series', 'patches/ports/signal-8.33.2/initial/module-pruning/0001-resolve-module-pruning.patch', 'patches/ports/signal-8.33.2/initial/module-pruning/0002-resolve-module-pruning.patch', 'patches/ports/signal-8.33.2/initial/module-pruning/0003-resolve-module-pruning.patch', 'patches/ports/signal-8.33.2/initial/module-pruning/0004-resolve-module-pruning.patch', 'patches/ports/signal-8.33.2/initial/module-pruning/0005-resolve-module-pruning.patch', 'patches/ports/signal-8.33.2/initial/module-pruning/0006-resolve-module-pruning.patch', 'patches/ports/signal-8.33.2/initial/module-pruning/0007-resolve-module-pruning.patch', 'patches/ports/signal-8.33.2/initial/module-pruning/series', 'patches/ports/signal-8.33.2/initial/networking-and-proxies/0001-resolve-networking-and-proxies.patch', 'patches/ports/signal-8.33.2/initial/networking-and-proxies/series', 'patches/ports/signal-8.33.2/initial/notifications-and-unifiedpush/0001-resolve-notifications-and-unifiedpush.patch', 'patches/ports/signal-8.33.2/initial/notifications-and-unifiedpush/series', 'patches/ports/signal-8.33.2/initial/payments-and-donations/0001-resolve-payments-and-donations.patch', 'patches/ports/signal-8.33.2/initial/payments-and-donations/series', 'patches/ports/signal-8.33.2/initial/privacy-controls/0001-resolve-privacy-controls.patch', 'patches/ports/signal-8.33.2/initial/privacy-controls/series', 'patches/ports/signal-8.33.2/initial/registration-and-linking/0001-resolve-registration-and-linking.patch', 'patches/ports/signal-8.33.2/initial/registration-and-linking/0002-resolve-registration-and-linking.patch', 'patches/ports/signal-8.33.2/initial/registration-and-linking/series', 'patches/ports/signal-8.33.2/initial/resources-and-branding/0001-resolve-resources-and-branding.patch', 'patches/ports/signal-8.33.2/initial/resources-and-branding/0002-resolve-resources-and-branding.patch', 'patches/ports/signal-8.33.2/initial/resources-and-branding/0003-resolve-resources-and-branding.patch', 'patches/ports/signal-8.33.2/initial/resources-and-branding/0004-resolve-resources-and-branding.patch', 'patches/ports/signal-8.33.2/initial/resources-and-branding/0005-resolve-resources-and-branding.patch', 'patches/ports/signal-8.33.2/initial/resources-and-branding/0006-resolve-resources-and-branding.patch', 'patches/ports/signal-8.33.2/initial/resources-and-branding/0007-resolve-resources-and-branding.patch', 'patches/ports/signal-8.33.2/initial/resources-and-branding/0008-resolve-resources-and-branding.patch', 'patches/ports/signal-8.33.2/initial/resources-and-branding/0009-resolve-resources-and-branding.patch', 'patches/ports/signal-8.33.2/initial/resources-and-branding/0010-resolve-resources-and-branding.patch', 'patches/ports/signal-8.33.2/initial/resources-and-branding/0011-resolve-resources-and-branding.patch', 'patches/ports/signal-8.33.2/initial/resources-and-branding/0012-resolve-resources-and-branding.patch', 'patches/ports/signal-8.33.2/initial/resources-and-branding/0013-resolve-resources-and-branding.patch', 'patches/ports/signal-8.33.2/initial/resources-and-branding/0014-resolve-resources-and-branding.patch', 'patches/ports/signal-8.33.2/initial/resources-and-branding/series', 'patches/ports/signal-8.33.2/initial/security-and-locking/0001-resolve-security-and-locking.patch', 'patches/ports/signal-8.33.2/initial/security-and-locking/series', 'patches/ports/signal-8.33.2/provenance.json', 'patches/ports/signal-8.33.2/series', 'patches/ports/signal-8.33.2/report-metadata.json'])

# Exact AI-assisted compatibility conversion artifacts for existing release layers.
RELEASE_PATHS = RELEASE_PATHS.union({
    'patches/ports/signal-8.33.2/followups/molly-behavior-unit-tests/0001-molly-behavior-unit-tests.patch',
    'patches/ports/signal-8.33.2/followups/molly-behavior-unit-tests/series',
    'patches/ports/signal-8.33.2/followups/molly-behavior-unit-tests/source-checkpoint.bundle',
    'patches/ports/signal-8.33.2/followups/lint-fixes/0001-lint-fixes.patch',
    'patches/ports/signal-8.33.2/followups/lint-fixes/series',
    'patches/ports/signal-8.33.2/followups/lint-fixes/source-checkpoint.bundle',
    'patches/ports/signal-8.33.2/followups/cdn-transport-test-fixture/0001-cdn-transport-test-fixture-converted.patch',
    'patches/ports/signal-8.33.2/followups/integration-and-regression-tests/0001-integration-and-regression-tests-converted.patch',
    'patches/ports/signal-8.33.2/followups/integration-and-regression-tests/delete-files.json',
    'patches/ports/signal-8.33.2/followups/netcipher-sdk-dsl/0001-netcipher-sdk-dsl-converted.patch',
    'patches/ports/signal-8.33.2/followups/restore-preference-import/0001-restore-preference-import-converted.patch',
    'patches/ports/signal-8.33.2/initial/app-updates/0001-resolve-app-updates-converted.patch',
    'patches/ports/signal-8.33.2/initial/app-updates/delete-files.json',
    'patches/ports/signal-8.33.2/initial/appearance/0001-resolve-appearance-converted.patch',
    'patches/ports/signal-8.33.2/initial/backup-and-restore/0001-resolve-backup-and-restore-converted.patch',
    'patches/ports/signal-8.33.2/initial/backup-and-restore/delete-files.json',
    'patches/ports/signal-8.33.2/initial/build-and-release/0001-resolve-build-and-release-converted.patch',
    'patches/ports/signal-8.33.2/initial/build-and-release/delete-files.json',
    'patches/ports/signal-8.33.2/initial/build-and-release/generate-translations.json',
    'patches/ports/signal-8.33.2/initial/integration/0001-resolve-integration-converted.patch',
    'patches/ports/signal-8.33.2/initial/integration/0002-resolve-integration-converted.patch',
    'patches/ports/signal-8.33.2/initial/integration/0003-resolve-integration-converted.patch',
    'patches/ports/signal-8.33.2/initial/integration/delete-files.json',
    'patches/ports/signal-8.33.2/initial/maps/0001-resolve-maps-converted.patch',
    'patches/ports/signal-8.33.2/initial/module-pruning/delete-files.json',
    'patches/ports/signal-8.33.2/initial/networking-and-proxies/0001-resolve-networking-and-proxies-converted.patch',
    'patches/ports/signal-8.33.2/initial/networking-and-proxies/delete-files.json',
    'patches/ports/signal-8.33.2/initial/notifications-and-unifiedpush/0001-resolve-notifications-and-unifiedpush-converted.patch',
    'patches/ports/signal-8.33.2/initial/notifications-and-unifiedpush/delete-files.json',
    'patches/ports/signal-8.33.2/initial/payments-and-donations/0001-resolve-payments-and-donations-converted.patch',
    'patches/ports/signal-8.33.2/initial/payments-and-donations/delete-files.json',
    'patches/ports/signal-8.33.2/initial/privacy-controls/0001-resolve-privacy-controls-converted.patch',
    'patches/ports/signal-8.33.2/initial/registration-and-linking/0001-resolve-registration-and-linking-converted.patch',
    'patches/ports/signal-8.33.2/initial/registration-and-linking/0002-resolve-registration-and-linking-converted.patch',
    'patches/ports/signal-8.33.2/initial/registration-and-linking/delete-files.json',
    'patches/ports/signal-8.33.2/initial/resources-and-branding/0001-resolve-resources-and-branding-converted.patch',
    'patches/ports/signal-8.33.2/initial/resources-and-branding/0002-resolve-resources-and-branding-converted.patch',
    'patches/ports/signal-8.33.2/initial/resources-and-branding/0003-resolve-resources-and-branding-converted.patch',
    'patches/ports/signal-8.33.2/initial/resources-and-branding/0004-resolve-resources-and-branding-converted.patch',
    'patches/ports/signal-8.33.2/initial/resources-and-branding/0005-resolve-resources-and-branding-converted.patch',
    'patches/ports/signal-8.33.2/initial/resources-and-branding/0006-resolve-resources-and-branding-converted.patch',
    'patches/ports/signal-8.33.2/initial/resources-and-branding/0007-resolve-resources-and-branding-converted.patch',
    'patches/ports/signal-8.33.2/initial/resources-and-branding/0008-resolve-resources-and-branding-converted.patch',
    'patches/ports/signal-8.33.2/initial/resources-and-branding/0009-resolve-resources-and-branding-converted.patch',
    'patches/ports/signal-8.33.2/initial/resources-and-branding/0010-resolve-resources-and-branding-converted.patch',
    'patches/ports/signal-8.33.2/initial/resources-and-branding/0011-resolve-resources-and-branding-converted.patch',
    'patches/ports/signal-8.33.2/initial/resources-and-branding/0012-resolve-resources-and-branding-converted.patch',
    'patches/ports/signal-8.33.2/initial/resources-and-branding/0013-resolve-resources-and-branding-converted.patch',
    'patches/ports/signal-8.33.2/initial/resources-and-branding/delete-files.json',
    'patches/ports/signal-8.33.2/initial/security-and-locking/0001-resolve-security-and-locking-converted.patch',
    'patches/ports/signal-8.33.2/initial/security-and-locking/delete-files.json',
})
BEHAVIOR = {'reregistration-linked-devices-reminder', 'legacy-backup-precedence',
            'donation-completion', 'backup-notification-id-upgrade',
            'passphrase-timeout-fallback', 'backup-revision-walk', 'restore-badges-log-theme',
            'network-initialization-cycle', 'enable-linked-history-transfer',
            'android-instrumentation-compatibility'}
ADDITIONS = {'registration-network-settings', 'donation-completion-copy', 'unifiedpush-registration-retry'}
FEATURES = ('retention-controls', 'firebase-free-build')


def addition_files(name):
    return ('series', '0001-' + name + '.patch', 'source-checkpoint.bundle')


def git(repo, *args, check=True):
    return subprocess.run(['git', '-C', str(repo), *args], stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, check=check).stdout


def metadata(path):
    return (path.startswith('tools/patches/') or path.startswith('patches/') or
            path in {'molly-build.json', 'docs/PATCH-PIPELINE.md', 'docs/PORT-REPORT.md'} or
            path in WORKFLOWS)


def report_evidence(path):
    prefix = 'patches/ports/signal-8.33.2/report-evidence/'
    if not path.startswith(prefix):
        return False
    name = path[len(prefix):]
    locale_evidence = {
        'translation-task-witness.tsv',
        'native-translation-byte-witness.tsv',
        'locale-revert-cohort.tsv', 'locale-revert-cohort.patch',
        'locale-revert-cohort.md', 'locale-revert-cohort.py',
        'locale-resource-probe.md', 'locale-resource-probe.py', 'locale-resource-probe.log',
        'locale-cohort-two-readme.md', 'locale-cohort-two-cohort.tsv',
        'locale-cohort-two-cohort.patch', 'locale-cohort-two-probe.py',
        'locale-cohort-two-resource-probe.py', 'locale-cohort-two-run.log',
        'locale-cohort-two-resource.log',
        'locale-remaining-readme.md', 'locale-remaining-cohort.tsv',
        'locale-remaining-cohort.patch', 'locale-remaining-resource.py',
        'locale-remaining-contracts.py', 'locale-remaining-resource.log',
        'locale-remaining-contracts.log',
        'post-format-README.md', 'post-format-classify.py',
        'post-format-cohort.patch', 'post-format-probe.py',
        'post-format-results.tsv', 'post-format-translation-xml-proof.py',
    }
    return name in {'receipts.md', 'cohort-receipts.md', 'action-start-trees.bundle'} or name in locale_evidence or bool(re.fullmatch(r'(?:fmt-(?:0[1-9]|1[0-6])|lex-(?:0[1-9]|[1-6][0-9]|7[0-4])|(?:locale-format|hunk)-[0-9a-f]{20})\.patch', name))


def allowed(scope, path):
    if scope == 'pipeline':
        return path.startswith('tools/patches/') or path == 'docs/PATCH-PIPELINE.md' or path in WORKFLOWS
    if scope in ('switch', 'release'):
        if not metadata(path):
            return True  # exact source projection checked independently below
        if scope == 'switch':
            return path.startswith('patches/molly/') or path in {'molly-build.json', '.github/workflows/diffuse.yml'}
        return report_evidence(path) or path in RELEASE_PATHS or path in {'molly-build.json', 'docs/PATCH-PIPELINE.md', 'docs/PORT-REPORT.md'}
    if scope == 'forks':
        prefix = 'patches/forks/signal-foss/'
        return (path in {'molly-build.json', 'docs/PORT-REPORT.md', 'docs/PATCH-PIPELINE.md',
                         'tools/patches/build.py', 'tools/patches/release_profiles.py',
                         'tools/patches/release_artifacts.py', 'tools/patches/test_build.py',
                         'tools/patches/test_fork_profiles.py', 'tools/patches/test_release_artifacts.py'} or
                path in {prefix + 'provenance.json', prefix + 'upstream-source.bundle',
                         prefix + 'reviewed-profiles.json'} or
                any(path.startswith(prefix + layer + '/')
                    for layer in ('firebase-free-build', 'retention-controls', 'retention-controls-fixes', 'collie-icon', 'collie-branding')))
    if scope in ('followups', 'additions', 'features'):
        if report_evidence(path):
            return True
        prefix = 'patches/ports/signal-8.33.2/'
        if path in {'molly-build.json', 'docs/PATCH-PIPELINE.md', 'docs/PORT-REPORT.md', prefix + 'series', prefix + 'provenance.json', prefix + 'report-metadata.json'}:
            return True
        if scope == 'followups':
            return any(path.startswith(prefix + 'followups/' + name + '/') for name in BEHAVIOR)
        return any(path in {prefix + 'followups/' + layer + '/' + name for name in addition_files(layer)}
                   for layer in (ADDITIONS if scope == 'additions' else FEATURES))
    return False


def check_feature_chain(repo, base, head):
    prefix = 'patches/ports/signal-8.33.2/'
    def read(ref, path):
        return git(repo, 'show', ref + ':' + path)
    try:
        base_series = read(base, prefix + 'series').splitlines()
        head_series = read(head, prefix + 'series').splitlines()
        patches = ['followups/' + name + '/0001-' + name + '.patch' for name in FEATURES]
        if head_series != base_series + [path.encode() for path in patches]:
            raise ValueError('features series is not the exact ordered extension')
        before = json.loads(read(base, prefix + 'provenance.json'))
        after = json.loads(read(head, prefix + 'provenance.json'))
        base_profile = json.loads(read(base, 'molly-build.json'))
        profile = json.loads(read(head, 'molly-build.json'))
        old_layers, layers = before['layers'], after['layers']
        if layers[:-2] != old_layers or len(layers) != len(old_layers) + 2:
            raise ValueError('features provenance changed prior layers')
        if before['target_tree'] != base_profile['target_tree'] or old_layers[-1]['after_tree'] != before['target_tree']:
            raise ValueError('features base checkpoint differs')
        previous_commit, previous_tree = old_layers[-1]['source_commit'], before['target_tree']
        for layer, name, patch in zip(layers[-2:], FEATURES, patches):
            if (layer['id'] != 'followups/' + name or layer['patches'] != [patch] or
                layer['source_bundle'] != 'followups/' + name + '/source-checkpoint.bundle' or
                layer['source_bundle_prerequisite'] != previous_commit or layer['before_tree'] != previous_tree or
                patch not in after['mailbox_sha256']):
                raise ValueError('features provenance chain differs')
            previous_commit, previous_tree = layer['source_commit'], layer['after_tree']
        if after['target_tree'] != previous_tree or profile['target_tree'] != previous_tree:
            raise ValueError('features final checkpoint differs')
        if any(after['mailbox_sha256'].get(k) != v for k, v in before['mailbox_sha256'].items()):
            raise ValueError('features provenance changed prior mailbox pins')
    except (KeyError, IndexError, TypeError, json.JSONDecodeError, subprocess.CalledProcessError) as exc:
        raise ValueError('invalid features chain metadata') from exc


def check_release_profiles(reviewed, current, reviewed_port=None, base_port=None):
    """Constrain products to reviewed composition and trusted port boundaries."""
    expected = copy.deepcopy(reviewed)
    actual = copy.deepcopy(current)
    rebased = False
    if (reviewed_port is None) != (base_port is None):
        raise ValueError('both reviewed and base port provenance are required')
    if reviewed_port is not None:
        def find_one(layers, **fields):
            matches = [layer for layer in layers if all(layer.get(k) == v for k, v in fields.items())]
            if len(matches) != 1:
                raise ValueError('profile port boundary is missing or ambiguous')
            return matches[0]
        for profile in expected['profiles'].values():
            old_tree = profile['base_tree']
            old = find_one(reviewed_port['layers'], source_commit=profile['base_checkpoint'], after_tree=old_tree)
            boundary = find_one(base_port['layers'], id=old['id'])
            commit, tree = boundary['source_commit'], boundary['after_tree']
            if not all(isinstance(value, str) and re.fullmatch(r'[0-9a-f]{40}', value)
                       for value in (commit, tree)):
                raise ValueError('invalid profile port boundary')
            rebased = rebased or (profile['base_checkpoint'], old_tree) != (commit, tree)
            profile['base_checkpoint'], profile['base_tree'] = commit, tree
            # A product with no fork or overlay is exactly its base source tree.
            if not profile['fork_series'] and profile['target_tree'] == old_tree:
                profile['target_tree'] = tree
    for name in ('molly-continued', 'molly-foss-continued'):
        if actual['profiles'][name].pop('icon_overlay', None) != ICON_OVERLAY:
            raise ValueError('Continued profiles must select the icon overlay')
    for name in ('molly-continued', 'molly-foss-continued', 'collie', 'collie-foss'):
        if not re.fullmatch(r'[0-9a-f]{40}', actual['profiles'][name].get('target_tree', '')):
            raise ValueError('invalid release profile target tree')
        actual['profiles'][name]['target_tree'] = expected['profiles'][name]['target_tree']
    if actual != expected:
        raise ValueError('release profiles differ from reviewed composition')
    return rebased


def check_reviewed_profile_snapshot(current, provenance, snapshot):
    """Check the fork's explicit review snapshot and its declared byte hash."""
    info = provenance.get('reviewed_profiles')
    if not isinstance(info, dict) or set(info) != {'path', 'sha256'}:
        raise ValueError('missing reviewed profile snapshot metadata')
    if info['path'] != 'reviewed-profiles.json' or not isinstance(info['sha256'], str) or not re.fullmatch(r'[0-9a-f]{64}', info['sha256']):
        raise ValueError('invalid reviewed profile snapshot metadata')
    if hashlib.sha256(snapshot).hexdigest() != info['sha256']:
        raise ValueError('reviewed profile snapshot hash differs')
    try:
        expected = json.loads(snapshot)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError('invalid reviewed profile snapshot') from exc
    if expected != current:
        raise ValueError('reviewed profile snapshot differs from selected profiles')
    # For an ordinary fork prefix the resulting tree is fixed by its last
    # checkpoint. Overlay targets are checked by materializing the overlay.
    for profile in current['profiles'].values():
        fork = profile.get('fork_series', [])
        if fork and not profile.get('icon_overlay'):
            matches = [layer for layer in provenance['layers']
                       if 'patches/forks/signal-foss/' + layer['series'] == fork[-1]]
            if len(matches) != 1 or profile.get('target_tree') != matches[0]['after_tree']:
                raise ValueError('profile target differs from fork checkpoint')


# Exact legacy-main files deliberately retained when the release-base is integrated.
# No other source projection difference is accepted for the main-based fork PR.
MAIN_INTEGRATION_LOWER = '7f0eb01a0ab472deeef3b528e7f508425044fa6a'
# PR38's merged raw baseline is a separate trusted maintenance projection.
# Older release reconstruction/transition scopes keep their original oracle.
MAIN_MAINTENANCE_BASE = 'f6f68cdee08fb8d9acb6a866b1673deb10525f20'
MAIN_CLEAN_ENTRIES_BLOB = 'b06a83f6e4584f27811c3f406579b7db296f4b59'

MAIN_PROJECTION_EXCEPTIONS = frozenset({
    '.github/FUNDING.yml', '.github/workflows/sync.yml',
    '.github/workflows/tagrelease.yml', '.github/workflows/retarget-main-prs.yml', 'README.md', 'SECURITY.md',
    'assets/badge_obtainium.png', 'assets/collie-foreground.svg',
    'assets/collie-icon.svg', 'assets/collie-matrix-invite.svg', 'pkcs11.config',
})

# This scope is deliberately separate from the historical stack validators.
NEXT_PORT_BASE = '7d486880ddc991af0d36f1e4709030255ef4dbad'
NEXT_PORT_SIGNAL = 'a74583b01e260944f699c4da03ba4e8ce04de32b'
NEXT_PORT_NATIVE_LOCK_SHA256 = '557e20c3f8c1d6290af3007bb8eba5469e5a77f569b8efc7dc2edc1e68ba7856'
NEXT_PORT_REVIEWED_PROFILES_SHA256 = '6a83007ed81ea5311ad1abcc96af4724567a2c91aed2befa503030ba2cb4d864'
NEXT_PORT_FORK_CREDITS_SHA256 = '9223d8969f6d07e111e8720c3102168ecc153536d7df361b0638183f10b70146'
NEXT_PORT_FORK_BUNDLE_SHA256 = 'e0e3b786f89d042fa4e3430fd1f09718c12ef4e6316e24976bfc665be931fb57'
NEXT_PORT_FORK_HEAD = 'b13bff463547475bbe1ae55335add689f52532e0'
NEXT_PORT_FORK_URL = 'https://github.com/tw-hx/Signal-Android'
NEXT_PORT_TOOLS = frozenset({
    '.github/workflows/molly-patch-release.yml', '.github/workflows/molly-unsigned-release.yml',
    'tools/patches/build.py', 'tools/patches/test_build.py', 'tools/patches/test_prepare_port_report.py',
    'tools/patches/native_release_check.py', 'tools/patches/test_native_release_check.py',
    'tools/patches/test_native_release_route.py',
    'tools/patches/release_artifacts.py', 'tools/patches/release_publish.py', 'tools/patches/release_sign.py',
    'tools/patches/release_collect.py', 'tools/patches/test_single_collie.py',
    'tools/patches/test_release_artifacts.py', 'tools/patches/test_instrumentation_checklist.py',
    'tools/patches/test_next_port_workflow.py', 'tools/patches/test_optional_instrumentation_workflow.py',
    'tools/patches/fork_profiles.py', 'tools/patches/release_profiles.py',
    'tools/patches/pins.py', 'tools/patches/scope.py', 'tools/patches/release.py',
    'tools/patches/test_fork_profiles.py', 'tools/patches/test_retention_profiles.py', 'tools/patches/test_stack_scope.py', 'tools/patches/test_release.py',
    'tools/patches/prepare_port_report.py', 'tools/patches/port_report.py',
    'tools/patches/line_origin.py', 'tools/patches/test_line_origin.py', 'tools/patches/fetch_reviewed_profiles.py',
})

NEXT_PORT_NATIVE_PATHS = frozenset({
    'tools/native/ATTRIBUTION.md',
    'tools/native/CIPD-PROVENANCE.md',
    'tools/native/NDK-PROVENANCE.md',
    'tools/native/RUST-PROVENANCE.md',
    'tools/native/README.md',
    'tools/native/build_ringrtc.py',
    'tools/native/build_sources.py',
    'tools/native/cipd_payloads.py',
    'tools/native/test_cipd_payloads.py',
    'tools/native/webrtc-cipd-payloads.json',
    'tools/native/efficient_ringrtc.py',
    'tools/native/host_jni_guard.py',
    'tools/native/host_jni_smoke.py',
    'tools/native/native-lock.json',
    'tools/native/native_driver.py',
    'tools/native/native_fallback_smoke.py',
    'tools/native/ndk_payloads.py',
    'tools/native/test_ndk_payloads.py',
    'tools/native/nested_git.py',
    'tools/native/test_nested_git.py',
    'tools/native/webrtc-git-dependencies.json',
    'tools/native/patches/libsignal-0.106.1-attributed.mbox',
    'tools/native/patches/ringrtc-2.74-attributed.mbox',
    'tools/native/patches/test_webrtc_socks_ipv6.py',
    'tools/native/patches/test_webrtc_unknown_proxy.py',
    'tools/native/patches/webrtc-8037b-molly-proxy-UNTESTED.patch',
    'tools/native/patches/webrtc-socks-ipv6-fix.patch',
    'tools/native/patches/webrtc-unknown-proxy-fix.patch',
    'tools/native/prepare_sources.py',
    'tools/native/rust_payloads.py',
    'tools/native/test_rust_payloads.py',
    'tools/native/rust-toolchain-payloads.json',
    'tools/native/setup_webrtc.py',
    'tools/native/stage_artifacts.py',
    'tools/native/test_build_native_driver.py',
    'tools/native/test_build_ringrtc.py',
    'tools/native/test_build_sources.py',
    'tools/native/test_efficient_ringrtc.py',
    'tools/native/test_gradle_environment.py',
    'tools/native/test_host_jni_guard.py',
    'tools/native/test_prepare_sources.py',
    'tools/native/test_setup_webrtc.py',
    'tools/native/test_stage_artifacts.py',
    'tools/native/test_verify_gradle.py',
    'tools/native/test_webrtc_deps.py',
    'tools/native/verify_gradle.py',
    'tools/native/webrtc_deps.py',
})

def check_next_port(repo, base, head):
    """Verify an 8.35.1 projection against the exact reviewed fork base."""
    actual_base = git(repo, 'rev-parse', '--verify', base + '^{commit}').decode().strip()
    if actual_base != NEXT_PORT_BASE:
        raise ValueError('next port base is not the pinned reviewed fork')
    if subprocess.run(['git', '-C', str(repo), 'merge-base', '--is-ancestor', base, head],
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode:
        raise ValueError('next port base is not an ancestor of head')
    projection = {raw.decode('utf-8', 'surrogateescape') for raw in
                  filter(None, git(repo, 'diff', '--no-renames', '--name-only', '-z', NEXT_PORT_SIGNAL, head).split(b'\0'))}
    changed = git(repo, 'diff', '--no-renames', '--name-only', '-z', base, head).split(b'\0')
    for raw in filter(None, changed):
        path = raw.decode('utf-8', 'surrogateescape')
        if path.startswith(('patches/ports/signal-8.35.1/', 'patches/forks/signal-foss/')) or path in NEXT_PORT_TOOLS or path in NEXT_PORT_NATIVE_PATHS or path in {
                'molly-build.json', 'README.md', 'docs/PORT-REPORT.md', 'docs/PATCH-PIPELINE.md'}:
            continue
        if path in MAIN_PROJECTION_EXCEPTIONS or metadata(path) or path in projection:
            raise ValueError('next port contains an unreviewed change: ' + path)
    for path in projection:
        if not metadata(path) and path not in NEXT_PORT_NATIVE_PATHS and path not in MAIN_PROJECTION_EXCEPTIONS and path != '.github/FUNDING.yml':
            raise ValueError('next port source projection differs: ' + path)
    if git(repo, 'rev-parse', head + ':patches/molly').decode().strip() != FOREST:
        raise ValueError('next port Molly forest differs')
    config = json.loads(git(repo, 'show', head + ':molly-build.json'))
    if config.get('signal', {}).get('commit') != NEXT_PORT_SIGNAL or config.get('molly', {}).get('target') != MOLLY:
        raise ValueError('next port profile source pins differ')
    if config.get('native') != 'tools/native/native-lock.json':
        raise ValueError('next port native lock is missing or differs')
    native_lock = git(repo, 'show', head + ':tools/native/native-lock.json', check=False)
    if hashlib.sha256(native_lock).hexdigest() != NEXT_PORT_NATIVE_LOCK_SHA256:
        raise ValueError('next port native lock bytes differ from pin')
    port = config.get('port')
    expected_series = 'patches/ports/signal-8.35.1/series'
    if not isinstance(port, dict) or port.get('series') != expected_series or not git(repo, 'show', head + ':' + expected_series, check=False).strip():
        raise ValueError('next port series is missing or differs')
    profiles = config.get('profiles')
    if not isinstance(profiles, dict) or set(profiles) != {'molly-minimal', 'molly-continued', 'molly-foss-continued', 'collie', 'collie-foss'} or any(not isinstance(p, dict) for p in profiles.values()):
        raise ValueError('next port release profiles are missing or differ')
    root = 'patches/forks/signal-foss/'
    snapshot = git(repo, 'show', head + ':' + root + 'reviewed-profiles.json')
    if hashlib.sha256(snapshot).hexdigest() != NEXT_PORT_REVIEWED_PROFILES_SHA256:
        raise ValueError('next port reviewed profile snapshot differs from pinned composition')
    try:
        reviewed = json.loads(snapshot)
        fork = json.loads(git(repo, 'show', head + ':' + root + 'provenance.json'))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError('next port fork provenance is invalid') from exc
    selected = {'profiles': profiles, 'fork_source': config.get('fork_source')}
    if json.dumps(selected, sort_keys=True, separators=(',', ':')) != json.dumps(reviewed, sort_keys=True, separators=(',', ':')):
        raise ValueError('next port profiles differ from pinned composition')
    if config.get('fork_source') != {'head': NEXT_PORT_FORK_HEAD, 'url': NEXT_PORT_FORK_URL}:
        raise ValueError('next port fork source differs from historical pin')
    if (fork.get('source_head'), fork.get('source_url'), fork.get('upstream_bundle_prerequisite'),
        fork.get('upstream_bundle_ref'), fork.get('reviewed_profiles'),
        fork.get('upstream_bundle'), fork.get('upstream_bundle_sha256')) != (
            NEXT_PORT_FORK_HEAD, NEXT_PORT_FORK_URL, NEW,
            'refs/heads/upstream-fork/signal-foss-8.33.2.0-FOSS',
            {'path': 'reviewed-profiles.json', 'sha256': NEXT_PORT_REVIEWED_PROFILES_SHA256},
            'upstream-source.bundle', NEXT_PORT_FORK_BUNDLE_SHA256):
        raise ValueError('next port fork provenance differs from historical pin')
    layers = fork.get('layers')
    if not isinstance(layers, list) or any(not isinstance(layer, dict) for layer in layers):
        raise ValueError('next port fork layer credits are invalid')
    credits = [{key: value for key, value in layer.items() if key in
                ('id', 'upstream_author', 'upstream_commit', 'upstream_credit', 'fixes')} for layer in layers]
    encoded = json.dumps(credits, sort_keys=True, separators=(',', ':')).encode()
    if hashlib.sha256(encoded).hexdigest() != NEXT_PORT_FORK_CREDITS_SHA256:
        raise ValueError('next port fork historical credits differ from pin')
    bundle = git(repo, 'show', head + ':' + root + 'upstream-source.bundle')
    if hashlib.sha256(bundle).hexdigest() != NEXT_PORT_FORK_BUNDLE_SHA256:
        raise ValueError('next port historical fork bundle differs from pin')
    return {'scope': 'next-port', 'profile_present': True, 'port_present': True}



# Exact audited main-preservation and portability entries; includes absence and mode.
MAIN_CLEAN_ENTRIES = json.loads((Path(__file__).parent / 'main-clean-entries.json').read_text())
# The validator and pin registry contain the manifest hash, so pinning their
# own blobs in that manifest would be circular. All other scope files are
# checked. PR-controlled code makes this an accidental-change check only.
MAIN_SCOPE_FILES = frozenset(['tools/patches/main-clean-entries.json', 'tools/patches/scope.py', 'tools/patches/pins.py'])
INITIAL_MAIN = '1861543f3ba23b0bc396cb39a0595346537b6df6'

def check_trusted_integration_lower(repo, integration_lower):
    actual = git(repo, 'rev-parse', '--verify', integration_lower + '^{commit}').decode().strip()
    if actual != MAIN_INTEGRATION_LOWER:
        raise ValueError('unexpected trusted release-base commit')


def mutable_main_documentation(path):
    # The generated report and pipeline documentation are part of the pinned
    # port evidence; other public documentation can evolve on trusted main.
    return (path in {'README.md', 'SECURITY.md', '.github/README.md'} or
            path.startswith('assets/') or
            (path.startswith('docs/') and path not in {'docs/PORT-REPORT.md', 'docs/PATCH-PIPELINE.md'}))


def check_clean_projection(repo, candidate, lower, allow_main_docs=False):
    # The audited tree, not historical ancestry, establishes the clean base.
    manifest = 'tools/patches/main-clean-entries.json'
    entry = git(repo, 'ls-tree', candidate, '--', manifest).decode().strip().split('\t')[0]
    if entry != '100644 blob ' + MAIN_CLEAN_ENTRIES_BLOB:
        raise ValueError('audited integration manifest differs')
    changed = git(repo, 'diff', '--no-renames', '--name-only', '-z', lower, candidate).split(b'\0')
    for raw in filter(None, changed):
        path = raw.decode('utf-8', 'surrogateescape')
        if (path not in MAIN_CLEAN_ENTRIES and path not in MAIN_SCOPE_FILES and
            not (allow_main_docs and mutable_main_documentation(path))):
            raise ValueError('unexpected change to release base: ' + path)
    for path, expected in MAIN_CLEAN_ENTRIES.items():
        if allow_main_docs and mutable_main_documentation(path):
            continue
        entry = git(repo, 'ls-tree', candidate, '--', path).decode().strip().split('\t')[0]
        if entry != expected:
            raise ValueError('audited integration entry differs: ' + path)


def check_main_transition(repo, base, head, integration_lower, allow_main_docs=False):
    check_trusted_integration_lower(repo, integration_lower)
    actual_base = git(repo, 'rev-parse', '--verify', base + '^{commit}').decode().strip()
    if actual_base != INITIAL_MAIN:
        raise ValueError('unexpected transition base')
    if subprocess.run(['git', '-C', str(repo), 'merge-base', '--is-ancestor', base, head],
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode:
        raise ValueError('main base is not an ancestor of transition head')
    check_clean_projection(repo, head, integration_lower, allow_main_docs=allow_main_docs)
    # Verify the whole historic stack against its pinned source checkpoints.
    candidates = git(repo, 'rev-list', '--ancestry-path', MOLLY + '..' + integration_lower).decode().splitlines()
    for candidate in candidates:
        try:
            check_scope(repo, 'additions', candidate, integration_lower)
            break
        except (ValueError, subprocess.CalledProcessError):
            continue
    else:
        raise ValueError('pinned release base has no validated additions phase')
    return {'scope': 'main-transition', 'profile_present': True, 'port_present': True}


def check_main_docs(repo, base, head):
    if subprocess.run(['git', '-C', str(repo), 'merge-base', '--is-ancestor', base, head],
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode:
        raise ValueError('main base is not an ancestor of head')
    for raw in filter(None, git(repo, 'diff', '--no-renames', '--name-only', '-z', base, head).split(b'\0')):
        path = raw.decode('utf-8', 'surrogateescape')
        if not mutable_main_documentation(path):
            raise ValueError('main documentation scope excludes: ' + path)
    return {'scope': 'main-docs', 'profile_present': False, 'port_present': False}


def main_docs_only(repo, base, head):
    """Select the inexpensive documentation gate only for an entirely docs diff."""
    changed = git(repo, 'diff', '--no-renames', '--name-only', '-z', base, head).split(b'\0')
    return all(mutable_main_documentation(p.decode('utf-8', 'surrogateescape'))
               for p in filter(None, changed))


def check_main_maintenance(repo, base, head, integration_lower):
    """Check patch/tool maintenance on main without admitting direct source edits."""
    check_trusted_integration_lower(repo, integration_lower)
    if subprocess.run(['git', '-C', str(repo), 'merge-base', '--is-ancestor', base, head],
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode:
        raise ValueError('main base is not an ancestor of head')
    # Maintenance may edit the fork's workflows, but must not restore Signal's
    # disabled workflows or replace the required workflows with symlinks.
    for path in ('.github/workflows/android.yml', '.github/workflows/diffuse.yml'):
        if git(repo, 'ls-tree', head, '--', path):
            raise ValueError('Signal workflow must be absent: ' + path)
    for path in ('.github/workflows/molly-patch-release.yml', '.github/workflows/molly-unsigned-release.yml'):
        if not git(repo, 'ls-tree', head, '--', path).decode().startswith('100644 blob '):
            raise ValueError('Molly workflow must be a regular 100644 file: ' + path)
    changed = git(repo, 'diff', '--no-renames', '--name-only', '-z', base, head).split(b'\0')
    for raw in filter(None, changed):
        path = raw.decode('utf-8', 'surrogateescape')
        if path.startswith('patches/molly/'):
            raise ValueError('official Molly forest must remain unchanged: ' + path)
        if not (mutable_main_documentation(path) or
                path.startswith(('tools/patches/', 'patches/ports/', 'patches/forks/')) or
                path in {'molly-build.json', 'docs/PORT-REPORT.md', 'docs/PATCH-PIPELINE.md'} or
                path in WORKFLOWS or path == '.github/workflows/retarget-main-prs.yml'):
            raise ValueError('main maintenance excludes direct source change: ' + path)
    if git(repo, 'rev-parse', head + ':patches/molly').decode().strip() != FOREST:
        raise ValueError('Molly forest does not match the official snapshot')
    projection_lower = integration_lower
    present = subprocess.run(['git','-C',str(repo),'cat-file','-e',MAIN_MAINTENANCE_BASE+'^{commit}'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode == 0
    if present and subprocess.run(['git','-C',str(repo),'merge-base','--is-ancestor',MAIN_MAINTENANCE_BASE,base],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode == 0:
        projection_lower = MAIN_MAINTENANCE_BASE
    # Compare the maintained tree to the trusted upstream source projection as
    # well as checking the PR diff. This also makes a trusted main snapshot
    # verifiable after earlier maintenance commits have landed.
    projected = git(repo, 'diff', '--no-renames', '--name-only', '-z', projection_lower, head).split(b'\0')
    for raw in filter(None, projected):
        path = raw.decode('utf-8', 'surrogateescape')
        if not (metadata(path) or path in MAIN_PROJECTION_EXCEPTIONS or
                mutable_main_documentation(path) or path == '.github/workflows/retarget-main-prs.yml'):
            raise ValueError('main source projection differs: ' + path)
    if git(repo, 'ls-tree', head, '--', 'pkcs11.config'):
        raise ValueError('main must not reintroduce the nonportable PKCS#11 configuration')
    try:
        prior = json.loads(git(repo, 'show', base + ':molly-build.json'))
        current = json.loads(git(repo, 'show', head + ':molly-build.json'))
        trusted = json.loads(git(repo, 'show', projection_lower + ':molly-build.json'))
        for key in ('signal', 'molly', 'native'):
            if prior[key] != current[key] or current[key] != trusted[key]:
                raise ValueError('main maintenance cannot change upstream source pin: ' + key)
        if ('profiles' in prior) != ('profiles' in current):
            raise ValueError('profile introduction requires fork scope')
    except (KeyError, TypeError, json.JSONDecodeError, subprocess.CalledProcessError) as exc:
        raise ValueError('invalid main maintenance profile') from exc
    return {'scope': 'main-maintenance', 'profile_present': True, 'port_present': True}


def check_main_integration(repo, base, head, integration_lower, allow_main_docs=False):
    # Main is the trusted merge base, and its tools and evidence may have
    # evolved through maintenance PRs after the initial clean integration.
    # Verify its current source boundary instead of requiring old tool blobs.
    check_main_maintenance(repo, base, base, integration_lower)
    for raw in filter(None, git(repo, 'diff', '--no-renames', '--name-only', '-z', base, head).split(b'\0')):
        path = raw.decode('utf-8', 'surrogateescape')
        if mutable_main_documentation(path) and not allow_main_docs:
            raise ValueError('main documentation changed: ' + path)
    # Compare the complete tree entries, including mode and absence.
    for path in MAIN_PROJECTION_EXCEPTIONS:
        if not (allow_main_docs and mutable_main_documentation(path)) and git(repo, 'ls-tree', base, '--', path) != git(repo, 'ls-tree', head, '--', path):
            raise ValueError('main projection changed: ' + path)


def check_scope(repo, scope, base, head='HEAD', integration_lower=None):
    if scope == 'next-port':
        if integration_lower is not None:
            raise ValueError('next port does not accept an integration lower base')
        return check_next_port(repo, base, head)
    main_snapshot = scope == 'main-snapshot'
    if scope == 'main-snapshot-transition':
        if integration_lower is None:
            raise ValueError('main snapshot requires pinned release base')
        return check_main_transition(repo, base, head, integration_lower, allow_main_docs=True)
    if main_snapshot:
        if integration_lower is None:
            raise ValueError('main snapshot requires pinned release base')
        scope = 'forks'
    if scope == 'main-transition':
        if integration_lower is None:
            raise ValueError('main transition requires pinned release base')
        return check_main_transition(repo, base, head, integration_lower)
    if scope == 'main-docs':
        if integration_lower is not None:
            raise ValueError('main documentation must not use release base')
        return check_main_docs(repo, base, head)
    if scope == 'main-maintenance':
        if integration_lower is None:
            raise ValueError('main maintenance requires pinned release base')
        return check_main_maintenance(repo, base, head, integration_lower)
    if integration_lower is not None:
        if scope != 'forks':
            raise ValueError('main integration is supported only for forks scope')
        check_trusted_integration_lower(repo, integration_lower)
        check_main_integration(repo, base, head, integration_lower, allow_main_docs=main_snapshot)
    if scope not in ('pipeline', 'switch', 'release', 'followups', 'additions', 'features', 'forks'):
        raise ValueError('unknown scope')
    if subprocess.run(['git', '-C', str(repo), 'merge-base', '--is-ancestor', base, head],
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode:
        raise ValueError('base is not an ancestor of head')
    changed = git(repo, 'diff', '--no-renames', '--name-only', '-z', base, head).split(b'\0')
    for raw in filter(None, changed):
        path = raw.decode('utf-8', 'surrogateescape')
        if not allowed(scope, path) and not (main_snapshot and mutable_main_documentation(path)):
            raise ValueError('path outside scope: ' + path)
    # These are the only workflow exceptions: two deletions and two regular file additions. Signal's APK diff
    # workflow needs a runner that only Signal's organization has, so it would stay queued forever.
    def entry(ref, path):
        return git(repo, 'ls-tree', ref, '--', path).decode().strip()
    if entry(head, '.github/workflows/android.yml'):
        raise ValueError('Signal Android workflow must be absent')
    if scope != 'pipeline' and entry(head, '.github/workflows/diffuse.yml'):
        raise ValueError('Signal APK diff workflow must be absent')
    for path in ('.github/workflows/molly-patch-release.yml', '.github/workflows/molly-unsigned-release.yml'):
        workflow = entry(head, path)
        if not workflow.startswith('100644 blob '):
            raise ValueError('Molly workflow must be a regular 100644 file: ' + path)
        if scope == 'pipeline':
            if entry(base, path):
                raise ValueError('Molly workflow must be added on pipeline branch: ' + path)
        elif entry(base, path) != workflow:
            raise ValueError('Molly workflow must be inherited unchanged: ' + path)
    def exists(ref, path):
        return subprocess.run(['git', '-C', str(repo), 'cat-file', '-e', ref + ':' + path],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
    if scope == 'pipeline':
        if git(repo, 'rev-parse', base + '^{commit}').decode().strip() != MOLLY:
            raise ValueError('pipeline base must be the official Molly commit')
    else:
        lower_source = {'switch': MOLLY, 'release': OLD, 'followups': NEW, 'additions': NEW, 'features': NEW, 'forks': NEW}[scope]
        for raw in filter(None, git(repo, 'diff', '--no-renames', '--name-only', '-z', lower_source, base).split(b'\0')):
            path = raw.decode('utf-8', 'surrogateescape')
            if not metadata(path) and not (integration_lower is not None and (path in MAIN_PROJECTION_EXCEPTIONS or mutable_main_documentation(path))):
                raise ValueError('lower base has incorrect source projection')
        base_profile = git(repo, 'show', base + ':molly-build.json', check=False)
        if scope == 'switch':
            if base_profile or exists(base, 'patches/molly'):
                raise ValueError('switch base must be the pipeline phase')
        else:
            try:
                lower = json.loads(base_profile)
                if lower['signal']['commit'] != lower_source or (lower['port'] is None) != (scope == 'release'):
                    raise ValueError('incorrect lower base profile')
            except (KeyError, TypeError, json.JSONDecodeError) as exc:
                raise ValueError('invalid lower base profile') from exc
            if not exists(base, 'patches/molly') or git(repo, 'rev-parse', base + ':patches/molly').decode().strip() != FOREST:
                raise ValueError('incorrect lower base forest')
        if scope == 'followups' and any(exists(base, 'patches/ports/signal-8.33.2/followups/' + name) for name in BEHAVIOR):
            raise ValueError('followups base already contains behavior patches')
    if scope == 'release' and any(exists(head, 'patches/ports/signal-8.33.2/followups/' + name) for name in BEHAVIOR):
        raise ValueError('release phase contains behavior patches')
    if scope == 'followups' and not all(exists(head, 'patches/ports/signal-8.33.2/followups/' + name) for name in BEHAVIOR):
        raise ValueError('followups phase is incomplete')
    if scope == 'additions':
        for layer in ADDITIONS:
            addition_path = 'patches/ports/signal-8.33.2/followups/' + layer
            if exists(base, addition_path):
                raise ValueError('additions base already contains ' + layer)
            if not all(exists(head, addition_path + '/' + name) for name in addition_files(layer)):
                raise ValueError('additions phase is incomplete')
    if scope == 'forks':
        for path in ('tools/patches/main-clean-entries.json', 'tools/patches/scope.py', 'tools/patches/fork_profiles.py',
                     'tools/patches/line_origin.py', 'tools/patches/port_report.py',
                     'tools/patches/release.py'):
            if git(repo, 'rev-parse', base + ':' + path).strip() != git(repo, 'rev-parse', head + ':' + path).strip():
                raise ValueError('release tool differs from trusted base: ' + path)
        # PR17 introduced application identities; all other selector code must
        # remain exactly the trusted lower implementation.
        path = 'tools/patches/release_profiles.py'
        selector = git(repo, 'show', base + ':' + path).decode()
        identity = git(repo, 'show', REVIEWED_PROFILES + ':' + path).decode()
        start = identity.index('# User-visible app title')
        end = identity.index('POLICY =', start)
        selector = selector.replace('POLICY =', identity[start:end] + 'POLICY =', 1)
        old = "'fork_series': fork, 'firebase_free': entry['firebase_free']}"
        new = "'fork_series': fork, 'firebase_free': entry['firebase_free'],\n                            'app': dict(zip(('title', 'file_name', 'package_id'), APP[name]))}"
        if selector.count(old) != 1 or selector.replace(old, new, 1) != git(repo, 'show', head + ':' + path).decode():
            raise ValueError('release profile selector differs from trusted code and reviewed identity')
        if git(repo, 'rev-parse', base + ':patches/ports').strip() != git(repo, 'rev-parse', head + ':patches/ports').strip():
            raise ValueError('fork scope must preserve the base port series')
        try:
            prior = json.loads(git(repo, 'show', base + ':molly-build.json'))
            current = json.loads(git(repo, 'show', head + ':molly-build.json'))
            reviewed = json.loads(git(repo, 'show', REVIEWED_PROFILES + ':molly-build.json'))
            reviewed_port = json.loads(git(repo, 'show', REVIEWED_PROFILES + ':patches/ports/signal-8.33.2/provenance.json'))
            base_port = json.loads(git(repo, 'show', base + ':patches/ports/signal-8.33.2/provenance.json'))
            selection = {'profiles': current['profiles'], 'fork_source': current['fork_source']}
            rebased = check_release_profiles({'profiles': reviewed['profiles'], 'fork_source': reviewed['fork_source']},
                                             selection, reviewed_port, base_port)
            if rebased:
                fork_provenance = json.loads(git(repo, 'show', head + ':patches/forks/signal-foss/provenance.json'))
                snapshot = git(repo, 'show', head + ':patches/forks/signal-foss/reviewed-profiles.json')
                check_reviewed_profile_snapshot(selection, fork_provenance, snapshot)
            current.pop('profiles', None)
            current.pop('fork_source', None)
            if current != prior:
                raise ValueError('fork scope may only add named profiles to the base configuration')
        except (KeyError, TypeError, json.JSONDecodeError, subprocess.CalledProcessError) as exc:
            raise ValueError('invalid fork profile configuration') from exc
    if scope == 'features':
        for layer in FEATURES:
            feature_path = 'patches/ports/signal-8.33.2/followups/' + layer
            if exists(base, feature_path):
                raise ValueError('features base already contains ' + layer)
            if not all(exists(head, feature_path + '/' + name) for name in addition_files(layer)):
                raise ValueError('features phase is incomplete')
        check_feature_chain(repo, base, head)
    # Verify lower phases from their own ancestors, so changes hidden in a base
    # cannot escape the adjacent diff. Each recursion moves to an earlier phase.
    if scope == 'switch':
        check_scope(repo, 'pipeline', MOLLY, base)
    elif scope in ('release', 'followups', 'additions', 'features', 'forks'):
        lower_phase = {'release': 'switch', 'followups': 'release', 'additions': 'followups', 'features': 'additions', 'forks': 'additions'}[scope]
        history_base = integration_lower if integration_lower is not None else base
        candidates = git(repo, 'rev-list', '--ancestry-path', MOLLY + '..' + history_base).decode().splitlines()
        for candidate in candidates:
            try:
                check_scope(repo, lower_phase, candidate, history_base)
                break
            except (ValueError, subprocess.CalledProcessError):
                continue
        else:
            raise ValueError('lower base has no validated preceding stack phase')
    source = {'pipeline': MOLLY, 'switch': OLD, 'release': NEW, 'followups': NEW, 'additions': NEW, 'features': NEW, 'forks': NEW}[scope]
    paths = git(repo, 'diff', '--no-renames', '--name-only', '-z', source, head).split(b'\0')
    for raw in filter(None, paths):
        path = raw.decode('utf-8', 'surrogateescape')
        if not metadata(path) and not (integration_lower is not None and (path in MAIN_PROJECTION_EXCEPTIONS or mutable_main_documentation(path))):
            raise ValueError('source projection differs: ' + path)
    probe = subprocess.run(['git', '-C', str(repo), 'rev-parse', head + ':patches/molly'],
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    forest = probe.stdout.decode().strip() if probe.returncode == 0 else ''
    if scope == 'pipeline':
        if forest:
            raise ValueError('pipeline branch must not contain Molly forest')
    elif forest != FOREST:
        raise ValueError('Molly forest does not match the official snapshot')
    profile = git(repo, 'show', head + ':molly-build.json', check=False)
    if scope == 'pipeline':
        if profile:
            raise ValueError('pipeline branch must not contain a profile')
    else:
        try:
            config = json.loads(profile)
            if (config['signal']['commit'] != source or config['molly']['target'] != MOLLY or
                config['molly']['tree'] != '3cd0bb01cf840e4b702187085432020d163d80ce' or
                config['native'] is not None or (config['port'] is None) != (scope == 'switch')):
                raise ValueError('invalid profile pins')
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError('invalid profile') from exc
    return {'scope': scope, 'profile_present': scope != 'pipeline', 'port_present': scope in ('release', 'followups', 'additions', 'features', 'forks')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scope', required=True, choices=('pipeline', 'switch', 'release', 'followups', 'additions', 'features', 'forks', 'main-transition', 'main-docs', 'main-maintenance', 'main-snapshot', 'main-snapshot-transition', 'next-port'))
    parser.add_argument('--base', required=True)
    parser.add_argument('--head', default='HEAD')
    parser.add_argument('--repo', default='.')
    parser.add_argument('--integration-lower', help='trusted historical release-base for a main-based forks PR')
    args = parser.parse_args()
    try:
        print(json.dumps(check_scope(args.repo, args.scope, args.base, args.head, args.integration_lower), sort_keys=True))
    except (ValueError, subprocess.CalledProcessError) as exc:
        print('scope check failed: ' + str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
