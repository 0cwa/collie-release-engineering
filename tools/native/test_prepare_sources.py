import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import webrtc_deps

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
spec = importlib.util.spec_from_file_location('prepare_sources', HERE / 'prepare_sources.py')
subject = importlib.util.module_from_spec(spec)
spec.loader.exec_module(subject)

class SourcePreparationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='native-prepare-test-')
        self.root = Path(self.temp.name)
        self.patches = self.root / 'patches'
        self.patches.mkdir()
        self.lock = json.loads((HERE / 'native-lock.json').read_text())
        self.sources = {}
        for name in ('libsignal', 'ringrtc', 'webrtc'):
            repo = self.root / ('input-' + name)
            repo.mkdir()
            self.git(repo, 'init', '--quiet', '--template=')
            (repo / 'sample.txt').write_text('base\n')
            if name == 'ringrtc':
                (repo / 'config').mkdir()
                (repo / 'config/webrtc.gclient.common').write_text('solutions = []\n')
                (repo / 'config/webrtc.gclient.android').write_text('# android\n')
            if name == 'webrtc':
                fields = ('webrtc_depot_tools_revision', 'webrtc_linux_jdk_cipd_instance', 'webrtc_siso_revision', 'webrtc_gn_android_ndk_version', 'opus_model')
                deps = b'\n'.join(self.lock['toolchain_reference'][key].encode() for key in fields) + b'\n' + webrtc_deps.TEST_MEDIA_HOOK
                (repo / 'DEPS').write_bytes(deps)
                self.lock['toolchain_reference']['webrtc_deps_sha256'] = hashlib.sha256(deps).hexdigest()
            self.git(repo, 'add', '.')
            self.git(repo, 'commit', '--quiet', '-m', 'base')
            self.lock['source_provenance'][name] = self.git(repo, 'rev-parse', 'HEAD').strip()
            self.sources[name] = repo
            count = 3 if name == 'webrtc' else 1
            for index in range(count):
                with (repo / 'sample.txt').open('a') as stream:
                    stream.write(f'{name} change {index}\n')
                self.git(repo, 'add', 'sample.txt')
                self.git(repo, 'commit', '--quiet', '-m', f'{name} change {index}')
                if name == 'webrtc':
                    filename = [x['path'] for x in self.lock['source_patches'] if Path(x['path']).name.startswith('webrtc-')][index]
                    (self.root / filename).write_bytes(self.git(repo, 'format-patch', '--stdout', '-1', raw=True))
            if name != 'webrtc':
                filename = next(x['path'] for x in self.lock['source_patches'] if Path(x['path']).name.startswith(name + '-'))
                (self.root / filename).write_bytes(self.git(repo, 'format-patch', '--stdout', '-1', raw=True))
        for item in self.lock['source_patches']:
            item['sha256'] = hashlib.sha256((self.root / item['path']).read_bytes()).hexdigest()
        self.lock_file = self.root / 'native-lock.json'
        self.lock_file.write_text(json.dumps(self.lock))

    def tearDown(self):
        self.temp.cleanup()

    def git(self, repo, *args, raw=False):
        result = subprocess.check_output(['git', '-C', str(repo), '-c', 'user.name=Original Author', '-c', 'user.email=author@example.invalid', '-c', 'commit.gpgsign=false', *args], stderr=subprocess.DEVNULL)
        return result if raw else result.decode()

    def test_prepares_same_trees_twice_and_preserves_mailbox_authors(self):
        a = subject.prepare(self.lock_file, self.sources, self.root / 'prepared-a')
        b = subject.prepare(self.lock_file, self.sources, self.root / 'prepared-b')
        for name in self.sources:
            self.assertEqual(a['sources'][name]['prepared_tree'], b['sources'][name]['prepared_tree'])
        for name in ('libsignal', 'ringrtc'):
            author = self.git(self.root / 'prepared-a' / name, 'show', '-s', '--format=%an <%ae>', 'HEAD').strip()
            self.assertEqual('Original Author <author@example.invalid>', author)
        self.assertEqual('source preparation only; native build and runtime unverified', a['scope'])

    def test_verifier_replays_patches_and_rejects_forged_source_tree(self):
        output = self.root / 'prepared'
        subject.prepare(self.lock_file, self.sources, output)
        proof = subject.verify_prepared(self.lock_file, output)
        self.assertEqual('independently replayed source trees only; build and runtime unverified', proof['scope'])
        repo = output / 'libsignal'
        (repo / 'sample.txt').write_text('malicious source\n')
        self.git(repo, 'add', 'sample.txt')
        self.git(repo, 'commit', '--quiet', '-m', 'unexpected change')
        # A caller-controlled source receipt cannot turn a different tree into the pinned one.
        (output / 'source-receipt.json').write_text('{}')
        with self.assertRaises(subject.Failure):
            subject.verify_prepared(self.lock_file, output)

    def test_verifier_rejects_unstaged_webrtc_change(self):
        output = self.root / 'prepared'
        subject.prepare(self.lock_file, self.sources, output)
        with (output / 'webrtc' / 'sample.txt').open('a') as stream:
            stream.write('unexpected change\n')
        with self.assertRaises(subject.Failure):
            subject.verify_prepared(self.lock_file, output)

    def test_unknown_commit_fails_before_creating_output(self):
        self.lock['source_provenance']['webrtc'] = '0' * 40
        self.lock_file.write_text(json.dumps(self.lock))
        with self.assertRaises(subject.Failure):
            subject.prepare(self.lock_file, self.sources, self.root / 'prepared')
        self.assertFalse((self.root / 'prepared').exists())

    def test_patch_tampering_fails_before_creating_output(self):
        patch = self.root / self.lock['source_patches'][0]['path']
        patch.write_bytes(patch.read_bytes() + b'bad')
        with self.assertRaises(subject.stage_artifacts.Failure):
            subject.prepare(self.lock_file, self.sources, self.root / 'prepared')
        self.assertFalse((self.root / 'prepared').exists())

    def test_failure_never_deletes_existing_output(self):
        existing = self.root / 'prepared'
        existing.mkdir()
        sentinel = existing / 'sentinel'
        sentinel.write_text('keep')
        with self.assertRaises(subject.Failure):
            subject.prepare(self.lock_file, self.sources, existing)
        self.assertEqual('keep', sentinel.read_text())

if __name__ == '__main__':
    unittest.main()
