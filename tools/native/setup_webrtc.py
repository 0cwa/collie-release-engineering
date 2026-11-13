#!/usr/bin/env python3
"""Prepare the pinned WebRTC gclient checkout for diagnostic native builds.

This does not attest the downloaded closure, build RingRTC, or authorize release.
It downloads dependencies if missing; callers must budget disk and network first.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

import prepare_sources
import stage_artifacts
import webrtc_deps

class Failure(Exception):
    pass

def require(condition, message):
    if not condition:
        raise Failure(message)

def run(args, cwd, env):
    print('+ ' + ' '.join(map(str, args)), flush=True)
    result = subprocess.run(args, cwd=cwd, env=env, check=False)
    require(result.returncode == 0, 'WebRTC dependency setup command failed')

def validate_depot(depot, reference):
    depot = depot.resolve(strict=True)
    require(depot.is_dir(), 'depot_tools is not a directory')
    for override in ('.cipd_client_root', '.cipd_client_platform'):
        require(not os.path.lexists(depot / override), 'depot_tools CIPD override is not allowed: ' + override)
    require(prepare_sources.git(depot, 'rev-parse', '--show-toplevel') == str(depot), 'depot_tools is not a repository root')
    prepare_sources.verify_worktree(depot)
    require(prepare_sources.git(depot, 'rev-parse', 'HEAD') == reference['webrtc_depot_tools_revision'], 'depot_tools revision differs from pinned DEPS')
    require(not prepare_sources.git(depot, 'diff', '--name-only') and not prepare_sources.git(depot, 'diff', '--cached', '--name-only'), 'depot_tools tracked files changed')
    gclient = depot / 'gclient'
    require(gclient.is_file() and gclient.resolve().is_relative_to(depot) and os.access(gclient, os.X_OK), 'Pinned depot_tools has no safe executable gclient')
    prepare_sources.git(depot, 'ls-files', '--error-unmatch', 'gclient')
    return depot

def setup(lock_path, sources, depot, output, runner=run):
    lock_path = lock_path.resolve(strict=True)
    lock = stage_artifacts.load_lock(lock_path)
    sources = sources.resolve(strict=True)
    proof = prepare_sources.verify_prepared(lock_path, sources)
    depot = validate_depot(depot, lock['toolchain_reference'])
    output = output.absolute()
    require(not output.exists(), 'WebRTC workspace must not exist')
    common = sources / 'ringrtc/config/webrtc.gclient.common'
    android = sources / 'ringrtc/config/webrtc.gclient.android'
    require(all(p.is_file() and not p.is_symlink() for p in (common, android)), 'Pinned RingRTC gclient configuration missing')
    output.mkdir(parents=True)
    (output / '.gclient').write_bytes(common.read_bytes() + b'\n' + android.read_bytes())
    temp = output / '.tmp'
    temp.mkdir()
    # Do not let inherited Python or depot bootstrap overrides bypass the pinned checkout.
    blocked = {'PYTHONPATH', 'PYTHONHOME', 'PYTHONUSERBASE', 'DEPOT_TOOLS_DIR',
               'DEPOT_TOOLS_UNAME_S', 'VPYTHON_BYPASS', 'CUSTOM_CIPD_CLIENT',
               'BASH_ENV', 'ENV'}
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(('GIT_', 'BASH_FUNC_')) and key not in blocked}
    env['PYTHONNOUSERSITE'] = '1'
    env['TMPDIR'] = str(temp)
    env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull)
    env.update(PATH=str(depot) + os.pathsep + env.get('PATH', ''), DEPOT_TOOLS_UPDATE='0', GIT_TERMINAL_PROMPT='0', GIT_NO_REPLACE_OBJECTS='1')
    revision = lock['source_provenance']['webrtc']
    gclient = str(depot / 'gclient')
    runner([gclient, 'sync', '--no-history', '--nohooks', '--jobs', '12', '--revision=src@' + revision], output, env)
    checkout = output / 'src'
    require(checkout.is_dir() and not checkout.is_symlink(), 'gclient did not create the expected WebRTC checkout')
    require(prepare_sources.git(checkout, 'rev-parse', 'HEAD') == revision, 'gclient checked out an unexpected WebRTC revision')
    deps = checkout / 'DEPS'
    with webrtc_deps.production_hooks_only(deps, lock['toolchain_reference']):
        runner([gclient, 'runhooks'], output, env)
    require(not prepare_sources.git(checkout, 'diff', '--name-only') and not prepare_sources.git(checkout, 'diff', '--cached', '--name-only'), 'WebRTC tracked source changed during hooks')
    for item in lock['source_patches']:
        if Path(item['path']).name.startswith('webrtc-'):
            prepare_sources.git(checkout, 'apply', '--index', '--check', str(lock_path.parent / item['path']))
            prepare_sources.git(checkout, 'apply', '--index', str(lock_path.parent / item['path']))
    tree = prepare_sources.git(checkout, 'write-tree')
    require(tree == proof['sources']['webrtc']['prepared_tree'], 'gclient WebRTC patched tree differs from independently replayed source')
    require(not prepare_sources.git(checkout, 'diff', '--name-only'), 'WebRTC worktree differs from its patched index')
    receipt = {'schema': 1, 'status': 'PARTIAL_WEBRTC_SETUP', 'release_allowed': False,
               'scope': 'gclient setup and patched source tree only; downloaded closure, native build and runtime unverified',
               'lock_sha256': stage_artifacts.digest(lock_path), 'webrtc_base': revision, 'webrtc_tree': tree,
               'depot_tools_revision': lock['toolchain_reference']['webrtc_depot_tools_revision']}
    (output / 'setup-receipt.json').write_text(json.dumps(receipt, indent=2, sort_keys=True) + '\n')
    return receipt

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lock', required=True, type=Path)
    parser.add_argument('--sources', required=True, type=Path)
    parser.add_argument('--depot-tools', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    try:
        setup(args.lock, args.sources, args.depot_tools, args.output)
    except (Failure, prepare_sources.Failure, stage_artifacts.Failure, webrtc_deps.Failure, OSError, ValueError) as exc:
        print(f'WebRTC setup failed: {exc}', file=sys.stderr)
        return 1
    return 0

if __name__ == '__main__':
    sys.exit(main())
