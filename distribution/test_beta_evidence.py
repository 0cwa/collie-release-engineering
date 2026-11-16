"""End-to-end evidence pack/verify/readiness tests with external tools mocked."""
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import types
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import beta_evidence as evidence
import binary_repo as binary
import channel_profile as channels
import feed
import migration_receipts as receipts
import pages
import test_beta_release_producers as producer_tests


class BetaEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.fixture = producer_tests.BetaProducerTests('test_real_collect_sign_and_existing_receipt_validator_accept_beta_graph')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.collect()
        self.signed, self.final_path, self.final, self.release_receipt = self.fixture.sign()
        self.root = self.fixture.root / 'release-evidence-input'
        self.root.mkdir()
        self.selected_id = int(self.fixture.run_id) + 100
        self._create_bundle_input()
        self.tools = {'aapt': self.fixture.build_tools / 'aapt2',
                      'apksigner': self.fixture.build_tools / 'apksigner'}
        self.index_modules = self._index_parser_modules()

    def _write_json(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n')

    def _pack_preindex_input(self, archive):
        files=[self.root/f'releases/{self.selected_id}.json']
        files += [self.root/f'bundles/{self.selected_id}/{kind}.json' for kind in ('source','build','release')]
        files += [self.root/'manifests/unsigned-manifest.json',self.root/'manifests/final-manifest.json',
                  self.root/'apk/trailblazer-beta.apk']
        with tarfile.open(archive,'w',format=tarfile.USTAR_FORMAT) as tar:
            for path in sorted(files,key=lambda item:item.relative_to(self.root).as_posix()):
                name=path.relative_to(self.root).as_posix();info=tarfile.TarInfo(name)
                info.size=path.stat().st_size;info.mode=0o644;info.uid=info.gid=0;info.mtime=0
                with path.open('rb') as stream:tar.addfile(info,stream)
        return archive

    def _preindex_api(self, endpoint, archive, digest):
        if endpoint==f'repos/0cwa/collie-release-engineering/releases/assets/771':
            return {'id':771,'name':evidence.RELEASE_INPUT_ASSET_NAME,'state':'uploaded',
                    'size':archive.stat().st_size,'digest':'sha256:'+digest}
        return self._source_api(endpoint)

    def _create_bundle_input(self):
        f = self.fixture
        channel = channels.BETA
        source = receipts.load(f.source_path, 'source', profile=channel.name)
        build = receipts.load(f.apks / 'build-receipt.json', 'build', profile=channel.name)
        release = receipts.load(self.signed / 'release-receipt.json', 'release', profile=channel.name)
        release_id = self.selected_id
        approval = {
            'schema': 2, 'release_id': release_id, 'tag': f.plan['tag'],
            'release_manifest_sha256': hashlib.sha256(self.final_path.read_bytes()).hexdigest(),
            'apk_sha256': self.final['apks'][0]['signed_sha256'],
            'apk_certificate_sha256': channel.apk_certificate, 'package': channel.package,
            'version_code': f.version_code, 'version_name': f.version,
            'aliases': {channel.apk_asset: self.final['apks'][0]['signed_sha256']},
            'release_receipt_sha256': receipts.digest(release, profile=channel.name),
            'receipt_approvals': {
                'source': receipts.digest(source, profile=channel.name),
                'build': receipts.digest(build, profile=channel.name),
                'release': receipts.digest(release, profile=channel.name),
            },
        }
        self._write_json(self.root / f'releases/{release_id}.json', approval)
        for kind, document in (('source', source), ('build', build), ('release', release)):
            self._write_json(self.root / f'bundles/{release_id}/{kind}.json', document)
        self._write_json(self.root / 'manifests/unsigned-manifest.json', json.loads(f.manifest_path.read_text()))
        shutil.copyfile(self.final_path, self.root / 'manifests/final-manifest.json')
        signed_apk = self.signed / channel.apk_asset
        (self.root / 'apk').mkdir()
        shutil.copyfile(signed_apk, self.root / 'apk/trailblazer-beta.apk')
        candidate = self.root / 'candidate'
        candidate.mkdir()
        indexed_apk = candidate / f'{channel.package}_{f.version_code}.apk'
        shutil.copyfile(signed_apk, indexed_apk)
        index_data = {
            'repo': {'address': channel.repository_url},
            'packages': {channel.package: {'releaseChannels': [], 'versions': {
                str(f.version_code): {
                    'manifest': {'versionCode': f.version_code, 'versionName': f.version,
                                 'signer': {'sha256': [channel.apk_certificate]}},
                    'file': {'sha256': binary.sha(indexed_apk), 'name': indexed_apk.name},
                }
            }}},
        }
        index_v1 = {'apps': [{'packageName': channel.package}], 'packages': {channel.package: [{
            'versionCode': f.version_code, 'versionName': f.version, 'hash': binary.sha(indexed_apk),
            'apkName': indexed_apk.name, 'signer': channel.apk_certificate, 'hashType': 'sha256'}]}}
        index_v2_raw = (json.dumps(index_data, sort_keys=True, separators=(',', ':')) + '\n').encode()
        (candidate / 'index-v2.json').write_bytes(index_v2_raw)
        for name, body in (('entry.jar', b'test-only signed entry'), ('entry.json', b'{}\n'),
                           ('index-v1.jar', b'test-only signed v1'), ('index-v1.json', b'{}\n')):
            (candidate / name).write_bytes(body)
        (candidate / 'icons').mkdir()
        (candidate / 'icons/trailblazer.png').write_bytes(b'test-only Beta icon bytes')
        self.entry = {'index': {'name': '/index-v2.json', 'sha256': hashlib.sha256(index_v2_raw).hexdigest()}}
        self.index_v1 = index_v1
        self.index_data = index_data
        metadata = Path(__file__).with_name('beta-metadata') / f'{channel.package}.yml'
        (self.root / 'metadata').mkdir()
        shutil.copyfile(metadata, self.root / f'metadata/{channel.package}.yml')
        repo_files = sorted(p for p in candidate.iterdir() if p.suffix in {'.jar', '.json'})
        generation = binary.generation_digest(candidate, channel)
        repo = {
            'schema': 2, 'kind': 'repository', 'payload': {
                'release_manifest_sha256': approval['release_manifest_sha256'],
                'release_receipt_sha256': approval['release_receipt_sha256'],
                'apk_sha256': approval['apk_sha256'], 'apk_certificate_sha256': channel.apk_certificate,
                'repository_certificate_sha256': channel.repository_certificate,
                'fdroidserver_revision': 'PyPI fdroidserver==2.4.5',
                'metadata_revision': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
                'indexes': [{'file': p.name, 'sha256': binary.sha(p)} for p in repo_files],
                'generation': generation,
            },
            'evidence': [
                {'status': 'PASS', 'description': receipts.PREPUBLICATION_REPOSITORY_EVIDENCE['candidate_index'],
                 'sha256': binary.sha(candidate / 'entry.jar')},
                {'status': 'PASS', 'description': receipts.PREPUBLICATION_REPOSITORY_EVIDENCE['client_acceptance'],
                 'sha256': 'e' * 64},
                {'status': 'NOT RUN', 'description': receipts.PREPUBLICATION_REPOSITORY_EVIDENCE['production_readback'],
                 'sha256': None},
            ],
        }
        receipts.validate(repo, 'repository', profile=channel.name)
        self._write_json(self.root / 'repository-receipt.json', repo)

    def _index_parser_modules(self):
        package = types.ModuleType('fdroidserver')
        index = types.ModuleType('fdroidserver.index')
        common = types.ModuleType('fdroidserver.common')
        common.config = {}

        def get_index(path, _fingerprint, **_kwargs):
            return (self.entry if Path(path).name == 'entry.jar' else self.index_v1, None, None)

        index.get_index_from_jar = get_index
        package.index = index
        package.common = common
        return {'fdroidserver': package, 'fdroidserver.index': index, 'fdroidserver.common': common}

    def _source_api(self, endpoint):
        return self.fixture.run_metadata

    def _readiness_api(self, endpoint):
        if '/releases/assets/' in endpoint:
            return {'id': 771, 'name': evidence.ASSET_NAME, 'state': 'uploaded',
                    'size': self.archive.stat().st_size, 'digest': 'sha256:' + self.bundle_sha}
        if '/releases/tags/' in endpoint:
            raise ValueError('draft lookup must use the approved release ID')
        if endpoint == f'repos/0cwa/collieim-android/releases/{self.selected_id}':
            return {'id': self.selected_id, 'tag_name': self.fixture.plan['tag'],
                    'name': self.fixture.plan['title'], 'draft': True, 'prerelease': True,
                    'assets': [{'id': 772, 'name': 'trailblazer-beta.apk', 'state': 'uploaded',
                                'digest': 'sha256:' + self.final['apks'][0]['signed_sha256']}]}
        return {'id': 12345, 'run_attempt': 3, 'head_repository': {'full_name': '0cwa/collieim-android'},
                'head_branch': 'trailblazer-beta', 'head_sha': self.fixture.commit,
                'path': channels.BETA.readiness_workflow, 'status': 'in_progress',
                'conclusion': None, 'event': 'workflow_dispatch'}

    def test_actual_pack_verify_and_readiness_emit_roundtrip(self):
        archive = self.fixture.root / 'evidence.tar'
        tag_verifier = lambda _repo, _tag, _commit: None
        with patch.dict(sys.modules, self.index_modules):
            packed = evidence.pack(self.root, archive, self.tools, run_api=self._source_api,
                                   tag_verifier=tag_verifier, release_api=self._readiness_api)
            second = evidence.pack(self.root, self.fixture.root / 'same-content.tar', self.tools,
                                   run_api=self._source_api, tag_verifier=tag_verifier,
                                   release_api=self._readiness_api)
            self.archive = archive
            self.bundle_sha = packed['sha256']
            output = self.fixture.root / 'trailblazer-beta-readiness.json'
            scratch = self.fixture.root / 'emitter-scratch'
            scratch.mkdir(mode=0o700)
            verified = evidence.verify_archive(archive, bundle_sha256=self.bundle_sha,
                asset_id=771, scratch=scratch, tools=self.tools, api=self._readiness_api,
                run_api=self._source_api, tag_verifier=tag_verifier,
                release_api=self._readiness_api)
            emitted = evidence.emit_readiness(
                bundle_sha256=self.bundle_sha, asset_id=771, run_id=12345, attempt=3,
                output=output, scratch=scratch, tools=self.tools, api=self._readiness_api,
                downloader=lambda _asset_id, path: shutil.copyfile(archive, path),
                source_run_api=self._source_api, tag_verifier=tag_verifier)
        self.assertEqual(packed['sha256'], second['sha256'])
        self.assertEqual(verified['version_name'], self.fixture.version)
        self.assertEqual(verified['package'], channels.BETA.package)
        self.assertEqual(emitted['kind'], 'collie-trailblazer-readiness')
        self.assertEqual(emitted['branch'], 'trailblazer-beta')
        self.assertEqual(emitted['workflow_path'], '.github/workflows/beta-readiness.yml')
        self.assertEqual(emitted['head_sha'], self.fixture.commit)
        self.assertEqual(emitted['apk_asset'], 'trailblazer-beta.apk')
        self.assertEqual(set(emitted), pages.READINESS_FIELDS)
        self.assertEqual(json.loads(output.read_text()), emitted)
        self.assertNotIn('readiness_artifact', emitted)

    def test_approved_beta_graph_generates_beta_route_candidate_end_to_end(self):
        archive=self.fixture.root/'candidate-input.tar'
        self._pack_preindex_input(archive)
        self.bundle_sha=hashlib.sha256(archive.read_bytes()).hexdigest()
        api=lambda endpoint:self._preindex_api(endpoint,archive,self.bundle_sha)
        scratch=self.fixture.root/'feed-candidate-scratch';scratch.mkdir(mode=0o700)
        source=self.fixture.root/'approved-source-checkout';source.mkdir()
        metadata=source/'fastlane/metadata/trailblazer-beta';(metadata/'en-US/images').mkdir(parents=True)
        (metadata/'en-US/title.txt').write_text('Collie TrailBlazer\n')
        (metadata/'en-US/images/icon.png').write_bytes(b'fixture icon')
        apk_hash=self.final['apks'][0]['signed_sha256'];code=self.fixture.version_code;version=self.fixture.version

        real_subprocess_run=feed.subprocess.run
        def fake_update(command,cwd=None,check=False,**kwargs):
            if not command or command[0]!='fdroid':
                return real_subprocess_run(command,cwd=cwd,check=check,**kwargs)
            repo=Path(cwd)/'repo';apk=repo/f'{channels.BETA.package}_{code}.apk'
            v2={'repo':{'address':channels.BETA.repository_url.rstrip('/')},'packages':{
                channels.BETA.package:{'versions':{str(code):{'manifest':{'versionCode':code,
                    'versionName':version,'signer':{'sha256':[channels.BETA.apk_certificate]}},
                    'file':{'name':apk.name,'sha256':apk_hash}}}}}}
            (repo/'index-v2.json').write_text(json.dumps(v2,sort_keys=True,separators=(',',':'))+'\n')
            v1={'apps':[{'packageName':channels.BETA.package}],'packages':{channels.BETA.package:[{
                'versionCode':code,'versionName':version,'hash':apk_hash,'apkName':apk.name,
                'signer':channels.BETA.apk_certificate,'hashType':'sha256'}]}}
            (repo/'index-v1.json').write_text(json.dumps(v1))
            (repo/'entry.jar').write_bytes(b'test-only generated entry signature')
            (repo/'index-v1.jar').write_bytes(b'test-only generated v1 signature')
            shutil.copyfile(Path(cwd)/'collie-icon.png',repo/'collie-icon.png')

        def get_index(path,_fingerprint,**_kwargs):
            repo=Path(path).parent
            index=json.loads((repo/'index-v2.json').read_text())
            if Path(path).name=='entry.jar':
                return ({'index':{'name':'/index-v2.json','sha256':hashlib.sha256((repo/'index-v2.json').read_bytes()).hexdigest()}},None,None)
            versions=index['packages'][channels.BETA.package]['versions'].values()
            return ({'apps':[{'packageName':channels.BETA.package}], 'packages':{channels.BETA.package:[{
                'versionCode':row['manifest']['versionCode'],'versionName':row['manifest']['versionName'],
                'hash':row['file']['sha256'],'apkName':row['file']['name'],
                'signer':row['manifest']['signer']['sha256'][0],'hashType':'sha256'} for row in versions]}},None,None)
        fdroidserver=types.ModuleType('fdroidserver');fdroidserver.index=types.SimpleNamespace(get_index_from_jar=get_index)
        fdroidserver.common=types.SimpleNamespace(config={})
        fdroid_index=types.ModuleType('fdroidserver.index');fdroid_index.get_index_from_jar=get_index
        fdroid_common=types.ModuleType('fdroidserver.common');fdroid_common.config={}
        with patch.dict(sys.modules,{'fdroidserver':fdroidserver,'fdroidserver.index':fdroid_index,
                'fdroidserver.common':fdroid_common}), \
                patch.dict(os.environ,{'FDROID_KEYSTORE_PASS':'test-only-not-a-key'}), \
                patch.object(feed,'validate_beta_source_input',return_value=metadata), \
                patch.object(feed,'stage_beta_source_metadata',return_value=metadata), \
                patch.object(feed,'verified_public_beta_history',return_value=0) as public_history, \
                patch.object(feed,'BETA_ICON_SHA256',hashlib.sha256(b'fixture icon').hexdigest()), \
                patch.object(feed,'capture_beta_route',return_value={'release':{'version_code':code-1,'version_name':'8.34.0'},
                    'files':{},'inventory_sha256':'f'*64}), \
                patch.object(feed.subprocess,'run',side_effect=fake_update), \
                patch.object(feed,'keystore_fingerprint',return_value=channels.BETA.repository_certificate):
            public_history.return_value=code+1
            with self.assertRaisesRegex((ValueError,SystemExit),'increas|version'):
                feed.build_beta_candidate(archive,self.bundle_sha,771,scratch,source,metadata,
                    self.fixture.root/'repo.p12',self.fixture.build_tools,self.fixture.root/'beta-site',
                    api=api,run_api=self._source_api,tag_verifier=lambda _repo,_tag,_commit:None)
            public_history.return_value=0
            result=feed.build_beta_candidate(archive,self.bundle_sha,771,scratch,source,metadata,
                self.fixture.root/'repo.p12',self.fixture.build_tools,self.fixture.root/'beta-site',
                api=api,run_api=self._source_api,tag_verifier=lambda _repo,_tag,_commit:None)
        self.assertEqual(result['source_commit'],self.fixture.commit)
        self.assertEqual(result['apk_sha256'],apk_hash)
        self.assertTrue((self.fixture.root/'beta-site/fdroid/beta/repo'/f'{channels.BETA.package}_{code}.apk').is_file())
        self.assertEqual((self.fixture.root/'beta-site/fdroid/beta/repo/collie-icon.png').read_bytes(),b'fixture icon')
        self.assertFalse((self.fixture.root/'beta-site/fdroid/repo').exists())
        generated=json.loads((self.fixture.root/'beta-site/fdroid/beta/repo/index-v2.json').read_text())
        self.assertEqual(set(generated['packages']),{channels.BETA.package})
        self.assertNotIn('releaseChannels',generated['packages'][channels.BETA.package])

    def test_preindex_rejection_preserves_preexisting_destination_and_parent(self):
        archive=self._pack_preindex_input(self.fixture.root/'existing-input.tar')
        digest=hashlib.sha256(archive.read_bytes()).hexdigest()
        api=lambda endpoint:self._preindex_api(endpoint,archive,digest)
        scratch=self.fixture.root/'existing-scratch';scratch.mkdir(mode=0o700)
        existing=scratch/'existing';existing.mkdir()
        sentinel=existing/'sentinel';sentinel.write_text('preserve')
        for destination in (existing,scratch/'..'):
            with self.subTest(destination=destination),self.assertRaises(ValueError):
                evidence.verify_release_input_archive(archive,bundle_sha256=digest,asset_id=771,
                    scratch=scratch,destination=destination,tools=self.tools,api=api)
            self.assertEqual(sentinel.read_text(),'preserve')
            self.assertTrue(archive.is_file())

    def test_preindex_final_manifest_fields_must_match_approved_receipt(self):
        archive=self._pack_preindex_input(self.fixture.root/'binding-input.tar')
        for field in ('commit','source_commit','certificate_sha256','engineering_revision',
                      'signed_sha256'):
            with self.subTest(field=field):
                root=self.fixture.root/('binding-'+field)
                evidence.extract_release_input_archive(archive,root)
                final_path=root/'manifests/final-manifest.json'
                final=json.loads(final_path.read_text())
                if field=='signed_sha256':final['apks'][0][field]='b'*64
                else:
                    width=64 if field=='certificate_sha256' else 40
                    final[field]=('0' if final.get(field)!='0'*width else '1')*width
                self._write_json(final_path,final)
                digest=hashlib.sha256(final_path.read_bytes()).hexdigest()
                release_path=root/f'bundles/{self.selected_id}/release.json'
                release=receipts.load(release_path,'release',profile=channels.BETA.name)
                release['payload']['release_manifest_sha256']=digest
                self._write_json(release_path,release)
                approval_path=root/f'releases/{self.selected_id}.json'
                approval=json.loads(approval_path.read_text())
                approval['release_manifest_sha256']=digest
                approval['receipt_approvals']['release']=receipts.digest(release,profile=channels.BETA.name)
                approval['release_receipt_sha256']=approval['receipt_approvals']['release']
                self._write_json(approval_path,approval)
                with self.assertRaisesRegex(ValueError,'manifest differs from pre-index release receipt'):
                    evidence.validate_preindex_release(root,self.selected_id,self.tools,
                        run_api=self._source_api,tag_verifier=lambda *_args:None)

    def test_preindex_extraction_retains_canonical_parent_when_alias_changes(self):
        archive=self._pack_preindex_input(self.fixture.root/'alias-input.tar')
        digest=hashlib.sha256(archive.read_bytes()).hexdigest()
        scratch=self.fixture.root/'alias-scratch';scratch.mkdir(mode=0o700)
        outside=self.fixture.root/'outside';(outside/'new').mkdir(parents=True)
        sentinel=outside/'new/sentinel';sentinel.write_text('preserve')
        alias=self.fixture.root/'alias';alias.symlink_to(scratch,target_is_directory=True)
        def change_alias(*_args,**_kwargs):
            alias.unlink();alias.symlink_to(outside,target_is_directory=True)
            raise ValueError('controlled extraction failure after creation')
        with patch.object(evidence.tarfile,'open',side_effect=change_alias), \
                self.assertRaisesRegex(ValueError,'controlled extraction failure'):
            evidence.verify_release_input_archive(archive,bundle_sha256=digest,asset_id=771,
                scratch=scratch,destination=alias/'new',tools=self.tools,
                api=lambda endpoint:self._preindex_api(endpoint,archive,digest))
        self.assertEqual(sentinel.read_text(),'preserve')
        self.assertFalse((scratch/'new').exists())

    def test_preindex_release_input_authenticates_archive_and_graph(self):
        archive=self._pack_preindex_input(self.fixture.root/'approved-input.tar')
        digest=hashlib.sha256(archive.read_bytes()).hexdigest()
        api=lambda endpoint:self._preindex_api(endpoint,archive,digest)
        scratch=self.fixture.root/'preindex-scratch';scratch.mkdir(mode=0o700)
        extracted=scratch/'verified-input'
        result=evidence.verify_release_input_archive(archive,bundle_sha256=digest,asset_id=771,
            scratch=scratch,destination=extracted,tools=self.tools,api=api,run_api=self._source_api,
            tag_verifier=lambda _repo,_tag,_commit:None)
        self.assertEqual(result['source_commit'],self.fixture.commit)
        self.assertEqual(result['package'],channels.BETA.package)
        self.assertEqual(result['apk_sha256'],self.final['apks'][0]['signed_sha256'])
        self.assertEqual(extracted.joinpath('apk/trailblazer-beta.apk').read_bytes(),
                         (self.root/'apk/trailblazer-beta.apk').read_bytes())

    def test_preindex_release_input_rejects_wrong_asset_digest_and_unknown_tar_member(self):
        archive=self._pack_preindex_input(self.fixture.root/'bad-input.tar')
        digest=hashlib.sha256(archive.read_bytes()).hexdigest()
        api=lambda endpoint:self._preindex_api(endpoint,archive,digest)
        scratch=self.fixture.root/'preindex-reject-scratch';scratch.mkdir(mode=0o700)
        with self.assertRaisesRegex(ValueError,'metadata differs'):
            evidence.verify_release_input_archive(archive,bundle_sha256='0'*64,asset_id=771,
                scratch=scratch,tools=self.tools,api=api,run_api=self._source_api,
                tag_verifier=lambda *_:None)
        extra=self.fixture.root/'extra.txt';extra.write_text('unexpected')
        unsafe=self.fixture.root/'unsafe-input.tar'
        with tarfile.open(unsafe,'w',format=tarfile.USTAR_FORMAT) as tar:
            for path in sorted([*self.root.rglob('*'),extra]):
                if path.is_file():
                    name=path.relative_to(self.root).as_posix() if path.is_relative_to(self.root) else 'unknown/extra.txt'
                    info=tarfile.TarInfo(name);info.size=path.stat().st_size;info.mode=0o644;info.uid=info.gid=0;info.mtime=0
                    with path.open('rb') as stream:tar.addfile(info,stream)
        with self.assertRaisesRegex(ValueError,'unsafe|unexpected'):
            evidence.extract_release_input_archive(unsafe,scratch/'unsafe-extracted')

    def test_preindex_release_input_rejects_changed_bytes_and_duplicate_members(self):
        archive=self._pack_preindex_input(self.fixture.root/'changed-input.tar')
        digest=hashlib.sha256(archive.read_bytes()).hexdigest()
        api=lambda endpoint:self._preindex_api(endpoint,archive,digest)
        with archive.open('ab') as stream:stream.write(b'changed after approval')
        scratch=self.fixture.root/'changed-input-scratch';scratch.mkdir(mode=0o700)
        with self.assertRaisesRegex(ValueError,'local release-input bytes'):
            evidence.verify_release_input_archive(archive,bundle_sha256=digest,asset_id=771,
                scratch=scratch,tools=self.tools,api=api,run_api=self._source_api,
                tag_verifier=lambda *_:None)
        duplicate=self.fixture.root/'duplicate-input.tar'
        with tarfile.open(duplicate,'w',format=tarfile.USTAR_FORMAT) as tar:
            for index in range(2):
                info=tarfile.TarInfo('apk/trailblazer-beta.apk');info.size=3;info.mode=0o644
                info.uid=info.gid=info.mtime=0
                tar.addfile(info,io.BytesIO(b'apk'))
        with self.assertRaisesRegex(ValueError,'duplicate|unsafe'):
            evidence.extract_release_input_archive(duplicate,scratch/'duplicate-extracted')

    def test_pack_rejects_wrong_channel_and_missing_indexed_bytes(self):
        approval_path = self.root / f'releases/{self.selected_id}.json'
        original = approval_path.read_bytes()
        approval = json.loads(original)
        approval['package'] = 'im.collie.app'
        self._write_json(approval_path, approval)
        with patch.dict(sys.modules, self.index_modules), self.assertRaises(ValueError):
            evidence.pack(self.root, self.fixture.root / 'bad-channel.tar', self.tools, run_api=self._source_api)
        approval_path.write_bytes(original)
        indexed_apk = self.root / 'candidate' / f'{channels.BETA.package}_{self.fixture.version_code}.apk'
        saved = indexed_apk.read_bytes()
        indexed_apk.write_bytes(saved + b'tamper')
        with patch.dict(sys.modules, self.index_modules), self.assertRaises(ValueError):
            evidence.pack(self.root, self.fixture.root / 'bad-indexed-apk.tar', self.tools, run_api=self._source_api)
        indexed_apk.write_bytes(saved)

    def test_pack_output_must_not_be_inside_the_input_tree(self):
        output = self.root / 'candidate-output.tar'
        with self.assertRaisesRegex(ValueError, 'outside its input directory'):
            evidence.pack(self.root, output, self.tools)
        self.assertFalse(output.exists())

    def test_inventory_rejects_special_files_instead_of_omitting_them(self):
        special = self.root / 'candidate/fifo'
        os.mkfifo(special)
        try:
            with self.assertRaisesRegex(ValueError, 'non-regular file'):
                evidence.inventory(self.root)
        finally:
            special.unlink()

    def test_pack_checks_actual_signed_apk_identity_and_asset_bytes(self):
        tools_root = self.fixture.root / 'tampered-tools'
        tools_root.mkdir()
        for tool in self.tools.values():
            shutil.copyfile(tool, tools_root / tool.name)
            (tools_root / tool.name).chmod(0o755)
        aapt = tools_root / self.tools['aapt'].name
        apksigner = tools_root / self.tools['apksigner'].name
        original_aapt, original_signer = aapt.read_text(), apksigner.read_text()
        cases = (
            ('package', original_aapt.replace(channels.BETA.package, 'im.collie.app')),
            ('version', original_aapt.replace(self.fixture.version, '8.35.2-1-unvalidated')),
            ('certificate', original_signer.replace(channels.BETA.apk_certificate, 'd' * 64)),
        )
        for name, script in cases:
            aapt.write_text(original_aapt)
            apksigner.write_text(original_signer)
            (apksigner if name == 'certificate' else aapt).write_text(script)
            with self.subTest(identity=name), patch.dict(sys.modules, self.index_modules), self.assertRaisesRegex(
                    ValueError, 'actual signed APK differs'):
                evidence.pack(self.root, self.fixture.root / ('wrong-' + name + '.tar'),
                    {'aapt': aapt, 'apksigner': apksigner}, run_api=self._source_api,
                    tag_verifier=lambda *_args: None, release_api=self._readiness_api)
        signed_apk = self.root / 'apk/trailblazer-beta.apk'
        original_apk = signed_apk.read_bytes()
        signed_apk.write_bytes(original_apk + b'asset-tamper')
        try:
            with patch.dict(sys.modules, self.index_modules), self.assertRaises(ValueError):
                evidence.pack(self.root, self.fixture.root / 'wrong-asset.tar', self.tools,
                    run_api=self._source_api, tag_verifier=lambda *_args: None, release_api=self._readiness_api)
        finally:
            signed_apk.write_bytes(original_apk)

    def test_pack_rejects_source_ci_metadata_claims_that_github_does_not_confirm(self):
        valid = dict(self.fixture.run_metadata)
        changes = (
            {'id': valid['id'] + 1}, {'run_attempt': valid['run_attempt'] + 1},
            {'head_repository': {'full_name': 'attacker/repo'}}, {'head_branch': 'main'},
            {'head_sha': 'f' * 40}, {'path': '.github/workflows/other.yml'},
            {'workflow_id': valid['workflow_id'] + 1}, {'status': 'in_progress'},
            {'conclusion': 'failure'}, {'event': 'push'},
        )
        for change in changes:
            claimed = dict(valid)
            claimed.update(change)
            with self.subTest(change=change), patch.dict(sys.modules, self.index_modules), self.assertRaisesRegex(
                    ValueError, 'authoritative Beta source CI run differs'):
                evidence.pack(self.root, self.fixture.root / 'bad-source-run.tar', self.tools,
                    run_api=lambda _endpoint, response=claimed: response,
                    tag_verifier=lambda *_args: None, release_api=self._readiness_api)

    def test_pack_rejects_receipt_byte_substitution_even_when_json_remains_parseable(self):
        path = self.root / f'bundles/{self.selected_id}/build.json'
        original = path.read_bytes()
        build = json.loads(original)
        build['payload']['source_commit'] = 'f' * 40
        self._write_json(path, build)
        try:
            with patch.dict(sys.modules, self.index_modules), self.assertRaises(ValueError):
                evidence.pack(self.root, self.fixture.root / 'receipt-substitution.tar', self.tools,
                    run_api=self._source_api, tag_verifier=lambda *_args: None,
                    release_api=self._readiness_api)
        finally:
            path.write_bytes(original)

    def test_input_allowlist_excludes_future_pages_approval_and_readiness_artifact(self):
        for name in ('approval.json', 'trailblazer-beta-readiness.json', 'future/readiness.zip'):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'not part of pre-readiness evidence')
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'unexpected bundle input'):
                evidence.inventory(self.root)
            path.unlink()

    def test_pack_requires_exact_signed_tag_and_beta_draft_metadata(self):
        archive = self.fixture.root / 'not-approved.tar'
        with patch.dict(sys.modules, self.index_modules), self.assertRaisesRegex(ValueError, 'signed tag'):
            evidence.pack(self.root, archive, self.tools, run_api=self._source_api,
                          tag_verifier=lambda *_args: (_ for _ in ()).throw(ValueError('signed tag rejected')),
                          release_api=self._readiness_api)
        self.assertFalse(archive.exists())
        for change in ({'id': self.selected_id + 1}, {'id': True},
                       {'name': 'wrong title'}, {'prerelease': False}):
            def wrong_release(endpoint, change=change):
                record = self._readiness_api(endpoint)
                if endpoint == f'repos/0cwa/collieim-android/releases/{self.selected_id}':
                    record.update(change)
                return record
            with self.subTest(change=change), patch.dict(sys.modules, self.index_modules), self.assertRaisesRegex(
                    ValueError, 'draft release identity'):
                evidence.pack(self.root, archive, self.tools, run_api=self._source_api,
                              tag_verifier=lambda *_args: None, release_api=wrong_release)
        def wrong_asset(endpoint):
            record = self._readiness_api(endpoint)
            if endpoint == f'repos/0cwa/collieim-android/releases/{self.selected_id}':
                record['assets'][0]['digest'] = 'sha256:' + 'f' * 64
            return record
        with patch.dict(sys.modules, self.index_modules), self.assertRaisesRegex(ValueError, 'APK asset'):
            evidence.pack(self.root, archive, self.tools, run_api=self._source_api,
                          tag_verifier=lambda *_args: None, release_api=wrong_asset)

    def test_extract_rejects_traversal_duplicates_symlinks_and_unknown_files(self):
        for kind in ('traversal', 'duplicate', 'symlink', 'hardlink', 'unknown', 'noncanonical-mode'):
            archive_path = self.fixture.root / (kind + '.tar')
            with tarfile.open(archive_path, 'w') as archive:
                if kind == 'traversal':
                    member = tarfile.TarInfo('../escape')
                    member.size = 1
                    archive.addfile(member, io.BytesIO(b'x'))
                elif kind == 'duplicate':
                    for _ in range(2):
                        member = tarfile.TarInfo('candidate/entry.jar')
                        member.size = 1
                        archive.addfile(member, io.BytesIO(b'x'))
                elif kind == 'symlink':
                    member = tarfile.TarInfo('candidate/entry.jar')
                    member.type = tarfile.SYMTYPE
                    member.linkname = '/etc/passwd'
                    archive.addfile(member)
                elif kind == 'hardlink':
                    member = tarfile.TarInfo('candidate/entry.jar')
                    member.type = tarfile.LNKTYPE
                    member.linkname = 'candidate/other.jar'
                    archive.addfile(member)
                elif kind == 'noncanonical-mode':
                    member = tarfile.TarInfo('candidate/entry.jar')
                    member.size = 1
                    member.mode = 0o755
                    archive.addfile(member, io.BytesIO(b'x'))
                else:
                    member = tarfile.TarInfo('unknown.txt')
                    member.size = 1
                    archive.addfile(member, io.BytesIO(b'x'))
            destination = self.fixture.root / (kind + '-out')
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                evidence.extract_archive(archive_path, destination)
            self.assertFalse(destination.exists())

    def test_extract_rejects_extension_headers_before_tarfile_preprocessing(self):
        for kind, method in ((tarfile.XHDTYPE, '_proc_pax'), (tarfile.XGLTYPE, '_proc_pax'),
                             (tarfile.GNUTYPE_LONGNAME, '_proc_gnulong'),
                             (tarfile.GNUTYPE_LONGLINK, '_proc_gnulong'),
                             (tarfile.GNUTYPE_SPARSE, '_proc_sparse')):
            archive_path = self.fixture.root / ('extension-' + kind.decode() + '.tar')
            member = tarfile.TarInfo('candidate/entry.jar')
            member.type = kind
            member.size = 1024 * 1024
            # Deliberately omit the payload: rejection must happen before any
            # extension parser attempts to read or decode its advertised size.
            archive_path.write_bytes(member.tobuf(format=tarfile.USTAR_FORMAT))
            destination = self.fixture.root / ('extension-' + kind.decode() + '-out')
            with self.subTest(kind=kind), patch.object(tarfile.TarInfo, method,
                    side_effect=AssertionError('extension parser must not run')) as parser:
                with self.assertRaises(ValueError):
                    evidence.extract_archive(archive_path, destination)
                parser.assert_not_called()
            self.assertFalse(destination.exists())

    def test_emitter_does_not_overwrite_output_created_after_precheck(self):
        archive = self.fixture.root / 'evidence.tar'
        tag_verifier = lambda *_args: None
        with patch.dict(sys.modules, self.index_modules):
            packed = evidence.pack(self.root, archive, self.tools, run_api=self._source_api,
                                   tag_verifier=tag_verifier, release_api=self._readiness_api)
        self.archive, self.bundle_sha = archive, packed['sha256']
        scratch = self.fixture.root / 'race-scratch'; scratch.mkdir(mode=0o700)
        original_is_symlink = Path.is_symlink
        for symlink in (False, True):
            output = self.fixture.root / ('race-output-' + str(symlink))
            victim = self.fixture.root / ('race-victim-' + str(symlink))
            victim.write_bytes(b'preserve existing bytes')
            injected = []
            def raced_is_symlink(path):
                result = original_is_symlink(path)
                if path == output and not result and not injected:
                    injected.append(True)
                    if symlink:
                        output.symlink_to(victim)
                    else:
                        output.write_bytes(b'preserve existing bytes')
                return result
            with self.subTest(symlink=symlink), patch.dict(sys.modules, self.index_modules),\
                    patch.object(Path, 'is_symlink', raced_is_symlink):
                with self.assertRaises((ValueError, FileExistsError)):
                    evidence.emit_readiness(bundle_sha256=self.bundle_sha, asset_id=771,
                        run_id=12345, attempt=3, output=output, scratch=scratch, tools=self.tools,
                        api=self._readiness_api, downloader=lambda _id, path: shutil.copyfile(archive, path),
                        source_run_api=self._source_api, tag_verifier=tag_verifier)
            self.assertTrue(injected)
            self.assertEqual(output.read_bytes(), b'preserve existing bytes')
            self.assertEqual(victim.read_bytes(), b'preserve existing bytes')

    def test_emit_rejects_bundle_hash_asset_and_authoritative_run_tampering(self):
        archive = self.fixture.root / 'evidence.tar'
        tag_verifier = lambda _repo, _tag, _commit: None
        with patch.dict(sys.modules, self.index_modules):
            packed = evidence.pack(self.root, archive, self.tools, run_api=self._source_api,
                                   tag_verifier=tag_verifier, release_api=self._readiness_api)
        self.archive, self.bundle_sha = archive, packed['sha256']
        scratch = self.fixture.root / 'scratch'; scratch.mkdir(mode=0o700)
        def run_emit(**overrides):
            values = dict(bundle_sha256=self.bundle_sha, asset_id=771, run_id=12345, attempt=3,
                output=self.fixture.root / ('output-' + str(len(list(self.fixture.root.glob('output-*'))))),
                scratch=scratch, tools=self.tools, api=self._readiness_api,
                downloader=lambda _asset_id, path: shutil.copyfile(archive, path), source_run_api=self._source_api,
                tag_verifier=tag_verifier)
            values.update(overrides)
            with patch.dict(sys.modules, self.index_modules):
                return evidence.emit_readiness(**values)
        with self.assertRaisesRegex(ValueError, 'asset metadata'):
            run_emit(bundle_sha256='f' * 64)
        with self.assertRaisesRegex(ValueError, 'asset metadata'):
            run_emit(asset_id=772)
        def bad_api(endpoint):
            if '/releases/' in endpoint:
                return self._readiness_api(endpoint)
            return {'id': 12345, 'run_attempt': 3,
                'head_repository': {'full_name': '0cwa/collieim-android'}, 'head_branch': 'main',
                'head_sha': self.fixture.commit, 'path': channels.BETA.readiness_workflow,
                'status': 'completed', 'conclusion': 'success', 'event': 'workflow_dispatch'}
        with self.assertRaisesRegex(ValueError, 'authoritative in-progress Beta readiness'):
            run_emit(api=bad_api)

    def test_emitter_requires_its_own_run_to_be_authoritative_and_in_progress(self):
        valid = self._readiness_api('readiness-run')
        for change in (
                {'id': valid['id'] + 1}, {'run_attempt': valid['run_attempt'] + 1},
                {'head_repository': {'full_name': 'attacker/repo'}},
                {'head_branch': 'main'}, {'head_sha': 'f' * 40},
                {'path': '.github/workflows/other.yml'}, {'status': 'completed'},
                {'status': 'queued'}, {'event': 'pull_request'}):
            record = dict(valid); record.update(change)
            with self.subTest(change=change), self.assertRaisesRegex(
                    ValueError, 'authoritative in-progress Beta readiness'):
                evidence.readiness_run(12345, 3, self.fixture.commit,
                                       api=lambda _endpoint, response=record: response)

    def test_asset_metadata_requires_exact_uploaded_asset_and_approved_digest(self):
        for change in ({'name': 'other.tar'}, {'state': 'new'}, {'size': 0},
                       {'digest': 'sha256:' + 'f' * 64}):
            valid = {'id': 771, 'name': evidence.ASSET_NAME, 'state': 'uploaded',
                     'size': 1024, 'digest': 'sha256:' + 'a' * 64}
            valid.update(change)
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, 'asset metadata'):
                evidence.verify_asset_metadata(771, 'a' * 64, api=lambda _endpoint: valid)

    def test_emitter_rejects_shared_or_group_readable_scratch(self):
        scratch = self.fixture.root / 'shared-scratch'
        scratch.mkdir(mode=0o755)
        with self.assertRaisesRegex(ValueError, 'owned private scratch'):
            evidence.emit_readiness(bundle_sha256='a' * 64, asset_id=771,
                run_id=12345, attempt=3, output=self.fixture.root / 'never.json',
                scratch=scratch, tools=self.tools,
                api=lambda _endpoint: {'id': 771, 'name': evidence.ASSET_NAME, 'state': 'uploaded',
                    'size': 10, 'digest': 'sha256:' + 'a' * 64})

    def test_emit_rejects_downloaded_bytes_that_disagree_with_approved_archive(self):
        archive = self.fixture.root / 'evidence.tar'
        tag_verifier = lambda *_args: None
        with patch.dict(sys.modules, self.index_modules):
            packed = evidence.pack(self.root, archive, self.tools, run_api=self._source_api,
                                   tag_verifier=tag_verifier, release_api=self._readiness_api)
        self.archive, self.bundle_sha = archive, packed['sha256']
        scratch = self.fixture.root / 'tamper-scratch'; scratch.mkdir(mode=0o700)
        output = self.fixture.root / 'tamper-readiness.json'
        def changed_download(_asset_id, path):
            shutil.copyfile(archive, path)
            with open(path, 'r+b') as stream:
                original = stream.read(1)
                stream.seek(0)
                stream.write(bytes([original[0] ^ 1]))
        with self.assertRaisesRegex(ValueError, 'downloaded fixed engineering asset bytes'):
            evidence.emit_readiness(bundle_sha256=self.bundle_sha, asset_id=771, run_id=12345, attempt=3,
                output=output, scratch=scratch, tools=self.tools, api=self._readiness_api,
                downloader=changed_download, source_run_api=self._source_api, tag_verifier=tag_verifier)
        self.assertFalse(output.exists())

    def test_standalone_verifier_binds_local_bytes_to_fixed_asset_sha(self):
        archive = self.fixture.root / 'evidence.tar'
        tag_verifier = lambda *_args: None
        with patch.dict(sys.modules, self.index_modules):
            packed = evidence.pack(self.root, archive, self.tools, run_api=self._source_api,
                tag_verifier=tag_verifier, release_api=self._readiness_api)
        self.archive, self.bundle_sha = archive, packed['sha256']
        scratch = self.fixture.root / 'standalone-scratch'
        scratch.mkdir(mode=0o700)
        archive.write_bytes(archive.read_bytes() + b'not-approved')
        with self.assertRaisesRegex(ValueError, 'local fixed engineering asset bytes'):
            evidence.verify_archive(archive, bundle_sha256=self.bundle_sha, asset_id=771,
                scratch=scratch, tools=self.tools, api=self._readiness_api,
                run_api=self._source_api, tag_verifier=tag_verifier,
                release_api=self._readiness_api)


if __name__ == '__main__':
    unittest.main()
