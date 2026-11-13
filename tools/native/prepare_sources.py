#!/usr/bin/env python3
"""Prepare pinned native source from caller-supplied local Git repositories.

No network fetch is performed. Results are not a native build or runtime proof.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import re
import stat
import tempfile
import subprocess
import sys

import stage_artifacts
import webrtc_deps

class Failure(Exception):
    pass

def require(condition, message):
    if not condition:
        raise Failure(message)

def git(repo, *arguments, data=None, extra_env=None, raw=False):
    env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull, GIT_ATTR_NOSYSTEM='1',
               GIT_TERMINAL_PROMPT='0', GIT_NO_REPLACE_OBJECTS='1', GIT_NO_LAZY_FETCH='1',
               GIT_COMMITTER_NAME='Collie Native Preparation', GIT_COMMITTER_EMAIL='native-preparation@localhost')
    if extra_env:
        env.update(extra_env)
    command = ['git', '-C', str(repo), '-c', 'core.hooksPath=/dev/null', '-c', 'core.autocrlf=false',
               '-c', 'core.fsmonitor=false', '-c', 'core.attributesFile=/dev/null',
               '-c', 'commit.gpgsign=false', '-c', 'rerere.enabled=false', *arguments]
    result = subprocess.run(command, input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    if result.returncode:
        raise Failure('Git source preparation failed: ' + result.stderr.decode(errors='replace').strip())
    return result.stdout if raw else result.stdout.decode().strip()


def verify_worktree(repo):
    """Check tracked bytes directly, independent of Git's cached stat promises."""
    repo = repo.resolve(strict=True)
    configuration = git(repo, 'config', '--local', '--null', '--list', raw=True)
    for record in configuration.split(b'\0'):
        key = record.split(b'\n', 1)[0].lower()
        require(not key.startswith((b'filter.', b'include.', b'includeif.')),
                'Local Git filters and included configuration are not allowed in prepared source')
    attributes = Path(git(repo, 'rev-parse', '--path-format=absolute', '--git-path', 'info/attributes'))
    require(not os.path.lexists(attributes) or
            (attributes.is_file() and not attributes.is_symlink() and attributes.stat().st_size == 0),
            'Local Git attributes are not allowed in prepared source')
    flags = [entry for entry in git(repo, 'ls-files', '-v', '-z', raw=True).split(b'\0') if entry]
    require(all(entry.startswith(b'H ') for entry in flags),
            'Prepared source has hidden or unmerged tracked files')
    for entry in git(repo, 'ls-files', '--stage', '-z', raw=True).split(b'\0'):
        if not entry:
            continue
        metadata, filename = entry.split(b'\t', 1)
        mode, expected, stage = metadata.split(b' ')
        require(stage == b'0', 'Prepared source has unmerged tracked files')
        relative = os.fsdecode(filename)
        path = repo / relative
        require(path.parent.resolve() == path.parent and path.parent.is_relative_to(repo),
                'Prepared source file has a symlinked parent or escapes checkout: ' + relative)
        if mode == b'160000':
            require(not os.path.lexists(path) or
                    (path.is_dir() and not path.is_symlink() and not any(path.iterdir())),
                    'Prepared source has an unverified populated Gitlink: ' + relative)
            continue
        details = path.lstat()
        if mode == b'120000':
            require(stat.S_ISLNK(details.st_mode), 'Prepared source symlink changed: ' + relative)
            contents = os.fsencode(os.readlink(path))
            actual = hashlib.sha1(b'blob ' + str(len(contents)).encode() + b'\0' + contents).hexdigest().encode()
        else:
            require(mode in (b'100644', b'100755') and stat.S_ISREG(details.st_mode),
                    'Prepared source file type changed: ' + relative)
            require(bool(details.st_mode & stat.S_IXUSR) == (mode == b'100755'),
                    'Prepared source executable mode changed: ' + relative)
            digest = hashlib.sha1(b'blob ' + str(details.st_size).encode() + b'\0')
            with path.open('rb') as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b''):
                    digest.update(block)
            actual = digest.hexdigest().encode()
        if actual != expected and mode in (b'100644', b'100755') and details.st_size <= 8 * 1024 * 1024:
            # Honor only CRLF conversion explicitly selected by pinned attributes.
            values = git(repo, 'check-attr', '--cached', '-z', 'text', 'eol', '--', relative, raw=True).split(b'\0')
            if values in ([filename, b'text', b'set', filename, b'eol', b'crlf', b''],
                          [filename, b'text', b'unspecified', filename, b'eol', b'crlf', b'']):
                contents = path.read_bytes()
                normalized = contents.replace(b'\r\n', b'\n')
                require(normalized.count(b'\n') == contents.count(b'\r\n'), 'Mixed line endings in prepared source')
                actual = hashlib.sha1(b'blob ' + str(len(normalized)).encode() + b'\0' + normalized).hexdigest().encode()
        require(actual == expected, 'Prepared source worktree bytes differ: ' + relative)

