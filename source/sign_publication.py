#!/usr/bin/env python3
"""Create an explicitly approved, local SSH-signed copy of a generated G/I candidate."""
import argparse
import hashlib
import json
import re
from pathlib import Path

import commit_messages
import publish
import signatures


REQUEST_FIELDS = {
    'schema', 'purpose', 'publication_plan_sha256', 'messages_sha256', 'current_main',
    'unsigned_source', 'unsigned_integration', 'publication_timestamp',
    'signer_fingerprint', 'committer_name', 'committer_email', 'authors',
}
IDENTITY = re.compile(r'^(author|committer) (.*) <([^<>]+)> [0-9]+ [+-][0-9]{4}$')


def canonical_json(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n').encode()


def load_unique_json(raw):
    value = json.loads(raw, object_pairs_hook=publish.unique_object)
    if canonical_json(value) != raw:
        raise ValueError('signing request must use canonical JSON')
    return value


def load_approved_request(path, digest):
    raw = Path(path).read_bytes()
    if hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError('signing request differs from external operator approval')
    request = load_unique_json(raw)
    if (set(request) != REQUEST_FIELDS or type(request.get('schema')) is not int
            or request.get('schema') != 1
            or request.get('purpose') != 'collie-source-publication-ssh-signing'):
        raise ValueError('unknown or missing signing-request fields')
    return request


def publication_inputs(repo, plan_path, plan_digest, messages_path, messages_digest,
                       current_main, source, integration):
    raw_plan = Path(plan_path).read_bytes()
    if hashlib.sha256(raw_plan).hexdigest() != plan_digest:
        raise ValueError('publication plan differs from external approval')
    plan = publish.load_plan(raw_plan)
    if plan['schema'] != 2:
        raise ValueError('only timestamp-bound schema 2 publications may be signed')
    raw_messages = Path(messages_path).read_bytes()
    if hashlib.sha256(raw_messages).hexdigest() != messages_digest:
        raise ValueError('commit messages differ from external approval')
    wording = commit_messages.validate(
        json.loads(raw_messages, object_pairs_hook=publish.unique_object), plan, plan_digest)
    publish.verify(repo, plan, source, integration, current_main, messages=wording,
                   publication_timestamp=plan['publication_timestamp'])
    layers = signatures.generated_layer_commits(repo, plan, source)
    if len(layers) != len(plan['layers']):
        raise ValueError('generated layer count differs from approved plan')
    for revision in [*layers, publish.oid(integration)]:
        raw_commit = publish.git(repo, 'cat-file', 'commit', revision)
        headers = raw_commit.split(b'\n\n', 1)[0]
        if b'\ngpgsig ' in headers or b'\ngpgsig-sha256 ' in headers:
            raise ValueError('signing input must be the verified unsigned candidate')
    return plan, wording, layers


def request_data(*, plan_digest, messages_digest, current_main, source, integration,
                 timestamp, fingerprint, committer_name, committer_email, authors):
    name, email = signatures.validate_identity(committer_name, committer_email)
    return {
        'schema': 1,
        'purpose': 'collie-source-publication-ssh-signing',
        'publication_plan_sha256': plan_digest,
        'messages_sha256': messages_digest,
        'current_main': publish.oid(current_main),
        'unsigned_source': publish.oid(source),
        'unsigned_integration': publish.oid(integration),
        'publication_timestamp': timestamp,
        'signer_fingerprint': fingerprint,
        'committer_name': name,
        'committer_email': email,
        'authors': authors,
    }


def candidate_authors(repo, plan, layers, integration):
    revisions = [*layers, publish.oid(integration)]
    return [signatures.commit_identities(repo, revision)['author'] for revision in revisions]


def prepare(repo, *, plan_path, plan_digest, messages_path, messages_digest,
            current_main, source, integration, public_key, committer_name,
            committer_email, output):
    plan, _wording, _layers = publication_inputs(
        repo, plan_path, plan_digest, messages_path, messages_digest,
        current_main, source, integration)
    authors = candidate_authors(repo, plan, _layers, integration)
    key = signatures.public_key_info(public_key)
    request = request_data(
        plan_digest=plan_digest, messages_digest=messages_digest, current_main=current_main,
        source=source, integration=integration,
        timestamp=plan['publication_timestamp'], fingerprint=key['fingerprint'],
        committer_name=committer_name, committer_email=committer_email, authors=authors)
    output = Path(output)
    with output.open('xb') as stream:
        stream.write(canonical_json(request))
    return {'signing_request': str(output),
            'signing_request_sha256': hashlib.sha256(canonical_json(request)).hexdigest(),
            'signer_fingerprint': key['fingerprint'],
            'unsigned_source': request['unsigned_source'],
            'unsigned_integration': request['unsigned_integration'],
            'status': 'REQUEST PREPARED; no signing key invoked'}


def commit_headers(repo, revision):
    raw = publish.git(repo, 'cat-file', 'commit', publish.oid(revision))
    headers, message = raw.split(b'\n\n', 1)
    if b'\ngpgsig ' in headers or b'\ngpgsig-sha256 ' in headers:
        raise ValueError('signing input must be unsigned')
    values = {}
    for line in headers.decode('utf-8').splitlines():
        match = IDENTITY.fullmatch(line)
        if match:
            values[match.group(1)] = (match.group(2), match.group(3))
    if set(values) != {'author', 'committer'}:
        raise ValueError('cannot safely parse original publication identity')
    return message.decode('utf-8'), values['author']


def sign(repo, *, plan_path, plan_digest, messages_path, messages_digest,
         current_main, source, integration, signing_request, approval_digest,
         public_key, allowed_signers):
    request = load_approved_request(signing_request, approval_digest)
    plan, wording, unsigned_layers = publication_inputs(
        repo, plan_path, plan_digest, messages_path, messages_digest,
        current_main, source, integration)
    key = signatures.validate_allowed_signers(
        allowed_signers, public_key=public_key,
        fingerprint=request['signer_fingerprint'], identity=request['committer_email'])
    expected = request_data(
        plan_digest=plan_digest, messages_digest=messages_digest, current_main=current_main,
        source=source, integration=integration,
        timestamp=plan['publication_timestamp'], fingerprint=key['fingerprint'],
        committer_name=request['committer_name'], committer_email=request['committer_email'],
        authors=candidate_authors(repo, plan, unsigned_layers, integration))
    if request != expected:
        raise ValueError('signing request does not bind these exact publication inputs and signer')

    signed_layers = []
    signed_parent = plan['signal']
    for unsigned_layer in unsigned_layers:
        raw_commit = publish.git(repo, 'cat-file', 'commit', unsigned_layer)
        headers, _ = raw_commit.split(b'\n\n', 1)
        tree_line = next((line for line in headers.splitlines() if line.startswith(b'tree ')), None)
        parents = [line[7:].decode() for line in headers.splitlines() if line.startswith(b'parent ')]
        expected_unsigned_parent = (plan['signal'] if not signed_layers
                                    else unsigned_layers[len(signed_layers) - 1])
        if tree_line is None or parents != [expected_unsigned_parent]:
            raise ValueError('unsigned generated layer graph changed during signing')
        message, author = commit_headers(repo, unsigned_layer)
        signed = publish.commit(
            repo, tree_line[5:].decode(), [signed_parent], message,
            publication_timestamp=plan['publication_timestamp'],
            signing_key=key['path'], author_name=author[0], author_email=author[1],
            committer_name=request['committer_name'], committer_email=request['committer_email'])
        signed_layers.append(signed)
        signed_parent = signed

    unsigned_integration = publish.git(repo, 'cat-file', 'commit', integration)
    headers, _ = unsigned_integration.split(b'\n\n', 1)
    tree_line = next((line for line in headers.splitlines() if line.startswith(b'tree ')), None)
    parents = [line[7:].decode() for line in headers.splitlines() if line.startswith(b'parent ')]
    if tree_line is None or parents != [plan['old_main'], publish.oid(source)]:
        raise ValueError('unsigned integration commit graph changed during signing')
    message, author = commit_headers(repo, integration)
    signed_integration = publish.commit(
        repo, tree_line[5:].decode(), [plan['old_main'], signed_parent], message,
        publication_timestamp=plan['publication_timestamp'],
        signing_key=key['path'], author_name=author[0], author_email=author[1],
        committer_name=request['committer_name'], committer_email=request['committer_email'])
    publish.verify(repo, plan, signed_layers[-1], signed_integration, current_main,
                   messages=wording, publication_timestamp=plan['publication_timestamp'])
    signatures.verify_generated_signatures(
        repo, plan, signed_layers[-1], signed_integration, public_key=key['path'],
        fingerprint=key['fingerprint'], identity=request['committer_email'],
        allowed_signers=allowed_signers, expected_authors=request['authors'],
        committer_name=request['committer_name'])
    return {'source_commit': signed_layers[-1], 'layer_commits': signed_layers,
            'integration_commit': signed_integration,
            'unsigned_source': publish.oid(source),
            'unsigned_integration': publish.oid(integration),
            'signer_fingerprint': key['fingerprint'],
            'signing_request_sha256': approval_digest,
            'plan_sha256': plan_digest, 'messages_sha256': messages_digest,
            'status': 'SIGNED CANDIDATE; no refs changed'}


def validate_request_binding(request, *, plan_digest, messages_digest, current_main,
                             publication_timestamp, fingerprint, identity):
    if (request['publication_plan_sha256'] != plan_digest
            or request['messages_sha256'] != messages_digest
            or request['current_main'] != publish.oid(current_main)
            or request['publication_timestamp'] != publication_timestamp
            or request['signer_fingerprint'] != fingerprint
            or request['committer_email'] != identity):
        raise ValueError('signing request does not bind this plan, base, messages, timestamp, or signer')
    publish.oid(request['unsigned_source'])
    publish.oid(request['unsigned_integration'])
    signatures.validate_identity(request['committer_name'], request['committer_email'])
    if (not isinstance(request['authors'], list) or len(request['authors']) < 2
            or any(not isinstance(author, dict) or set(author) != {'name', 'email'}
                   or not isinstance(author['name'], str) or not isinstance(author['email'], str)
                   for author in request['authors'])):
        raise ValueError('signing request author inventory is malformed')
    return request


def add_common(parser):
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--approved-plan-sha256', required=True)
    parser.add_argument('--messages', type=Path, required=True)
    parser.add_argument('--approved-messages-sha256', required=True)
    parser.add_argument('--current-main', required=True)
    parser.add_argument('--source', required=True, help='verified unsigned G candidate')
    parser.add_argument('--integration', required=True, help='verified unsigned I candidate')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest='action', required=True)
    prepare_parser = subparsers.add_parser('prepare', help='write a public-only signing approval request')
    add_common(prepare_parser)
    prepare_parser.add_argument('--signing-public-key', type=Path, required=True)
    prepare_parser.add_argument('--committer-name', required=True)
    prepare_parser.add_argument('--committer-email', required=True)
    prepare_parser.add_argument('--output', type=Path, required=True)
    sign_parser = subparsers.add_parser('sign', help='sign the exact externally approved request')
    add_common(sign_parser)
    sign_parser.add_argument('--signing-request', type=Path, required=True)
    sign_parser.add_argument('--approved-signing-request-sha256', required=True)
    sign_parser.add_argument('--signing-public-key', type=Path, required=True)
    sign_parser.add_argument('--allowed-signers', type=Path, required=True)
    args = parser.parse_args()
    common = dict(repo=args.repo, plan_path=args.plan, plan_digest=args.approved_plan_sha256,
                  messages_path=args.messages, messages_digest=args.approved_messages_sha256,
                  current_main=args.current_main, source=args.source, integration=args.integration)
    if args.action == 'prepare':
        result = prepare(**common, public_key=args.signing_public_key,
                         committer_name=args.committer_name,
                         committer_email=args.committer_email, output=args.output)
    else:
        result = sign(**common, signing_request=args.signing_request,
                      approval_digest=args.approved_signing_request_sha256,
                      public_key=args.signing_public_key, allowed_signers=args.allowed_signers)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
