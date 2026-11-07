import unittest
import os
import subprocess
import tempfile
from unittest.mock import patch
from pathlib import Path
import efficient_ringrtc

class EfficientRingRtcTest(unittest.TestCase):
    def test_expected_transform_and_unexpected_source_fails_closed(self):
        source = """        './gradlew',
        '-PringrtcVersion={}
ninja_args = ['third_party/siso/cipd/siso', 'ninja', '-C', webrtc_output_dir] + NINJA_TARGETS
shutil.copytree(webrtc_output_dir, output_dir, dirs_exist_ok=True, ignore_dangling_symlinks=True)
d = os.path.basename(jar)
            os.makedirs(d, exist_ok=True)
[GetArchBuildDir(build_dir, arch, debug) for arch in ARCHS]
"""
        output = efficient_ringrtc.adapt(source)
        self.assertNotIn('shutil.copytree(', output)
        self.assertIn("'--project-dir', gradle_dir", output)
        self.assertIn("'-local_jobs', '12'", output)
        self.assertIn("GetArchBuildDir(os.path.join(webrtc_src_dir, 'out')", output)
        with self.assertRaises(RuntimeError): efficient_ringrtc.adapt(source + source)
        with self.assertRaises(RuntimeError): efficient_ringrtc.adapt('wrong source')

    def test_project_directory_passed_to_verified_gradle(self):
        source = """        './gradlew',
        '-PringrtcVersion={}'.format(version),
ninja_args = ['third_party/siso/cipd/siso', 'ninja', '-C', webrtc_output_dir] + NINJA_TARGETS
shutil.copytree(webrtc_output_dir, output_dir, dirs_exist_ok=True, ignore_dangling_symlinks=True)
d = os.path.basename(jar)
            os.makedirs(d, exist_ok=True)
[GetArchBuildDir(build_dir, arch, debug) for arch in ARCHS]
"""
        segment = efficient_ringrtc.adapt(source).split('ninja_args', 1)[0]
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            project = root / 'ringrtc project'
            project.mkdir()
            log = root / 'args.txt'
            fake = root / 'gradle'
            fake.write_text('#!/bin/sh\nprintf "%s\\n" "$PWD" "$@" > "$TEST_GRADLE_LOG"\n')
            fake.chmod(0o755)
            with patch.dict(os.environ, {'COLLIE_VERIFIED_GRADLE':str(fake), 'TEST_GRADLE_LOG':str(log)}):
                values = {'os':os, 'gradle_dir':str(project), 'version':'2.74.0'}
                exec('cmd = [\n' + segment + ']\n', values)
                subprocess.run(values['cmd'], cwd=project, check=True)
            lines = log.read_text().splitlines()
            self.assertEqual(str(project), lines[0])
            self.assertEqual(['--project-dir', str(project), '-PringrtcVersion=2.74.0'], lines[1:])
