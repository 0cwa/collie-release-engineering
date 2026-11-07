#!/usr/bin/env python3
"""Compose a full-tree security publication draft without creating commits.

The command starts at the historical reconstructed checkpoint, reruns the
current publication producer for an explicitly pinned application base, replays
the existing release/native/MollySocket layers, and adds the pinned security
workflow patch. It writes trees and an ordinary draft plan only; it never makes
G/I commits or changes refs.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'source'))
import publish
import commit_messages

HEX40 = re.compile(r'[0-9a-f]{40}\Z')
HEX64 = re.compile(r'[0-9a-f]{64}\Z')
OWNER_PATHS = ('README.md', 'LICENSE', 'NOTICE', 'assets')
SHORT_SUBJECTS = {
    'Resolve modify/delete conflicts for modules Molly removes and remove new upstream files added beneath those excluded modules':
        'Resolve excluded-module modify/delete conflicts',
    'Carry Molly SOCKS, Tor, DNS and transport choices through the updated Signal networking interfaces and NetCipher integration':
        'Preserve Molly proxy, DNS, and transport choices in updated Signal interfaces',
    'Resolve shared application, account, service and core integration files whose APIs or feature boundaries changed upstream':
        'Resolve shared app, account, service, and core integration changes',
}


def load_publication_layer():
    path = ROOT / 'source/layers/publication.py'
    spec = importlib.util.spec_from_file_location('current_publication_layer', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def git(repo, *args, data=None):
    env = publish.environment()
    env['GIT_NO_LAZY_FETCH'] = '1'
    return subprocess.run(
        ['git', '-C', str(repo), '-c', 'core.hooksPath=/dev/null',
         '-c', 'core.attributesFile=/dev/null', *args],
        input=data, env=env, check=True, stdout=subprocess.PIPE,
    ).stdout


def commit_parents(repo, commit):
    return git(repo, 'show', '-s', '--format=%P', publish.oid(commit)).decode().split()


def tree_entries(repo, tree, path):
    raw = git(repo, 'ls-tree', '-r', '-z', '--full-tree', publish.oid(tree), '--', path)
    entries = []
    for record in raw.split(b'\0'):
        if not record:
            continue
        metadata, name = record.split(b'\t', 1)
        mode, kind, oid = metadata.decode().split()
        entries.append((mode, kind, oid, name.decode()))
    return entries


def tree_blob(repo, tree, path):
    entries = tree_entries(repo, tree, path)
    if len(entries) != 1 or entries[0][1] != 'blob':
        raise ValueError(f'expected one regular tracked blob at {path}')
    return entries[0]


def apply_patch(stage, patch_path, expected_sha256=None, expected_paths=None):
    patch = Path(patch_path).read_bytes()
    digest = hashlib.sha256(patch).hexdigest()
    if expected_sha256 is not None and digest != expected_sha256:
        raise ValueError('static feature patch SHA-256 differs from its manifest')
    names = {
        b.decode()
        for a, b in re.findall(rb'(?m)^diff --git a/(.+) b/(.+)$', patch)
        if a == b
    }
    if expected_paths is not None and names != set(expected_paths):
        raise ValueError('static feature patch path set differs from its manifest')
    git(stage, 'apply', '--cached', '--binary', data=patch)
    return digest, names


def write_new(path, raw):
    path = Path(path)
    if path.exists() or path.is_symlink():
        raise ValueError('refusing to overwrite existing draft output: ' + str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, 'O_NOFOLLOW'):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o644)
    with os.fdopen(descriptor, 'wb') as output:
        output.write(raw)


def load_json(path):
    return json.loads(Path(path).read_bytes(), object_pairs_hook=publish.unique_object)


def verify_security_outputs(repo, tree, manifest):
    for path in manifest['paths']:
        mode, kind, oid, _ = tree_blob(repo, tree, path)
        if mode != manifest['modes'][path] or kind != 'blob':
            raise ValueError('security output mode/type differs: ' + path)
        content = git(repo, 'cat-file', 'blob', oid)
        if hashlib.sha256(content).hexdigest() != manifest['files'][path]:
            raise ValueError('security output bytes differ from pinned renderer manifest: ' + path)


def verify_feature_paths(repo, tree, source_repo, source_tree, paths):
    for path in paths:
        actual = tree_blob(repo, tree, path)
        expected = tree_blob(source_repo, source_tree, path)
        if actual[:3] != expected[:3]:
            raise ValueError('feature layer output differs from its reviewed source checkpoint: ' + path)


def verify_release_metadata(repo, tree):
    build = git(repo, 'show', f'{tree}:BUILD.md')
    gradle = git(repo, 'show', f'{tree}:app/build.gradle.kts')
    properties = git(repo, 'show', f'{tree}:gradle.properties')
    if (b'releaseVersionCode and releaseVersionName are normal source-defined Gradle properties' not in build
            or b'DRAFT candidate proposal:** 8.35.1-2-rc1 / 177703. This value is not allocated' not in build
            or b'providers.gradleProperty("releaseVersionCode")' not in gradle
            or b'providers.gradleProperty("releaseVersionName")' not in gradle
            or b'releaseVersionCode=177703\nreleaseVersionName=8.35.1-2-rc1' not in properties
            or b'DRAFT proposal only; not reserved' not in properties
            or b'throw GradleException("Direct Android builds BLOCKED:' not in gradle):
        raise ValueError('current publication producer omitted the explicit DRAFT version proposal or direct-build gate')


def verify_ordinary_pr_policy(repo, tree, stage):
    workflow = git(repo, 'show', f'{tree}:.github/workflows/source.yml').decode()
    if 'pull_request' not in workflow or 'workflow_dispatch' not in workflow:
        raise ValueError('published source workflow does not accept ordinary pull requests')
    command = 'git diff --check && test -f app/build.gradle.kts && test ! -d tools/patches'
    if command not in workflow:
        raise ValueError('ordinary source pull request workflow checks changed')
    # Execute the same lightweight source-PR check against the composed staged
    # tree. Release assembly remains blocked independently in Gradle.
    subprocess.run(['git', '-C', str(stage), 'diff', '--check'],
                   env=publish.environment(), check=True)
    if not (Path(stage) / 'app/build.gradle.kts').is_file() or (Path(stage) / 'tools/patches').exists():
        raise ValueError('ordinary source pull request checks do not pass on the composed tree')


def verify_publication_preimage(repo, tree, manifest):
    workflow = '.github/workflows/source.yml'
    mode, kind, oid, _ = tree_blob(repo, tree, workflow)
    if mode != '100644' or kind != 'blob':
        raise ValueError('current publication source workflow is not a regular file')
    content = git(repo, 'cat-file', 'blob', oid)
    if hashlib.sha256(content).hexdigest() != manifest['ordering']['publication_output_sha256']:
        raise ValueError('current publication output differs from the security layer preimage')
    for path in manifest['ordering']['requires_absent_paths']:
        if tree_entries(repo, tree, path):
            raise ValueError('security layer target already exists after publication cleanup: ' + path)


def validate_security_manifest(manifest):
    expected_paths = [
        '.github/codeql/codeql-config.yml',
        '.github/codeql/reviewed-findings.json',
        '.github/workflows/codeql.yml',
        '.github/workflows/publish-screenshots.yml',
        '.github/workflows/update-screenshots.yml',
    ]
    ordering = manifest.get('ordering', {})
    if manifest.get('kind') != 'feature-layer' or manifest.get('paths') != expected_paths:
        raise ValueError('security layer does not declare the five canonical publication outputs')
    if ordering.get('apply_after') != 'source/layers/publication.py::replace_application_workflows':
        raise ValueError('security layer ordering does not bind the actual publication cleanup function')
    if ordering.get('publication_output_path') != '.github/workflows/source.yml':
        raise ValueError('security layer ordering does not bind source.yml as the publication marker')
    if set(ordering.get('requires_absent_paths', [])) != set(expected_paths):
        raise ValueError('security layer must require every canonical output to be absent before apply')
    if set(manifest.get('files', {})) != set(expected_paths) or set(manifest.get('modes', {})) != set(expected_paths):
        raise ValueError('security layer output hashes or modes do not cover the canonical output set')
    for path in expected_paths:
        if not HEX64.fullmatch(manifest['files'][path]) or manifest['modes'][path] != '100644':
            raise ValueError('security layer output hash/mode is invalid: ' + path)
    if not HEX64.fullmatch(ordering.get('publication_output_sha256', '')):
        raise ValueError('security layer publication marker digest is invalid')


def shorten_message_subjects(messages):
    for entry in messages['layers']:
        if len(entry['subject']) > 120:
            source_name = entry['changes'].splitlines()[0].strip().rstrip('.')
            replacement = SHORT_SUBJECTS.get(source_name)
            if replacement is None or len(replacement) > 120:
                raise ValueError('publication layer has no reviewed short commit subject: ' + entry['changes'].splitlines()[0])
            entry['subject'] = replacement
    return messages


def set_owner_entries(repo, stage, current_main):
    """Copy exact current-main owner entries into the staged source index."""
    existing = git(stage, 'ls-files', '-z', '--', *OWNER_PATHS).split(b'\0')
    existing = [path for path in existing if path]
    if existing:
        git(stage, 'update-index', '--force-remove', '-z', '--stdin', data=b'\0'.join(existing) + b'\0')
    for path in OWNER_PATHS:
        entries = tree_entries(repo, current_main, path)
        if not entries:
            raise ValueError('current application base is missing owner path: ' + path)
        for mode, kind, oid, name in entries:
            if kind not in ('blob', 'commit'):
                raise ValueError('unexpected owner entry type: ' + name)
            git(stage, 'update-index', '--add', '--cacheinfo', f'{mode},{oid},{name}')


def compose(repo, checkpoint_repo, stage, engineering, plan_path,
            prior_generated, prior_integration, reconstruction_commit, current_main, output_plan,
            output_messages, output_markdown):
    repo = Path(repo).resolve()
    checkpoint_repo = Path(checkpoint_repo).resolve()
    stage = Path(stage).resolve()
    engineering = Path(engineering).resolve()
    publish.plain(repo)
    dirty = git(engineering, 'status', '--porcelain', '--',
                'source/compose_security_publication.py', 'source/layers/publication.py').decode().strip()
    if dirty:
        raise ValueError('producer implementation must be committed before composing a draft')
    engineering_revision = git(engineering, 'rev-parse', 'HEAD').decode().strip()
    publish.oid(engineering_revision)
    plan = publish.load_plan(Path(plan_path).read_bytes())
    current_main = publish.oid(current_main)
    prior_generated = publish.oid(prior_generated)
    prior_integration = publish.oid(prior_integration)
    reconstruction_commit = publish.oid(reconstruction_commit)

    security_manifest = load_json(ROOT / 'source/layers/security-workflows.json')
    validate_security_manifest(security_manifest)
    if current_main != security_manifest['application_base']:
        raise ValueError('security layer application base differs from the explicitly reviewed current main')
    if publish.tree(checkpoint_repo, prior_generated) != plan['final_tree']:
        raise ValueError('prior G3 tree differs from the existing source plan final checkpoint')
    if publish.tree(checkpoint_repo, prior_integration) != plan['final_tree']:
        raise ValueError('prior I3 tree differs from the existing source plan final checkpoint')
    if commit_parents(checkpoint_repo, prior_integration) != [plan['old_main'], prior_generated]:
        raise ValueError('prior I3 parents differ from the existing source plan')
    if publish.tree(checkpoint_repo, reconstruction_commit) != plan['reconstructed_tree']:
        raise ValueError('reconstruction commit tree differs from the plan checkpoint')
    git(checkpoint_repo, 'merge-base', '--is-ancestor', plan['old_main'], current_main)
    git(checkpoint_repo, 'merge-base', '--is-ancestor', reconstruction_commit, prior_generated)

    reconstructed_index = max(i for i, layer in enumerate(plan['layers'])
                              if layer['after_tree'] == plan['reconstructed_tree'])
    retained_layers = plan['layers'][:reconstructed_index + 1]
    if retained_layers[-1]['after_tree'] != plan['reconstructed_tree']:
        raise ValueError('existing plan does not end the retained prefix at reconstruction')

    top = git(stage, 'rev-parse', '--show-toplevel').decode().strip()
    if Path(top).resolve() != stage:
        raise ValueError('stage path must be the root of a registered application worktree')
    stage_head = git(stage, 'rev-parse', 'HEAD').decode().strip()
    if stage_head != current_main:
        raise ValueError('stage worktree must be checked out at the pinned current application base')
    if git(stage, 'write-tree').decode().strip() != plan['reconstructed_tree']:
        raise ValueError('stage index is not the verified prepublication reconstruction tree')

    security_patch_path = ROOT / security_manifest['patch']
    if hashlib.sha256(security_patch_path.read_bytes()).hexdigest() != security_manifest['patch_sha256']:
        raise ValueError('security layer patch digest differs from checked-in manifest')
    if security_manifest['policy']['revision'] != '124ca006b36c64e12807db2d1737d7f1faf06471':
        raise ValueError('security renderer policy pin differs from reviewed immutable revision')

    # This is the production publication producer. It updates owner content,
    # workflow replacement, BUILD/VERIFY, and build-input metadata from 96fe.
    publication = load_publication_layer()
    publication.apply_outputs(stage, engineering, repo, application_base=current_main,
                              publication_plan={'old_main': current_main})
    git(stage, 'add', '-A')
    tree = git(stage, 'write-tree').decode().strip()
    records = []

    def record(kind, name, before, after, provenance):
        if before == after:
            raise ValueError('publication layer unexpectedly made no tree change: ' + name)
        records.append({'name': name, 'kind': kind, 'before_tree': before,
                        'after_tree': after, 'provenance': provenance})

    record('publication',
           f'Regenerate publication from {current_main[:12]}',
           plan['reconstructed_tree'], tree,
           f'Ran source/layers/publication.py from exact committed engineering revision {engineering_revision} with recipe pin {publication.RECIPE_REVISION} and explicit application_base={current_main}. The producer copies README/LICENSE/NOTICE/assets from that exact commit, replaces legacy workflows with the reviewed source-only gate, and regenerates BUILD.md, VERIFY.md, build-input metadata, release version properties, and the unconditional direct-build gate. The ordinary pull-request workflow remains usable; release assembly remains blocked.')
    verify_release_metadata(repo, tree)
    verify_ordinary_pr_policy(repo, tree, stage)

    native_patch_path = ROOT / 'source/layers/native-repository.patch'
    native_layer = plan['layers'][-1]
    before = tree
    native_hash, native_paths = apply_patch(stage, native_patch_path)
    tree = git(stage, 'write-tree').decode().strip()
    record('standalone-build', native_layer['name'], before, tree,
           native_layer['provenance'] + f' Replayed static layer patch SHA-256 {native_hash}, paths {sorted(native_paths)}.')

    molly = load_json(ROOT / 'source/layers/mollysocket.json')
    molly_patch = ROOT / molly['patch']
    before = tree
    molly_hash, _ = apply_patch(stage, molly_patch, molly['patch_sha256'], molly['paths'])
    tree = git(stage, 'write-tree').decode().strip()
    if git(checkpoint_repo, 'cat-file', '-t', molly['after_tree']).strip() != b'tree':
        raise ValueError('MollySocket source checkpoint is missing or its tree differs')
    verify_feature_paths(repo, tree, checkpoint_repo, molly['after_tree'], molly['paths'])
    record('feature', molly['subject'], before, tree,
           f'Original reviewed MollySocket feature commit {molly["source_commit"]}; source parent {molly["source_parent"]}; original author {molly["author"]["name"]} <{molly["author"]["email"]}>. Complete binary/mode patch SHA-256 {molly_hash}.')

    publication_tree = tree
    verify_publication_preimage(repo, publication_tree, security_manifest)
    before = tree
    security_hash, security_paths = apply_patch(
        stage, security_patch_path, security_manifest['patch_sha256'], security_manifest['paths'])
    tree = git(stage, 'write-tree').decode().strip()
    verify_security_outputs(repo, tree, security_manifest)
    if set(security_paths) != set(security_manifest['paths']):
        raise ValueError('security layer changed paths differ from its manifest')
    record('feature', security_manifest['name'], before, tree,
           f'Canonical renderer {security_manifest["policy"]["renderer"]} from {security_manifest["policy"]["origin"]} at immutable revision {security_manifest["policy"]["revision"]}; renderer SHA-256 {security_manifest["policy"]["renderer_sha256"]}; static output patch SHA-256 {security_hash}. Actions analysis only; Java/Kotlin/native source not run.')

    plan['old_main'] = current_main
    plan['layers'] = retained_layers + records
    plan['final_tree'] = tree
    publish.validate_plan(plan)
    publish.verify_user_files(repo, tree, current_main)
    verify_publication_preimage(repo, publication_tree, security_manifest)
    verify_security_outputs(repo, tree, security_manifest)

    workflow = json.loads(git(repo, 'show', f'{tree}:build-inputs/native-publication.json'))
    if workflow.get('status') != 'BLOCKED':
        raise ValueError('regenerated source must retain the unconditional Android build gate')
    gradle = git(repo, 'show', f'{tree}:app/build.gradle.kts')
    if b'throw GradleException("Direct Android builds BLOCKED:' not in gradle:
        raise ValueError('regenerated source lost the direct Android build gate')

    plan_bytes = (json.dumps(plan, indent=2) + '\n').encode()
    plan_sha256 = hashlib.sha256(plan_bytes).hexdigest()
    messages = shorten_message_subjects(commit_messages.preview(plan, plan_sha256))
    commit_messages.validate(messages, plan, plan_sha256)
    write_new(output_plan, plan_bytes)
    write_new(output_messages, (json.dumps(messages, indent=2) + '\n').encode())
    write_new(output_markdown, commit_messages.markdown(messages).encode())
    return {'plan': str(output_plan), 'plan_sha256': plan_sha256,
            'current_main': current_main, 'prior_generated': prior_generated,
            'prior_integration': prior_integration,
            'reconstructed_tree': plan['reconstructed_tree'],
            'publication_tree': publication_tree, 'final_tree': tree,
            'layers_added': records, 'messages': str(output_messages),
            'markdown': str(output_markdown), 'status': 'DRAFT; no G/I commits generated'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, required=True, help='full application Git repository')
    parser.add_argument('--checkpoint-repo', type=Path, required=True, help='read-only repository containing verified old G3/I3 commits')
    parser.add_argument('--stage', type=Path, required=True, help='registered worktree at the prepublication checkpoint')
    parser.add_argument('--engineering', type=Path, default=ROOT)
    parser.add_argument('--plan', type=Path, default=ROOT / 'source/candidate-plan-v3.json')
    parser.add_argument('--prior-generated', required=True, help='verified prior G3 commit')
    parser.add_argument('--prior-integration', required=True, help='verified prior I3 commit')
    parser.add_argument('--reconstruction-commit', required=True, help='commit whose tree is the plan reconstructed_tree')
    parser.add_argument('--current-main', required=True, help='explicit current application main snapshot')
    parser.add_argument('--output-plan', type=Path, required=True)
    parser.add_argument('--output-messages', type=Path, required=True)
    parser.add_argument('--output-markdown', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(compose(args.repo, args.checkpoint_repo, args.stage, args.engineering, args.plan,
                             args.prior_generated, args.prior_integration,
                             args.reconstruction_commit, args.current_main,
                             args.output_plan, args.output_messages,
                             args.output_markdown), indent=2))


if __name__ == '__main__':
    main()
