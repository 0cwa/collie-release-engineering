"""Descriptor-anchored directory and exclusive-file operations for release evidence."""
import os
from pathlib import Path
import re
import secrets
import stat

_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_FILE_NAME = re.compile(r'[A-Za-z0-9_.-]+\Z')


def _directory_mode(info, *, private):
    if not stat.S_ISDIR(info.st_mode):
        raise ValueError('release path component must be a real directory')
    if private:
        if info.st_uid != os.getuid() or info.st_mode & 0o022:
            raise ValueError('release output directory must be user-owned and not group/other writable')
    else:
        if info.st_uid not in (0, os.getuid()):
            raise ValueError('release path ancestor must be owned by root or the current user')
        if info.st_mode & 0o022 and not (info.st_uid == 0 and info.st_mode & stat.S_ISVTX):
            raise ValueError('writable release path ancestor must be root-owned and sticky')


def _absolute(path):
    value = Path(path)
    if not value.is_absolute():
        value = Path.cwd() / value
    if '..' in value.parts:
        raise ValueError('release directory path cannot contain parent traversal')
    return Path(os.path.normpath(value))


def open_trusted_directory(path, *, forbidden_tree=None):
    """Open an existing path without following any component symlink.

    Ancestors must be root/current-user owned. The only writable ancestor
    exception is a root-owned sticky directory such as /tmp. The final output
    directory must be current-user owned and not group/other writable.
    """
    absolute = _absolute(path)
    if forbidden_tree is not None:
        forbidden = Path(os.path.realpath(forbidden_tree))
        if absolute == forbidden or forbidden in absolute.parents:
            raise ValueError('release output directory must be outside the source checkout')
    descriptor = os.open('/', _DIRECTORY_FLAGS)
    try:
        parts = absolute.parts[1:]
        if not parts:
            _directory_mode(os.fstat(descriptor), private=True)
        for index, part in enumerate(parts):
            child = os.open(part, _DIRECTORY_FLAGS, dir_fd=descriptor)
            try:
                _directory_mode(os.fstat(child), private=index == len(parts) - 1)
            except BaseException:
                os.close(child)
                raise
            os.close(descriptor)
            descriptor = child
        return descriptor, absolute
    except BaseException:
        os.close(descriptor)
        raise


def open_private_directory_at(parent_fd, name, *, create=False):
    """Open one safe, user-owned directory anchored to an already-open parent."""
    if not isinstance(name, str) or not _FILE_NAME.fullmatch(name) or name in ('.', '..'):
        raise ValueError('invalid release directory component')
    if create:
        try:
            os.mkdir(name, 0o700, dir_fd=parent_fd)
        except FileExistsError:
            pass
    child = os.open(name, _DIRECTORY_FLAGS, dir_fd=parent_fd)
    try:
        _directory_mode(os.fstat(child), private=True)
    except BaseException:
        os.close(child)
        raise
    return child


def publish_exclusive_at(directory_fd, name, data):
    """Atomically publish exact bytes under an open directory, refusing replacement."""
    if not isinstance(name, str) or not _FILE_NAME.fullmatch(name) or name in ('.', '..'):
        raise ValueError('invalid release output filename')
    temporary = None
    try:
        for _ in range(8):
            candidate = '.release-' + secrets.token_hex(12) + '.tmp'
            try:
                descriptor = os.open(candidate, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                                     os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=directory_fd)
                temporary = candidate
                break
            except FileExistsError:
                continue
        else:
            raise ValueError('could not allocate private release output file')
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, 'wb') as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.link(temporary, name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd,
                follow_symlinks=False)
        os.unlink(temporary, dir_fd=directory_fd)
        temporary = None
        os.fsync(directory_fd)
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary, dir_fd=directory_fd)
            except FileNotFoundError:
                pass
