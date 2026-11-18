import copy
import shutil
import unittest
from unittest.mock import patch

import binary_repo as b
import pages as p
import test_pages as fixtures


class NativeGenerationAdmissionTest(unittest.TestCase):
    setUp = fixtures.PagesTest.setUp
    write = fixtures.PagesTest.write
    prepare = fixtures.PagesTest.prepare
    fetch = fixtures.PagesTest.fetch
    seal_previous = fixtures.PagesTest.seal_previous
    add_native = fixtures.NativePagesTest.add_native

    def add_generation(self):
        self.original_files = copy.deepcopy(self.add_native(only=True))
        files = copy.deepcopy(self.original_files)
        self.new_files = {}
        for module, extension in (('libsignal-android', 'aar'), ('libsignal-client', 'jar')):
            version = '0.107.0-collie.1'
            for suffix in (extension, 'pom'):
                name = f'native-inputs/v2/maven/im/collie/native/{module}/{version}/{module}-{version}.{suffix}'
                path = self.bundle / 'native' / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(('new fixture ' + name).encode())
                self.new_files[name] = {'sha256': b.sha(path), 'bytes': path.stat().st_size}
        files.update(self.new_files)
        self.generation = {'schema': 1, 'kind': 'collie-pages-native-generation',
                           'origin': p.ORIGIN, 'generation': 2, 'files': files}
        self.generation_path = self.root / 'native-generations/v2.json'
        self.generation_path.parent.mkdir()
        self.registry_path = self.root / 'native-generations.json'
        registry_patch = patch.object(p, 'NATIVE_GENERATIONS', self.registry_path)
        registry_patch.start()
        self.addCleanup(registry_patch.stop)
        self.register()

    def register(self):
        b.atomic_json(self.generation_path, self.generation)
        b.atomic_json(self.registry_path, {'schema': 1, 'manifests': [
            {'path': 'native-generations/v2.json', 'sha256': b.sha(self.generation_path)}]})
        b.atomic_json(self.bundle / 'native-manifest.json', self.generation)
        self.approval['native_manifest_sha256'] = b.sha(self.bundle / 'native-manifest.json')
        self.write()

    def retain_supplied(self):
        for name in self.generation['files']:
            target = self.bundle / 'previous' / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(self.bundle / 'native' / name, target)
        self.seal_previous()

    def test_reviewed_generation_adds_only_new_libsignal_and_keeps_original_bytes(self):
        self.add_generation()
        original_policy = self.native_policy.read_bytes()
        plan = self.prepare()
        for phase in ('phase1', 'phase2'):
            native = {name: value for name, value in plan['manifests'][phase].items()
                      if name.startswith('native-inputs/')}
            self.assertEqual(native, self.generation['files'])
            self.assertEqual({name: native[name] for name in self.original_files}, self.original_files)
        self.assertEqual(self.native_policy.read_bytes(), original_policy)
        self.assertFalse(p._authorized_empty_native_plan(plan))

    def test_existing_v1_feed_bundle_retains_later_generation(self):
        self.add_generation()
        self.retain_supplied()
        for name in self.new_files:
            (self.bundle / 'native' / name).unlink()
        b.atomic_json(self.bundle / 'native-manifest.json', b.load(self.native_policy))
        self.approval['native_manifest_sha256'] = b.sha(self.bundle / 'native-manifest.json')
        self.write()
        plan = self.prepare()
        for phase in ('phase1', 'phase2'):
            for name, entry in self.new_files.items():
                self.assertEqual(plan['manifests'][phase][name], entry)

    def test_caller_manifest_and_approval_cannot_admit_unregistered_generation(self):
        self.add_generation()
        b.atomic_json(self.registry_path, {'schema': 1, 'manifests': []})
        with self.assertRaisesRegex(ValueError, 'not a registered'):
            self.prepare()
        self.assertFalse(self.output.exists())

    def test_registered_policy_tampering_and_payload_tampering_are_rejected(self):
        self.add_generation()
        raw = self.generation_path.read_bytes()
        self.generation_path.write_bytes(raw + b' ')
        with self.assertRaisesRegex(ValueError, 'digest differs'):
            self.prepare()
        self.generation_path.write_bytes(raw)
        name = next(iter(self.new_files))
        (self.bundle / 'native' / name).write_bytes(b'tampered')
        with self.assertRaisesRegex(ValueError, 'exact approved file set'):
            self.prepare()
        self.assertFalse(self.output.exists())

    def test_registration_cannot_rewrite_native_v1_or_admit_partial_libsignal(self):
        self.add_generation()
        original = copy.deepcopy(self.generation)
        old_name = next(iter(self.original_files))
        new_name = next(name for name in self.new_files if name.endswith('.pom'))
        mutations = [lambda files: files[old_name].update(sha256='0' * 64),
                     lambda files: files.pop(new_name),
                     lambda files: files.update({'native-inputs/v2/maven/maven-metadata.xml':
                                                 {'sha256': '0' * 64, 'bytes': 5}}),
                     lambda files: files[new_name].update(bytes=True)]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                self.generation = copy.deepcopy(original)
                mutate(self.generation['files'])
                self.register()
                with self.assertRaises(ValueError):
                    self.prepare()
                self.assertFalse(self.output.exists())

    def test_new_generation_still_obeys_the_complete_site_budget(self):
        self.add_generation()
        self.approval['maximum_bytes'] = 100
        self.write()
        with self.assertRaisesRegex(ValueError, 'budget'):
            self.prepare()


if __name__ == '__main__':
    unittest.main()
