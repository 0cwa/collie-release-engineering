import copy
import hashlib
import io
import shutil
import stat
import unittest
from unittest.mock import patch
import zipfile

import binary_repo as b
import pages as p
import release_inputs as inputs
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

    def archive_fixture(self, *, change=None, extra=None, symlink=None):
        raw = io.BytesIO()
        with zipfile.ZipFile(raw, 'w') as bundle:
            for name in self.new_files:
                info = zipfile.ZipInfo(name)
                if name == symlink:
                    info.external_attr = (stat.S_IFLNK | 0o777) << 16
                data = (self.bundle / 'native' / name).read_bytes()
                bundle.writestr(info, b'x' * len(data) if name == change else data)
            if extra:
                bundle.writestr(extra, b'extra')
        self.archive = raw.getvalue()
        record = {'url': f'https://github.com/{inputs.native_generations.REPOSITORY}/releases/download/native-v2/native.zip',
                  'sha256': hashlib.sha256(self.archive).hexdigest(), 'bytes': len(self.archive)}
        self.generation['archive'] = record
        self.release = {'tag_name': 'native-v2', 'draft': False, 'immutable': True,
                        'assets': [{'id': 1, 'name': 'native.zip', 'state': 'uploaded',
                                    'browser_download_url': record['url'], 'size': record['bytes'],
                                    'digest': 'sha256:' + record['sha256']}]}
        self.register()

    def stage_generation(self, destination):
        def api(endpoint):
            self.assertEqual(endpoint, f'repos/{inputs.native_generations.REPOSITORY}/releases/tags/native-v2')
            return self.release

        def downloader(url, output, size, digest, *, hosts):
            if url.startswith(p.ORIGIN):
                self.assertEqual(hosts, {'repo.collie.im'})
                path = url.removeprefix(p.ORIGIN)
                if 'archive' in self.generation:
                    self.assertIn(path, self.original_files)
                data = (self.bundle / 'native' / path).read_bytes()
            else:
                self.assertEqual(hosts, inputs.ASSET_HOSTS)
                self.assertEqual(url, self.generation['archive']['url'])
                data = self.archive
            inputs.write_verified(io.BytesIO(data), output, size, digest)

        return inputs.stage_native_generation(self.generation_path, destination, baseline_path=self.native_policy,
                                              registry_path=self.registry_path, api=api, downloader=downloader)

    def test_stage_archive_at_canonical_paths_and_use_for_pages_publication(self):
        self.add_generation()
        self.archive_fixture()
        staged = self.bundle / 'staged'
        self.assertEqual(self.stage_generation(staged), len(self.generation['files']))
        shutil.rmtree(self.bundle / 'native')
        staged.rename(self.bundle / 'native')
        plan = self.prepare()
        for path, record in self.generation['files'].items():
            self.assertEqual(plan['manifests']['phase2'][path], record)

    def test_generation_without_archive_fetches_all_files_from_pages(self):
        self.add_generation()
        staged = self.root / 'staged'
        self.assertEqual(self.stage_generation(staged), len(self.generation['files']))
        self.assertEqual(p.inventory(staged), self.generation['files'])

    def test_archive_members_must_match_reviewed_inventory_and_hashes(self):
        self.add_generation()
        name = next(iter(self.new_files))
        for mutation, error in (({'change': name}, 'approved size or digest'),
                                ({'extra': '../unexpected'}, 'exactly the pinned'),
                                ({'symlink': name}, 'pinned regular file')):
            with self.subTest(mutation=mutation), self.assertRaisesRegex(ValueError, error):
                self.archive_fixture(**mutation)
                self.stage_generation(self.root / 'staged')
            self.assertFalse((self.root / 'staged').exists())

    def test_archive_requires_an_immutable_release_and_matching_asset_pin(self):
        self.add_generation()
        self.archive_fixture()
        self.release['immutable'] = False
        with self.assertRaisesRegex(ValueError, 'published immutable CRE release'):
            self.stage_generation(self.root / 'staged')
        self.release['immutable'] = True
        self.release['assets'][0]['digest'] = 'sha256:' + '0' * 64
        with self.assertRaisesRegex(ValueError, 'differs from the registered pin'):
            self.stage_generation(self.root / 'staged')
        self.archive_fixture()
        self.archive = b'x' * len(self.archive)
        with self.assertRaisesRegex(ValueError, 'approved size or digest'):
            self.stage_generation(self.root / 'staged')
        self.assertFalse((self.root / 'staged').exists())


if __name__ == '__main__':
    unittest.main()
