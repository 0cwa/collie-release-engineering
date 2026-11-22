"""Data-only metadata export against real local Git objects and offline API seams."""
import base64
import copy
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

import release_inputs as inputs


TAG = 'v8.35.2-1-rc1'
PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/l9sAAAAASUVORK5CYII=')


def native_plan(name='source.pom'):
    path = 'im/collie/library/1/' + name
    return json.dumps({'status': 'NOT PUBLISHED', 'files': [
        {'path': path, 'url': inputs.ORIGIN + inputs.NATIVE_PREFIX + path,
         'bytes': 4, 'sha256': hashlib.sha256(b'data').hexdigest()}
    ]}, indent=2).encode() + b'\n'


class LocalGitAPI:
    """Produce GitHub-shaped JSON from Git's own tree/blob object storage."""
    def __init__(self, root):
        self.root = root
        root.mkdir()
        self.calls = []
        self.mutate = lambda _endpoint, value: value
        self.git('init', '-q', '--object-format=sha1')
        self.git('config', 'user.name', 'Metadata fixture')
        self.git('config', 'user.email', 'fixture@example.invalid')
        self.write(inputs.NATIVE_PLAN, native_plan())
        self.write('fastlane/Fastfile', b'raise "app code must never execute"\n')
        self.write('.gitattributes', b'* filter=untrusted-app-filter\n')
        self.write('.github/actions/app/action.yml', b'runs: {using: composite, steps: []}\n')
        self.add_listing()
        self.commit = self.save()

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.root), *args], stderr=subprocess.PIPE)

    def write(self, path, data):
        destination = self.root / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)

    def add_listing(self):
        prefix = inputs.METADATA_PREFIX + '/en-US/'
        for name, data in {'title.txt': b'Collie\n', 'short_description.txt': b'A private messenger\n',
                           'full_description.txt': b'Reviewed release description\n',
                           'changelogs/177706.txt': b'Updated\n', 'images/icon.png': PNG,
                           'images/featureGraphic.png': PNG}.items():
            self.write(prefix + name, data)
        for number in range(1, 10):
            self.write(prefix + f'images/phoneScreenshots/{number}.png', PNG)

    def save(self, *, add=True):
        if add:
            self.git('add', '--all')
        self.git('-c', 'commit.gpgSign=false', 'commit', '-qm', 'Local metadata fixture')
        return self.git('rev-parse', 'HEAD').decode().strip()

    def __call__(self, endpoint, limit):
        self.calls.append(endpoint)
        prefix = f'repos/{inputs.APP_REPOSITORY}/git/'
        if not endpoint.startswith(prefix):
            raise AssertionError('unexpected API host/repository path')
        kind, oid = endpoint[len(prefix):].split('/', 1)
        if kind == 'commits':
            value = {'sha': oid, 'tree': {'sha': self.git('rev-parse', oid + '^{tree}').decode().strip()}}
        elif kind == 'trees':
            recursive = oid.endswith('?recursive=1')
            oid = oid.removesuffix('?recursive=1')
            args = ('ls-tree', '-z', '-r', '-t', oid) if recursive else ('ls-tree', '-z', oid)
            entries = []
            for record in self.git(*args).split(b'\0'):
                if not record:
                    continue
                header, path = record.split(b'\t', 1)
                mode, entry_type, entry_sha = header.decode().split()
                item = {'path': path.decode(), 'mode': mode, 'type': entry_type, 'sha': entry_sha}
                if entry_type == 'blob':
                    item['size'] = int(self.git('cat-file', '-s', entry_sha))
                entries.append(item)
            value = {'sha': oid, 'truncated': False, 'tree': entries}
        elif kind == 'blobs':
            data = self.git('cat-file', 'blob', oid)
            encoded = base64.b64encode(data).decode()
            value = {'sha': oid, 'encoding': 'base64', 'size': len(data),
                     'content': '\n'.join(encoded[n:n + 60] for n in range(0, len(encoded), 60)) + '\n'}
        else:
            raise AssertionError('unexpected Git data endpoint: ' + endpoint)
        return json.dumps(self.mutate(endpoint, value)).encode()


