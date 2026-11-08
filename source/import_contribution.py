#!/usr/bin/env python3
"""Import reviewed application changes as complete checked overlays.

Maintainers run this before publication, never during a production Gradle build.
Use a single commit for one landed PR, or an explicit aggregate for a backlog.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import publish

PATCH_SHA256 = re.compile(r'[0-9a-f]{64}\Z')


def import_reviewed(repo, plan_path, reviewed_commit, overlay_path, expected_patch_sha256=None, current_main=None):
    publish.plain(repo)
    plan = publish.load_plan(plan_path.read_bytes())
    parents = publish.git(repo, 'show', '-s', '--format=%P', publish.oid(reviewed_commit)).decode().split()
    if len(parents) != 1 or publish.tree(repo, parents[0]) != plan['final_tree']:
        raise ValueError('contribution parent differs from complete approved source tree')
    if current_main is None:
        raise ValueError('independently pinned current main snapshot required')
    publish.oid(current_main)
    publish.git(repo, 'merge-base', '--is-ancestor', plan['old_main'], current_main)
    publish.git(repo, 'merge-base', '--is-ancestor', reviewed_commit, current_main)
    if publish.tree(repo,current_main) != publish.tree(repo,reviewed_commit):
        raise ValueError('current main tree differs from reviewed contribution; review/import the complete newer tip instead')
    record = publish.round_trip(repo, parents[0], reviewed_commit, overlay_path)
    if expected_patch_sha256 is not None and record['patch_sha256'] != expected_patch_sha256:
        raise ValueError('contribution patch differs from separately reviewed digest')
    # Replay into a separate index. No source checkout is changed, and normal
    # contribution workflows never require contributors to edit the patch forest.
    with tempfile.TemporaryDirectory(prefix='contribution-index-', dir=overlay_path.parent) as scratch:
        env = publish.environment(); env['GIT_INDEX_FILE'] = str(Path(scratch)/'index')
        def indexed(*args):
            return subprocess.run(['git','-C',str(repo),'-c','core.hooksPath=/dev/null',
                                   '-c','core.attributesFile=/dev/null',*args],
                                  env=env,check=True,stdout=subprocess.PIPE).stdout
        indexed('read-tree',plan['final_tree'])
        indexed('apply','--cached','--binary',str(overlay_path.resolve()))
        actual=indexed('write-tree').decode().strip()
        if actual != publish.tree(repo,reviewed_commit):
            raise ValueError('round-trip reconstruction differs from full reviewed tree')
    plan['old_main'] = current_main
    plan['layers'].append({'name':'Import reviewed ordinary application contribution',
                           'kind':'publication','before_tree':plan['final_tree'], 'after_tree':actual,
                           'provenance':f'Original reviewed contribution {reviewed_commit}; complete binary/mode delta SHA256 {record["patch_sha256"]}. Original commit retains contributor attribution; generated publication is composite.'})
    plan['final_tree']=actual
    publish.validate_plan(plan)
    return {'plan':plan,'contribution_commit':reviewed_commit,**record}


def import_aggregate(repo, plan_path, overlay_path, current_main, expected_patch_sha256):
    """Import an externally reviewed S-to-T application-main delta.

    This handles a landed backlog without trying to merge or rebase PRs. The
    caller independently pins current_main and reviews the complete binary Git
    delta digest. We replay that delta from plan.old_main in a private index and
    require the resulting complete tree to equal current_main before writing an
    overlay or rebinding the plan.
    """
    publish.plain(repo)
    if publish.git(repo, 'rev-parse', '--show-prefix').decode().strip():
        raise ValueError('aggregate repository path must be the Git worktree root')
    plan = publish.load_plan(plan_path.read_bytes())
    base_main = publish.oid(plan['old_main'])
    target_main = publish.oid(current_main)
    overlay_path = Path(overlay_path)
    if not isinstance(expected_patch_sha256, str) or not PATCH_SHA256.fullmatch(expected_patch_sha256):
        raise ValueError('separately reviewed aggregate patch SHA-256 required')
    if not overlay_path.parent.is_dir():
        raise ValueError('aggregate overlay parent directory must already exist')
    if overlay_path.exists() or overlay_path.is_symlink():
        raise ValueError('refusing to overwrite an existing aggregate patch')
    if base_main == target_main:
        raise ValueError('aggregate target must advance the approved application base')

    base_tree = publish.tree(repo, base_main)
    if base_tree != plan['final_tree']:
        raise ValueError('aggregate base tree differs from the complete approved source tree')
    try:
        publish.git(repo, 'merge-base', '--is-ancestor', base_main, target_main)
    except subprocess.CalledProcessError as error:
        raise ValueError('aggregate current main is not descended from the approved base') from error

    target_tree = publish.tree(repo, target_main)
    if target_tree == base_tree:
        raise ValueError('aggregate application delta is empty')
    delta = publish.git(repo, 'diff', '--binary', '--full-index', '--no-ext-diff',
                        '--no-textconv', '--no-renames', base_main, target_main, '--')
    patch_sha256 = hashlib.sha256(delta).hexdigest()
    if patch_sha256 != expected_patch_sha256:
        raise ValueError('aggregate patch differs from separately reviewed digest')

    # A private index keeps the live checkout and its index untouched. Full tree
    # equality catches missing/extra paths, content, modes, symlinks and gitlinks.
    with tempfile.TemporaryDirectory(prefix='aggregate-index-', dir=overlay_path.parent) as scratch:
        env = publish.environment()
        env['GIT_INDEX_FILE'] = str(Path(scratch) / 'index')

        def indexed(*args, data=None):
            return subprocess.run(['git', '-C', str(repo), '-c', 'core.hooksPath=/dev/null',
                                   '-c', 'core.attributesFile=/dev/null', *args],
                                  input=data, env=env, check=True, stdout=subprocess.PIPE).stdout

        indexed('read-tree', base_tree)
        try:
            indexed('apply', '--cached', '--binary', data=delta)
        except subprocess.CalledProcessError as error:
            raise ValueError('aggregate delta does not apply cleanly to the approved base') from error
        actual_tree = indexed('write-tree').decode().strip()
        if actual_tree != target_tree:
            raise ValueError('aggregate replay differs from the complete current-main tree')

    plan['old_main'] = target_main
    plan['layers'].append({
        'name': 'Import reviewed aggregate application backlog',
        'kind': 'publication',
        'before_tree': base_tree,
        'after_tree': actual_tree,
        'provenance': (
            f'Reviewed aggregate application delta {base_main}..{target_main}; '
            f'complete binary/mode patch SHA256 {patch_sha256}. Application main '
            f'history through {target_main} remains reachable from the integration '
            'parent; individual PR authorship is not inferred from the aggregate '
            'delta, and generated publication is composite.'
        ),
    })
    plan['final_tree'] = actual_tree
    publish.validate_plan(plan)

    # O_EXCL also refuses an existing dangling symlink or a path created after
    # the early check above; no reviewed bytes are silently replaced.
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, 'O_NOFOLLOW'):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(overlay_path, flags, 0o600)
    except FileExistsError as error:
        raise ValueError('refusing to overwrite an existing aggregate patch') from error
    with os.fdopen(descriptor, 'wb') as output:
        output.write(delta)

    return {
        'plan': plan,
        'aggregate_base': base_main,
        'current_main': target_main,
        'before_tree': base_tree,
        'after_tree': actual_tree,
        'patch_sha256': patch_sha256,
    }


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo',required=True,type=Path)
    parser.add_argument('--plan',required=True,type=Path)
    import_mode = parser.add_mutually_exclusive_group(required=True)
    import_mode.add_argument('--reviewed-commit',help='reviewed single-parent application contribution')
    import_mode.add_argument('--aggregate',action='store_true',help='review the complete plan.old_main to current-main delta')
    parser.add_argument('--overlay',required=True,type=Path)
    parser.add_argument('--expected-patch-sha256',required=True)
    parser.add_argument('--current-main',required=True,help='independently read current application main tip')
    a=parser.parse_args()
    if a.aggregate:
        result=import_aggregate(a.repo.resolve(),a.plan,a.overlay,a.current_main,a.expected_patch_sha256)
    else:
        result=import_reviewed(a.repo.resolve(),a.plan,a.reviewed_commit,a.overlay,a.expected_patch_sha256,a.current_main)
    print(json.dumps(result,indent=2))
