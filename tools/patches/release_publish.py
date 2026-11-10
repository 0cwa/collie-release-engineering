#!/usr/bin/env python3
"""Create a verified draft release, or explicitly publish an already verified draft."""
import argparse
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import time

import version_policy
import runtime_receipts as runtime
import release_artifacts as a
from release_sign import validate_instrumentation_checklist


def command(args, dry=False, **kwargs):
    args = [str(x) for x in args]
    print('+ ' + shlex.join(args), flush=True)
    if not dry:
        return subprocess.run(args, check=True, **kwargs)
    return None


class PhaseRecorder:
    """Write optional local-only publisher timings without recording inputs."""
    MAX_REPORT_BYTES = 16 * 1024

    class Phase:
        def __init__(self, owner, name):
            self.owner = owner
            self.record = {'phase': name}

        def __enter__(self):
            self.record['started_monotonic_ns'] = time.monotonic_ns()
            return self.record

        def __exit__(self, error_type, _error, _traceback):
            self.record['duration_ns'] = time.monotonic_ns() - self.record['started_monotonic_ns']
            self.record['status'] = 'failed' if error_type else 'success'
            self.owner.records.append(self.record)
            return False

    def __init__(self, path=None):
        self.records = []
        self.fd = None
        if path is not None:
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            if hasattr(os, 'O_NOFOLLOW'):
                flags |= os.O_NOFOLLOW
            self.fd = os.open(path, flags, 0o600)
            try:
                os.fchmod(self.fd, 0o600)
            except OSError:
                os.close(self.fd)
                self.fd = None
                raise

    def phase(self, name):
        return self.Phase(self, name)

    def finish(self, result):
        if self.fd is None:
            return
        report = {'schema': 1, 'scope': 'github-release-publisher',
                  'result': result, 'phases': self.records}
        data = (json.dumps(report, sort_keys=True, separators=(',', ':')) + '\n').encode()
        if len(data) > self.MAX_REPORT_BYTES:
            raise OSError('phase report exceeds its size limit')
        fd, self.fd = self.fd, None
        try:
            offset = 0
            while offset < len(data):
                written = os.write(fd, data[offset:])
                if written <= 0:
                    raise OSError('phase report write made no progress')
                offset += written
            os.fsync(fd)
        finally:
            os.close(fd)


def asset_file_bytes(paths):
    return sum(path.stat().st_size for path in paths)


def validate_asset_metadata(release, assets, expected, *, allow_missing=False):
    """Check remote IDs, names and sizes; return IDs and whether bytes are needed."""
    remote_assets = release.get('assets') if isinstance(release, dict) else None
    if not isinstance(remote_assets, list):
        raise ValueError('release asset metadata is missing')
    desired = {path.name: path for path in assets}
    if len(desired) != len(assets) or set(expected) != set(desired):
        raise ValueError('local expected asset set is inconsistent')
    seen_names = set()
    seen_ids = set()
    asset_ids = {}
    needs_byte_check = False
    for asset in remote_assets:
        if not isinstance(asset, dict):
            raise ValueError('release asset metadata is malformed')
        name = asset.get('name')
        if not isinstance(name, str) or name not in desired or name in seen_names:
            raise ValueError('unexpected or duplicate release asset')
        remote_id = asset.get('id')
        if not isinstance(remote_id, str) or not remote_id or len(remote_id) > 512 or remote_id in seen_ids:
            raise ValueError('release asset ID is missing or duplicated')
        size = asset.get('size')
        if type(size) is not int or size != desired[name].stat().st_size:
            raise ValueError('release asset size differs: ' + name)
        seen_names.add(name)
        seen_ids.add(remote_id)
        asset_ids[name] = remote_id

        digest = asset.get('digest')
        if not isinstance(digest, str) or not re.fullmatch(r'sha256:[0-9a-f]{64}', digest):
            needs_byte_check = True
        elif digest != 'sha256:' + expected[name]:
            raise ValueError('GitHub release asset digest differs: ' + name)

    if not allow_missing and seen_names != set(desired):
        raise ValueError('release state, prerelease setting or exact asset list differs')
    if allow_missing and not seen_names <= set(desired):
        raise ValueError('unexpected draft asset set')
    return asset_ids, needs_byte_check


