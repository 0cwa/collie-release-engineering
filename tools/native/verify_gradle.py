"""Compare an extracted Gradle distribution with a pinned ZIP; no release attestation."""
from __future__ import annotations
import hashlib
import os
from pathlib import Path, PurePosixPath
import stat
from zipfile import ZipFile, BadZipFile

class Failure(Exception): pass

def require(value, message):
    if not value: raise Failure(message)

def digest(stream):
    h = hashlib.sha256()
    for block in iter(lambda: stream.read(1024 * 1024), b''):
        h.update(block)
    return h.hexdigest()

def verify(archive, directory, expected_sha256, top='gradle-9.3.1'):
    archive, directory = Path(archive), Path(directory)
    require(archive.is_file() and not archive.is_symlink(), 'Gradle ZIP must be a regular file')
    require(directory.is_dir() and not directory.is_symlink(), 'Gradle distribution must be a real directory')
    require(not directory.stat().st_mode & 0o022, 'Gradle distribution root is group or world writable')
    expected = {}
    try:
        fd = os.open(archive, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        with os.fdopen(fd, 'rb') as f:
            require(stat.S_ISREG(os.fstat(f.fileno()).st_mode), 'Gradle ZIP must be a regular file')
            require(digest(f) == expected_sha256, 'Gradle distribution ZIP checksum differs')
            f.seek(0)
            with ZipFile(f) as z:
                for item in z.infolist():
                    name = item.filename
                    require('\\' not in name and '\x00' not in name and not any(part in ('.', '..') for part in name.split('/')), 'Unsafe Gradle ZIP entry')
                    path = PurePosixPath(name)
                    require(not path.is_absolute() and all(p not in ('.', '..') for p in path.parts) and path.parts and path.parts[0] == top, 'Unsafe Gradle ZIP path')
                    relative = path.relative_to(top)
                    require(str(relative) != '.' or item.is_dir(), 'Invalid Gradle ZIP root')
                    if str(relative) == '.': continue
                    key = str(relative)
                    require(key not in expected, 'Duplicate Gradle ZIP entry')
                    mode = item.external_attr >> 16
                    require(stat.S_IFMT(mode) in (0, stat.S_IFREG, stat.S_IFDIR), 'Unsupported Gradle ZIP entry type')
                    expected[key] = (item, bool(mode & 0o111))
                allowed_dirs = {str(PurePosixPath(p).parent) for p in expected}
                allowed_dirs.update(p for p, (i, _) in expected.items() if i.is_dir())
                allowed_dirs.update(str(parent) for p in tuple(allowed_dirs) for parent in PurePosixPath(p).parents)
                def walk_error(exc):
                    raise Failure('Cannot inspect extracted Gradle directory') from exc
                seen = set()
                for base, dirs, files in os.walk(directory, followlinks=False, onerror=walk_error):
                    for name in dirs + files:
                        p = Path(base) / name
                        require(not p.is_symlink(), 'Symlink in extracted Gradle distribution')
                        require(not p.stat().st_mode & 0o022, 'Extracted Gradle path is group or world writable')
                        rel = p.relative_to(directory).as_posix()
                        if p.is_dir():
                            require(rel in allowed_dirs, 'Unexpected extracted Gradle directory')
                            continue
                        require(p.is_file() and rel in expected and not expected[rel][0].is_dir(), 'Unexpected extracted Gradle file')
                        info, executable = expected[rel]
                        actual_exec = bool(p.stat().st_mode & 0o111)
                        # Gradle's Unix wrapper does not preserve the Windows batch file's ZIP execute bit.
                        require(actual_exec == executable or (rel == 'bin/gradle.bat' and executable and not actual_exec), 'Extracted Gradle executable mode differs: ' + rel)
                        with z.open(info) as original, p.open('rb') as actual:
                            require(digest(original) == digest(actual), 'Extracted Gradle file differs from pinned ZIP')
                        seen.add(rel)
                require(seen == {p for p, (i, _) in expected.items() if not i.is_dir()}, 'Extracted Gradle file missing')
    except BadZipFile as exc:
        raise Failure('Invalid Gradle ZIP') from exc
    binary = directory / 'bin/gradle'
    require(binary.is_file() and not binary.is_symlink() and os.access(binary, os.X_OK), 'Verified Gradle executable missing')
    return binary.resolve()
