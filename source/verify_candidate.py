#!/usr/bin/env python3
"""Read-only candidate audit; use a separately approved verifier revision."""
import argparse
import hashlib
import json
from pathlib import Path
import commit_messages
import publish
import signatures
import sign_publication

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--repo', type=Path, required=True)
parser.add_argument('--plan', type=Path, required=True)
parser.add_argument('--expected-plan-sha256', required=True)
parser.add_argument('--current-main', required=True)
parser.add_argument('--expected-source', required=True)
parser.add_argument('--expected-integration', required=True)
parser.add_argument('--messages', type=Path)
parser.add_argument('--expected-messages-sha256')
parser.add_argument('--signing-public-key', type=Path)
parser.add_argument('--expected-signer-fingerprint')
parser.add_argument('--signing-identity-email')
parser.add_argument('--allowed-signers', type=Path)
parser.add_argument('--signing-request', type=Path)
parser.add_argument('--expected-signing-request-sha256')
args = parser.parse_args()
raw = args.plan.read_bytes()
if hashlib.sha256(raw).hexdigest() != args.expected_plan_sha256:
    raise ValueError('plan approval digest differs')
plan = publish.load_plan(raw)
signature_args = (args.signing_public_key, args.expected_signer_fingerprint,
                  args.signing_identity_email, args.allowed_signers,
                  args.signing_request, args.expected_signing_request_sha256)
if any(value is not None for value in signature_args):
    if any(value is None for value in signature_args):
        parser.error('signed verification requires public key, fingerprint, identity email, allowed-signers, and approved signing request')
    if plan['schema'] != 2:
        parser.error('signed verification requires a timestamp-bound schema 2 plan')
    if args.messages is None or args.expected_messages_sha256 is None:
        parser.error('signed verification requires the approved commit messages and digest')
    message_raw = args.messages.read_bytes()
    if hashlib.sha256(message_raw).hexdigest() != args.expected_messages_sha256:
        parser.error('commit messages differ from external approval')
    wording = commit_messages.validate(
        json.loads(message_raw, object_pairs_hook=publish.unique_object), plan,
        args.expected_plan_sha256)
    request = sign_publication.load_approved_request(
        args.signing_request, args.expected_signing_request_sha256)
    sign_publication.validate_request_binding(
        request, plan_digest=args.expected_plan_sha256,
        messages_digest=args.expected_messages_sha256, current_main=args.current_main,
        publication_timestamp=plan['publication_timestamp'],
        fingerprint=args.expected_signer_fingerprint, identity=args.signing_identity_email)
    if len(request['authors']) != len(plan['layers']) + 1:
        parser.error('signing request author inventory differs from generated layer count')
    publish.verify(args.repo, plan, args.expected_source, args.expected_integration,
                   args.current_main, messages=wording,
                   publication_timestamp=plan['publication_timestamp'])
    signed = signatures.verify_generated_signatures(
        args.repo, plan, args.expected_source, args.expected_integration,
        public_key=args.signing_public_key, fingerprint=args.expected_signer_fingerprint,
        identity=args.signing_identity_email, allowed_signers=args.allowed_signers,
        expected_authors=request['authors'], committer_name=request['committer_name'])
    signature_result = {'signatures':'PASS','signer_fingerprint':args.expected_signer_fingerprint,
                        'signed_generated_commits':len(signed),
                        'signing_request_sha256':args.expected_signing_request_sha256}
    scope = 'Complete source graph/tree/date/message and pinned SSH-signature verification; no build, production authorization or signed attestation'
else:
    if (args.messages is None) != (args.expected_messages_sha256 is None):
        parser.error('commit messages and expected digest must be supplied together')
    wording = None
    if args.messages is not None:
        message_raw = args.messages.read_bytes()
        if hashlib.sha256(message_raw).hexdigest() != args.expected_messages_sha256:
            parser.error('commit messages differ from external approval')
        wording = commit_messages.validate(
            json.loads(message_raw, object_pairs_hook=publish.unique_object), plan,
            args.expected_plan_sha256)
    publish.verify(args.repo, plan, args.expected_source, args.expected_integration,
                   args.current_main, messages=wording)
    signature_result = {'signatures':'NOT CHECKED'}
    scope = 'Complete source graph/tree identity only; no build, production authorization or signed attestation'
print(json.dumps({'status':'PASS','scope':scope,
                  'source':args.expected_source,'integration':args.expected_integration,'base':args.current_main,
                  'plan_sha256':args.expected_plan_sha256,**signature_result},indent=2))