def prepare(lock_path, inputs, output):
    lock_path = lock_path.resolve(strict=True)
    lock = stage_artifacts.load_lock(lock_path)
    output = output.absolute()
    require(not output.exists(), 'Source output must not exist')
    # Verify all source commits are present before creating any output.
    repositories = {}
    for name in ('libsignal', 'ringrtc', 'webrtc'):
        source = inputs[name].resolve(strict=True)
        require(source.is_dir(), 'Source input is not a repository directory')
        commit = lock['source_provenance'][name]
        require(git(source, 'cat-file', '-t', commit) == 'commit', 'Pinned commit is unavailable')
        if name == 'webrtc':
            deps = git(source, 'show', commit + ':DEPS', raw=True)
            try:
                webrtc_deps.verify_reference(deps, lock['toolchain_reference'])
            except webrtc_deps.Failure as exc:
                raise Failure(str(exc)) from exc
        repositories[name] = source
    output.mkdir(parents=True)
    receipt = {'schema': 1, 'scope': 'source preparation only; native build and runtime unverified',
               'lock_sha256': stage_artifacts.digest(lock_path), 'sources': {}}
    try:
        for name in ('libsignal', 'ringrtc', 'webrtc'):
            target = output / name
            target.mkdir()
            git(target, 'init', '--quiet', '--template=')
            base = lock['source_provenance'][name]
            git(target, 'fetch', '--quiet', '--no-tags', '--depth=1', str(repositories[name]), base)
            git(target, 'checkout', '--quiet', '--detach', 'FETCH_HEAD')
            require(git(target, 'rev-parse', 'HEAD') == base, 'Fetched source differs from pinned commit')
            if name in ('libsignal', 'ringrtc'):
                patch = next(lock_path.parent / x['path'] for x in lock['source_patches'] if Path(x['path']).name.startswith(name + '-'))
                git(target, 'am', '--3way', str(patch))
                staged_tree = git(target, 'rev-parse', 'HEAD^{tree}')
                require(not git(target, 'status', '--porcelain=v1', '--untracked-files=all'), 'Unexpected source changes after mailbox application')
            else:
                for item in lock['source_patches']:
                    if Path(item['path']).name.startswith('webrtc-'):
                        git(target, 'apply', '--index', '--check', str(lock_path.parent / item['path']))
                        git(target, 'apply', '--index', str(lock_path.parent / item['path']))
                staged_tree = git(target, 'write-tree')
                # git diff without --cached compares tracked worktree to the prepared index.
                require(not git(target, 'diff', '--name-only'), 'Prepared WebRTC files differ from its index')
                require(not git(target, 'ls-files', '--others', '--exclude-standard'), 'Unexpected untracked WebRTC source')
            receipt['sources'][name] = {'base': base, 'prepared_tree': staged_tree,
                                        'head': git(target, 'rev-parse', 'HEAD')}
        (output / 'source-receipt.json').write_text(json.dumps(receipt, indent=2, sort_keys=True) + '\n')
        return receipt
    except Exception:
        # Only the newly created output, never caller-owned inputs, is removed on failure.
        shutil.rmtree(output)
        raise

