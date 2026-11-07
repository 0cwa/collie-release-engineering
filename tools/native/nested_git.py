"""Check the retained WebRTC snapshot and the upstream Java hook output.

This is not an attestation of DEPS, untracked files, CIPD or toolchain contents.
"""
from __future__ import annotations
import hashlib
import json
import os
import stat
from pathlib import Path, PurePosixPath
import re
import subprocess

HOOK_SHA256 = '4e77e437b318e5a78dba04ac5591dc3c77d35b78899b65211d01727b985ab85b'

class Failure(Exception):
    pass

def require(value, message):
    if not value:
        raise Failure(message)

def git(root, *args):
    env = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
    env['GIT_ATTR_NOSYSTEM'] = '1'
    p = subprocess.run(['git', '--no-replace-objects', '-c', 'core.fsmonitor=false', '-c', 'core.attributesFile=/dev/null', '-C', str(root), *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    require(p.returncode == 0, 'Git dependency check failed: ' + str(root))
    return p.stdout

def verify(workspace, manifest, expected_digest):
    workspace = workspace.resolve(strict=True)
    require(manifest.is_file() and not manifest.is_symlink(), 'Git dependency manifest must be a regular file')
    data = manifest.read_bytes()
    require(hashlib.sha256(data).hexdigest() == expected_digest, 'Git dependency manifest hash differs')
    rows = json.loads(data)['repositories']
    require(isinstance(rows, list) and rows, 'Empty Git dependency manifest')
    hook = workspace / 'src/ringrtc/patch_java_release.py'
    require(hook.is_file() and hook.resolve() == hook and hashlib.sha256(hook.read_bytes()).hexdigest() == HOOK_SHA256, 'Upstream Java hook differs')
    seen = set()
    for item in rows:
        require(set(item) == {'path', 'commit'}, 'Unexpected manifest entry')
        name, commit = item['path'], item['commit']
        p = PurePosixPath(name)
        require(not p.is_absolute() and name == p.as_posix() and len(p.parts) > 1 and p.parts[0] == 'src' and all(x not in ('.', '..') for x in p.parts), 'Invalid dependency path')
        require(name not in seen and re.fullmatch('[0-9a-f]{40}', commit), 'Duplicate path or invalid dependency commit')
        seen.add(name)
        repo = (workspace / name).resolve(strict=True)
        require(repo == workspace.joinpath(*p.parts) and repo.is_relative_to(workspace), 'Git dependency path contains symlink or escapes workspace')
        require(Path(git(repo, 'rev-parse', '--show-toplevel').decode().strip()).resolve() == repo, 'Git dependency is not its own checkout')
        require(git(repo, 'rev-parse', 'HEAD').decode().strip() == commit, 'Git dependency HEAD differs: ' + name)
        info_attrs = Path(os.fsdecode(git(repo, 'rev-parse', '--path-format=absolute', '--git-path', 'info/attributes').strip()))
        require(info_attrs.is_absolute(), 'Git attributes path must be absolute: ' + name)
        require(not os.path.lexists(info_attrs) or (info_attrs.is_file() and not info_attrs.is_symlink() and info_attrs.stat().st_size == 0), 'Untracked local Git attributes are not allowed: ' + name)
        git(repo, 'diff-index', '--cached', '--quiet', 'HEAD', '--')
        for entry in git(repo, 'ls-files', '--stage', '-z').split(b'\0'):
            if not entry:
                continue
            meta, raw_name = entry.split(b'\t', 1)
            mode, oid, stage = meta.split(b' ')
            require(stage == b'0', 'Unmerged Git dependency index: ' + name)
            relative = os.fsdecode(raw_name)
            path = repo / relative
            require(path.parent.resolve() == path.parent and path.parent.is_relative_to(repo), 'Tracked dependency file has a symlinked parent or escapes checkout: ' + name)
            if mode == b'160000':
                # The recorded snapshot populates none of these Git submodules.
                # Reject content rather than silently trusting an unverified checkout.
                if os.path.lexists(path):
                    require(path.is_dir() and not path.is_symlink() and not any(path.iterdir()), 'Unverified populated Gitlink: ' + name + '/' + relative)
                continue
            st = path.lstat()
            if name == 'src/build' and relative in ('android/gyp/compile_java.py', 'android/gyp/turbine.py'):
                require(mode in (b'100644', b'100755') and stat.S_ISREG(st.st_mode) and bool(st.st_mode & stat.S_IXUSR) == (mode == b'100755'), 'Unexpected Java hook output file mode')
                continue  # The exact hook output is checked below.
            if mode == b'120000':
                require(stat.S_ISLNK(st.st_mode), 'Expected Git dependency symlink: ' + name)
                contents = os.fsencode(os.readlink(path))
                digest = hashlib.sha1(b'blob ' + str(len(contents)).encode() + b'\0' + contents).hexdigest().encode()
            else:
                require(mode in (b'100644', b'100755') and stat.S_ISREG(st.st_mode), 'Unexpected Git dependency file type: ' + name)
                require(bool(st.st_mode & stat.S_IXUSR) == (mode == b'100755'), 'Git dependency mode differs: ' + name)
                h = hashlib.sha1(b'blob ' + str(st.st_size).encode() + b'\0')
                with path.open('rb') as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                        h.update(chunk)
                digest = h.hexdigest().encode()
            if digest != oid and mode in (b'100644', b'100755') and st.st_size <= 8 * 1024 * 1024:
                # Git's pinned checkout attributes can require CRLF even though the blob is LF.
                attrs = git(repo, 'check-attr', '--cached', '-z', 'text', 'eol', '--', relative).split(b'\0')
                if attrs in ([raw_name, b'text', b'set', raw_name, b'eol', b'crlf', b''], [raw_name, b'text', b'unspecified', raw_name, b'eol', b'crlf', b'']):
                    contents = path.read_bytes()
                    normalized = contents.replace(b'\r\n', b'\n')
                    require(normalized.count(b'\n') == contents.count(b'\r\n'), 'Mixed line endings in Git dependency: ' + name + '/' + relative)
                    digest = hashlib.sha1(b'blob ' + str(len(normalized)).encode() + b'\0' + normalized).hexdigest().encode()
            require(digest == oid, 'Git dependency file bytes differ: ' + name + '/' + relative)
        index = [entry for entry in git(repo, 'ls-files', '-v', '-z').split(b'\0') if entry]
        require(all(entry.startswith(b'H ') for entry in index), 'Git dependency has hidden or unmerged tracked files: ' + name)
        if name != 'src/build':
            continue
        patterns = {'compile_java.py': r"('--release',(?:\s*#[^\n]*\n)*\s*')\d+'", 'turbine.py': r"(\['--release', ')\d+'"}
        for filename, pattern in patterns.items():
            rel = 'android/gyp/' + filename
            original = git(repo, 'show', 'HEAD:' + rel).decode('utf-8')
            transformed, count = re.subn(pattern, lambda m: m.group(1) + "21'", original)
            require(count == 1 and (repo / rel).read_bytes() == transformed.encode('utf-8'), 'Unexpected upstream Java hook output: ' + rel)
    require('src/build' in seen, 'Missing build dependency')
    return len(rows)