@unittest.skipUnless(shutil.which('git'), 'Git required for real object fixtures')
class MetadataExportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.api = LocalGitAPI(self.root / 'source')
        self.destination = self.root / 'export'

    def receipt(self, source=None, listing=None):
        source = source or self.api.commit
        return {'schema': 1, 'kind': 'collie-feed-release-inputs', 'repository': inputs.APP_REPOSITORY,
                'latest': TAG, 'source_commit': source, 'listing_commit': listing or source,
                'tag_object': 'a' * 40, 'releases': [{'tag': TAG}]}

    def export(self, receipt=None):
        return inputs.export_metadata(receipt or self.receipt(), self.destination, api=self.api)

    def assert_rejected(self, pattern=None):
        context = self.assertRaisesRegex(ValueError, pattern) if pattern else self.assertRaises(ValueError)
        with context:
            self.export()
        self.assertFalse(self.destination.exists())

    def test_exports_only_metadata_with_git_hashes_and_preserves_all_screenshots(self):
        record = self.export()
        paths = {item['path'] for item in record['files']}
        self.assertEqual(len(paths), 16)
        self.assertEqual(paths, {str(path.relative_to(self.destination))
                                 for path in self.destination.rglob('*') if path.is_file()})
        for item in record['files']:
            exported = self.destination / item['path']
            expected = self.api.git('show', self.api.commit + ':' + item['path'])
            self.assertEqual(exported.read_bytes(), expected)
            self.assertEqual(item['source_commit'], self.api.commit)
            self.assertEqual(item['git_blob'], self.api.git('rev-parse', self.api.commit + ':' + item['path']).decode().strip())
            self.assertEqual(item['sha256'], hashlib.sha256(expected).hexdigest())
            self.assertEqual(exported.stat().st_mode & 0o777, 0o644)
            self.assertFalse(exported.is_symlink())
        for number in range(1, 10):
            self.assertIn(inputs.METADATA_PREFIX + f'/en-US/images/phoneScreenshots/{number}.png', paths)
        for path in ('fastlane/Fastfile', '.gitattributes', '.github/actions/app/action.yml'):
            oid = self.api.git('rev-parse', self.api.commit + ':' + path).decode().strip()
            self.assertFalse(any(call.endswith('/blobs/' + oid) for call in self.api.calls))
            self.assertFalse((self.destination / path).exists())
        self.assertFalse((self.destination / '.git').exists())

    def test_listing_exception_never_changes_the_native_source_or_normalizes_ledger_bytes(self):
        source = self.api.commit
        self.api.write(inputs.NATIVE_PLAN, native_plan('listing-must-not-be-used.pom'))
        self.api.write(inputs.METADATA_PREFIX + '/en-US/title.txt', b'Historical listing\n')
        listing = self.api.save()
        with patch.dict(inputs.LEGACY_LISTINGS, {(TAG, source): listing}, clear=True):
            record = self.export(self.receipt(source, listing))
        self.assertEqual((self.destination / inputs.NATIVE_PLAN).read_bytes(), native_plan())
        self.assertEqual((self.destination / inputs.METADATA_PREFIX / 'en-US/title.txt').read_bytes(), b'Historical listing\n')
        for item in record['files']:
            self.assertEqual(item['source_commit'], source if item['path'] == inputs.NATIVE_PLAN else listing)
        self.assertEqual(record['source_tree'], self.api.git('rev-parse', source + '^{tree}').decode().strip())
        self.assertEqual(record['listing_tree'], self.api.git('rev-parse', listing + '^{tree}').decode().strip())

    def test_receipt_cannot_introduce_a_moving_ref_or_unreviewed_listing_exception(self):
        for changed in ({'source_commit': 'main'}, {'listing_commit': 'f' * 40}, {'schema': True},
                        {'repository': 'elsewhere/app'}, {'latest': 'trailblazer-v8.35.2'}):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                self.export({**self.receipt(), **changed})
        self.assertEqual(self.api.calls, [])

    def test_missing_listing_or_native_file_never_falls_back_to_another_commit(self):
        for path in (inputs.METADATA_PREFIX, inputs.NATIVE_PLAN):
            with self.subTest(path=path):
                self.api.git('rm', '-r', '--', path)
                commit = self.api.save(add=False)
                with self.assertRaisesRegex(ValueError, 'absent|exact source native'):
                    self.export(self.receipt(commit))
                self.assertFalse(self.destination.exists())
                self.api.git('reset', '--hard', self.api.commit)
        self.assertFalse(any('/main' in endpoint for endpoint in self.api.calls))

    def test_executable_metadata_is_rejected_even_with_valid_git_objects(self):
        path = self.api.root / inputs.METADATA_PREFIX / 'en-US/title.txt'
        path.chmod(0o755)
        self.api.commit = self.api.save()
        self.assert_rejected('path, mode or file size')

    def test_metadata_symlinks_are_rejected_even_with_valid_git_objects(self):
        path = self.api.root / inputs.METADATA_PREFIX / 'en-US/title.txt'
        path.unlink()
        path.symlink_to('../../../../Fastfile')
        self.api.commit = self.api.save()
        self.assert_rejected('path, mode or file size')

    def test_gitlinks_inside_metadata_are_rejected_even_with_valid_git_objects(self):
        path = inputs.METADATA_PREFIX + '/en-US/title.txt'
        self.api.git('rm', '--', path)
        self.api.git('update-index', '--add', '--cacheinfo', '160000,' + self.api.commit + ',' + path)
        self.api.commit = self.api.save(add=False)
        self.assert_rejected('path, mode or file size')

    def test_named_png_screenshots_are_exported(self):
        # Collie 8.36.3-1 ships phoneScreenshots/settings-overview.png next to the numbered ones.
        path = inputs.METADATA_PREFIX + '/en-US/images/phoneScreenshots/settings-overview.png'
        self.api.write(path, PNG)
        record = self.export(self.receipt(self.api.save()))
        self.assertIn(path, {item['path'] for item in record['files']})
        self.assertEqual((self.destination / path).read_bytes(), PNG)

    def test_unreviewed_code_hidden_paths_and_non_png_images_are_rejected(self):
        for path in ('en-US/run.sh', 'en-US/.hidden', 'en-US/images/icon.svg', 'en-US/images/phoneScreenshots/run.py',
                     'en-US/images/phoneScreenshots/.hidden.png', 'en-US/images/phoneScreenshots/a.b.png'):
            with self.subTest(path=path):
                full = inputs.METADATA_PREFIX + '/' + path
                self.api.write(full, b'unreviewed input')
                changed = self.api.save()
                with self.assertRaisesRegex(ValueError, 'unreviewed Fastlane'):
                    self.export(self.receipt(changed))
                self.assertFalse(self.destination.exists())
                self.api.git('reset', '--hard', self.api.commit)

    def test_wrong_png_magic_control_text_or_invalid_native_plan_is_rejected(self):
        for path, data in ((inputs.METADATA_PREFIX + '/en-US/images/icon.png', b'not a PNG'),
                           (inputs.METADATA_PREFIX + '/en-US/title.txt', b'name\0command'),
                           (inputs.NATIVE_PLAN, b'{"files":[]}')):
            with self.subTest(path=path):
                self.api.write(path, data)
                changed = self.api.save()
                with self.assertRaises(ValueError):
                    self.export(self.receipt(changed))
                self.assertFalse(self.destination.exists())
                self.api.git('reset', '--hard', self.api.commit)

    def test_tree_and_commit_response_identity_truncation_and_paths_are_checked(self):
        mutations = (
            ('/commits/', lambda value: value.update(sha='f' * 40)),
            ('/trees/', lambda value: value.update(sha='f' * 40)),
            ('/trees/', lambda value: value.update(truncated=True)),
            ('/trees/', lambda value: value['tree'].append(copy.deepcopy(value['tree'][0]))),
            ('/trees/', lambda value: value['tree'][0].update(path='../escape')),
            ('/trees/', lambda value: value['tree'][0].update(sha='f' * 40)),
        )
        for marker, mutate in mutations:
            def alter(endpoint, value):
                if marker in endpoint:
                    mutate(value)
                return value
            self.api.mutate = alter
            with self.subTest(marker=marker):
                self.assert_rejected()

    def test_blob_identity_size_base64_and_git_content_hash_are_checked(self):
        mutations = (
            lambda value: value.update(sha='f' * 40),
            lambda value: value.update(size=value['size'] + 1),
            lambda value: value.update(encoding='utf-8'),
            lambda value: value.update(content='!' + value['content'][1:]),
            lambda value: value.update(content=base64.b64encode(b'x' * value['size']).decode()),
        )
        for mutate in mutations:
            def alter(endpoint, value):
                if '/blobs/' in endpoint:
                    mutate(value)
                return value
            self.api.mutate = alter
            self.assert_rejected()

    def test_encoded_content_is_bounded_before_base64_decode(self):
        def alter(endpoint, value):
            if '/blobs/' in endpoint:
                value['content'] = 'A' * (value['size'] * 8 + 100)
            return value
        self.api.mutate = alter
        with patch.object(inputs.base64, 'b64decode', side_effect=AssertionError('must reject before decode')):
            self.assert_rejected('blob response')

    def test_count_decoded_and_api_byte_budgets_are_enforced(self):
        for constant in ('MAX_METADATA_FILES', 'MAX_METADATA_BYTES', 'MAX_METADATA_RESPONSE_BYTES', 'MAX_METADATA_API_BYTES', 'MAX_TREE_ENTRIES'):
            with self.subTest(constant=constant), patch.object(inputs, constant, 1):
                self.assert_rejected()

    def test_individual_text_and_png_size_limits_are_checked_before_blob_fetch(self):
        for suffix, data in (('title.txt', b'a' * (inputs.MAX_TEXT_BYTES + 1)),
                             ('images/icon.png', PNG + b'a' * inputs.MAX_PNG_BYTES)):
            with self.subTest(suffix=suffix):
                self.api.calls.clear()
                self.api.write(inputs.METADATA_PREFIX + '/en-US/' + suffix, data)
                changed = self.api.save()
                with self.assertRaisesRegex(ValueError, 'file size'):
                    self.export(self.receipt(changed))
                self.assertFalse(any('/blobs/' in endpoint for endpoint in self.api.calls))
                self.assertFalse(self.destination.exists())
                self.api.git('reset', '--hard', self.api.commit)

    def test_existing_output_and_symlink_parent_are_rejected_before_api_calls(self):
        self.destination.mkdir()
        with self.assertRaisesRegex(ValueError, 'new metadata'):
            self.export()
        self.destination.rmdir()
        link = self.root / 'link'
        link.symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'new metadata'):
            inputs.export_metadata(self.receipt(), link / 'export', api=self.api)
        self.assertEqual(self.api.calls, [])


