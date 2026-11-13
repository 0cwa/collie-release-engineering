import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
import warnings
from zipfile import ZipFile, ZipInfo

import compare_reproduction as subject


class ReproductionComparisonTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='native-comparison-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.reference = self.root / 'reference'
        self.reference.mkdir()
        self.outputs = self.root / 'rebuilt'
        self.outputs.mkdir()
        native = Path(__file__).parent
        self.lock = json.loads((native / 'native-lock.json').read_text())
        shutil.copytree(native / 'patches', self.root / 'patches')
        self.rebuilt = {}
        self.references = {}
        self.entries = {}
        for module in self.lock['modules']:
            name = module['name']
            entries = {'classes.jar' if module['extension'] == 'aar' else 'org/signal/Client.class': b'java fixture'}
            for abi in self.lock['required_abis']:
                for library in module['native_libraries']:
                    entries[f'jni/{abi}/{library}'] = self.elf(abi)
            if name == 'libsignal-android':
                # Existing testing JNI is compared even though it is not listed as
                # a required production library in the native lock.
                entries['jni/arm64-v8a/libsignal_jni_testing.so'] = self.elf('arm64-v8a')
            self.entries[name] = entries
            reference = self.reference / (name + '-' + module['output_version'] + '.' + module['extension'])
            self.write_archive(reference, entries)
            rebuilt = self.outputs / (name + '.' + module['extension'])
            shutil.copyfile(reference, rebuilt)
            self.rebuilt[name] = rebuilt
            self.references[name] = reference
            module['binary_sha256'] = hashlib.sha256(reference.read_bytes()).hexdigest()
        self.lock_path = self.root / 'native-lock.json'
        self.save_lock()

    def elf(self, abi):
        elf_class, machine = subject.ELF_ABIS[abi]
        header = bytearray(64 if elf_class == 2 else 52)
        header[:7] = b'\x7fELF' + bytes((elf_class, 1, 1))
        header[16:18] = (3).to_bytes(2, 'little')
        header[18:20] = machine.to_bytes(2, 'little')
        return bytes(header) + b'native fixture'

    def write_archive(self, path, entries, timestamp=(2026, 1, 1, 0, 0, 0)):
        with ZipFile(path, 'w') as archive:
            for name, contents in entries.items():
                entry = ZipInfo(name, date_time=timestamp)
                entry.external_attr = 0o100644 << 16
                archive.writestr(entry, contents)

    def save_lock(self):
        self.lock_path.write_text(json.dumps(self.lock))

    def compare(self):
        return subject.compare(self.lock_path, self.reference, self.rebuilt)

    def cli(self, report):
        args = ['--lock', str(self.lock_path), '--reference-dir', str(self.reference), '--report', str(report)]
        for name, path in self.rebuilt.items():
            args.extend(['--' + name, str(path)])
        return subject.main(args)

    def test_exact_complete_archive_matches_are_not_independence_or_release_proof(self):
        report = self.compare()
        self.assertEqual('EXACT_BYTES_MATCH', report['status'])
        self.assertFalse(report['release_allowed'])
        self.assertFalse(report['independence_verified'])
        ringrtc = report['modules']['ringrtc-android']
        self.assertEqual(8, len(ringrtc['rebuilt_native_libraries']))
        self.assertEqual(0, self.cli(self.root / 'match.json'))

    def test_zip_timestamp_only_difference_still_fails_exact_comparison(self):
        self.write_archive(self.rebuilt['ringrtc-android'], self.entries['ringrtc-android'], (2026, 2, 1, 0, 0, 0))
        report = self.compare()
        self.assertEqual('ARTIFACT_MISMATCH', report['status'])
        self.assertEqual('ZIP_METADATA_ONLY', report['modules']['ringrtc-android']['status'])
        self.assertFalse(report['modules']['ringrtc-android']['exact_match'])
        self.assertEqual(1, self.cli(self.root / 'mismatch.json'))

    def test_testing_library_payload_difference_is_reported(self):
        path = 'jni/arm64-v8a/libsignal_jni_testing.so'
        self.entries['libsignal-android'][path] += b'changed code'
        self.write_archive(self.rebuilt['libsignal-android'], self.entries['libsignal-android'])
        report = self.compare()
        self.assertEqual('ARTIFACT_MISMATCH', report['status'])
        self.assertEqual('PAYLOAD_DIFFERENCE', report['modules']['libsignal-android']['status'])
        self.assertIn(path, report['modules']['libsignal-android']['changed_entries'])

    def test_reference_must_match_locked_hash(self):
        self.references['libsignal-client'].write_bytes(b'unapproved reference')
        report = self.compare()
        self.assertEqual('BLOCKED_INPUT', report['status'])
        self.assertIn('differs from the checked-in native lock', report['modules']['libsignal-client']['error'])

    def test_missing_abi_is_rejected_even_if_both_inputs_and_lock_match(self):
        module = next(item for item in self.lock['modules'] if item['name'] == 'ringrtc-android')
        del self.entries['ringrtc-android']['jni/x86/libringrtc.so']
        self.write_archive(self.references['ringrtc-android'], self.entries['ringrtc-android'])
        shutil.copyfile(self.references['ringrtc-android'], self.rebuilt['ringrtc-android'])
        module['binary_sha256'] = hashlib.sha256(self.references['ringrtc-android'].read_bytes()).hexdigest()
        self.save_lock()
        report = self.compare()
        self.assertEqual('BLOCKED_INPUT', report['status'])
        self.assertIn('missing a required ABI', report['modules']['ringrtc-android']['error'])

    def test_wrong_machine_under_correct_abi_name_is_rejected(self):
        self.entries['ringrtc-android']['jni/arm64-v8a/libringrtc.so'] = self.elf('x86_64')
        self.write_archive(self.rebuilt['ringrtc-android'], self.entries['ringrtc-android'])
        report = self.compare()
        self.assertEqual('BLOCKED_INPUT', report['status'])
        self.assertIn('machine differs', report['modules']['ringrtc-android']['error'])

    def test_duplicate_and_unsafe_archive_entries_are_rejected(self):
        path = self.rebuilt['libsignal-client']
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', UserWarning)
            with ZipFile(path, 'a') as archive:
                archive.writestr('org/signal/Client.class', b'duplicate')
        self.assertIn('Duplicate archive entry', self.compare()['modules']['libsignal-client']['error'])
        self.write_archive(path, {'../outside': b'unsafe'})
        self.assertIn('Unsafe archive entry', self.compare()['modules']['libsignal-client']['error'])

    def test_missing_artifact_emits_blocked_report(self):
        self.rebuilt['ringrtc-android'].unlink()
        path = self.root / 'blocked.json'
        self.assertEqual(1, self.cli(path))
        report = json.loads(path.read_text())
        self.assertEqual('BLOCKED_INPUT', report['status'])
        self.assertFalse(report['release_allowed'])

    def test_missing_or_modified_lock_input_emits_blocked_report(self):
        patch = self.root / self.lock['source_patches'][0]['path']
        patch.write_text('changed source patch')
        report = self.compare()
        self.assertEqual('BLOCKED_INPUT', report['status'])
        self.assertIn('patch hash mismatch', report['error'])

    def test_malformed_lock_documents_emit_blocked_report(self):
        for contents in ('[]', '{"schema": 1, "input_group": "org.signal", "output_group": "im.collie.native", "modules": [null]}'):
            with self.subTest(contents=contents):
                self.lock_path.write_text(contents)
                report = self.compare()
                self.assertEqual('BLOCKED_INPUT', report['status'])
                self.assertIn('error', report)

    def test_existing_report_is_not_overwritten(self):
        path = self.root / 'prior.json'
        path.write_text('prior result')
        self.assertEqual(2, self.cli(path))
        self.assertEqual('prior result', path.read_text())

    def test_same_file_and_symlink_are_rejected(self):
        self.rebuilt['libsignal-client'] = self.references['libsignal-client']
        self.assertIn('distinct files', self.compare()['modules']['libsignal-client']['error'])
        link = self.outputs / 'link.jar'
        link.symlink_to(self.references['libsignal-client'])
        self.rebuilt['libsignal-client'] = link
        self.assertIn('symlink', self.compare()['modules']['libsignal-client']['error'])


if __name__ == '__main__':
    unittest.main()
