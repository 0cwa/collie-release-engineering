#!/usr/bin/env python3
"""Run a diagnostic RingRTC/WebRTC Android build; this does not authorize release."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import sys

import build_sources
import cipd_payloads
import nested_git
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

def fresh_environment(sources, checkout, rust_bin, env):
    """Reject previous compiler outputs; do not delete caller-owned build state.

    This deliberately does not authenticate dependency caches or claim a hermetic
    build. The resulting diagnostic receipt remains insufficient for release.
    """
    ringrtc = sources / 'ringrtc'
    for root, names in ((checkout, ('out', 'build.ninja', '.ninja_log', '.ninja_deps',
                                  'build/config/siso/.sisorc', 'build/siso/.sisorc')),
                        (ringrtc, ('out', 'build', '.gradle', 'target', 'src/rust/target'))):
        for relative in names:
            require(not os.path.lexists(root / relative), 'Fresh diagnostic output already exists: ' + str(root / relative))
    native_driver.audit_fresh_environment(ringrtc, env)
    result = dict(env)
    result.update(CARGO_NET_OFFLINE='true', CARGO_INCREMENTAL='0',
                  RUSTC=str((rust_bin / 'rustc').resolve(strict=True)),
                  RUSTC_WRAPPER='', RUSTC_WORKSPACE_WRAPPER='',
                  CCACHE_DISABLE='1', SCCACHE_DISABLE='1',
                  PYTHONNOUSERSITE='1', COLLIE_NATIVE_FRESH='1')
    return result


def diagnostic_build(lock_path, sources, workspace, sdk, java_home, rust_bin, output, gradle_zip, gradle_distribution, runner=build_sources.run, fresh=False):
    workspace = workspace.resolve(strict=True)
    lock_path = lock_path.resolve(strict=True)
    sources = sources.resolve(strict=True)
    lock_sha = stage_artifacts.digest(lock_path)
    lock = stage_artifacts.load_lock(lock_path)
    require(stage_artifacts.digest(lock_path) == lock_sha, 'Native lock changed during loading')
    source_proof = prepare_sources.verify_prepared(lock_path, sources)
    receipt_path = workspace / 'setup-receipt.json'
    require(receipt_path.is_file() and not receipt_path.is_symlink() and receipt_path.stat().st_size <= 16384, 'WebRTC setup receipt must be a small regular file')
    receipt = json.loads(receipt_path.read_text())
    expected_receipt = {
        'schema': 1, 'status': 'PARTIAL_WEBRTC_SETUP', 'release_allowed': False,
        'scope': 'gclient setup and patched source tree only; downloaded closure, native build and runtime unverified',
        'lock_sha256': stage_artifacts.digest(lock_path),
        'webrtc_base': lock['source_provenance']['webrtc'],
        'webrtc_tree': source_proof['sources']['webrtc']['prepared_tree'],
        'depot_tools_revision': lock['toolchain_reference']['webrtc_depot_tools_revision'],
    }
    require(receipt == expected_receipt, 'WebRTC setup receipt does not match the lock and independently replayed source')
    checkout = workspace / 'src'
    require(checkout.is_dir() and not checkout.is_symlink(), 'WebRTC checkout missing')
    prepare_sources.verify_worktree(checkout)
    require(prepare_sources.git(checkout, 'rev-parse', 'HEAD') == lock['source_provenance']['webrtc'], 'Unexpected WebRTC revision')
    require(prepare_sources.git(checkout, 'write-tree') == source_proof['sources']['webrtc']['prepared_tree'], 'WebRTC tree differs from independent patch replay')
    require(not prepare_sources.git(checkout, 'diff', '--name-only'), 'WebRTC worktree differs from verified index')
    require(java_home.resolve(strict=True) == (checkout / 'third_party/jdk/current').resolve(strict=True), 'RingRTC requires the JDK from the WebRTC checkout')
    gradle_executable = verify_gradle.verify(gradle_zip, gradle_distribution, '17f277867f6914d61b1aa02efab1ba7bb439ad652ca485cd8ca6842fccec6e43')
    nested_manifest = Path(__file__).with_name('webrtc-git-dependencies.json')
    nested_hash = '493532b881488cca5d83fda97199ca01e7ccadba5c710151ed660cde4b3d7c9f'
    nested_git.verify(workspace, nested_manifest, nested_hash)
    cipd_reference = Path(__file__).with_name('webrtc-cipd-payloads.json')
    cipd_hash = 'ceb49d26ce4fc03b4dde9009c5e4863b0d40a08dd593cac61b0f4a1321dfe303'
    cipd_payloads.verify(workspace, cipd_reference, cipd_hash)
    checked_lock, checked_proof, env, _ = build_sources.preflight(lock_path, sources, sdk, java_home, rust_bin, runner, java_major=25)
    require(checked_lock == lock and checked_proof['sources'] == source_proof['sources'], 'Native inputs changed during preflight')
    # The upstream wrapper uploads when this variable is present; diagnostics never publish.
    env = {key: value for key, value in env.items() if not key.startswith('CLOUDSDK_') and not key.startswith('ORG_GRADLE_PROJECT_signing')}
    if fresh:
        env = fresh_environment(sources, checkout, rust_bin, env)
    else:
        # The adapter flag is owned by this invocation, never inherited.
        env = {key: value for key, value in env.items() if key != 'COLLIE_NATIVE_FRESH'}
    ringrtc = sources.resolve(strict=True) / 'ringrtc'
    link = ringrtc / 'src/webrtc'
    require(not link.exists() and not link.is_symlink(), 'RingRTC WebRTC path is already occupied')
    output = output.absolute()
    require(not os.path.lexists(output), 'Diagnostic output already exists')
    output.mkdir(parents=True)
    api = checkout / 'ringrtc/rffi/api'
    target = (ringrtc / 'src/rust/src/webrtc_include').resolve(strict=True)
    require((not api.exists() and not api.is_symlink()) or (api.is_symlink() and api.resolve() == target), 'WebRTC RingRTC API path is already occupied')
    owned_api = None
    owned_link = None
    try:
        if not api.is_symlink():
            api.symlink_to(target, target_is_directory=True)
            stat = api.lstat()
            owned_api = (stat.st_dev, stat.st_ino)
        # Upstream wrapper expects this precise in-tree layout.
        link.symlink_to(workspace, target_is_directory=True)
        stat = link.lstat()
        owned_link = (stat.st_dev, stat.st_ino)
        env['OUTPUT_DIR'] = str(output)
        env['COLLIE_VERIFIED_GRADLE'] = str(gradle_executable)
        env['PATH'] = str(checkout / 'buildtools/linux64') + os.pathsep + env['PATH']
        command = [sys.executable, str(Path(__file__).with_name('efficient_ringrtc.py')), str(ringrtc / 'bin/build-aar.py'), '--build-dir=' + str(output), '--webrtc-src-dir=' + str(checkout), '--gradle-dir=' + str(ringrtc), '--publish-version=2.74.0', '--webrtc-version=8037b', '--release']
        runner(command, ringrtc, env)
        require(verify_gradle.verify(gradle_zip, gradle_distribution, '17f277867f6914d61b1aa02efab1ba7bb439ad652ca485cd8ca6842fccec6e43') == gradle_executable, 'Gradle distribution changed during build')
        require(prepare_sources.verify_prepared(lock_path, sources)['sources'] == source_proof['sources'], 'Prepared native sources changed during build')
        nested_git.verify(workspace, nested_manifest, nested_hash)
        cipd_payloads.verify(workspace, cipd_reference, cipd_hash)
        rust_payloads.verify(rust_bin.resolve(strict=True).parent, Path(__file__).with_name('rust-toolchain-payloads.json'), 'e9a644fc90306fd0eaa5d7b7323c3baea2865f460179d0b98cdbb07a2ba63cd0')
        ndk_payloads.verify(sdk.resolve(strict=True) / 'ndk/28.0.13004108', 'cc399724320a6e46fe83a068364630a9943760d7a83fd90570d660ac58786d9d', 8449)
        prepare_sources.verify_worktree(checkout)
        require(prepare_sources.git(checkout, 'write-tree') == source_proof['sources']['webrtc']['prepared_tree'], 'WebRTC index changed during build')
        require(not prepare_sources.git(checkout, 'diff', '--name-only'), 'WebRTC tracked source changed during build')
        aar = list(output.glob('gradle/outputs/aar/*release.aar'))
        require(len(aar) == 1, 'Expected exactly one RingRTC release AAR')
        item = next(m for m in lock['modules'] if m['name'] == 'ringrtc-android')
        digest = stage_artifacts.digest(aar[0])
        require(stage_artifacts.digest(lock_path) == lock_sha, 'Native lock changed during diagnostic build')
        build_facts = {'fresh_output_check': fresh,
                       'commands': [command],
                       'toolchain_references': {'gradle_sha256': '17f277867f6914d61b1aa02efab1ba7bb439ad652ca485cd8ca6842fccec6e43',
                                               'nested_git_sha256': nested_hash, 'cipd_sha256': cipd_hash,
                                               'rust_sha256': 'e9a644fc90306fd0eaa5d7b7323c3baea2865f460179d0b98cdbb07a2ba63cd0',
                                               'ndk_sha256': 'cc399724320a6e46fe83a068364630a9943760d7a83fd90570d660ac58786d9d'},
                       'selected_environment': {key: env.get(key) for key in ('CARGO_NET_OFFLINE', 'CARGO_INCREMENTAL', 'RUSTC',
                                                                            'RUSTC_WRAPPER', 'RUSTC_WORKSPACE_WRAPPER',
                                                                            'CCACHE_DISABLE', 'SCCACHE_DISABLE', 'COLLIE_NATIVE_FRESH')}}
        if digest != item['binary_sha256']:
            # Failure evidence cannot be used to stage or authorize this artifact.
            failure = {'schema': 1, 'status': 'FAILED_ARTIFACT_MISMATCH', 'release_allowed': False,
                       'scope': 'failed diagnostic; artifact is not approved for staging or release',
                       'lock_sha256': lock_sha, 'sources': source_proof['sources'],
                       'aar_sha256': digest, 'expected_aar_sha256': item['binary_sha256'], **build_facts}
            with (output / 'failure-receipt.json').open('x') as destination:
                json.dump(failure, destination, indent=2, sort_keys=True)
                destination.write('\n')
            raise Failure('RingRTC artifact differs from locked candidate; reproducibility is unproved')
        proof = {'schema': 1, 'status': 'PARTIAL_RINGRTC_BUILD', 'release_allowed': False,
                 'scope': 'diagnostic only; downloaded closure and proxy runtime unverified',
                 'lock_sha256': stage_artifacts.digest(lock_path), 'sources': source_proof['sources'], 'aar_sha256': digest, **build_facts}
        with (output / 'build-receipt.json').open('x') as destination:
            json.dump(proof, destination, indent=2, sort_keys=True)
            destination.write('\n')
        return proof
    finally:
        # Only remove the exact links created by this invocation.
        for path, identity, expected in ((link, owned_link, workspace), (api, owned_api, target)):
            if identity is not None and path.is_symlink():
                stat = path.lstat()
                if (stat.st_dev, stat.st_ino) == identity and path.resolve() == expected:
                    path.unlink()

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for arg in ('lock', 'sources', 'workspace', 'sdk', 'java-home', 'rust-bin', 'output', 'gradle-zip', 'gradle-distribution'):
        parser.add_argument('--' + arg, required=True, type=Path)
    parser.add_argument('--fresh', action='store_true', help='reject previous native outputs and disable build caches; does not authenticate dependency inputs')
    args = parser.parse_args()
    try:
        diagnostic_build(args.lock.resolve(), args.sources, args.workspace, args.sdk, args.java_home, args.rust_bin, args.output, args.gradle_zip, args.gradle_distribution, fresh=args.fresh)
    except (Failure, build_sources.Failure, prepare_sources.Failure, stage_artifacts.Failure, verify_gradle.Failure, nested_git.Failure, cipd_payloads.Failure, rust_payloads.Failure, ndk_payloads.Failure, OSError, ValueError, KeyError) as exc:
        print(f'RingRTC diagnostic failed: {exc}', file=sys.stderr)
        return 1
    return 0

if __name__ == '__main__':
    sys.exit(main())