class MetadataTransportTests(unittest.TestCase):
    def test_git_api_is_get_only_fixed_host_and_stream_bounded(self):
        endpoint = f'repos/{inputs.APP_REPOSITORY}/git/commits/' + 'a' * 40
        for data, accepted in ((b'1234', True), (b'12345', False)):
            request = Mock()
            request.__enter__ = Mock(return_value=request)
            request.__exit__ = Mock(return_value=False)
            request.stdout = io.BytesIO(data)
            request.wait.return_value = 0
            with patch.object(inputs.subprocess, 'Popen', return_value=request) as popen:
                if accepted:
                    self.assertEqual(inputs.github_metadata_read(endpoint, 4), data)
                    request.kill.assert_not_called()
                else:
                    with self.assertRaisesRegex(ValueError, 'byte budget'):
                        inputs.github_metadata_read(endpoint, 4)
                    request.kill.assert_called_once()
                self.assertEqual(popen.call_args.args[0], ['gh', 'api', '--hostname', 'github.com', '--method', 'GET',
                                                         '-H', 'Accept: application/vnd.github+json', endpoint])

    def test_duplicate_response_keys_and_oversized_responses_are_rejected_before_use(self):
        for raw in (b'{"sha":"a","sha":"b"}', b'[]'):
            reader = inputs.MetadataReader(lambda _endpoint, _limit: raw)
            with self.assertRaises(ValueError):
                reader.read('commits/' + 'a' * 40)
        reader = inputs.MetadataReader(lambda _endpoint, limit: b' ' * (limit + 1))
        with self.assertRaisesRegex(ValueError, 'byte budget'):
            reader.read('commits/' + 'a' * 40)


if __name__ == '__main__':
    unittest.main()
