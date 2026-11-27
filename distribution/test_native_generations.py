import copy
import hashlib
import io
import shutil
import stat
import struct
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
    add_native = fixtures.NativePagesTest.add_native

    def seal_previous(self):
        (self.bundle / 'previous/site-manifest.json').unlink(missing_ok=True)
        fixtures.PagesTest.seal_previous(self)

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
        entry = {'path': self.generation_path.relative_to(self.root).as_posix(),
                 'sha256': b.sha(self.generation_path)}
        manifests = []
        if hasattr(self, 'live_native'):
            entry['baseline'] = sorted(self.live_native)
            manifests.append(self.prior_registration)
        b.atomic_json(self.registry_path, {'schema': 1, 'manifests': manifests + [entry]})
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

    def add_live_generation(self):
        self.add_generation()
        self.retain_supplied()
        self.prior_registration = b.load(self.registry_path)['manifests'][0]
        self.retired = {name: (self.bundle / 'native' / name).read_bytes()
                        for name in self.original_files if '/libsignal-' in name}
        for name in self.retired:
            (self.bundle / 'previous' / name).unlink()
            (self.bundle / 'native' / name).unlink()
        self.seal_previous()
        self.live_native = p.inventory(self.bundle / 'native')
        self.new_files = {}
        version = '2.75.0-collie.1'
        module = 'ringrtc-android'
        for suffix in ('aar', 'pom'):
            name = f'native-inputs/v3/maven/im/collie/native/{module}/{version}/{module}-{version}.{suffix}'
            path = self.bundle / 'native' / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(('fixture ' + name).encode())
            self.new_files[name] = {'sha256': b.sha(path), 'bytes': path.stat().st_size}
        self.generation = dict(self.generation, generation=3, files=self.live_native | self.new_files)
        self.generation_path = self.root / 'native-generations/v3.json'
        self.register()

    def test_live_baseline_stages_and_preserves_files_in_both_phases(self):
        self.add_live_generation()
        self.archive_fixture()
        staged = self.root / 'staged'
        self.assertEqual(self.stage_generation(staged), 8)
        self.assertEqual(p.inventory(staged), self.generation['files'])
        shutil.rmtree(self.bundle / 'native')
        staged.rename(self.bundle / 'native')
        plan = self.prepare()
        for phase in ('phase1', 'phase2'):
            native = {name: entry for name, entry in plan['manifests'][phase].items()
                      if name.startswith('native-inputs/')}
            self.assertEqual(native, self.live_native | self.new_files)
            self.assertTrue(self.retired.keys().isdisjoint(native))
        self.assertFalse(p._authorized_empty_native_plan(plan))

    def test_live_baseline_uses_the_six_immutable_production_pins(self):
        self.add_live_generation()
        policy = inputs.native_generations
        original = policy.REGISTRY.parent / 'native-generations/v2.json'
        registered = policy.load_generation(original)
        production_files = {name: registered['files'][name] for name in self.live_native}
        self.assertEqual(len(production_files), 6)
        self.assertEqual(sum(entry['bytes'] for entry in production_files.values()), 313_077_213)
        shutil.copyfile(original, self.generation_path.with_name('v2.json'))
        self.prior_registration['sha256'] = b.sha(original)
        self.generation['files'] = production_files | self.new_files
        self.register()
        self.assertEqual(policy.load_generation(self.generation_path, registry_path=self.registry_path), self.generation)

    def test_live_baseline_rejects_tampered_or_missing_active_files(self):
        self.add_live_generation()
        name = next(iter(self.live_native))
        target = self.bundle / 'previous' / name
        original = target.read_bytes()
        for contents in (None, b'tampered'):
            with self.subTest(contents=contents):
                if contents is None:
                    target.unlink()
                else:
                    target.write_bytes(contents)
                self.seal_previous()
                with self.assertRaisesRegex(ValueError, 'native baseline must remain complete'):
                    self.prepare()
        target.write_bytes(original)
        self.seal_previous()
        supplied = self.bundle / 'native' / name
        for contents in (None, b'tampered'):
            with self.subTest(supplied=contents):
                if contents is None:
                    supplied.unlink()
                else:
                    supplied.write_bytes(contents)
                with self.assertRaisesRegex(ValueError, 'exact approved file set'):
                    self.prepare()
        self.assertFalse(self.output.exists())

    def test_already_retained_historical_native_file_must_match_its_pin(self):
        self.add_live_generation()
        name, contents = next(iter(self.retired.items()))
        target = self.bundle / 'previous' / name
        target.write_bytes(b'tampered')
        self.seal_previous()
        with self.assertRaisesRegex(ValueError, 'retained native files differ'):
            self.prepare()
        target.write_bytes(contents)
        self.seal_previous()
        plan = self.prepare()
        for phase in ('phase1', 'phase2'):
            self.assertEqual(plan['manifests'][phase][name], self.original_files[name])

    def test_new_policy_cannot_omit_active_files_or_use_unregistered_baseline(self):
        self.add_live_generation()
        name = next(iter(self.live_native))
        original = self.generation['files'].pop(name)
        self.register()
        with self.assertRaisesRegex(ValueError, 'preserve every pinned baseline byte'):
            self.prepare()
        self.generation['files'][name] = original
        self.register()
        registry = b.load(self.registry_path)
        registry['manifests'][-1]['baseline'].append(next(iter(self.new_files)))
        b.atomic_json(self.registry_path, registry)
        with self.assertRaisesRegex(ValueError, 'previously approved'):
            self.prepare()

    def archive_fixture(self, *, change=None, extra=None, symlink=None,
                        compression=zipfile.ZIP_STORED, comment=b''):
        raw = io.BytesIO()
        with zipfile.ZipFile(raw, 'w') as bundle:
            bundle.comment = comment
            for name in self.new_files:
                info = zipfile.ZipInfo(name)
                if name == symlink:
                    info.external_attr = (stat.S_IFLNK | 0o777) << 16
                data = (self.bundle / 'native' / name).read_bytes()
                bundle.writestr(info, b'x' * len(data) if name == change else data, compress_type=compression)
            if extra:
                bundle.writestr(extra, b'extra')
        self.pin_archive(raw.getvalue())

    def pin_archive(self, raw):
        self.archive = raw
        tag = f"native-v{self.generation['generation']}"
        record = {'url': f'https://github.com/{inputs.native_generations.REPOSITORY}/releases/download/{tag}/native.zip',
                  'sha256': hashlib.sha256(self.archive).hexdigest(), 'bytes': len(self.archive)}
        self.generation['archive'] = record
        self.release = {'tag_name': tag, 'draft': False, 'immutable': True,
                        'assets': [{'id': 1, 'name': 'native.zip', 'state': 'uploaded',
                                    'browser_download_url': record['url'], 'size': record['bytes'],
                                    'digest': 'sha256:' + record['sha256']}]}
        self.register()

    def stage_generation(self, destination):
        def api(endpoint):
            self.assertEqual(endpoint, f"repos/{inputs.native_generations.REPOSITORY}/releases/tags/{self.release['tag_name']}")
            return self.release

        def downloader(url, output, size, digest, *, hosts):
            if url.startswith(p.ORIGIN):
                self.assertEqual(hosts, {'repo.collie.im'})
                path = url.removeprefix(p.ORIGIN)
                if 'archive' in self.generation:
                    self.assertIn(path, getattr(self, 'live_native', self.original_files))
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

    def test_deflated_zip64_archive_with_comment_stages(self):
        self.add_generation()
        with patch.object(zipfile, 'ZIP64_LIMIT', 1):
            self.archive_fixture(compression=zipfile.ZIP_DEFLATED, comment=b'native generation')
        self.assertIn(b'PK\x06\x07', self.archive)
        staged = self.root / 'staged'
        self.assertEqual(self.stage_generation(staged), len(self.generation['files']))
        self.assertEqual(p.inventory(staged), self.generation['files'])

    def test_unsupported_compression_is_rejected_before_opening_members(self):
        self.add_generation()
        for compression in (zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA):
            with self.subTest(compression=compression):
                self.archive_fixture(compression=compression)
                with (patch.object(zipfile.ZipFile, 'open') as member_open,
                      self.assertRaisesRegex(ValueError, 'unsupported compression')):
                    self.stage_generation(self.root / 'staged')
                member_open.assert_not_called()

    def test_invalid_directories_are_rejected_before_zipfile_construction(self):
        self.add_generation()
        self.archive_fixture(extra='unexpected')
        extra = self.archive
        self.archive_fixture()
        normal = self.archive
        with patch.object(zipfile, 'ZIP64_LIMIT', 1):
            self.archive_fixture()
        zip64 = self.archive

        def changed(raw, offset, fmt, *values):
            data = bytearray(raw)
            struct.pack_into(fmt, data, offset, *values)
            return bytes(data)

        for label, raw, error in (
                ('oversize', changed(normal, len(normal) - 10, '<I', inputs.MAX_NATIVE_ZIP_DIRECTORY + 1),
                 'exceeds byte budget'),
                ('ZIP64 oversize', changed(zip64, zip64.index(b'PK\x06\x06') + 40, '<Q',
                                           inputs.MAX_NATIVE_ZIP_DIRECTORY + 1), 'exceeds byte budget'),
                ('excess entries', changed(normal, len(normal) - 14, '<HH', 101, 101), 'exactly the pinned'),
                ('false count', changed(extra, len(extra) - 14, '<HH', len(self.new_files), len(self.new_files)),
                 'differs from its declared entries'),
                ('truncated end', normal[:-1], 'missing or truncated'),
                ('wrong offset', changed(normal, len(normal) - 6, '<I', 0), 'invalid or truncated')):
            with self.subTest(label=label):
                self.pin_archive(raw)
                with (patch.object(zipfile, 'ZipFile') as constructor,
                      self.assertRaisesRegex(ValueError, error)):
                    self.stage_generation(self.root / 'staged')
                constructor.assert_not_called()

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
