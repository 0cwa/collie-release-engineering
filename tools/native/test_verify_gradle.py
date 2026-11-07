import hashlib
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile, ZipInfo
import verify_gradle

class VerifyGradleTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='gradle-dist-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.zip = self.root / 'distribution.zip'
        self.dist = self.root / 'gradle-9.3.1'
        (self.dist / 'bin').mkdir(parents=True)
        (self.dist / 'init.d').mkdir()
        (self.dist / 'bin/gradle').write_bytes(b'#!/bin/sh\nexit 0\n')
        (self.dist / 'bin/gradle').chmod(0o755)
        (self.dist / 'bin/gradle.bat').write_bytes(b'@echo off\r\n')
        (self.dist / 'bin/gradle.bat').chmod(0o640)
        (self.dist / 'init.d/readme.txt').write_bytes(b'readme')
        self.write_zip()

    def write_zip(self, extra=None):
        with ZipFile(self.zip, 'w') as z:
            for name, mode in [('bin/gradle',0o755),('bin/gradle.bat',0o755),('init.d/readme.txt',0o644)]:
                info=ZipInfo('gradle-9.3.1/'+name)
                info.create_system=3
                info.external_attr=(stat.S_IFREG|mode)<<16
                z.writestr(info,(self.dist/name).read_bytes())
            if extra: z.writestr(extra,b'bad')
        self.hash=hashlib.sha256(self.zip.read_bytes()).hexdigest()

    def test_matching_zip_and_extraction_pass(self):
        self.assertEqual((self.dist/'bin/gradle').resolve(),verify_gradle.verify(self.zip,self.dist,self.hash))

    def test_version_specific_distribution_root_is_required(self):
        with self.assertRaisesRegex(verify_gradle.Failure, 'Unsafe Gradle ZIP path'):
            verify_gradle.verify(self.zip, self.dist, self.hash, top='gradle-9.6.0')

    def test_changed_cached_file_and_injected_init_script_fail(self):
        (self.dist/'bin/gradle').write_bytes(b'tampered')
        with self.assertRaisesRegex(verify_gradle.Failure,'differs'): verify_gradle.verify(self.zip,self.dist,self.hash)
        (self.dist/'bin/gradle').write_bytes(b'#!/bin/sh\nexit 0\n')
        (self.dist/'init.d/evil.gradle').write_text('malicious')
        with self.assertRaisesRegex(verify_gradle.Failure,'Unexpected'): verify_gradle.verify(self.zip,self.dist,self.hash)

    def test_missing_extracted_file_is_rejected(self):
        (self.dist/'init.d/readme.txt').unlink()
        with self.assertRaisesRegex(verify_gradle.Failure,'file missing'): verify_gradle.verify(self.zip,self.dist,self.hash)

    def test_wrong_archive_checksum_is_rejected(self):
        with self.assertRaisesRegex(verify_gradle.Failure,'checksum'): verify_gradle.verify(self.zip,self.dist,'0'*64)

    def test_traversal_and_symlinks_fail(self):
        self.write_zip('gradle-9.3.1/../escape')
        with self.assertRaisesRegex(verify_gradle.Failure,'Unsafe'): verify_gradle.verify(self.zip,self.dist,self.hash)
        self.write_zip()
        (self.dist/'init.d/readme.txt').unlink()
        (self.dist/'init.d/readme.txt').symlink_to(self.zip)
        with self.assertRaisesRegex(verify_gradle.Failure,'Symlink'): verify_gradle.verify(self.zip,self.dist,self.hash)

    def test_writable_extracted_file_fails(self):
        (self.dist/'bin/gradle').chmod(0o777)
        with self.assertRaisesRegex(verify_gradle.Failure,'writable'): verify_gradle.verify(self.zip,self.dist,self.hash)

    def test_unexpected_empty_directory_is_rejected(self):
        (self.dist / 'untracked').mkdir()
        with self.assertRaisesRegex(verify_gradle.Failure, 'Unexpected extracted Gradle directory'):
            verify_gradle.verify(self.zip, self.dist, self.hash)

    def test_directory_traversal_error_is_rejected(self):
        def failing_walk(*args, **kwargs):
            kwargs['onerror'](PermissionError('fixture'))
            yield from ()
        with patch.object(verify_gradle.os, 'walk', side_effect=failing_walk):
            with self.assertRaisesRegex(verify_gradle.Failure, 'Cannot inspect'):
                verify_gradle.verify(self.zip, self.dist, self.hash)

    def test_archive_path_replacement_cannot_change_verified_zip(self):
        real_digest = verify_gradle.digest
        changed = False
        def swap_after_first_digest(stream):
            nonlocal changed
            result = real_digest(stream)
            if not changed:
                changed = True
                self.zip.rename(self.root / 'original.zip')
                self.zip.write_bytes(b'not the verified archive')
            return result
        with patch.object(verify_gradle, 'digest', side_effect=swap_after_first_digest):
            self.assertEqual((self.dist/'bin/gradle').resolve(), verify_gradle.verify(self.zip, self.dist, self.hash))

    def test_changed_executable_mode_fails(self):
        (self.dist/'bin/gradle').chmod(0o644)
        with self.assertRaisesRegex(verify_gradle.Failure,'mode differs'): verify_gradle.verify(self.zip,self.dist,self.hash)
