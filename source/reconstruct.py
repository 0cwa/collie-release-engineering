#!/usr/bin/env python3
"""Run the unchanged legacy assembler with public inputs in a fresh object store.

Construction ends at the output checkout. This program is never a Gradle task.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess

ORIGINS = {
    'signal': 'https://github.com/signalapp/Signal-Android.git',
    'molly': 'https://github.com/mollyim/mollyim-android.git',
}


def environment():
    env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
               GIT_ATTR_NOSYSTEM='1', GIT_NO_REPLACE_OBJECTS='1', LC_ALL='C')
    return env


def run(directory, *args):
    return subprocess.run(args, cwd=directory, env=environment(), check=True,
                          stdout=subprocess.PIPE, text=True).stdout.strip()


def reconstruct(engineering, workspace, profile='collie'):
    engineering = engineering.resolve()
    # Check lexical leaves before resolve(): dangling symlinks must remain
    # visible, and legacy release.py writes its report without exclusive create.
    requested = workspace.absolute()
    targets = (requested, requested.with_name(requested.name + '-source'),
               requested.with_name(requested.name + '-reconstruction.json'))
    for target in targets:
        if target.exists() or target.is_symlink():
            raise ValueError('workspace/output/report must not exist: ' + str(target))
    workspace = requested.resolve()
    revision = run(engineering, 'git', 'rev-parse', 'HEAD')
    if run(engineering, 'git', 'status', '--porcelain', '--untracked-files=normal'):
        raise ValueError('engineering checkout must be clean')
    config = json.loads((engineering / 'molly-build.json').read_text())
    workspace.mkdir(parents=True)
    run(workspace, 'git', 'init', '--template=', '-q')
    # No alternates, shared clone, replace refs, inherited config or local object store.
    for name, origin in ORIGINS.items():
        commit = config[name]['commit' if name == 'signal' else 'target']
        run(workspace, 'git', '-c', 'core.hooksPath=/dev/null', 'fetch',
            '--filter=blob:none', '--no-tags', origin, commit)
    run(workspace, 'git', 'checkout', '--detach', config['signal']['commit'])
    for path in ('tools/patches', 'tools/native', 'patches'):
        shutil.copytree(engineering / path, workspace / path, dirs_exist_ok=True)
    shutil.copyfile(engineering / 'molly-build.json', workspace / 'molly-build.json')
    run(workspace, 'python3', 'tools/patches/verify_source_bundles.py')
    output = workspace.parent / (workspace.name + '-source')
    report = workspace.parent / (workspace.name + '-reconstruction.json')
    run(workspace, 'python3', 'tools/patches/release.py', '--repo', str(workspace),
        '--config', 'molly-build.json', '--profile', profile,
        '--worktree', str(output), '--report', str(report))
    proof = json.loads(report.read_text())
    if proof.get('status') != 'PASS':
        raise ValueError('legacy reconstruction did not pass')
    return {'engineering_commit': revision, 'profile': profile,
            'tree': proof['tree'], 'output': str(output), 'report': str(report)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--engineering', type=Path, default=Path('.'))
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--historical-profile', default='collie',
                        choices=['collie', 'collie-foss', 'molly-minimal',
                                 'molly-continued', 'molly-foss-continued'])
    args = parser.parse_args()
    print(json.dumps(reconstruct(args.engineering, args.workspace, args.historical_profile), indent=2))
