import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / 'source'))
import commit_messages
import publish
import signatures
import sign_publication


class PublicationSigningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR'))
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        publish.git(self.repo, 'init', '--template=', '-q')
        self.empty = publish.git(self.repo, 'mktree', data=b'').decode().strip()
        self.signal = publish.commit(self.repo, self.empty, [], 'fixture upstream\n')
        self.signal_raw = publish.git(self.repo, 'cat-file', 'commit', self.signal)
        self.old = publish.commit(self.repo, self.empty, [], 'fixture old application\n')
        publish.git(self.repo, 'update-ref', 'HEAD', self.signal)
        first_blob = publish.git(self.repo, 'hash-object', '-w', '--stdin', data=b'first')
        first_blob = first_blob.decode().strip()
        second_blob = publish.git(self.repo, 'hash-object', '-w', '--stdin', data=b'second')
        second_blob = second_blob.decode().strip()
        self.first_tree = publish.git(
            self.repo, 'mktree', data=f'100644 blob {first_blob}\tfile.txt\n'.encode()).decode().strip()
        self.final_tree = publish.git(
            self.repo, 'mktree', data=f'100644 blob {second_blob}\tfile.txt\n'.encode()).decode().strip()
        self.timestamp = '2026-11-09T16:30:00Z'
        self.plan = {
            'schema': 2, 'publication_timestamp': self.timestamp,
            'signal': self.signal, 'old_main': self.old,
            'reconstructed_tree': self.first_tree, 'final_tree': self.final_tree,
            'layers': [
                {'name': 'First feature', 'before_tree': self.empty,
                 'after_tree': self.first_tree, 'kind': 'feature',
                 'provenance': 'Fixture author one'},
                {'name': 'Second feature', 'before_tree': self.first_tree,
                 'after_tree': self.final_tree, 'kind': 'feature',
                 'provenance': 'Fixture author two'},
            ],
        }
        self.plan_path = self.root / 'plan.json'
        self.plan_path.write_text(json.dumps(self.plan, separators=(',', ':')))
        self.plan_digest = hashlib.sha256(self.plan_path.read_bytes()).hexdigest()
        self.messages = commit_messages.preview(self.plan, self.plan_digest)
        self.messages_path = self.root / 'messages.json'
        self.messages_path.write_text(json.dumps(self.messages, separators=(',', ':')))
        self.messages_digest = hashlib.sha256(self.messages_path.read_bytes()).hexdigest()
        self.unsigned = publish.publish(
            self.repo, self.plan_path, self.plan_digest, self.old,
            messages=self.messages_path, approved_messages_sha256=self.messages_digest)
        for revision in [*self.unsigned['layer_commits'], self.unsigned['integration_commit']]:
            header = publish.git(self.repo, 'cat-file', 'commit', revision).split(b'\n\n', 1)[0]
            self.assertNotIn(b'\ngpgsig ', header)
        self.email = 'operator@example.invalid'
        self.name = 'Publication Operator Fixture'
        self.key_path = self.root / 'fixture-signing-key'
        subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '',
                        '-C', 'disposable test fixture', '-f', str(self.key_path)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        self.pub_path = Path(str(self.key_path) + '.pub')
        self.key = signatures.public_key_info(self.pub_path)
        self.allowed_signers = self.root / 'allowed_signers'
        pub_fields = self.pub_path.read_text().split()
        self.allowed_signers.write_text(
            f'{self.email} namespaces="git" {pub_fields[0]} {pub_fields[1]}\n')
        self.agent_socket = self.root / 'ssh-agent.sock'
        agent = subprocess.run(['ssh-agent', '-s', '-a', str(self.agent_socket)],
                               text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               check=True).stdout
        pid = re.search(r'SSH_AGENT_PID=([0-9]+)', agent).group(1)
        self.agent_env = dict(os.environ, SSH_AUTH_SOCK=str(self.agent_socket), SSH_AGENT_PID=pid)
        subprocess.run(['ssh-add', '-q', str(self.key_path)], env=self.agent_env,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        self.addCleanup(lambda: subprocess.run(
            ['ssh-agent', '-k'], env=self.agent_env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False))
        self.request_path = self.root / 'signing-request.json'
        with patch.dict(os.environ, self.agent_env):
            self.request_result = sign_publication.prepare(
                self.repo, plan_path=self.plan_path, plan_digest=self.plan_digest,
                messages_path=self.messages_path, messages_digest=self.messages_digest,
                current_main=self.old, source=self.unsigned['source_commit'],
                integration=self.unsigned['integration_commit'], public_key=self.pub_path,
                committer_name=self.name, committer_email=self.email,
                output=self.request_path)

    def signed(self):
        with patch.dict(os.environ, self.agent_env):
            return sign_publication.sign(
                self.repo, plan_path=self.plan_path, plan_digest=self.plan_digest,
                messages_path=self.messages_path, messages_digest=self.messages_digest,
                current_main=self.old, source=self.unsigned['source_commit'],
                integration=self.unsigned['integration_commit'],
                signing_request=self.request_path,
                approval_digest=self.request_result['signing_request_sha256'],
                public_key=self.pub_path, allowed_signers=self.allowed_signers)

    def test_signs_every_generated_layer_and_integration_without_rewriting_upstream(self):
        refs_before = publish.git(self.repo, 'for-each-ref', '--format=%(refname) %(objectname)')
        signed = self.signed()
        refs_after = publish.git(self.repo, 'for-each-ref', '--format=%(refname) %(objectname)')
        self.assertEqual(refs_after, refs_before)
        self.assertNotEqual(signed['source_commit'], self.unsigned['source_commit'])
        self.assertNotEqual(signed['integration_commit'], self.unsigned['integration_commit'])
        self.assertEqual(len(signed['layer_commits']), 2)
        self.assertEqual(publish.tree(self.repo, signed['source_commit']), self.final_tree)
        self.assertEqual(publish.tree(self.repo, signed['integration_commit']), self.final_tree)
        self.assertEqual(publish.git(self.repo, 'show', '-s', '--format=%P',
                                     signed['integration_commit']).decode().split(),
                         [self.old, signed['source_commit']])
        self.assertEqual(publish.git(self.repo, 'show', '-s', '--format=%P',
                                     signed['layer_commits'][0]).decode().strip(), self.signal)
        for revision in [*signed['layer_commits'], signed['integration_commit']]:
            dates = publish.git(self.repo, 'show', '-s', '--format=%aI%x00%cI', revision)
            self.assertEqual(dates.decode().strip().split('\x00'), [self.timestamp, self.timestamp])
        self.assertEqual(signatures.verify_generated_signatures(
            self.repo, self.plan, signed['source_commit'], signed['integration_commit'],
            public_key=self.pub_path, fingerprint=self.key['fingerprint'],
            identity=self.email, allowed_signers=self.allowed_signers),
            [*signed['layer_commits'], signed['integration_commit']])
        self.assertEqual(publish.git(self.repo, 'cat-file', 'commit', self.signal), self.signal_raw)
        verifier = Path(__file__).parents[1] / 'source/verify_candidate.py'
        def verify_with_request(request_path, request_digest):
            return subprocess.run([
                sys.executable, str(verifier), '--repo', str(self.repo),
                '--plan', str(self.plan_path), '--expected-plan-sha256', self.plan_digest,
                '--current-main', self.old, '--expected-source', signed['source_commit'],
                '--expected-integration', signed['integration_commit'],
                '--messages', str(self.messages_path),
                '--expected-messages-sha256', self.messages_digest,
                '--signing-public-key', str(self.pub_path),
                '--expected-signer-fingerprint', self.key['fingerprint'],
                '--signing-identity-email', self.email,
                '--allowed-signers', str(self.allowed_signers),
                '--signing-request', str(request_path),
                '--expected-signing-request-sha256', request_digest,
            ], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        output = verify_with_request(
            self.request_path, self.request_result['signing_request_sha256'])
        self.assertEqual(output.returncode, 0, output.stderr)
        result = json.loads(output.stdout)
        self.assertEqual(result['status'], 'PASS')
        self.assertEqual(result['signed_generated_commits'], 3)

        substituted_request = json.loads(self.request_path.read_text())
        substituted_request['unsigned_source'] = self.signal
        substituted_request['unsigned_integration'] = self.signal
        substituted_path = self.root / 'substituted-signing-request.json'
        substituted_raw = (json.dumps(substituted_request, sort_keys=True,
                                      separators=(',', ':')) + '\n')
        substituted_path.write_text(substituted_raw)
        substituted_digest = hashlib.sha256(substituted_raw.encode()).hexdigest()
        substituted_output = verify_with_request(substituted_path, substituted_digest)
        self.assertNotEqual(substituted_output.returncode, 0,
                            'verifier accepted a rehashed request for unrelated upstream OIDs')

        other_plan = json.loads(self.plan_path.read_text())
        other_plan['layers'][0]['name'] = 'Different valid publication layer'
        other_plan_path = self.root / 'other-plan.json'
        other_plan_path.write_text(json.dumps(other_plan, separators=(',', ':')))
        other_plan_digest = hashlib.sha256(other_plan_path.read_bytes()).hexdigest()
        other_messages = commit_messages.preview(other_plan, other_plan_digest)
        other_messages_path = self.root / 'other-messages.json'
        other_messages_path.write_text(json.dumps(other_messages, separators=(',', ':')))
        other_messages_digest = hashlib.sha256(other_messages_path.read_bytes()).hexdigest()
        other_unsigned = publish.publish(
            self.repo, other_plan_path, other_plan_digest, self.old,
            messages=other_messages_path, approved_messages_sha256=other_messages_digest)
        unrelated_request = json.loads(self.request_path.read_text())
        unrelated_request['unsigned_source'] = other_unsigned['source_commit']
        unrelated_request['unsigned_integration'] = other_unsigned['integration_commit']
        unrelated_path = self.root / 'unrelated-valid-signing-request.json'
        unrelated_raw = json.dumps(unrelated_request, sort_keys=True, separators=(',', ':')) + '\n'
        unrelated_path.write_text(unrelated_raw)
        unrelated_digest = hashlib.sha256(unrelated_raw.encode()).hexdigest()
        unrelated_output = verify_with_request(unrelated_path, unrelated_digest)
        self.assertNotEqual(unrelated_output.returncode, 0,
                            'verifier accepted a rehashed request for a valid graph from a different plan')

    def test_signing_request_digest_and_wrong_public_key_fail_closed(self):
        with self.assertRaisesRegex(ValueError, 'external operator approval'):
            sign_publication.sign(
                self.repo, plan_path=self.plan_path, plan_digest=self.plan_digest,
                messages_path=self.messages_path, messages_digest=self.messages_digest,
                current_main=self.old, source=self.unsigned['source_commit'],
                integration=self.unsigned['integration_commit'],
                signing_request=self.request_path, approval_digest='0' * 64,
                public_key=self.pub_path, allowed_signers=self.allowed_signers)
        other_path = self.root / 'other-key'
        subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(other_path)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        with self.assertRaisesRegex(ValueError, 'fingerprint differs'):
            sign_publication.sign(
                self.repo, plan_path=self.plan_path, plan_digest=self.plan_digest,
                messages_path=self.messages_path, messages_digest=self.messages_digest,
                current_main=self.old, source=self.unsigned['source_commit'],
                integration=self.unsigned['integration_commit'],
                signing_request=self.request_path,
                approval_digest=self.request_result['signing_request_sha256'],
                public_key=Path(str(other_path) + '.pub'),
                allowed_signers=self.allowed_signers)
        signed = self.signed()
        other_info = signatures.public_key_info(Path(str(other_path) + '.pub'))
        other_allowed = self.root / 'other_allowed_signers'
        other_fields = Path(str(other_path) + '.pub').read_text().split()
        other_allowed.write_text(
            f'{self.email} namespaces="git" {other_fields[0]} {other_fields[1]}\n')
        with self.assertRaisesRegex(ValueError, 'signature failed'):
            signatures.verify_generated_signatures(
                self.repo, self.plan, signed['source_commit'], signed['integration_commit'],
                public_key=Path(str(other_path) + '.pub'),
                fingerprint=other_info['fingerprint'], identity=self.email,
                allowed_signers=other_allowed)

    def test_cli_prepares_request_and_signs_only_that_digest(self):
        tool = Path(__file__).parents[1] / 'source/sign_publication.py'
        request_path = self.root / 'cli-request.json'
        common = [
            '--repo', str(self.repo), '--plan', str(self.plan_path),
            '--approved-plan-sha256', self.plan_digest,
            '--messages', str(self.messages_path),
            '--approved-messages-sha256', self.messages_digest,
            '--current-main', self.old, '--source', self.unsigned['source_commit'],
            '--integration', self.unsigned['integration_commit'],
        ]
        prepared = subprocess.run([
            sys.executable, str(tool), 'prepare', *common,
            '--signing-public-key', str(self.pub_path), '--committer-name', self.name,
            '--committer-email', self.email, '--output', str(request_path),
        ], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        result = json.loads(prepared.stdout)
        self.assertIn('no signing key invoked', result['status'])
        signed = subprocess.run([
            sys.executable, str(tool), 'sign', *common,
            '--signing-request', str(request_path),
            '--approved-signing-request-sha256', result['signing_request_sha256'],
            '--signing-public-key', str(self.pub_path),
            '--allowed-signers', str(self.allowed_signers),
        ], env=self.agent_env, text=True, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, check=True)
        signing_result = json.loads(signed.stdout)
        self.assertIn('no refs changed', signing_result['status'])
        request = json.loads(request_path.read_text())
        signatures.verify_generated_signatures(
            self.repo, self.plan, signing_result['source_commit'],
            signing_result['integration_commit'], public_key=self.pub_path,
            fingerprint=self.key['fingerprint'], identity=self.email,
            allowed_signers=self.allowed_signers,
            expected_authors=request['authors'],
            committer_name=self.name)

    def test_unsigned_layer_in_mixed_generated_chain_is_rejected(self):
        signed = self.signed()
        second_layer = self.plan['layers'][1]
        unsigned_second = publish.commit(
            self.repo, second_layer['after_tree'], [signed['layer_commits'][0]],
            commit_messages.message({k: self.messages['layers'][1][k] for k in ('subject', 'body')}),
            publication_timestamp=self.timestamp)
        unsigned_integration = publish.commit(
            self.repo, self.final_tree, [self.old, unsigned_second],
            commit_messages.message(self.messages['integration']),
            publication_timestamp=self.timestamp)
        publish.verify(self.repo, self.plan, unsigned_second, unsigned_integration,
                       self.old, messages=self.messages, publication_timestamp=self.timestamp)
        with self.assertRaisesRegex(ValueError, 'signature failed'):
            signatures.verify_generated_signatures(
                self.repo, self.plan, unsigned_second, unsigned_integration,
                public_key=self.pub_path, fingerprint=self.key['fingerprint'],
                identity=self.email, allowed_signers=self.allowed_signers)

    def test_tampered_message_tree_parent_or_date_fails_graph_verification(self):
        signed = self.signed()
        original_message = commit_messages.message(
            {k: self.messages['layers'][1][k] for k in ('subject', 'body')})
        changed_message = publish.commit(
            self.repo, self.final_tree, [self.plan['signal']], original_message + 'tampered\n',
            publication_timestamp=self.timestamp)
        changed_message_integration = publish.commit(
            self.repo, self.final_tree, [self.old, changed_message],
            commit_messages.message(self.messages['integration']),
            publication_timestamp=self.timestamp)
        with self.assertRaisesRegex(ValueError, 'layer wording differs'):
            publish.verify(self.repo, self.plan, changed_message, changed_message_integration,
                           self.old, messages=self.messages, publication_timestamp=self.timestamp)
        changed_tree_integration = publish.commit(
            self.repo, self.first_tree, [self.old, signed['source_commit']],
            commit_messages.message(self.messages['integration']),
            publication_timestamp=self.timestamp)
        with self.assertRaisesRegex(ValueError, 'complete final tree differs'):
            publish.verify(self.repo, self.plan, signed['source_commit'], changed_tree_integration,
                           self.old, messages=self.messages, publication_timestamp=self.timestamp)
        with self.assertRaisesRegex(ValueError, 'integration parents differ'):
            publish.verify(self.repo, self.plan, signed['source_commit'], signed['source_commit'],
                           self.old, messages=self.messages, publication_timestamp=self.timestamp)
        altered = dict(self.plan, publication_timestamp='2026-11-09T16:30:01Z')
        with self.assertRaisesRegex(ValueError, 'dates differ'):
            publish.verify(self.repo, altered, signed['source_commit'], signed['integration_commit'],
                           self.old, publication_timestamp=altered['publication_timestamp'])


if __name__ == '__main__':
    unittest.main()
