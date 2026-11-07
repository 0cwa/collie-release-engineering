"""Exercise the actual CI emulator step when adb is not on the host PATH."""
import os
from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ('molly-patch-release.yml', 'molly-unsigned-release.yml')


def workflow_step(text, name):
    section = text.split('      - name: ' + name + '\n', 1)[1]
    script = section.split('        run: |\n', 1)[1].split('\n      - name:', 1)[0]
    return textwrap.dedent(script).replace('${{ matrix.profile }}', 'collie')


class EmulatorPathTest(unittest.TestCase):
    def test_ci_uses_sdk_platform_tools_when_host_has_no_adb(self):
        for filename in WORKFLOWS:
            with self.subTest(workflow=filename), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                sdk = root / 'sdk'
                bin_dir = root / 'bin'
                bin_dir.mkdir()
                for name in ('awk', 'bash', 'timeout', 'tr', 'sleep', 'yes', 'mkdir', 'touch'):
                    (bin_dir / name).symlink_to('/usr/bin/' + name)
                (bin_dir / 'df').write_text('#!/bin/bash\nprintf "Filesystem 1024-blocks Used Available Capacity Mounted on\\nfixture 20000000 1 10000000 1%% /\\n"\n')
                (bin_dir / 'df').chmod(0o755)
                sudo = bin_dir / 'sudo'
                sudo.write_text('#!/bin/bash\nexit 0\n')
                sudo.chmod(0o755)
                # The workflow starts a traced server and waits for its socket.
                # Simulate its lifecycle without binding the host's ADB port.
                python = bin_dir / 'python3'
                python.write_text('''#!/bin/bash
if [[ "$1" == tools/patches/adb_trace_server.py ]]; then
  touch "$RUNNER_TEMP/fixture-server"
  exec sleep 30
fi
exit 0
''')
                python.chmod(0o755)
                for relative, body in (
                    ('cmdline-tools/latest/bin/avdmanager', 'if [[ ${AVD_CREATE_FAIL:-} == 1 ]]; then exit 0; fi; dir="${ANDROID_AVD_HOME:-$JAVA_USER_HOME/.android/avd}"; mkdir -p "$dir"; echo avd > "$dir/instrumentation.ini"'),
                    ('cmdline-tools/latest/bin/sdkmanager', 'printf \"%s\\n\" \"$*\" >> \"$RUNNER_TEMP/sdkmanager.log\"'),
                    ('emulator/emulator', 'printf "%s\\n" "$@" > "$RUNNER_TEMP/emulator-args"; dir="${ANDROID_AVD_HOME:-$HOME/.android/avd}"; if [[ ! -s "$dir/instrumentation.ini" ]]; then echo "Unknown AVD name [instrumentation]" >&2; touch "$RUNNER_TEMP/no-avd"; exit 1; fi; touch "$RUNNER_TEMP/avd-ok"; exec sleep 30'),
                    ('platform-tools/adb', 'if [[ "$*" == *getprop* ]]; then echo 1; exit 0; fi; for i in {1..100}; do [[ -f "$RUNNER_TEMP/no-avd" ]] && exit 124; [[ -f "$RUNNER_TEMP/avd-ok" ]] && exit 0; sleep 0.01; done; exit 124'),
                ):
                    path = sdk / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text('#!/bin/bash\n' + body + '\n')
                    path.chmod(0o755)
                gate = root / 'tools/patches/instrumentation_gate.sh'
                gate.parent.mkdir(parents=True)
                (gate.parent / 'emulator_startup.sh').write_bytes((ROOT / 'tools/patches/emulator_startup.sh').read_bytes())
                gate.write_text('#!/bin/bash\ncommand -v adb >/dev/null\n')
                gate.chmod(0o755)
                workflow = (ROOT / '.github/workflows' / filename).read_text()
                install = workflow_step(workflow, 'Install Android SDK')
                env = {'PATH': str(bin_dir), 'ANDROID_HOME': str(sdk), 'HOME': temp, 'RUNNER_TEMP': temp, 'JAVA_USER_HOME': str(root / 'java-home')}
                installed = subprocess.run(['/usr/bin/bash', '-e', '-o', 'pipefail', '-c', install], cwd=root,
                                           env=env, text=True, capture_output=True, timeout=10)
                self.assertEqual(installed.returncode, 0, installed.stderr)
                self.assertIn('platform-tools', (root / 'sdkmanager.log').read_text())
                step = workflow_step(workflow, 'Run default and IMO instrumentation on a disposable emulator')
                step = step.replace('(echo > /dev/tcp/127.0.0.1/5037)', 'test -e "$RUNNER_TEMP/fixture-server"')
                result = subprocess.run(['/usr/bin/bash', '-e', '-o', 'pipefail', '-c', step], cwd=root,
                                        env=env, text=True, capture_output=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                if 'adb_trace_server.py' in step:
                    self.assertTrue((root / 'fixture-server').exists(), 'traced server did not start')
                self.assertEqual((root / 'emulator-args').read_text().splitlines(),
                                 ['-avd', 'instrumentation', '-no-window', '-no-audio',
                                  '-no-boot-anim', '-gpu', 'swangle'])
                missing_root = root / 'missing'
                missing_root.mkdir()
                env.update(RUNNER_TEMP=str(missing_root), AVD_CREATE_FAIL='1')
                missing = subprocess.run(['/usr/bin/bash', '-e', '-o', 'pipefail', '-c', step], cwd=root,
                                         env=env, text=True, capture_output=True, timeout=10)
                self.assertNotEqual(missing.returncode, 0)
                self.assertFalse((missing_root / 'no-avd').exists(), 'emulator started without an AVD')


if __name__ == '__main__':
    unittest.main()
