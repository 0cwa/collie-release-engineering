#!/usr/bin/env python3
"""Sign an independently authenticated release manifest and its five APKs offline."""
import argparse
import getpass
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile

import version_policy
import runtime_receipts as runtime
import release_artifacts as artifacts


def run(args, **kwargs):
    return subprocess.run([str(x) for x in args], check=True, **kwargs)


def read_instrumentation_checklist(path, approved_commit):
    """Read the maintainer's explicit, commit-bound instrumentation assessment."""
    if path is None:
        raise ValueError('an instrumentation checklist is required before signing')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > 16384:
            raise ValueError('instrumentation checklist must be a regular file no larger than 16 KiB')
        with os.fdopen(fd, 'rb', closefd=False) as source:
            raw = source.read(16385)
        if len(raw) > 16384:
            raise ValueError('instrumentation checklist is too large')
    finally:
        os.close(fd)
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('instrumentation checklist has duplicate fields')
            result[key] = value
        return result
    checklist = json.loads(raw, object_pairs_hook=unique_object)
    return validate_instrumentation_checklist(checklist, approved_commit)


def validate_instrumentation_checklist(checklist, approved_commit):
    if not isinstance(checklist, dict) or set(checklist) != {'commit', 'status', 'evidence', 'reason'}:
        raise ValueError('instrumentation checklist must contain exactly commit, status, evidence and reason')
    if any(not isinstance(checklist[key], str) for key in checklist):
        raise ValueError('instrumentation checklist fields must be strings')
    if checklist['commit'] != approved_commit:
        raise ValueError('instrumentation checklist commit differs from the approved commit')
    if checklist['status'] not in ('passed', 'failed', 'not run'):
        raise ValueError('instrumentation status must be passed, failed or not run')
    for name in ('evidence', 'reason'):
        value = checklist[name]
        if len(value) > 2048 or any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError('instrumentation evidence and reason must be single-line text')
    if checklist['status'] == 'passed' and not checklist['evidence'].strip():
        raise ValueError('passed instrumentation requires evidence')
    if checklist['status'] != 'passed' and not checklist['reason'].strip():
        raise ValueError('failed or not-run instrumentation requires a reason for accepting the gap')
    return checklist


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest', type=Path, required=True)
    p.add_argument('--manifest-sha256', required=True, help='digest authenticated independently from the downloaded artifact')
    p.add_argument('--approved-commit', required=True)
    p.add_argument('--engineering-revision', help='independently approved schema2 engineering revision')
    p.add_argument('--workflow-revision', help='independently approved schema2 workflow revision')
    p.add_argument('--approved-run', required=True)
    p.add_argument('--certificate-sha256', required=True)
    p.add_argument('--apks', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--build-tools', type=Path, required=True)
    p.add_argument('--attestation-repo', help='verify with gh; supply a trusted GH repository')
    p.add_argument('--instrumentation-checklist', type=Path, default=os.environ.get('COLLIE_INSTRUMENTATION_CHECKLIST'),
                   help='commit-bound instrumentation JSON (or set COLLIE_INSTRUMENTATION_CHECKLIST)')
    p.add_argument('--rehearse', action='store_true')
    p.add_argument('--scratch', type=Path, required=True, help='existing private directory for temporary files')
    p.add_argument('--version-ledger', type=Path, help='durable shared operator reservation ledger')
    p.add_argument('--published-inventory', type=Path, help='fresh verified production release inventory')
    p.add_argument('--published-inventory-sha256', help='independently approved inventory digest')
    runtime.arguments(p)
    args = p.parse_args()
    identities = ({'source_commit':args.approved_commit,'engineering_revision':args.engineering_revision,'workflow_revision':args.workflow_revision}
                  if args.engineering_revision or args.workflow_revision else None)
    try:
        context=runtime.plan(args,historical_rehearsal=args.rehearse)
        if context and context[0]['mode']=='new-source':
            if args.rehearse:raise ValueError('new-source fixtures must pin a pre-created test key; unknown rehearsal signer cannot satisfy plan')
            identities=runtime.identities(context)
        if not args.scratch.is_dir():
            raise ValueError('--scratch must be an existing private directory')
        if args.output.exists():
            raise ValueError('output directory already exists')
        with tempfile.TemporaryDirectory(prefix='release-sign-', dir=args.scratch) as temp:
            temp = Path(temp)
            snapshot_dir = temp / 'inputs'
            snapshot_dir.mkdir(mode=0o700)
            manifest = snapshot_dir / 'manifest.json'
            artifacts.snapshot(args.manifest, manifest)
            if artifacts.sha256(manifest) != args.manifest_sha256:
                raise ValueError('manifest differs from independently authenticated SHA-256')
            m = json.loads(manifest.read_text())
            runtime.check_manifest(context,m)
            entries = artifacts.validate_manifest(m, args.approved_commit, args.approved_run, args.certificate_sha256, identities)
            graph=runtime.signing(args,context,m) if context and context[0]['mode']=='new-source' else None
            if not args.rehearse:
                artifacts.require_canonical_release(m)
                reservation = dict(m, _unsigned_manifest_sha256=args.manifest_sha256)
                version_policy.enforce(reservation, args.version_ledger, args.published_inventory, args.published_inventory_sha256)
            checklist = read_instrumentation_checklist(args.instrumentation_checklist, args.approved_commit)
            print('Instrumentation checklist (maintainer-provided; not independently verified):')
            print(json.dumps(checklist, indent=2, ensure_ascii=True), flush=True)
            if args.build_tools.name != m.get('build_tools') or m.get('build_tools') != m.get('input_pins', {}).get('android', {}).get('build_tools'):
                raise ValueError('build-tools directory does not match manifest pin')
            if args.attestation_repo:
                run(['gh', 'attestation', 'verify', manifest, '--repo', args.attestation_repo,
                     '--source-digest', 'sha1:' + args.approved_commit])
            single = m.get('artifact_mode') == 'single-collie'
            inputs = [next(e for e in entries if e['profile'] == 'collie')] if single else entries
            for e in inputs:
                apk = snapshot_dir / e['file']
                artifacts.snapshot(args.apks / e['file'], apk)
                if artifacts.sha256(apk) != e['sha256']:
                    raise ValueError('unsigned APK hash mismatch: ' + e['file'])
                artifacts.check_unsigned(apk)
                artifacts.check_metadata(args.build_tools / 'aapt2', apk, m['version_code'], m['version_name'])
            env = os.environ.copy()
            if args.rehearse:
                keystore = temp / 'rehearsal.p12'
                env['MOLLY_KEYSTORE_PASSWORD'] = 'rehearsal-only-password'
                env['MOLLY_KEY_PASSWORD'] = env['MOLLY_KEYSTORE_PASSWORD']
                alias = 'rehearsal'
                run(['keytool', '-genkeypair', '-keystore', keystore, '-storetype', 'PKCS12',
                     '-storepass:env', 'MOLLY_KEYSTORE_PASSWORD', '-keypass:env', 'MOLLY_KEY_PASSWORD',
                     '-alias', alias, '-keyalg', 'RSA', '-keysize', '3072', '-validity', '7',
                     '-dname', 'CN=Disposable rehearsal'], env=env, stdout=subprocess.DEVNULL)
            else:
                keystore = Path(os.environ['MOLLY_KEYSTORE'])
                alias = os.environ['MOLLY_KEY_ALIAS']
                env['MOLLY_KEYSTORE_PASSWORD'] = os.environ.get('MOLLY_KEYSTORE_PASSWORD') or getpass.getpass('Keystore password: ')
                env['MOLLY_KEY_PASSWORD'] = os.environ.get('MOLLY_KEY_PASSWORD') or getpass.getpass('Key password: ')
            args.output.mkdir(parents=True)
            signed = []
            ordered = sorted(entries, key=lambda e: e['profile'] != 'collie') if single else entries
            canonical = None
            for e in ordered:
                apk = snapshot_dir / ('collie.apk' if single else e['file'])
                dest = args.output / e['file']
                if single and canonical is not None:
                    shutil.copyfile(canonical, dest)
                else:
                    aligned = temp / e['file']
                    # Sign a fresh aligned copy of the checked input.
                    run([args.build_tools / 'zipalign', '-f', '-p', '4', apk, aligned])
                    run([args.build_tools / 'zipalign', '-c', '-p', '4', aligned])
                    run([args.build_tools / 'apksigner', 'sign', '--ks', keystore, '--ks-key-alias', alias,
                         '--ks-pass', 'env:MOLLY_KEYSTORE_PASSWORD', '--key-pass', 'env:MOLLY_KEY_PASSWORD',
                         '--v2-signing-enabled', 'true', '--v3-signing-enabled', 'true',
                         *(['--v4-signing-enabled', 'false'] if single else []), '--out', dest, aligned], env=env)
                    if single:
                        canonical = dest
                run([args.build_tools / 'zipalign', '-c', '-p', '4', dest])
                result = run([args.build_tools / 'apksigner', 'verify', '--verbose', '--print-certs', dest],
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True).stdout
                print(result, end='')
                digests = re.findall(r'Signer #\d+ certificate SHA-256 digest: ([0-9a-fA-F:]+)', result)
                if len(digests) != 1 or (not args.rehearse and digests[0].replace(':', '').lower() != args.certificate_sha256):
                    raise ValueError('unexpected APK signing certificate')
                if not all(re.search(r'Verified using v' + v + r' scheme \([^\n]*\): true', result) for v in ('2', '3')):
                    raise ValueError('APK lacks verified v2 and v3 signatures')
                artifacts.check_metadata(args.build_tools / 'aapt2', dest, m['version_code'], m['version_name'])
                signed.append({'profile': e['profile'], 'file': e['file'], 'unsigned_sha256': e['sha256'],
                               'signed_sha256': artifacts.sha256(dest), 'certificate_sha256': digests[0].replace(':', '').lower()})
            final = {'schema': 1, 'rehearsal': args.rehearse, 'unsigned_manifest_sha256': args.manifest_sha256,
                     'commit': args.approved_commit, 'run_id': str(args.approved_run), 'version_code': m['version_code'],
                     'version_name': m['version_name'], 'certificate_sha256': signed[0]['certificate_sha256'], 'apks': signed,
                     'instrumentation_checklist': checklist}
            if single:
                if len({x['signed_sha256'] for x in signed}) != 1:
                    raise ValueError('signed compatibility aliases differ')
                final['artifact_mode'] = 'single-collie'
                final['source_profile'] = 'collie'
                final['package'] = artifacts.PACKAGE
            if any(x['certificate_sha256'] != final['certificate_sha256'] for x in signed):
                raise ValueError('signed APKs have different certificates')
            if graph:
                final.update(schema=2,package=artifacts.PACKAGE,release_mode='new-source',release_plan_sha256=context[1],source_receipt_sha256=m['source_receipt_sha256'],build_receipt_sha256=m['build_receipt_sha256'],**runtime.identities(context))
            raw_final=(json.dumps(final,indent=2)+'\n').encode()
            (args.output / 'final-manifest.json').write_bytes(raw_final)
            if graph:
                runtime.write(args.output/'release-receipt.json',runtime.release(context,*graph,final,raw_final))
    except (ValueError, KeyError, OSError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        print('release signing failed:', exc, file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
