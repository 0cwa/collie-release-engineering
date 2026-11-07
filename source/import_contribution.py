#!/usr/bin/env python3
"""Recover historical source-first changes with exact patch/tree verification.

This helper does not authorize new code-first application contributions.
New application work must follow the canonical patch-first development policy.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import publish


def import_reviewed(repo, plan_path, reviewed_commit, overlay_path, expected_patch_sha256=None, current_main=None):
    publish.plain(repo)
    plan = publish.load_plan(plan_path.read_bytes())
    parents = publish.git(repo, 'show', '-s', '--format=%P', publish.oid(reviewed_commit)).decode().split()
    if len(parents) != 1 or publish.tree(repo, parents[0]) != plan['final_tree']:
        raise ValueError('contribution parent differs from complete approved source tree')
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
    if current_main is not None:
        publish.oid(current_main)
        publish.git(repo, 'merge-base', '--is-ancestor', plan['old_main'], current_main)
        publish.git(repo, 'merge-base', '--is-ancestor', reviewed_commit, current_main)
        plan['old_main'] = current_main
    plan['layers'].append({'name':'Import reviewed ordinary application contribution',
                           'kind':'publication','before_tree':plan['final_tree'], 'after_tree':actual,
                           'provenance':f'Original reviewed contribution {reviewed_commit}; complete binary/mode delta SHA256 {record["patch_sha256"]}. Original commit retains contributor attribution; generated publication is composite.'})
    plan['final_tree']=actual
    publish.validate_plan(plan)
    return {'plan':plan,'contribution_commit':reviewed_commit,**record}


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo',required=True,type=Path)
    parser.add_argument('--plan',required=True,type=Path)
    parser.add_argument('--reviewed-commit',required=True)
    parser.add_argument('--overlay',required=True,type=Path)
    parser.add_argument('--expected-patch-sha256',required=True)
    parser.add_argument('--current-main',required=True,help='independently read reviewed current application main; must retain contribution')
    a=parser.parse_args()
    print(json.dumps(import_reviewed(a.repo.resolve(),a.plan,a.reviewed_commit,a.overlay,a.expected_patch_sha256,a.current_main),indent=2))
