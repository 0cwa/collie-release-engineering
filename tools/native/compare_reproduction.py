#!/usr/bin/env python3
"""Compare rebuilt native archives with the exact locked distributed binaries.

This checks supplied files. It cannot establish where a supplied rebuild came
from, authenticate the builder, or prove runtime behavior. No result authorizes
release, and a ZIP-metadata-only difference is still a failed exact comparison.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import sys
import zlib
from zipfile import BadZipFile, ZipFile

import stage_artifacts

MODULES = ('libsignal-android', 'libsignal-client', 'ringrtc-android')
ELF_ABIS = {'arm64-v8a': (2, 183), 'armeabi-v7a': (1, 40),
            'x86': (1, 3), 'x86_64': (2, 62)}
MAX_ENTRIES = 100_000
MAX_UNCOMPRESSED = 2 * 1024 * 1024 * 1024


class Failure(Exception):
    pass


def require(condition, message):
    if not condition:
        raise Failure(message)


def regular_file(path):
    path = Path(os.path.abspath(path))
    require(not any(parent.is_symlink() for parent in (path, *path.parents)),
            'Input path contains a symlink: ' + str(path))
    require(path.is_file(), 'Input file is missing or not regular: ' + str(path))
    return path


def elf_header(header, abi, name):
    expected_class, expected_machine = ELF_ABIS[abi]
    require(len(header) >= 20 and header[:4] == b'\x7fELF', 'Invalid ELF library: ' + name)
    require(header[4] == expected_class and header[5:7] == b'\x01\x01',
            'ELF class, endianness or version differs from Android ABI: ' + name)
    require(int.from_bytes(header[16:18], 'little') == 3 and
            int.from_bytes(header[18:20], 'little') == expected_machine,
            'ELF type or machine differs from Android ABI: ' + name)


def archive_manifest(path, module, required_abis):
    """Hash every archive payload without extracting or normalizing the input."""
    payloads = {}
    metadata = {}
    native = {}
    with ZipFile(path) as archive:
        entries = archive.infolist()
        require(0 < len(entries) <= MAX_ENTRIES, 'Archive entry count is invalid')
        require(sum(item.file_size for item in entries) <= MAX_UNCOMPRESSED,
                'Archive exceeds the diagnostic uncompressed-size limit')
        names = set()
        for item in entries:
            name = item.filename
            canonical = name[:-1] if item.is_dir() else name
            parts = PurePosixPath(canonical)
            require(bool(canonical) and not parts.is_absolute() and
                    parts.as_posix() == canonical and '\\' not in canonical and
                    all(part not in ('.', '..') for part in parts.parts),
                    'Unsafe archive entry: ' + name)
            require(canonical not in names, 'Duplicate archive entry: ' + name)
            names.add(canonical)
            require(not item.flag_bits & 1, 'Encrypted archive entry: ' + name)
            mode = item.external_attr >> 16
            require(stat.S_IFMT(mode) in (0, stat.S_IFDIR if item.is_dir() else stat.S_IFREG),
                    'Archive entry is not a regular file or directory: ' + name)
            metadata[name] = {'date_time': list(item.date_time), 'compression': item.compress_type,
                              'external_attr': item.external_attr, 'extra_hex': item.extra.hex(),
                              'comment_hex': item.comment.hex()}
            if item.is_dir():
                require(item.file_size == 0, 'Archive directory has content: ' + name)
                continue
            digest = hashlib.sha256()
            length = 0
            header = b''
            with archive.open(item) as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                    if not header:
                        header = chunk[:20]
                    length += len(chunk)
                    require(length <= item.file_size, 'Archive entry size changed: ' + name)
                    digest.update(chunk)
            require(length == item.file_size, 'Archive entry is truncated: ' + name)
            payloads[name] = {'sha256': digest.hexdigest(), 'bytes': length}
            if name.startswith('jni/') and name.endswith('.so'):
                require(len(parts.parts) == 3 and parts.parts[1] in ELF_ABIS,
                        'Unexpected native-library layout or ABI: ' + name)
                elf_header(header, parts.parts[1], name)
                native[name] = payloads[name]
        if module['extension'] == 'aar':
            required = {'classes.jar'} | {f'jni/{abi}/{library}' for abi in required_abis
                                          for library in module['native_libraries']}
            require(required.issubset(payloads),
                    'Native AAR is missing a required ABI, library or classes.jar: ' +
                    ', '.join(sorted(required - payloads.keys())))
        return {'payloads': payloads, 'native_libraries': native,
                'metadata': metadata, 'entry_order': [entry.filename for entry in entries],
                'archive_comment_hex': archive.comment.hex()}


def compare_module(reference, rebuilt, module, required_abis):
    reference = regular_file(reference)
    rebuilt = regular_file(rebuilt)
    require(not os.path.samefile(reference, rebuilt),
            'Reference and rebuilt artifact must be distinct files')
    reference_sha = stage_artifacts.digest(reference)
    require(reference_sha == module['binary_sha256'],
            'Reference artifact differs from the checked-in native lock')
    rebuilt_sha = stage_artifacts.digest(rebuilt)
    before = archive_manifest(reference, module, required_abis)
    after = archive_manifest(rebuilt, module, required_abis)
    old, new = before['payloads'], after['payloads']
    removed = sorted(old.keys() - new.keys())
    added = sorted(new.keys() - old.keys())
    changed = sorted(name for name in old.keys() & new.keys() if old[name] != new[name])
    changed_metadata = sorted(name for name in before['metadata'].keys() & after['metadata'].keys()
                              if before['metadata'][name] != after['metadata'][name])
    exact = reference_sha == rebuilt_sha
    classification = 'EXACT' if exact else ('ZIP_METADATA_ONLY' if not (removed or added or changed) else 'PAYLOAD_DIFFERENCE')
    # Check again to avoid reporting a comparison of files modified during read.
    require(stage_artifacts.digest(reference) == reference_sha and
            stage_artifacts.digest(rebuilt) == rebuilt_sha, 'Artifact changed during comparison')
    return {'status': classification, 'exact_match': exact,
            'reference_sha256': reference_sha, 'rebuilt_sha256': rebuilt_sha,
            'reference_file': reference.name, 'rebuilt_file': rebuilt.name,
            'removed_entries': removed, 'added_entries': added,
            'changed_entries': {name: {'reference': old[name], 'rebuilt': new[name]} for name in changed},
            'changed_metadata_entries': changed_metadata,
            'entry_order_changed': before['entry_order'] != after['entry_order'],
            'archive_comment_changed': before['archive_comment_hex'] != after['archive_comment_hex'],
            'reference_native_libraries': before['native_libraries'],
            'rebuilt_native_libraries': after['native_libraries']}


def compare(lock_path, reference_dir, rebuilt):
    report = {'schema': 1, 'status': 'BLOCKED_INPUT', 'release_allowed': False,
              'independence_verified': False,
              'scope': 'supplied artifact byte comparison only; independent builder, full source-to-binary chain and runtime are not authenticated',
              'modules': {}}
    try:
        lock_path = regular_file(lock_path)
        lock_sha = stage_artifacts.digest(lock_path)
        lock = stage_artifacts.load_lock(lock_path)
        require(set(rebuilt) == set(MODULES), 'All three rebuilt native modules are required')
        report.update(lock_sha256=lock_sha, source_provenance=lock['source_provenance'],
                      required_abis=lock['required_abis'])
        for module in lock['modules']:
            name = module['name']
            reference = reference_dir / (name + '-' + module['output_version'] + '.' + module['extension'])
            try:
                report['modules'][name] = compare_module(reference, rebuilt[name], module, lock['required_abis'])
            except (Failure, OSError, ValueError, BadZipFile, RuntimeError, NotImplementedError, zlib.error) as exc:
                report['modules'][name] = {'status': 'BLOCKED_INPUT', 'exact_match': False, 'error': str(exc)}
        require(stage_artifacts.digest(lock_path) == lock_sha, 'Native lock changed during comparison')
        values = report['modules'].values()
        if any(item['status'] == 'BLOCKED_INPUT' for item in values):
            report['status'] = 'BLOCKED_INPUT'
        elif all(item['exact_match'] for item in values):
            report['status'] = 'EXACT_BYTES_MATCH'
        else:
            report['status'] = 'ARTIFACT_MISMATCH'
    except (Failure, stage_artifacts.Failure, OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        report['error'] = str(exc)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lock', required=True, type=Path)
    parser.add_argument('--reference-dir', required=True, type=Path)
    for name in MODULES:
        parser.add_argument('--' + name, required=True, type=Path)
    parser.add_argument('--report', required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        require(not os.path.lexists(args.report), 'Report already exists; choose a new path')
        report = compare(args.lock, args.reference_dir, {name: getattr(args, name.replace('-', '_')) for name in MODULES})
        args.report.parent.mkdir(parents=True, exist_ok=True)
        with args.report.open('x') as output:
            json.dump(report, output, indent=2, sort_keys=True)
            output.write('\n')
        print(report['status'])
        return 0 if report['status'] == 'EXACT_BYTES_MATCH' else 1
    except (Failure, OSError) as exc:
        print('native artifact comparison failed: ' + str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