def download_and_verify(args, names, expected, downloads, phase):
    downloads.mkdir()
    try:
        command(['gh', 'release', 'download', args.tag, '--repo', args.repo, '--dir', downloads])
    finally:
        phase['bytes_downloaded'] = sum(
            (downloads / name).stat().st_size
            for name in names if (downloads / name).is_file()
        )
    for name in names:
        path = downloads / name
        if a.sha256(path) != expected[name]:
            raise ValueError('downloaded asset hash differs: ' + name)
    return phase['bytes_downloaded']



def create_signed_tag(repo, tag, commit, key, name, email, askpass, dry):
    """Create and push an annotated SSH-signed tag, cleaning only our local tag."""
    if not key or not name or not email:
        raise ValueError('draft requires --signing-key, --tagger-name and --tagger-email')
    env = os.environ.copy()
    if askpass:
        env['SSH_ASKPASS'] = str(askpass)
        env['SSH_ASKPASS_REQUIRE'] = 'force'
    created = False
    try:
        command(['git', '-c', 'user.name=' + name, '-c', 'user.email=' + email,
                 '-c', 'gpg.format=ssh', '-c', 'user.signingkey=' + str(key),
                 'tag', '-s', tag, commit, '-m', 'Release ' + tag], dry, env=env)
        created = True
        command(['git', 'push', 'https://github.com/' + repo + '.git',
                 'refs/tags/' + tag + ':refs/tags/' + tag], dry)
    finally:
        if created:
            command(['git', 'tag', '-d', tag], dry)


