#!/usr/bin/env python3
"""Build and check the libsignal portion of a prepared native source set.

RingRTC/WebRTC dependency closure is not yet verified or built here. This tool can
only emit a partial diagnostic receipt and cannot authorize a release.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

import native_driver
import ndk_payloads
import prepare_sources
import rust_payloads
import stage_artifacts
import verify_gradle

class Failure(Exception):
    pass

def require(condition, message):
    if not condition:
        raise Failure(message)

def run(args, cwd, env):
    print('+ ' + ' '.join(args), flush=True)
    process = subprocess.Popen(args, cwd=cwd, env=env, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True, errors='replace')
    output = []
    assert process.stdout is not None
    for line in process.stdout:
        print(line, end='', flush=True)
        stripped = line.strip()
        if 'test result:' in stripped or stripped.startswith(('rustc ', 'openjdk version ', 'java version ')) or stripped.endswith('-android') or stripped.endswith('-androideabi'):
            output.append(stripped)
    require(process.wait() == 0, 'Native diagnostic command failed')
    return output

def properties(path):
    require(path.is_file() and not path.is_symlink(), 'Required toolchain properties missing')
    return {key.strip(): value.strip() for key, value in (line.split('=', 1) for line in path.read_text().splitlines() if '=' in line and not line.lstrip().startswith('#'))}

def preflight(lock_path, source_root, sdk_root, java_home, rust_bin, runner=run, java_major=21):
    native_driver.audit_gradle_environment()
    lock = stage_artifacts.load_lock(lock_path.resolve(strict=True))
    proof = prepare_sources.verify_prepared(lock_path, source_root)
    toolchain = lock['toolchain_reference']
    require(toolchain.get('rust_version') == '1.98.1', 'Unexpected Rust version reference')
    require(toolchain.get('ringrtc_aar_android_ndk_revision') == '28.0.13004108', 'Unexpected Android NDK reference')
    sdk_root = sdk_root.resolve(strict=True)
    java_home = java_home.resolve(strict=True)
    ndk = properties(sdk_root / 'ndk' / '28.0.13004108' / 'source.properties')
    require(ndk.get('Pkg.Revision', '').strip() == '28.0.13004108', 'Android NDK version does not match the recorded build')
    ndk_payloads.verify(sdk_root / 'ndk/28.0.13004108', 'cc399724320a6e46fe83a068364630a9943760d7a83fd90570d660ac58786d9d', 8449)
    rust_bin = rust_bin.resolve(strict=True)
    require(rust_bin.name == 'bin', 'Pass the direct Rust toolchain bin directory, not rustup shims')
    rust_reference = Path(__file__).with_name('rust-toolchain-payloads.json')
    rust_payloads.verify(rust_bin.parent, rust_reference, 'e9a644fc90306fd0eaa5d7b7323c3baea2865f460179d0b98cdbb07a2ba63cd0')
    cargo, rustc, rustup = (rust_bin / name for name in ('cargo', 'rustc', 'rustup'))
    java = java_home / 'bin/java'
    require(all(path.is_file() and os.access(path, os.X_OK) for path in (cargo, rustc, java)), 'Required Rust or Java toolchain missing')
    env = os.environ.copy()
    temp = source_root.parent / '.native-tmp'
    temp.mkdir(exist_ok=True)
    require(temp.is_dir() and not temp.is_symlink(), 'Native temporary directory must be a real directory')
    env['TMPDIR'] = str(temp.resolve())
    env['RUSTUP_TOOLCHAIN'] = '1.98.1'
    # Keep existing SDK/Rust cache selections but ensure Gradle receives explicit tool locations.
    env.update(PATH=str(rust_bin) + os.pathsep + env.get('PATH', ''),
               ANDROID_HOME=str(sdk_root), ANDROID_SDK_ROOT=str(sdk_root), JAVA_HOME=str(java_home),
               ANDROID_NDK_HOME=str(sdk_root / 'ndk/28.0.13004108'),
               ANDROID_NDK_ROOT=str(sdk_root / 'ndk/28.0.13004108'))
    libsignal = source_root / 'libsignal'
    java_versions = runner([str(java), '-version'], libsignal, env)
    require(any(re.match(r'^(?:openjdk|java) version "' + str(java_major) + r'(?:[."+]|$)', line) for line in java_versions), f'Java {java_major} is required for this diagnostic build')
    versions = runner([str(rustc), '--version'], libsignal, env)
    require(any(re.match(r'^rustc 1[.]98[.]1(?:\s|$)', line) for line in versions), 'Rust toolchain version differs')
    required_targets = {'aarch64-linux-android', 'armv7-linux-androideabi', 'i686-linux-android', 'x86_64-linux-android'}
    if rustup.is_file() and os.access(rustup, os.X_OK):
        targets = set(runner([str(rustup), 'target', 'list', '--installed'], libsignal, env))
        require(required_targets.issubset(targets), 'One or more Android Rust targets are missing')
    else:
        # A direct installed toolchain has no rustup shim; validate its standard-library
        # files locally. This is a preflight check, not toolchain authentication.
        rustlib = rust_bin.parent / 'lib/rustlib'
        require(all(any(p.is_file() and p.stat().st_size > 0 for p in (rustlib / target / 'lib').glob('libstd-*.rlib')) for target in required_targets),
                'One or more Android Rust target libraries are missing')
    return lock, proof, env, str(cargo)

def diagnostic_build(lock_path, source_root, sdk_root, java_home, rust_bin, report, gradle_zip, gradle_distribution, runner=run, fresh=False):
    require(not report.exists(), 'Native diagnostic report already exists')
    gradle = verify_gradle.verify(gradle_zip, gradle_distribution, '87a2216cc1f9122192d4e0fe905ffdf1b4c72cff797e9f733b174e157cadd396', top='gradle-9.6.0')
    lock_sha = stage_artifacts.digest(lock_path)
    lock, proof, env, cargo = preflight(lock_path, source_root, sdk_root, java_home, rust_bin, runner)
    require(stage_artifacts.digest(lock_path) == lock_sha, 'Native lock changed during preflight')
    libsignal = source_root / 'libsignal'
    if fresh:
        require(not env.get('CARGO_TARGET_DIR') and not env.get('CARGO_BUILD_TARGET_DIR'), 'Fresh diagnostic requires the default Cargo target directory')
        for relative in ('target', 'java/.gradle', 'java/build', 'java/android/build', 'java/client/build', 'java/server/build', 'java/android/src/main/jniLibs', 'java/android/src/main/assets'):
            require(not os.path.lexists(libsignal / relative), 'Fresh diagnostic output already exists: ' + relative)
        require(not env.get('RUSTC_WRAPPER') and not env.get('RUSTC_WORKSPACE_WRAPPER'), 'Fresh diagnostic does not permit inherited Rust compiler wrappers')
        env.update(CARGO_NET_OFFLINE='true', CARGO_INCREMENTAL='0', CARGO_TARGET_DIR=str((libsignal / 'target').resolve()),
                   RUSTC=str((rust_bin / 'rustc').resolve()), RUSTC_WRAPPER='', RUSTC_WORKSPACE_WRAPPER='')
    cargo_args = [cargo, 'test', '--locked', '-p', 'libsignal-net', 'configured_proxy_never_falls_back_to_direct']
    result = runner(cargo_args, libsignal, env)
    require(any(re.search(r'test result: ok[.]\s+4 passed;', line) for line in result), 'Expected four proxy-mode tests did not pass')
    gradle_args = [str(gradle), '--project-dir', str(libsignal / 'java'), '--no-daemon']
    if fresh:
        gradle_args += ['--offline', '--no-build-cache', '--no-configuration-cache', '--rerun-tasks']
    gradle_args += [':android:assembleRelease', '-x', ':makeJniLibrariesDesktop']
    runner(gradle_args, libsignal / 'java', env)
    require(verify_gradle.verify(gradle_zip, gradle_distribution, '87a2216cc1f9122192d4e0fe905ffdf1b4c72cff797e9f733b174e157cadd396', top='gradle-9.6.0') == gradle, 'Gradle distribution changed during diagnostic build')
    # Source contents must remain the pinned prepared trees after the build.
    rust_payloads.verify(rust_bin.resolve(strict=True).parent, Path(__file__).with_name('rust-toolchain-payloads.json'), 'e9a644fc90306fd0eaa5d7b7323c3baea2865f460179d0b98cdbb07a2ba63cd0')
    ndk_payloads.verify(sdk_root.resolve(strict=True) / 'ndk/28.0.13004108', 'cc399724320a6e46fe83a068364630a9943760d7a83fd90570d660ac58786d9d', 8449)
    after = prepare_sources.verify_prepared(lock_path, source_root)
    require(after['sources'] == proof['sources'], 'Native source changed during diagnostic build')
    aar = sorted((libsignal / 'java/android/build/outputs/aar').glob('*.aar'))
    jars = sorted(p for p in (libsignal / 'java/client/build/libs').glob('*.jar') if not p.name.endswith(('-sources.jar', '-javadoc.jar')))
    require(len(aar) == 1 and len(jars) == 1, 'Cannot identify exactly one built libsignal AAR and client JAR')
    modules = {m['name']: m for m in lock['modules']}
    hashes = {'libsignal-android': stage_artifacts.digest(aar[0]), 'libsignal-client': stage_artifacts.digest(jars[0])}
    require(stage_artifacts.digest(lock_path) == lock_sha, 'Native lock changed during diagnostic build')
    expected = {name: modules[name]['binary_sha256'] for name in hashes}
    build_facts = {'fresh_output_check': fresh, 'gradle_distribution_sha256': stage_artifacts.digest(gradle_zip),
                   'rustc_sha256': stage_artifacts.digest(rust_bin / 'rustc'),
                   'java_sha256': stage_artifacts.digest(java_home / 'bin/java'),
                   'ndk_reference_sha256': 'cc399724320a6e46fe83a068364630a9943760d7a83fd90570d660ac58786d9d',
                   'rust_reference_sha256': 'e9a644fc90306fd0eaa5d7b7323c3baea2865f460179d0b98cdbb07a2ba63cd0',
                   'commands': [cargo_args, gradle_args],
                   'selected_environment': {key: env.get(key) for key in ('CXX', 'BORING_BSSL_RUST_CPPLIB', 'LIBRARY_PATH', 'LD_LIBRARY_PATH', 'CARGO_NET_OFFLINE', 'CARGO_INCREMENTAL', 'CARGO_TARGET_DIR', 'RUSTC', 'RUSTC_WRAPPER', 'RUSTC_WORKSPACE_WRAPPER')}}
    if hashes != expected:
        # This is a failure record, never a staging or release authorization.
        failure = {'schema': 1, 'status': 'FAILED_ARTIFACT_MISMATCH', 'release_allowed': False,
                   'scope': 'failed diagnostic; artifacts are not approved for staging or release',
                   'lock_sha256': lock_sha, 'sources': proof['sources'],
                   'artifacts': hashes, 'expected_artifacts': expected, **build_facts}
        report.parent.mkdir(parents=True, exist_ok=True)
        with report.open('x') as output:
            json.dump(failure, output, indent=2, sort_keys=True)
            output.write('\n')
        raise Failure('Local libsignal artifacts differ from locked candidate bytes; reproducibility is unproved')
    receipt = {'schema': 1, 'status': 'PARTIAL_LIBSIGNAL_ONLY', 'release_allowed': False,
               'scope': 'libsignal diagnostic build only; RingRTC, WebRTC closure and runtime unverified',
               'lock_sha256': stage_artifacts.digest(lock_path), 'sources': proof['sources'], 'artifacts': hashes,
               **build_facts}
    report.parent.mkdir(parents=True, exist_ok=True)
    with report.open('x') as output:
        json.dump(receipt, output, indent=2, sort_keys=True)
        output.write('\n')
    return receipt

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lock', required=True, type=Path)
    parser.add_argument('--sources', required=True, type=Path)
    parser.add_argument('--sdk', required=True, type=Path)
    parser.add_argument('--java-home', required=True, type=Path)
    parser.add_argument('--rust-bin', required=True, type=Path)
    parser.add_argument('--report', required=True, type=Path)
    parser.add_argument('--gradle-zip', required=True, type=Path)
    parser.add_argument('--gradle-distribution', required=True, type=Path)
    parser.add_argument('--fresh', action='store_true', help='require absent build outputs and rebuild without Gradle caches')
    args = parser.parse_args()
    try:
        diagnostic_build(args.lock.resolve(), args.sources.resolve(), args.sdk, args.java_home, args.rust_bin, args.report, args.gradle_zip, args.gradle_distribution, fresh=args.fresh)
    except (Failure, prepare_sources.Failure, stage_artifacts.Failure, verify_gradle.Failure, rust_payloads.Failure, ndk_payloads.Failure, OSError, ValueError) as exc:
        print(f'native diagnostic build failed: {exc}', file=sys.stderr)
        return 1
    return 0

if __name__ == '__main__':
    sys.exit(main())