def verify_prepared(lock_path, source_root):
    lock_path = lock_path.resolve(strict=True)
    lock = stage_artifacts.load_lock(lock_path)
    source_root = source_root.resolve(strict=True)
    result = {}
    for name in ('libsignal', 'ringrtc', 'webrtc'):
        repo = source_root / name
        require(repo.is_dir() and not repo.is_symlink(), 'Prepared source is missing or a symlink')
        base = lock['source_provenance'][name]
        require(git(repo, 'cat-file', '-t', base) == 'commit', 'Pinned source base is unavailable')
        verify_worktree(repo)
        if name == 'webrtc':
            deps = git(repo, 'show', base + ':DEPS', raw=True)
            try:
                webrtc_deps.verify_reference(deps, lock['toolchain_reference'])
            except webrtc_deps.Failure as exc:
                raise Failure(str(exc)) from exc
        object_dir = git(repo, 'rev-parse', '--path-format=absolute', '--git-path', 'objects')
        with tempfile.TemporaryDirectory(prefix='native-source-proof-', dir=source_root.parent) as temp:
            temp_root = Path(temp)
            objects = temp_root / 'objects'
            objects.mkdir()
            env = {'GIT_INDEX_FILE': str(temp_root / 'index'), 'GIT_OBJECT_DIRECTORY': str(objects),
                   'GIT_ALTERNATE_OBJECT_DIRECTORIES': object_dir}
            git(repo, 'read-tree', base, extra_env=env)
            selected = [item for item in lock['source_patches'] if Path(item['path']).name.startswith(name + '-')]
            require(bool(selected), 'No patches for prepared source')
            for item in selected:
                data = (lock_path.parent / item['path']).read_bytes()
                if name != 'webrtc':
                    data_parts = [part for part in re.split(rb'(?=^From [0-9a-f]{40} Mon Sep 17 00:00:00 2001$)', data, flags=re.M) if part.strip()]
                else:
                    data_parts = [data]
                require(bool(data_parts), 'Empty native source patch')
                for part in data_parts:
                    git(repo, 'apply', '--cached', '--binary', '--whitespace=nowarn', data=part, extra_env=env)
            expected_tree = git(repo, 'write-tree', extra_env=env)
        if name == 'webrtc':
            require(git(repo, 'rev-parse', 'HEAD') == base, 'Prepared WebRTC base changed')
            actual_tree = git(repo, 'write-tree')
            require(not git(repo, 'diff', '--name-only'), 'Prepared WebRTC worktree changed')
            require(not git(repo, 'ls-files', '--others', '--exclude-standard'), 'Unexpected untracked WebRTC files')
        else:
            actual_tree = git(repo, 'rev-parse', 'HEAD^{tree}')
            require(not git(repo, 'status', '--porcelain=v1', '--untracked-files=all'), 'Prepared native source changed')
        require(actual_tree == expected_tree, 'Prepared native source differs from replayed pinned patches')
        result[name] = {'base': base, 'prepared_tree': actual_tree}
    return {'schema': 1, 'scope': 'independently replayed source trees only; build and runtime unverified',
            'lock_sha256': stage_artifacts.digest(lock_path), 'sources': result}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lock', required=True, type=Path)
    parser.add_argument('--verify-prepared', action='store_true')
    for name in ('libsignal', 'ringrtc', 'webrtc'):
        parser.add_argument('--' + name, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    try:
        if args.verify_prepared:
            result = verify_prepared(args.lock, args.output)
        else:
            inputs = {k: getattr(args, k) for k in ('libsignal', 'ringrtc', 'webrtc')}
            require(all(inputs.values()), 'All three local source repositories are required')
            result = prepare(args.lock, inputs, args.output)
    except (Failure, stage_artifacts.Failure, OSError, ValueError) as exc:
        print(f'native source preparation failed: {exc}', file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0

if __name__ == '__main__':
    sys.exit(main())