def verify_remote_tag(repo, tag, commit):
    ref = command(['gh', 'api', f'repos/{repo}/git/ref/tags/{tag}'],
                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    obj = json.loads(ref.stdout)['object']
    if obj.get('type') != 'tag' or not re.fullmatch(r'[0-9a-f]{40}', obj.get('sha', '')):
        raise ValueError('remote release tag is not a signed annotated tag')
    result = command(['gh', 'api', f"repos/{repo}/git/tags/{obj['sha']}"],
                     stdout=subprocess.PIPE, text=True)
    signed = json.loads(result.stdout)
    if (signed.get('tag') != tag or signed.get('object', {}).get('type') != 'commit'
            or signed.get('object', {}).get('sha') != commit
            or signed.get('verification', {}).get('verified') is not True):
        raise ValueError('remote tag signature or approved commit verification failed')

def missing_remote(error):
    message = str(error.stderr or '').strip()
    return error.returncode == 1 and (message == 'release not found' or 'HTTP 404' in message)


def find_release(repo, tag):
    try:
        info = command(['gh', 'release', 'view', tag, '--repo', repo, '--json', 'isDraft,isPrerelease,assets'],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    except subprocess.CalledProcessError as error:
        if missing_remote(error):
            return None
        raise
    return json.loads(info.stdout)


def ensure_signed_tag(args):
    try:
        verify_remote_tag(args.repo, args.tag, args.approved_commit)
    except subprocess.CalledProcessError as error:
        if not missing_remote(error):
            raise
        create_signed_tag(args.repo, args.tag, args.approved_commit, args.signing_key,
                          args.tagger_name, args.tagger_email, args.ssh_askpass, False)
        verify_remote_tag(args.repo, args.tag, args.approved_commit)


def resume_draft_assets(args, assets, expected, downloads, phases):
    """Complete interrupted uploads without replacing any existing asset."""
    with phases.phase('draft_resume_verify') as phase:
        phase['method'] = 'gh_release_metadata_and_tag_check'
        phase['bytes_downloaded'] = 0
        verify_remote_tag(args.repo, args.tag, args.approved_commit)
        release = find_release(args.repo, args.tag)
        if (release is None or release.get('isDraft') is not True
                or release.get('isPrerelease') is not args.prerelease):
            raise ValueError('draft resume requires same draft/channel')
        names = [asset.get('name') for asset in release.get('assets', [])
                 if isinstance(asset, dict)]
        asset_ids, needs_byte_check = validate_asset_metadata(
            release, assets, expected, allow_missing=True)
        method = 'github_asset_metadata'
        phase['bytes_downloaded'] = 0
        if names and needs_byte_check:
            method = 'full_byte_fallback_missing_or_unsupported_digest'
            download_and_verify(args, names, expected, downloads, phase)
        phase['method'] = method

    missing = [path for path in assets if path.name not in asset_ids]
    if missing:
        with phases.phase('draft_resume_upload') as phase:
            phase['method'] = 'gh_release_upload_file_payload'
            phase['asset_file_bytes_submitted'] = asset_file_bytes(missing)
            command(['gh', 'release', 'upload', args.tag, '--repo', args.repo, *missing])
    return asset_ids


def readback(args, assets, expected, downloads, phase, *, expected_draft=None,
             force_full_bytes=False):
    phase['method'] = 'gh_release_metadata_and_tag_check'
    phase['bytes_downloaded'] = 0
    verify_remote_tag(args.repo, args.tag, args.approved_commit)
    release = find_release(args.repo, args.tag)
    if (release is None or type(release.get('isDraft')) is not bool
            or (expected_draft is not None and release['isDraft'] is not expected_draft)
            or release.get('isPrerelease') is not args.prerelease):
        raise ValueError('release state, prerelease setting or exact asset list differs')
    phase['method'] = 'github_asset_metadata'
    asset_ids, needs_byte_check = validate_asset_metadata(release, assets, expected)
    if force_full_bytes:
        method = 'github_metadata_plus_full_byte_readback'
    elif needs_byte_check:
        method = 'full_byte_fallback_missing_or_unsupported_digest'
    elif args.action == 'publish' and not release['isDraft']:
        method = 'github_metadata_plus_full_byte_readback'
    else:
        method = 'github_asset_metadata'
    phase['method'] = method
    if force_full_bytes or needs_byte_check or (args.action == 'publish' and not release['isDraft']):
        if needs_byte_check:
            phase['method'] = 'full_byte_fallback_missing_or_unsupported_digest'
        download_and_verify(args, [path.name for path in assets], expected, downloads, phase)
    return release, asset_ids

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=('draft', 'publish'))
    p.add_argument('--repo', required=True)
    p.add_argument('--tag', required=True)
    p.add_argument('--approved-commit', required=True)
    p.add_argument('--engineering-revision', help='independently approved schema2 engineering revision')
    p.add_argument('--workflow-revision', help='independently approved schema2 workflow revision')
    p.add_argument('--final-manifest', type=Path, required=True)
    p.add_argument('--final-manifest-sha256', required=True)
    p.add_argument('--provenance', type=Path, required=True)
    p.add_argument('--notes', type=Path, required=True)
    p.add_argument('--scratch', type=Path, required=True)
    p.add_argument('--signing-key', type=Path, help='SSH private key for creating the signed tag')
    p.add_argument('--tagger-name', help='name for the signed tag')
    p.add_argument('--tagger-email', help='GitHub-verified email for the signed tag')
    p.add_argument('--ssh-askpass', type=Path, help='optional helper supplying the SSH key passphrase')
    p.add_argument('--prerelease', action='store_true', help='create and verify a testing prerelease')
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--phase-report', type=Path,
                   help='optional exclusive mode-0600 local JSON publisher timing report')
    p.add_argument('--version-ledger', type=Path, help='durable shared operator reservation ledger')
    p.add_argument('--published-inventory', type=Path, help='fresh verified production release inventory')
    p.add_argument('--published-inventory-sha256', help='independently approved inventory digest')
    runtime.arguments(p)
    args = p.parse_args()
    identities = ({'source_commit':args.approved_commit,'engineering_revision':args.engineering_revision,'workflow_revision':args.workflow_revision}
                  if args.engineering_revision or args.workflow_revision else None)
    phases = None
    result = 'failed'
    try:
        phases = PhaseRecorder(args.phase_report)
        context=runtime.plan(args,historical_rehearsal=args.dry_run)
        if context and context[0]['mode']=='new-source':identities=runtime.identities(context)
        if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', args.repo):
            raise ValueError('invalid GitHub repository')
        if not re.fullmatch(r'v[A-Za-z0-9][A-Za-z0-9._-]{0,79}', args.tag) or '..' in args.tag:
            raise ValueError('invalid release tag')
        if not re.fullmatch(r'[0-9a-f]{40}', args.approved_commit):
            raise ValueError('approved commit must be a full SHA-1')
        if not args.scratch.is_dir():
            raise ValueError('scratch directory must already exist')
        with tempfile.TemporaryDirectory(prefix='release-publish-', dir=args.scratch) as tmp:
            tmp = Path(tmp)
            inputs = tmp / 'inputs'
            inputs.mkdir(mode=0o700)
            final = inputs / 'final-manifest.json'
            a.snapshot(args.final_manifest, final)
            if a.sha256(final) != args.final_manifest_sha256:
                raise ValueError('final manifest differs from independently approved digest')
            manifest = json.loads(final.read_text())
            runtime.check_manifest(context,manifest)
            if manifest.get('schema') != (2 if context and context[0]['mode']=='new-source' else 1) or manifest.get('rehearsal') is not False or manifest.get('commit') != args.approved_commit:
                raise ValueError('final manifest is not a real release of the approved commit')
            if args.repo.lower() == '0cwa/collieim-android':
                a.require_canonical_release(manifest)
                a.validate_version_code(manifest.get('version_code'), a.PACKAGE)
            validate_instrumentation_checklist(manifest.get('instrumentation_checklist'), args.approved_commit)
            entries = manifest['apks']
            if len(entries) != 5 or {x['profile'] for x in entries} != a.PROFILES:
                raise ValueError('final manifest must contain all five profiles')
            assets = []
            expected = {}
            for entry in entries:
                if entry['file'] != entry['profile'] + '.apk':
                    raise ValueError('invalid APK filename')
                path = inputs / entry['file']
                a.snapshot(args.final_manifest.parent / entry['file'], path)
                expected[path.name] = entry['signed_sha256']
                if a.sha256(path) != expected[path.name]:
                    raise ValueError('signed APK hash differs: ' + entry['file'])
                assets.append(path)
            provenance = inputs / 'provenance.json'
            notes = inputs / 'release-notes.md'
            a.snapshot(args.provenance, provenance)
            a.snapshot(args.notes, notes)
            if a.sha256(provenance) != manifest.get('unsigned_manifest_sha256'):
                raise ValueError('provenance does not match the unsigned manifest used for signing')
            final_mode = manifest.get('artifact_mode', 'five-profiles')
            if final_mode not in ('five-profiles', 'single-collie'):
                raise ValueError('unknown release artifact mode')
            try:
                unsigned = json.loads(provenance.read_text())
            except json.JSONDecodeError:
                if final_mode == 'single-collie':
                    raise ValueError('single-collie provenance must be a JSON manifest')
                unsigned = None
            unsigned_mode = unsigned.get('artifact_mode', 'five-profiles') if isinstance(unsigned, dict) else 'five-profiles'
            if final_mode != unsigned_mode:
                raise ValueError('signed and unsigned artifact modes differ')
            if final_mode == 'single-collie':
                a.validate_alias_provenance(manifest, unsigned, args.approved_commit, identities)
            if context and context[0]['mode']=='new-source':
                graph=runtime.publication(args,context,unsigned,manifest,final.read_bytes())
                for name,document in zip(('source.json','build.json','release.json'),graph):
                    receipt_path=inputs/name;runtime.write(receipt_path,document)
                    assets.append(receipt_path);expected[name]=a.sha256(receipt_path)
            if args.repo.lower()=='0cwa/collieim-android' and not args.dry_run:
                version_policy.enforce(manifest,args.version_ledger,args.published_inventory,args.published_inventory_sha256)
                version_policy.bind_signed(args.version_ledger,manifest)
            assets += [final, provenance, notes]
            expected.update({final.name: args.final_manifest_sha256,
                             provenance.name: manifest['unsigned_manifest_sha256'],
                             notes.name: a.sha256(notes)})
            sums = inputs / 'SHA256SUMS'
            sums.write_text(''.join(expected[path.name] + '  ' + path.name + '\n' for path in assets))
            sums.chmod(0o600)
            expected[sums.name] = a.sha256(sums)
            assets.append(sums)
            existing = None if args.dry_run else find_release(args.repo, args.tag)
            created_this_run = False
            if args.action == 'draft' and existing is None:
                if args.dry_run:
                    create_signed_tag(args.repo, args.tag, args.approved_commit, args.signing_key,
                                      args.tagger_name, args.tagger_email, args.ssh_askpass, True)
                else:
                    ensure_signed_tag(args)
                try:
                    with phases.phase('draft_upload') as phase:
                        phase['method'] = ('dry_run_no_remote_io' if args.dry_run
                                           else 'gh_release_create_file_payload')
                        phase['asset_file_bytes_submitted'] = (0 if args.dry_run
                                                               else asset_file_bytes(assets))
                        command(['gh', 'release', 'create', args.tag, '--repo', args.repo, '--verify-tag', '--draft',
                                 '--title', args.tag, '--notes-file', notes,
                                 *(['--prerelease', '--latest=false'] if args.prerelease else []), *assets], args.dry_run)
                    created_this_run = not args.dry_run
                except subprocess.CalledProcessError:
                    # A successful create followed by lost response is safe to
                    # resume only after the same complete release is read back.
                    if find_release(args.repo, args.tag) is None:
                        raise
            if not args.dry_run and args.action == 'draft':
                resumed_asset_ids = {}
                if not created_this_run:
                    resumed_asset_ids = resume_draft_assets(
                        args, assets, expected, tmp / 'downloaded-existing', phases)
            if args.dry_run:
                with phases.phase('dry_run_preview') as phase:
                    phase['method'] = 'dry_run_no_remote_io'
                    phase['bytes_downloaded'] = 0
                    phase['asset_file_bytes_submitted'] = 0
                    command(['gh', 'release', 'download', args.tag, '--repo', args.repo,
                             '--dir', tmp / 'downloaded'], True)
                    if args.action == 'publish':
                        command(['gh', 'release', 'edit', args.tag, '--repo', args.repo, '--draft=false'], True)
            else:
                phase_name = 'draft_verify' if args.action == 'draft' else 'prepublish_verify'
                with phases.phase(phase_name) as phase:
                    release, asset_ids = readback(
                        args, assets, expected, tmp / 'downloaded-prepublish', phase,
                        expected_draft=True if args.action == 'draft' else None)
                    if args.action == 'draft' and any(
                            asset_ids.get(name) != remote_id
                            for name, remote_id in resumed_asset_ids.items()):
                        raise ValueError('existing draft asset IDs changed during resume')
                if args.action == 'publish' and release['isDraft']:
                    with phases.phase('publish_transition') as phase:
                        phase['method'] = 'gh_release_edit_draft_false'
                        phase['asset_file_bytes_submitted'] = 0
                        command(['gh', 'release', 'edit', args.tag, '--repo', args.repo, '--draft=false'])
                    with phases.phase('public_readback') as phase:
                        published, published_asset_ids = readback(
                            args, assets, expected, tmp / 'downloaded-published', phase,
                            expected_draft=False, force_full_bytes=True)
                        if published['isDraft']:
                            raise ValueError('publication did not become visible after readback')
                        if published_asset_ids != asset_ids:
                            raise ValueError('published release asset IDs differ from prepublish verification')
                elif args.action == 'publish':
                    # Idempotent retries of a release already published still require
                    # the full byte readback; readback selected it above.
                    phase = phases.records[-1]
                    phase['phase'] = 'public_readback'
            result = 'success'
    except (ValueError, KeyError, OSError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        print('release publishing failed:', exc, file=sys.stderr)
    finally:
        if phases is not None:
            try:
                phases.finish(result)
            except OSError as exc:
                print('release succeeded but phase report could not be completed:' if result == 'success'
                      else 'release phase report could not be completed:', exc, file=sys.stderr)
                result = 'failed'
    return 0 if result == 'success' else 1


if __name__ == '__main__':
    raise SystemExit(main())
