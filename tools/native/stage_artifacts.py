#!/usr/bin/env python3
"""Stage explicitly pinned native artifacts under distinct, local-only coordinates.

This is an artifact gate, not a native source builder or reproducibility attestation.
The caller must run the generated init script with strict dependency verification.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys
import xml.etree.ElementTree as ET
from zipfile import BadZipFile, ZipFile

POM = '{http://maven.apache.org/POM/4.0.0}'
GRADLE = '{https://schema.gradle.org/dependency-verification}'
ET.register_namespace('', 'http://maven.apache.org/POM/4.0.0')
ET.register_namespace('xsi', 'http://www.w3.org/2001/XMLSchema-instance')

class Failure(Exception):
    pass

def require(condition, message):
    if not condition:
        raise Failure(message)

def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for part in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(part)
    return h.hexdigest()

def load_lock(path):
    lock = json.loads(path.read_text())
    require(lock.get('schema') == 1, 'Unsupported native lock')
    require(lock.get('input_group') == 'org.signal' and lock.get('output_group') == 'im.collie.native', 'Unexpected coordinate namespace')
    modules = lock.get('modules', [])
    require({m.get('name') for m in modules} == {'libsignal-android', 'libsignal-client', 'ringrtc-android'} and len(modules) == 3, 'Wrong native module set')
    require(set(lock.get('required_abis', [])) == {'arm64-v8a', 'armeabi-v7a', 'x86', 'x86_64'}, 'Wrong ABI set')
    for commit in lock.get('source_provenance', {}).values():
        require(re.fullmatch(r'[0-9a-f]{40}', commit or '') is not None, 'Invalid source pin')
    require(set(lock.get('source_provenance', {})) == {'libsignal', 'ringrtc', 'webrtc'}, 'Incomplete source pins')
    toolchain = lock.get('toolchain_reference', {})
    require(re.fullmatch(r'[0-9a-f]{64}', toolchain.get('webrtc_deps_sha256', '')) is not None, 'Missing WebRTC DEPS hash')
    patches = lock.get('source_patches', [])
    expected_paths = ['patches/libsignal-0.106.1-attributed.mbox', 'patches/ringrtc-2.74-attributed.mbox', 'patches/webrtc-8037b-molly-proxy-UNTESTED.patch', 'patches/webrtc-unknown-proxy-fix.patch', 'patches/webrtc-socks-ipv6-fix.patch']
    require([item.get('path') for item in patches] == expected_paths, 'Unexpected source patch set or order')
    for item in patches:
        require(re.fullmatch(r'[0-9a-f]{64}', item.get('sha256', '')) is not None, 'Invalid source patch hash')
        require(digest(safe_file(path.parent, path.parent / item['path'])) == item['sha256'], 'Source patch hash mismatch')
    for m in modules:
        for key in ('input_version', 'output_version'):
            require(re.fullmatch(r'[0-9][A-Za-z0-9.-]*', m.get(key, '')) is not None, 'Invalid version')
        require(m['input_version'] != m['output_version'], 'Output version must be distinct')
        require(m['extension'] == ('jar' if m['name'] == 'libsignal-client' else 'aar'), 'Unexpected artifact type')
        for key in ('binary_sha256', 'pom_sha256'):
            require(re.fullmatch(r'[0-9a-f]{64}', m.get(key, '')) is not None, 'Invalid artifact hash')
        expected = {'libsignal-android': ['libsignal_jni.so'], 'libsignal-client': [], 'ringrtc-android': ['libringrtc.so', 'libringrtc_rffi.so']}[m['name']]
        require(m.get('native_libraries') == expected, 'Unexpected library set')
    return lock

def safe_file(root, path):
    root = root.resolve(strict=True)
    require(root.is_dir(), 'Input repository is not a directory')
    relative = path.relative_to(root)
    current = root
    for part in relative.parts:
        current = current / part
        require(not current.is_symlink(), 'Artifact path contains symlink')
    require(path.is_file() and path.resolve().is_relative_to(root), 'Artifact missing or outside repository')
    return path

def input_files(root, lock, module):
    directory = root / Path(lock['input_group'].replace('.', '/')) / module['name'] / module['input_version']
    stem = module['name'] + '-' + module['input_version']
    binary = safe_file(root, directory / (stem + '.' + module['extension']))
    pom = safe_file(root, directory / (stem + '.pom'))
    require(digest(binary) == module['binary_sha256'], 'Native artifact hash mismatch')
    require(digest(pom) == module['pom_sha256'], 'Native POM hash mismatch')
    try:
        with ZipFile(binary) as archive:
            names = archive.namelist()
            require(len(names) == len(set(names)), 'Duplicate archive entries')
            require(archive.testzip() is None, 'Corrupt artifact archive')
            if module['extension'] == 'aar':
                required = {'classes.jar'} | {f'jni/{abi}/{name}' for abi in lock['required_abis'] for name in module['native_libraries']}
                require(required.issubset(names), 'Native AAR is missing a required ABI or class JAR')
    except BadZipFile as exc:
        raise Failure('Invalid native artifact archive') from exc
    rewrite_pom(pom.read_bytes(), lock, module)
    return binary, pom

def rewrite_pom(data, lock, module):
    root = ET.fromstring(data)
    for field, value in (('groupId', lock['input_group']), ('artifactId', module['name']), ('version', module['input_version'])):
        require(root.findtext(POM + field) == value, 'Unexpected input POM coordinate')
    root.find(POM + 'groupId').text = lock['output_group']
    root.find(POM + 'version').text = module['output_version']
    versions = {m['name']: m['output_version'] for m in lock['modules']}
    for dep in root.findall(f'{POM}dependencies/{POM}dependency'):
        group, name = dep.find(POM + 'groupId'), dep.find(POM + 'artifactId')
        if group is not None and name is not None and group.text == lock['input_group']:
            require(name.text in versions and dep.find(POM + 'version') is not None, 'Unexpected upstream native POM dependency')
            group.text = lock['output_group']
            dep.find(POM + 'version').text = versions[name.text]
    return ET.tostring(root, encoding='utf-8', xml_declaration=True)

def render_metadata(original, lock, receipts):
    # Preserve historical POM trust for other groups while requiring our native POM hashes.
    broad_pom = '<trust file=".*[.]pom" regex="true"/>'
    narrow_pom = '<trust group="(?!im[.]collie[.]native$).*" file=".*[.]pom" regex="true"/>'
    require(original.count(broad_pom) <= 1, 'Unexpected duplicate POM trust rules')
    narrowed = original.replace(broad_pom, narrow_pom)
    xml = ET.fromstring(narrowed)
    require(xml.tag == GRADLE + 'verification-metadata', 'Unexpected verification XML')
    config = xml.find(GRADLE + 'configuration')
    require(config is not None and config.findtext(GRADLE + 'verify-metadata') == 'true', 'Metadata verification must be enabled')
    require(original.count('   </components>') == 1, 'Unexpected verification layout')
    for trust in config.findall(f'{GRADLE}trusted-artifacts/{GRADLE}trust'):
        for module in lock['modules']:
            for extension in (module['extension'], 'pom'):
                values = {'group': lock['output_group'], 'name': module['name'], 'version': module['output_version'], 'file': module['name'] + '-' + module['output_version'] + '.' + extension}
                def matches(key, value):
                    pattern = trust.get(key)
                    return pattern is None or (re.fullmatch(pattern, value) is not None if trust.get('regex') == 'true' else pattern == value)
                require(not all(matches(k, v) for k, v in values.items()), 'Trust rule bypasses native artifact verification')
    for entry in xml.findall(f'{GRADLE}components/{GRADLE}component'):
        require(not any((entry.get('group'), entry.get('name'), entry.get('version')) == (lock['output_group'], m['name'], m['output_version']) for m in lock['modules']), 'Native coordinate already exists in verification metadata')
    entries = []
    for m in lock['modules']:
        entries.append(f'      <component group="{lock["output_group"]}" name="{m["name"]}" version="{m["output_version"]}">')
        for name, value in sorted(receipts[m['name']].items()):
            entries.extend([f'         <artifact name="{name}">', f'            <sha256 value="{value}" origin="Pinned local native artifact; not independent reproducibility proof"/>', '         </artifact>'])
        entries.append('      </component>')
    return narrowed.replace('   </components>', '\n'.join(entries) + '\n   </components>')

def render_init(lock, repo):
    # The URI is generated for this local stage only; no machine path is tracked.
    uri = json.dumps(repo.resolve().as_uri())
    mappings = '\n'.join(f"          substitute module('{group}:{m['name']}') using module('{lock['output_group']}:{m['name']}:{m['output_version']}')" for group in ('im.molly', 'org.signal') for m in lock['modules'])
    filters = '\n'.join(f"      includeModule('{lock['output_group']}', '{m['name']}')" for m in lock['modules'])
    return f"""settingsEvaluated {{ settings ->
  settings.dependencyResolutionManagement.repositories.exclusiveContent {{
    forRepository {{ settings.dependencyResolutionManagement.repositories.maven {{ url = uri({uri}) }} }}
    filter {{
{filters}
    }}
  }}
}}
gradle.beforeProject {{ project ->
  project.configurations.configureEach {{
    resolutionStrategy.dependencySubstitution {{
{mappings}
    }}
  }}
}}
"""

def stage(lock_path, input_root, output_root, verification):
    lock_path = lock_path.resolve(strict=True)
    lock = load_lock(lock_path)
    input_root = input_root.resolve(strict=True)
    require(not output_root.exists(), 'Output must not exist')
    verification = verification.resolve(strict=True)
    original = verification.read_text()
    # Reject unsafe metadata and artifacts before creating any output.
    empty_hashes = {m['name']: {} for m in lock['modules']}
    render_metadata(original, lock, empty_hashes)
    inputs = {m['name']: input_files(input_root, lock, m) for m in lock['modules']}
    output_root.mkdir(parents=True)
    repo = output_root / 'maven'
    original_poms = output_root / 'original-poms'
    original_poms.mkdir()
    receipts = {}
    for m in lock['modules']:
        binary, pom = inputs[m['name']]
        outdir = repo / Path(lock['output_group'].replace('.', '/')) / m['name'] / m['output_version']
        outdir.mkdir(parents=True)
        stem = m['name'] + '-' + m['output_version']
        target_binary = outdir / (stem + '.' + m['extension'])
        target_pom = outdir / (stem + '.pom')
        shutil.copyfile(binary, target_binary)
        (original_poms / (m['name'] + '.pom')).write_bytes(pom.read_bytes())
        target_pom.write_bytes(rewrite_pom(pom.read_bytes(), lock, m))
        receipts[m['name']] = {p.name: digest(p) for p in (target_binary, target_pom)}
    (output_root / 'verification-metadata.xml').write_text(render_metadata(original, lock, receipts))
    (output_root / 'native.init.gradle').write_text(render_init(lock, repo))
    receipt = {'schema': 1, 'scope': 'pinned artifact staging only; source build and runtime unverified', 'lock_sha256': digest(lock_path), 'input_verification_sha256': digest(verification), 'artifacts': receipts, 'verification_sha256': digest(output_root / 'verification-metadata.xml'), 'init_sha256': digest(output_root / 'native.init.gradle')}
    (output_root / 'receipt.json').write_text(json.dumps(receipt, indent=2, sort_keys=True) + '\n')
    return receipt

def verify_stage(lock_path, output_root, verification):
    lock_path = lock_path.resolve(strict=True)
    lock = load_lock(lock_path)
    output_root = output_root.resolve(strict=True)
    verification = verification.resolve(strict=True)
    receipt = json.loads(safe_file(output_root, output_root / 'receipt.json').read_text())
    require(receipt.get('schema') == 1 and receipt.get('lock_sha256') == digest(lock_path), 'Native receipt does not match lock')
    require(receipt.get('input_verification_sha256') == digest(verification), 'Base verification metadata changed')
    artifacts = receipt.get('artifacts', {})
    require(set(artifacts) == {m['name'] for m in lock['modules']}, 'Native receipt module set differs')
    expected_hashes = {}
    for m in lock['modules']:
        outdir = output_root / 'maven' / Path(lock['output_group'].replace('.', '/')) / m['name'] / m['output_version']
        stem = m['name'] + '-' + m['output_version']
        binary_name, pom_name = stem + '.' + m['extension'], stem + '.pom'
        require(set(artifacts[m['name']]) == {binary_name, pom_name}, 'Native receipt artifact set differs')
        binary = safe_file(output_root, outdir / binary_name)
        pom = safe_file(output_root, outdir / pom_name)
        original_pom = safe_file(output_root, output_root / 'original-poms' / (m['name'] + '.pom'))
        require(digest(binary) == m['binary_sha256'], 'Staged binary differs from pinned input')
        require(digest(original_pom) == m['pom_sha256'], 'Original native POM differs from pinned input')
        expected_pom = rewrite_pom(original_pom.read_bytes(), lock, m)
        require(pom.read_bytes() == expected_pom, 'Staged native POM differs from the approved transformation')
        expected_hashes[m['name']] = {binary_name: m['binary_sha256'], pom_name: hashlib.sha256(expected_pom).hexdigest()}
        require(artifacts[m['name']] == expected_hashes[m['name']], 'Native receipt artifact hashes differ')
    expected_metadata = render_metadata(verification.read_text(), lock, expected_hashes).encode()
    expected_init = render_init(lock, output_root / 'maven').encode()
    for field, filename, expected in (('verification_sha256', 'verification-metadata.xml', expected_metadata), ('init_sha256', 'native.init.gradle', expected_init)):
        actual = safe_file(output_root, output_root / filename).read_bytes()
        require(actual == expected and receipt.get(field) == hashlib.sha256(expected).hexdigest(), 'Generated native integration differs from approved transformation')
    return receipt

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lock', required=True, type=Path)
    parser.add_argument('--input-maven', type=Path)
    parser.add_argument('--verify-stage', action='store_true')
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--verification', required=True, type=Path)
    args = parser.parse_args()
    try:
        if args.verify_stage:
            receipt = verify_stage(args.lock, args.output, args.verification)
        else:
            require(args.input_maven is not None, '--input-maven is required for staging')
            receipt = stage(args.lock, args.input_maven, args.output, args.verification)
    except (Failure, OSError, ValueError, ET.ParseError, BadZipFile) as exc:
        print(f'native artifact staging failed: {exc}', file=sys.stderr)
        return 1
    print(json.dumps(receipt, indent=2))
    return 0

if __name__ == '__main__':
    sys.exit(main())
