import hashlib
import json
import shutil
import unittest
from pathlib import Path

import libsignal_candidate as subject
import test_prepare_sources as fixtures


def make_candidate(fixture):
    repo = fixture.sources['libsignal']
    fixture.git(repo, 'checkout', '--detach', fixture.lock['source_provenance']['libsignal'])
    (repo / 'rust-toolchain').write_text('1.98.1\n')
    (repo / 'java').mkdir()
    (repo / 'java/build.gradle').write_text('allprojects {\n    version = "0.107.0"\n}\n')
    (repo / '.gitignore').write_text('java/**/build/\ntarget/\n')
    fixture.git(repo, 'add', '.')
    fixture.git(repo, 'commit', '--quiet', '-m', 'new upstream source fixture')
    source = fixture.git(repo, 'rev-parse', 'HEAD').strip()
    old_patch = fixture.root / fixture.lock['source_patches'][0]['path']
    patch = fixture.root / 'patches/libsignal-0.107.0-attributed.mbox'
    shutil.copyfile(old_patch, patch)
    fixture.git(repo, 'apply', '--index', str(patch))
    tree = fixture.git(repo, 'write-tree').strip()
    candidate = {'schema': 1, 'kind': 'collie-libsignal-source-candidate',
                 'status': 'SOURCE_ONLY', 'release_allowed': False,
                 'base_lock_sha256': hashlib.sha256(fixture.lock_file.read_bytes()).hexdigest(),
                 'source_commit': source, 'prepared_tree': tree,
                 'input_version': '0.107.0', 'output_version': '0.107.0-collie.1',
                 'patch': {'path': patch.relative_to(fixture.root).as_posix(),
                           'sha256': hashlib.sha256(patch.read_bytes()).hexdigest()}}
    path = fixture.root / 'candidate.json'
    path.write_text(json.dumps(candidate))
    return path


class CandidatePreparationTest(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.SourcePreparationTest('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.root = self.fixture.root
        self.candidate = make_candidate(self.fixture)
        self.output = self.root / 'candidate-source'

    def prepare(self):
        return subject.prepare(self.candidate, self.fixture.lock_file,
                               self.fixture.sources['libsignal'], self.output)

    def test_libsignal_only_preparation_preserves_original_author_and_binary_lock(self):
        original = self.fixture.lock_file.read_bytes()
        result = self.prepare()
        self.assertFalse(result['release_allowed'])
        self.assertEqual(set(result['sources']), {'libsignal'})
        self.assertEqual(original, self.fixture.lock_file.read_bytes())
        self.assertFalse((self.output / 'ringrtc').exists())
        author = self.fixture.git(self.output / 'libsignal', 'show', '-s', '--format=%an').strip()
        self.assertEqual(author, 'Original Author')
        self.assertEqual(result, subject.verify_prepared(self.candidate, self.fixture.lock_file, self.output))

    def test_candidate_cannot_claim_release_acceptance_or_silently_change_lock(self):
        original = self.candidate.read_text()
        for mutation in ({'release_allowed': True}, {'status': 'ACCEPTED'},
                         {'base_lock_sha256': '0' * 64}, {'binary_sha256': '0' * 64}):
            with self.subTest(mutation=mutation):
                value = json.loads(original)
                value.update(mutation)
                self.candidate.write_text(json.dumps(value))
                with self.assertRaises(subject.Failure):
                    self.prepare()
                self.assertFalse(self.output.exists())

    def test_modified_patch_or_tree_is_rejected_and_existing_output_is_preserved(self):
        value = json.loads(self.candidate.read_text())
        value['prepared_tree'] = '0' * 40
        self.candidate.write_text(json.dumps(value))
        with self.assertRaisesRegex(subject.Failure, 'pinned source tree'):
            self.prepare()
        self.assertFalse(self.output.exists())
        self.output.mkdir()
        (self.output / 'sentinel').write_text('keep')
        with self.assertRaisesRegex(subject.Failure, 'already exists'):
            self.prepare()
        self.assertEqual((self.output / 'sentinel').read_text(), 'keep')

    def test_worktree_tampering_cannot_be_hidden_by_receipt_or_index_flags(self):
        self.prepare()
        repo = self.output / 'libsignal'
        self.fixture.git(repo, 'update-index', '--assume-unchanged', 'sample.txt')
        (repo / 'sample.txt').write_text('changed source')
        (self.output / 'source-receipt.json').write_text('{}')
        with self.assertRaisesRegex(subject.prepare_sources.Failure, 'hidden or unmerged'):
            subject.verify_prepared(self.candidate, self.fixture.lock_file, self.output)

    def test_git_worktree_cannot_hide_inputs_from_the_build_directory(self):
        self.prepare()
        repo = self.output / 'libsignal'
        alternate = repo.with_name(repo.name + ' ')
        shutil.copytree(repo, alternate, ignore=shutil.ignore_patterns('.git'))
        self.fixture.git(repo, 'config', 'core.worktree', str(alternate))
        (repo / 'java/local.properties').write_text('unreviewed build input')
        with self.assertRaisesRegex(subject.Failure, 'Git worktree differs'):
            subject.verify_prepared(self.candidate, self.fixture.lock_file, self.output)

    def test_ignored_java_sdk_and_native_resource_injection_is_rejected(self):
        self.prepare()
        repo = self.output / 'libsignal'
        for name in ('java/client/src/main/java/Extra.java', 'java/local.properties',
                     'java/client/src/main/resources/libsignal_jni.so',
                     'java/shared/resources/libsignal_jni.so'):
            with self.subTest(input=name):
                target = repo / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text('unreviewed build input')
                for after_build in (False, True):
                    with self.assertRaisesRegex(subject.Failure, 'unreviewed input'):
                        subject.verify_prepared(self.candidate, self.fixture.lock_file, self.output,
                                                allow_generated=after_build)
                exclude = repo / '.git/info/exclude'
                exclude.parent.mkdir(exist_ok=True)
                exclude.write_text(name + '\n')
                with self.assertRaisesRegex(subject.Failure, 'local Git exclude'):
                    subject.verify_prepared(self.candidate, self.fixture.lock_file, self.output)
                exclude.unlink()
                target.unlink()

    def test_generated_outputs_are_rejected_before_build_and_allowed_only_after(self):
        self.prepare()
        repo = self.output / 'libsignal'
        for name in ('target/debug/dependency', 'java/client/build/libs/client.jar',
                     'java/android/src/main/jniLibs/arm64-v8a/libsignal_jni.so'):
            with self.subTest(output=name):
                target = repo / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text('generated fixture')
                with self.assertRaisesRegex(subject.Failure, 'unreviewed input'):
                    subject.verify_prepared(self.candidate, self.fixture.lock_file, self.output)
                subject.verify_prepared(self.candidate, self.fixture.lock_file, self.output, allow_generated=True)
                target.unlink()


if __name__ == '__main__':
    unittest.main()
