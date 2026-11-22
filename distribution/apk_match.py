"""Check that a published signed APK is an exact CI build plus its signature.

The unsigned CI APK is aligned the way apksigner aligns it, the published APK's
signing records are copied onto it, and the result must equal the published
file byte for byte. This is the method of the app's
tooling/release/verify-apk-match.sh, with the two upstream helper files pinned
by commit and SHA-256 instead of fetched unchecked.
"""
import argparse
import hashlib
import importlib.util
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
from zipfile import ZipFile

import release_inputs

TOOLS = {
    'zipalign.py': ('https://raw.githubusercontent.com/obfusk/reproducible-apk-tools/'
                    '9401d827a2404827ec3de6cb90a879d9fbd3f60d/zipalign.py',
                    7575, 'a6ab434128cf796d8eb9c1b9e143249e56b35ddca5f5302e61e43d592ab549c9'),
    'apksigcopier.py': ('https://raw.githubusercontent.com/obfusk/apksigcopier/'
                        'bc08f220f8c4b5e429b99f3263a56c68f1cda4e9/apksigcopier/__init__.py',
                        43624, '97f094dbad90fa06da4659bc8b29b193d1ae8c1affad0fb54fccc7fbd705b881'),
}


def fetch_tools(directory, *, downloader=release_inputs.download):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    for name, (url, size, digest) in TOOLS.items():
        downloader(url, directory / name, size, digest, hosts={'raw.githubusercontent.com'})
    return directory


def _verified_tool(directory, name):
    path = Path(directory) / name
    _url, size, digest = TOOLS[name]
    data = path.read_bytes()
    if len(data) != size or hashlib.sha256(data).hexdigest() != digest:
        raise ValueError('pinned APK comparison helper differs: ' + name)
    return path


def _apksigcopier(directory):
    spec = importlib.util.spec_from_file_location('collie_pinned_apksigcopier',
                                                  _verified_tool(directory, 'apksigcopier.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _signing_directory(directory, entries, is_meta):
    """Return raw central-directory records for the trailing v1 signature entries."""
    start = offset = 0
    for entry in entries:
        if directory[offset:offset + 4] != b'PK\x01\x02':
            raise ValueError('invalid central-directory record')
        name, extra, comment = struct.unpack_from('<HHH', directory, offset + 28)
        offset += 46 + name + extra + comment
        if not is_meta(entry.filename):
            start = offset
    if offset != len(directory):
        raise ValueError('unexpected central-directory layout')
    return directory[start:]


def _copy_signing_entries(release, rebuilt, signatures, block_start):
    for entry in sorted(signatures, key=lambda item: item.header_offset):
        if release.tell() != entry.header_offset:
            raise ValueError('unexpected data between signing entries')
        header = release.read(30)
        if header[:4] != b'PK\x03\x04':
            raise ValueError('invalid signing entry header')
        name, extra = struct.unpack_from('<HH', header, 26)
        rebuilt.write(header)
        rebuilt.write(release.read(name + extra + entry.compress_size))
        if entry.flag_bits & 8:
            descriptor = release.read(12)
            if descriptor[:4] == b'PK\x07\x08':
                descriptor += release.read(4)
            rebuilt.write(descriptor)
    padding = block_start - release.tell()
    if not 0 <= padding < 4096 or release.read(padding) != bytes(padding):
        raise ValueError('expected zero padding before the signing block')
    rebuilt.write(bytes(padding))


def reconstruct(published, aligned, output, sigcopier):
    """Port of the app's tooling/release/copy-apk-signatures.py."""
    signed = sigcopier.zip_data(str(published))
    unsigned = sigcopier.zip_data(str(aligned))
    signed_directory = signed.cd_and_eocd[:signed.eocd_offset - signed.cd_offset]
    unsigned_directory_size = unsigned.eocd_offset - unsigned.cd_offset
    block_start, signing_block = sigcopier.extract_v2_sig(str(published))
    with ZipFile(published) as apk:
        entries = apk.infolist()
    signatures = [entry for entry in entries if sigcopier.is_meta(entry.filename)]
    if signatures and (entries[-len(signatures):] != signatures or
                       min(entry.header_offset for entry in signatures) != unsigned.cd_offset or
                       any(entry.header_offset >= unsigned.cd_offset
                           for entry in entries[:-len(signatures)])):
        raise ValueError('v1 signing records must follow all unsigned entries')
    signature_directory = _signing_directory(signed_directory, entries, sigcopier.is_meta)
    with open(aligned, 'rb') as source, open(published, 'rb') as release, open(output, 'xb') as rebuilt:
        remaining = unsigned.cd_offset
        while remaining:
            chunk = source.read(min(remaining, 1 << 20))
            if not chunk:
                raise ValueError('truncated unsigned APK')
            rebuilt.write(chunk)
            remaining -= len(chunk)
        release.seek(unsigned.cd_offset)
        _copy_signing_entries(release, rebuilt, signatures, block_start)
        rebuilt.write(signing_block)
        directory_offset = rebuilt.tell()
        rebuilt.write(unsigned.cd_and_eocd[:unsigned_directory_size])
        rebuilt.write(signature_directory)
        end = bytearray(unsigned.cd_and_eocd[unsigned_directory_size:])
        disk_entries, total_entries = struct.unpack_from('<HH', end, 8)
        struct.pack_into('<HHII', end, 8, disk_entries + len(signatures), total_entries + len(signatures),
                         rebuilt.tell() - directory_offset, directory_offset)
        rebuilt.write(end)


def sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as source:
        for block in iter(lambda: source.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def same_build(unsigned, published, tools):
    """Return the published SHA-256 if it is the unsigned build plus its signature."""
    zipalign = _verified_tool(tools, 'zipalign.py')
    sigcopier = _apksigcopier(tools)
    with tempfile.TemporaryDirectory(prefix='collie-apk-match-') as temporary:
        aligned = Path(temporary) / 'aligned.apk'
        rebuilt = Path(temporary) / 'reconstructed.apk'
        subprocess.run([sys.executable, '-I', str(zipalign), '--page-size', '16', '--pad-like-apksigner',
                        '--replace', '4', str(unsigned), str(aligned)], check=True)
        reconstruct(published, aligned, rebuilt, sigcopier)
        expected = sha256(published)
        if sha256(rebuilt) != expected:
            raise ValueError('published APK is not the CI build plus its signature')
        return expected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('unsigned', type=Path)
    parser.add_argument('published', type=Path)
    parser.add_argument('--tools', type=Path, required=True,
                        help='new directory for the pinned helpers, or one fetched earlier')
    args = parser.parse_args()
    if not args.tools.exists():
        fetch_tools(args.tools)
    print(same_build(args.unsigned, args.published, args.tools))


if __name__ == '__main__':
    main()
