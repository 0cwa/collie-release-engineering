"""Public-key-pinned verification for generated SSH-signed publication commits."""
import re
import shlex
import subprocess
from pathlib import Path

import publish


FINGERPRINT = re.compile(r'SHA256:[A-Za-z0-9+/]+')
IDENTITY = re.compile(r'^(author|committer) (.*) <([^<>]+)> [0-9]+ [+-][0-9]{4}$')


def public_key_info(public_key):
    """Return safe public metadata; never load a private key file."""
    path = Path(public_key)
    if not path.is_file() or path.is_symlink() or path.suffix != '.pub':
        raise ValueError('signing key selector must be an existing SSH .pub file')
    with path.open('r', encoding='utf-8') as stream:
        first = stream.readline(4096)
        has_extra = bool(stream.readline(1))
    fields = first.split()
    if has_extra or len(fields) < 2 or not fields[0].startswith(('ssh-', 'ecdsa-', 'sk-')):
        raise ValueError('signing public key file must contain exactly one public key')
    key_type, key_data = fields[:2]
    if key_type not in {
            'ssh-ed25519', 'ssh-rsa', 'ecdsa-sha2-nistp256',
            'sk-ssh-ed25519@openssh.com', 'sk-ecdsa-sha2-nistp256@openssh.com'}:
        raise ValueError('unsupported SSH signing key type')
    result = subprocess.run(['ssh-keygen', '-lf', str(path)], text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    match = FINGERPRINT.search(result.stdout) if result.returncode == 0 else None
    if match is None:
        raise ValueError('cannot fingerprint signing public key')
    return {'path': path.resolve(), 'type': key_type, 'data': key_data,
            'fingerprint': match.group(0)}


def validate_identity(name, email):
    if not isinstance(name, str) or not name.strip() or any(c in name for c in '\r\n\0<>'):
        raise ValueError('committer name must be a non-empty single-line name')
    if (not isinstance(email, str) or not re.fullmatch(r'[^\s<>@]+@[^\s<>@]+', email)
            or any(c in email for c in '\r\n\0')):
        raise ValueError('committer email must be a single email address')
    return name, email


def validate_allowed_signers(path, *, public_key, fingerprint, identity):
    """Require a one-key, one-principal Git namespace trust file."""
    key = public_key_info(public_key)
    if key['fingerprint'] != fingerprint:
        raise ValueError('signing public key fingerprint differs from approved fingerprint')
    lines = [line for line in Path(path).read_text(encoding='utf-8').splitlines()
             if line.strip() and not line.lstrip().startswith('#')]
    if len(lines) != 1:
        raise ValueError('allowed-signers file must contain exactly one trusted signer')
    try:
        fields = shlex.split(lines[0], comments=False, posix=True)
    except ValueError as error:
        raise ValueError('malformed allowed-signers entry') from error
    if fields != [identity, 'namespaces=git', key['type'], key['data']]:
        raise ValueError('allowed-signers entry must exactly match approved identity and key')
    return key


def verify_signature(repo, revision, *, public_key, fingerprint, identity, allowed_signers):
    validate_allowed_signers(allowed_signers, public_key=public_key,
                             fingerprint=fingerprint, identity=identity)
    command = ['git', '-C', str(repo), '-c', 'core.hooksPath=/dev/null',
               '-c', 'gpg.format=ssh',
               '-c', f'gpg.ssh.allowedSignersFile={Path(allowed_signers).resolve()}',
               '-c', 'commit.gpgSign=false', 'verify-commit', publish.oid(revision)]
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            env=publish.environment())
    if result.returncode:
        raise ValueError('generated commit signature failed pinned SSH verification')


def generated_layer_commits(repo, plan, generated):
    """Return generated layer commits in plan order; upstream parents are excluded."""
    cursor = publish.oid(generated)
    revisions = []
    for layer in reversed(plan['layers']):
        revisions.append(cursor)
        parents = publish.git(repo, 'show', '-s', '--format=%P', cursor).decode().split()
        if len(parents) != 1:
            raise ValueError('generated layer does not have exactly one parent')
        cursor = parents[0]
    if cursor != plan['signal']:
        raise ValueError('generated layer chain does not end at authentic upstream commit')
    return list(reversed(revisions))


def commit_identities(repo, revision):
    raw = publish.git(repo, 'cat-file', 'commit', publish.oid(revision))
    headers = raw.split(b'\n\n', 1)[0].decode('utf-8')
    result = {}
    for line in headers.splitlines():
        match = IDENTITY.fullmatch(line)
        if match:
            result[match.group(1)] = {'name': match.group(2), 'email': match.group(3)}
    if set(result) != {'author', 'committer'}:
        raise ValueError('generated commit identity header is malformed')
    return result


def verify_generated_signatures(repo, plan, generated, integration, *, public_key,
                                fingerprint, identity, allowed_signers,
                                expected_authors=None, committer_name=None):
    revisions = generated_layer_commits(repo, plan, generated)
    revisions.append(publish.oid(integration))
    for index, revision in enumerate(revisions):
        identities = commit_identities(repo, revision)
        if expected_authors is not None and identities['author'] != expected_authors[index]:
            raise ValueError('generated commit author differs from approved signing request')
        if committer_name is not None and identities['committer'] != {
                'name': committer_name, 'email': identity}:
            raise ValueError('generated committer differs from approved signing request')
        verify_signature(repo, revision, public_key=public_key, fingerprint=fingerprint,
                         identity=identity, allowed_signers=allowed_signers)
    return revisions
