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


def resume_draft_assets(args, assets, expected, downloads):
    """Complete interrupted uploads without replacing any existing asset."""
    verify_remote_tag(args.repo,args.tag,args.approved_commit)
    release=find_release(args.repo,args.tag)
    if release is None or release.get('isDraft') is not True or release.get('isPrerelease') is not args.prerelease:
        raise ValueError('draft resume requires same draft/channel')
    names=[x['name'] for x in release.get('assets',[])]
    desired={p.name:p for p in assets}
    if len(names)!=len(set(names)) or set(names)-desired.keys():raise ValueError('unexpected draft asset set')
    if names:
        downloads.mkdir()
        command(['gh','release','download',args.tag,'--repo',args.repo,'--dir',downloads])
        for name in names:
            if a.sha256(downloads/name)!=expected[name]:raise ValueError('existing draft asset differs: '+name)
    missing=[desired[name] for name in desired if name not in names]
    if missing:
        command(['gh','release','upload',args.tag,'--repo',args.repo,*missing])

def readback(args, assets, expected, downloads):
    verify_remote_tag(args.repo, args.tag, args.approved_commit)
    release = find_release(args.repo, args.tag)
    if (release is None or type(release.get('isDraft')) is not bool
            or (args.action == 'draft' and not release['isDraft'])
            or release.get('isPrerelease') is not args.prerelease
            or {x['name'] for x in release.get('assets', [])} != {x.name for x in assets}):
        raise ValueError('release state, prerelease setting or exact asset list differs')
    downloads.mkdir()
    command(['gh', 'release', 'download', args.tag, '--repo', args.repo, '--dir', downloads])
    for path in assets:
        if a.sha256(downloads / path.name) != expected[path.name]:
            raise ValueError('downloaded asset hash differs: ' + path.name)
    return release

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
    p.add_argument('--version-ledger', type=Path, help='durable shared operator reservation ledger')
    p.add_argument('--published-inventory', type=Path, help='fresh verified production release inventory')
    p.add_argument('--published-inventory-sha256', help='independently approved inventory digest')
    runtime.arguments(p)
    args = p.parse_args()
    identities = ({'source_commit':args.approved_commit,'engineering_revision':args.engineering_revision,'workflow_revision':args.workflow_revision}
                  if args.engineering_revision or args.workflow_revision else None)
    try:
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
            if args.action == 'draft' and existing is None:
                if args.dry_run:
                    create_signed_tag(args.repo, args.tag, args.approved_commit, args.signing_key,
                                      args.tagger_name, args.tagger_email, args.ssh_askpass, True)
                else:
                    ensure_signed_tag(args)
                try:
                    command(['gh', 'release', 'create', args.tag, '--repo', args.repo, '--verify-tag', '--draft',
                             '--title', args.tag, '--notes-file', notes,
                             *(['--prerelease', '--latest=false'] if args.prerelease else []), *assets], args.dry_run)
                except subprocess.CalledProcessError:
                    # A successful create followed by lost response is safe to
                    # resume only after the same complete release is read back.
                    if find_release(args.repo, args.tag) is None:
                        raise
            if not args.dry_run and args.action == 'draft':
                resume_draft_assets(args, assets, expected, tmp / 'downloaded-existing')
            if args.dry_run:
                command(['gh', 'release', 'download', args.tag, '--repo', args.repo, '--dir', tmp / 'downloaded'], True)
                if args.action == 'publish':
                    command(['gh', 'release', 'edit', args.tag, '--repo', args.repo, '--draft=false'], True)
            else:
                release = readback(args, assets, expected, tmp / 'downloaded')
                if args.action == 'publish' and release['isDraft']:
                    command(['gh', 'release', 'edit', args.tag, '--repo', args.repo, '--draft=false'])
                    published = readback(args, assets, expected, tmp / 'downloaded-published')
                    if published['isDraft']:
                        raise ValueError('publication did not become visible after readback')
    except (ValueError, KeyError, OSError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        print('release publishing failed:', exc, file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
