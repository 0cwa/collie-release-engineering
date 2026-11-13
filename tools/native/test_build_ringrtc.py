import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import build_ringrtc

class RingRtcDiagnosticTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='ringrtc-diagnostic-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.lock = self.root / 'lock.json'
        self.lock.write_text('{}')
        self.sources = self.root / 'sources'
        (self.sources / 'ringrtc/src/rust/src/webrtc_include').mkdir(parents=True)
        self.workspace = self.root / 'workspace'
        (self.workspace / 'src/ringrtc/rffi').mkdir(parents=True)
        (self.workspace / 'setup-receipt.json').write_text(json.dumps({'status':'PARTIAL_WEBRTC_SETUP','release_allowed':False,'lock_sha256':hashlib.sha256(self.lock.read_bytes()).hexdigest()}))
        (self.workspace / 'src/third_party/jdk/current').mkdir(parents=True)
        self.java = self.workspace / 'src/third_party/jdk/current'
        (self.root / 'toolchain/bin').mkdir(parents=True)
        (self.root / 'toolchain/bin/rustc').write_text('fixture compiler')
        self.output = self.root / 'out'
        self.expected = b'aar'
        self.config = {'source_provenance':{'webrtc':'base'}, 'toolchain_reference':{'webrtc_depot_tools_revision':'depot'}, 'modules':[{'name':'ringrtc-android','binary_sha256':hashlib.sha256(self.expected).hexdigest()}]}
        self.proof = {'sources':{'webrtc':{'prepared_tree':'tree'}}}
        (self.workspace / 'setup-receipt.json').write_text(json.dumps({'schema':1,'status':'PARTIAL_WEBRTC_SETUP','release_allowed':False,'scope':'gclient setup and patched source tree only; downloaded closure, native build and runtime unverified','lock_sha256':hashlib.sha256(self.lock.read_bytes()).hexdigest(),'webrtc_base':'base','webrtc_tree':'tree','depot_tools_revision':'depot'}))
        self.env = {'PATH':'/usr/bin', 'CLOUDSDK_AUTH_ACCESS_TOKEN':'do-not-use', 'ORG_GRADLE_PROJECT_signingKey':'do-not-use'}

    def git(self, _path, *args):
        if args[0] == 'rev-parse': return 'base'
        if args[0] == 'write-tree': return 'tree'
        if args[0] == 'diff': return ''
        raise AssertionError(args)

    def run_build(self, fail=False, nested_effect=None, cipd_effect=None, rust_effect=None, ndk_effect=None, fresh=False, worktree_effect=None):
        def runner(args, cwd, env):
            self.assertEqual('--release', args[-1])
            self.assertEqual(str(self.root / 'gradle/bin/gradle'), env['COLLIE_VERIFIED_GRADLE'])
            self.assertNotIn('CLOUDSDK_AUTH_ACCESS_TOKEN', env)
            self.assertNotIn('ORG_GRADLE_PROJECT_signingKey', env)
            if fresh:
                self.assertEqual('1', env['COLLIE_NATIVE_FRESH'])
                self.assertEqual('true', env['CARGO_NET_OFFLINE'])
                self.assertEqual('0', env['CARGO_INCREMENTAL'])
                self.assertEqual('', env['RUSTC_WRAPPER'])
            self.assertEqual(self.workspace, (self.sources / 'ringrtc/src/webrtc').resolve())
            if fail: raise build_ringrtc.Failure('simulated failure')
            aar = self.output / 'gradle/outputs/aar/ringrtc-release.aar'
            aar.parent.mkdir(parents=True)
            aar.write_bytes(self.expected)
        with patch.object(build_ringrtc.stage_artifacts, 'load_lock', return_value=self.config), patch.object(build_ringrtc.verify_gradle, 'verify', return_value=self.root / 'gradle/bin/gradle'), patch.object(build_ringrtc.nested_git, 'verify', return_value=70, side_effect=nested_effect), patch.object(build_ringrtc.cipd_payloads, 'verify', return_value=(59,26010), side_effect=cipd_effect), patch.object(build_ringrtc.rust_payloads, 'verify', return_value=372, side_effect=rust_effect), patch.object(build_ringrtc.ndk_payloads, 'verify', return_value=8449, side_effect=ndk_effect), patch.object(build_ringrtc.build_sources, 'preflight', return_value=(self.config,self.proof,self.env,'cargo')), patch.object(build_ringrtc.prepare_sources, 'git', side_effect=self.git), patch.object(build_ringrtc.prepare_sources, 'verify_prepared', return_value=self.proof), patch.object(build_ringrtc.prepare_sources, 'verify_worktree', side_effect=worktree_effect):
            return build_ringrtc.diagnostic_build(self.lock, self.sources, self.workspace, self.root, self.java, self.root / 'toolchain/bin', self.output, self.root / 'gradle.zip', self.root / 'gradle', runner, fresh=fresh)

    def test_only_partial_receipt_and_no_upload_credentials(self):
        result = self.run_build()
        self.assertFalse(result['release_allowed'])
        self.assertEqual('PARTIAL_RINGRTC_BUILD', result['status'])
        self.assertFalse((self.sources / 'ringrtc/src/webrtc').exists())
        self.assertFalse((self.workspace / 'src/ringrtc/rffi/api').is_symlink())

    def test_fresh_build_records_scope_and_rejects_inherited_fresh_flag(self):
        self.env['COLLIE_NATIVE_FRESH'] = 'unexpected'
        result = self.run_build(fresh=True)
        self.assertTrue(result['fresh_output_check'])
        self.assertFalse(result['release_allowed'])
        self.assertEqual('1', result['selected_environment']['COLLIE_NATIVE_FRESH'])

    def test_nonfresh_build_does_not_inherit_fresh_claim(self):
        self.env['COLLIE_NATIVE_FRESH'] = '1'
        result = self.run_build()
        self.assertFalse(result['fresh_output_check'])
        self.assertIsNone(result['selected_environment']['COLLIE_NATIVE_FRESH'])

    def test_fresh_build_rejects_previous_web_rtc_and_cargo_outputs(self):
        for root, relative in ((self.workspace / 'src', 'out'),
                               (self.workspace / 'src', '.ninja_log'),
                               (self.workspace / 'src', '.ninja_deps'),
                               (self.workspace / 'src', 'build.ninja'),
                               (self.workspace / 'src', 'build/config/siso/.sisorc'),
                               (self.sources / 'ringrtc', 'build'),
                               (self.sources / 'ringrtc', '.gradle'),
                               (self.sources / 'ringrtc', 'target'),
                               (self.sources / 'ringrtc', 'src/rust/target')):
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('old compiler output')
            with self.subTest(relative=relative), self.assertRaisesRegex(build_ringrtc.Failure, 'Fresh diagnostic output already exists'):
                self.run_build(fresh=True)
            self.assertEqual('old compiler output', path.read_text())
            path.unlink()
        self.assertFalse(self.output.exists())

    def test_fresh_build_rejects_dangling_output_symlink(self):
        path = self.workspace / 'src/out'
        path.symlink_to(self.root / 'missing')
        with self.assertRaisesRegex(build_ringrtc.Failure, 'Fresh diagnostic output already exists'):
            self.run_build(fresh=True)
        self.assertTrue(path.is_symlink())

    def test_fresh_mismatch_receipt_cannot_authorize_release(self):
        self.expected = b'different artifact'
        with self.assertRaisesRegex(build_ringrtc.Failure, 'reproducibility is unproved'):
            self.run_build(fresh=True)
        result = json.loads((self.output / 'failure-receipt.json').read_text())
        self.assertTrue(result['fresh_output_check'])
        self.assertFalse(result['release_allowed'])

    def test_nested_dependency_change_before_or_after_build_rejects_receipt(self):
        for effect in ([build_ringrtc.nested_git.Failure('before')], [70, build_ringrtc.nested_git.Failure('after')]):
            with self.subTest(effect=effect):
                with self.assertRaises(build_ringrtc.nested_git.Failure): self.run_build(nested_effect=effect)
                self.assertFalse((self.output / 'build-receipt.json').exists())
                if self.output.exists():
                    import shutil
                    shutil.rmtree(self.output)

    def test_active_web_rtc_worktree_change_before_or_after_build_rejects_receipt(self):
        for effect in ([build_ringrtc.prepare_sources.Failure('before')],
                       [None, build_ringrtc.prepare_sources.Failure('after')]):
            with self.subTest(effect=effect):
                with self.assertRaises(build_ringrtc.prepare_sources.Failure):
                    self.run_build(worktree_effect=effect)
                self.assertFalse((self.output / 'build-receipt.json').exists())
                if self.output.exists():
                    import shutil
                    shutil.rmtree(self.output)

    def test_cipd_change_before_or_after_build_rejects_receipt(self):
        for effect in ([build_ringrtc.cipd_payloads.Failure('before')], [(59,26010), build_ringrtc.cipd_payloads.Failure('after')]):
            with self.subTest(effect=effect):
                with self.assertRaises(build_ringrtc.cipd_payloads.Failure): self.run_build(cipd_effect=effect)
                self.assertFalse((self.output / 'build-receipt.json').exists())
                if self.output.exists():
                    import shutil
                    shutil.rmtree(self.output)

    def test_rust_payload_change_after_build_rejects_receipt(self):
        with self.assertRaises(build_ringrtc.rust_payloads.Failure): self.run_build(rust_effect=[build_ringrtc.rust_payloads.Failure('after')])
        self.assertFalse((self.output / 'build-receipt.json').exists())

    def test_ndk_change_after_build_rejects_receipt(self):
        with self.assertRaises(build_ringrtc.ndk_payloads.Failure): self.run_build(ndk_effect=[build_ringrtc.ndk_payloads.Failure('after')])
        self.assertFalse((self.output / 'build-receipt.json').exists())

    def test_failure_removes_only_own_link(self):
        with self.assertRaises(build_ringrtc.Failure): self.run_build(fail=True)
        self.assertFalse((self.sources / 'ringrtc/src/webrtc').exists())
        self.assertFalse((self.output / 'build-receipt.json').exists())
        self.assertFalse((self.workspace / 'src/ringrtc/rffi/api').is_symlink())

    def test_mismatched_output_fails_with_failure_only_receipt(self):
        self.expected = b'different artifact'
        with self.assertRaisesRegex(build_ringrtc.Failure, 'reproducibility is unproved'):
            self.run_build()
        receipt = json.loads((self.output / 'failure-receipt.json').read_text())
        self.assertEqual('FAILED_ARTIFACT_MISMATCH', receipt['status'])
        self.assertFalse(receipt['release_allowed'])
        self.assertNotEqual(receipt['aar_sha256'], receipt['expected_aar_sha256'])
        self.assertFalse((self.output / 'build-receipt.json').exists())
        with self.assertRaisesRegex(build_ringrtc.Failure, 'already exists'):
            self.run_build()

    def test_lock_change_during_build_rejects_report(self):
        old_digest = build_ringrtc.stage_artifacts.digest
        def changing_digest(path):
            self.digest_calls += 1
            return 'changed' if self.digest_calls > 3 else old_digest(path)
        self.digest_calls = 0
        with patch.object(build_ringrtc.stage_artifacts, 'digest', side_effect=changing_digest):
            with self.assertRaisesRegex(build_ringrtc.Failure, 'lock changed'):
                self.run_build()
        self.assertFalse((self.output / 'build-receipt.json').exists())
        self.assertFalse((self.output / 'failure-receipt.json').exists())

    def test_existing_api_link_is_preserved(self):
        api = self.workspace / 'src/ringrtc/rffi/api'
        api.symlink_to(self.sources / 'ringrtc/src/rust/src/webrtc_include', target_is_directory=True)
        self.run_build()
        self.assertTrue(api.is_symlink())


    def test_failed_second_link_creation_cleans_first_link(self):
        original = Path.symlink_to
        def create(path, target, *args, **kwargs):
            if path == self.sources / 'ringrtc/src/webrtc':
                raise OSError('cannot create workspace link')
            return original(path, target, *args, **kwargs)
        with patch.object(Path, 'symlink_to', create):
            with self.assertRaises(OSError):
                self.run_build()
        self.assertFalse((self.workspace / 'src/ringrtc/rffi/api').is_symlink())

    def test_forged_receipt_is_rejected_before_running_any_toolchain(self):
        receipt_path = self.workspace / 'setup-receipt.json'
        original = json.loads(receipt_path.read_text())
        for field in ('webrtc_base', 'webrtc_tree', 'depot_tools_revision'):
            with self.subTest(field=field):
                forged = dict(original)
                forged[field] = 'forged'
                receipt_path.write_text(json.dumps(forged))
                with patch.object(build_ringrtc.stage_artifacts, 'load_lock', return_value=self.config), patch.object(build_ringrtc.prepare_sources, 'verify_prepared', return_value=self.proof), patch.object(build_ringrtc.build_sources, 'preflight', side_effect=AssertionError('toolchain was invoked')):
                    with self.assertRaisesRegex(build_ringrtc.Failure, 'receipt does not match'):
                        build_ringrtc.diagnostic_build(self.lock, self.sources, self.workspace, self.root, self.java, self.root / 'toolchain/bin', self.output, self.root / 'gradle.zip', self.root / 'gradle')
                self.assertFalse(self.output.exists())
        receipt_path.write_text(json.dumps(original))

    def test_missing_gradle_zip_is_rejected_before_toolchain(self):
        with patch.object(build_ringrtc.stage_artifacts, 'load_lock', return_value=self.config), patch.object(build_ringrtc.prepare_sources, 'verify_prepared', return_value=self.proof), patch.object(build_ringrtc.prepare_sources, 'verify_worktree'), patch.object(build_ringrtc.prepare_sources, 'git', side_effect=self.git), patch.object(build_ringrtc.build_sources, 'preflight', side_effect=AssertionError('toolchain was invoked')):
            with self.assertRaisesRegex(build_ringrtc.verify_gradle.Failure, 'ZIP must be a regular file'):
                build_ringrtc.diagnostic_build(self.lock, self.sources, self.workspace, self.root, self.java, self.root, self.output, self.root / 'missing.zip', self.root / 'missing-distribution')
        self.assertFalse(self.output.exists())

    def test_symlinked_receipt_is_rejected(self):
        receipt_path = self.workspace / 'setup-receipt.json'
        target = self.root / 'other-receipt.json'
        receipt_path.rename(target)
        receipt_path.symlink_to(target)
        with patch.object(build_ringrtc.stage_artifacts, 'load_lock', return_value=self.config), patch.object(build_ringrtc.prepare_sources, 'verify_prepared', return_value=self.proof), patch.object(build_ringrtc.build_sources, 'preflight', side_effect=AssertionError('toolchain was invoked')):
            with self.assertRaisesRegex(build_ringrtc.Failure, 'small regular file'):
                build_ringrtc.diagnostic_build(self.lock, self.sources, self.workspace, self.root, self.java, self.root / 'toolchain/bin', self.output, self.root / 'gradle.zip', self.root / 'gradle')

    def test_existing_path_is_never_replaced(self):
        occupied = self.sources / 'ringrtc/src/webrtc'
        occupied.mkdir()
        with self.assertRaises(build_ringrtc.Failure): self.run_build()
        self.assertTrue(occupied.is_dir())
        self.assertFalse(self.output.exists())
