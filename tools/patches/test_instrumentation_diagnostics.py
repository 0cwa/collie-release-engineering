import importlib.util
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time
import unittest

SCRIPT = Path(__file__).with_name('instrumentation_diagnostics.py')
spec = importlib.util.spec_from_file_location('diagnostics', SCRIPT)
diagnostics = importlib.util.module_from_spec(spec)
spec.loader.exec_module(diagnostics)

FAKE_ADB = '''#!/usr/bin/env python3
import os, pathlib, subprocess, sys, time
root = pathlib.Path(os.environ['FAKE_ROOT'])
args = sys.argv[1:]
if args[:2] == ['-s', 'emulator-5554']:
    args = args[2:]
if args[:2] == ['shell', 'getprop']:
    if (root / 'offline').exists():
        sys.exit(1)
    print('0' if (root / 'real').exists() else '1')
elif args[0] == 'logcat':
    count = root / 'count'
    n = int(count.read_text()) + 1 if count.exists() else 1
    temp = root / 'count.tmp'
    temp.write_text(str(n))
    temp.replace(count)
    (root / 'logcat-pid').write_text(str(os.getpid()))
    if n == 1 and (root / 'large-tail').exists():
        sys.stdout.write('BEGIN-TAIL' + 'z' * 65536 + 'END-TAIL')
        sys.stdout.flush()
        sys.exit(0)
    if n == 1 and (root / 'disconnect').exists():
        sys.stdout.write('x' * (6 * 1024 * 1024))
        sys.stdout.flush()
        print('transport disconnected', file=sys.stderr, flush=True)
        sys.exit(7)
    print('reconnected-log', flush=True)
    print('11-05 18:08:41 I adbd    : host offline', flush=True)
    print('11-05 18:08:41 E ShellCommandClient: binder missing', flush=True)
    time.sleep(30)
elif args[0] == 'get-state':
    if (root / 'hang').exists():
        subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
        time.sleep(30)
    print('offline' if (root / 'offline').exists() else 'device')
elif args[0] == 'devices':
    print('List of devices attached')
    print('emulator-5554 ' + ('offline' if (root / 'offline').exists() else 'device'))
    print('private-serial device')
'''


class DiagnosticsTest(unittest.TestCase):
    def wait_for(self, predicate, message, seconds=9):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            try:
                if predicate():
                    return
            except (FileNotFoundError, ValueError):
                pass
            time.sleep(.05)
        self.fail(message)

    def setup_fake(self, root):
        adb = root / 'adb'
        adb.write_text(FAKE_ADB)
        adb.chmod(0o755)
        return dict(os.environ, PATH=str(root) + ':' + os.environ['PATH'], FAKE_ROOT=str(root))

    def test_reconnects_bounds_and_stops_with_hung_descendant(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = self.setup_fake(root)
            (root / 'disconnect').touch()
            (root / 'hang').touch()
            logs = root / 'logs'
            proc = subprocess.Popen(['python3', str(SCRIPT), str(logs), 'emulator-5554'], env=env)
            try:
                self.wait_for(lambda: int((root / 'count').read_text()) >= 2, 'logcat did not reconnect')
                self.wait_for(lambda: (logs / 'device-liveness.txt').exists(), 'checkpoint missing')
                proc.send_signal(signal.SIGTERM)
                self.assertEqual(proc.wait(timeout=5), 0)
                log = (logs / 'system-logcat.txt').read_bytes()
                events = (logs / 'logcat-events.txt').read_text()
                state = (logs / 'device-liveness.txt').read_text()
                self.assertLessEqual(len(log), 5 * 1024 * 1024)
                self.assertIn(b'reconnected-log', log)
                filtered = (logs / 'filtered-lifecycle.txt').read_text()
                self.assertIn('adbd    : host offline', filtered)
                self.assertIn('ShellCommandClient: binder missing', filtered)
                self.assertNotIn('reconnected-log', filtered)
                self.assertIn('transport disconnected', events)
                self.assertIn('exit=7', events)
                self.assertIn('timeout', state)
                self.assertNotIn('private-serial', state)
                self.assertIn('MemAvailable:', state)
                self.assertIn('disk bytes', state)
                self.assertLessEqual((logs / 'logcat-events.txt').stat().st_size, 1024 * 1024)
                self.assertLessEqual((logs / 'device-liveness.txt').stat().st_size, 2 * 1024 * 1024)
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait()

    def test_offline_restarts_live_logcat_and_checkpoint_survives_kill(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = self.setup_fake(root)
            logs = root / 'logs'
            proc = subprocess.Popen(['python3', str(SCRIPT), str(logs), 'emulator-5554'], env=env)
            try:
                self.wait_for(lambda: (logs / 'system-logcat.txt').exists() and b'reconnected-log' in (logs / 'system-logcat.txt').read_bytes(), 'first checkpoint missing')
                (root / 'offline').touch()
                self.wait_for(lambda: 'transport offline' in (logs / 'logcat-events.txt').read_text(), 'offline restart missing')
                (root / 'offline').unlink()
                self.wait_for(lambda: int((root / 'count').read_text()) >= 2, 'online reconnect missing')
                self.wait_for(lambda: (logs / 'logcat-events.txt').read_text().count('logcat started') >= 2, 'second checkpoint missing')
                proc.kill()
                proc.wait(timeout=3)
                self.assertIn(b'reconnected-log', (logs / 'system-logcat.txt').read_bytes())
                self.assertIn('transport offline', (logs / 'logcat-events.txt').read_text())
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait()
                try:
                    os.killpg(int((root / 'logcat-pid').read_text()), signal.SIGKILL)
                except ProcessLookupError:
                    pass

    def test_rejects_other_serial_and_never_logs_non_emulator(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = self.setup_fake(root)
            logs = root / 'logs'
            result = subprocess.run(['python3', str(SCRIPT), str(logs), 'physical-device'], env=env, capture_output=True, timeout=4)
            self.assertEqual(result.returncode, 2)
            self.assertFalse(logs.exists())
            (root / 'real').touch()
            proc = subprocess.Popen(['python3', str(SCRIPT), str(logs), 'emulator-5554'], env=env)
            try:
                self.wait_for(lambda: 'verification unavailable' in (logs / 'logcat-events.txt').read_text(), 'verification failure missing')
                self.assertFalse((root / 'count').exists())
                self.assertIn('adb devices', (logs / 'device-liveness.txt').read_text())
            finally:
                proc.send_signal(signal.SIGTERM)
                proc.wait(timeout=5)

    def test_initial_offline_retries_verification_and_collects_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = self.setup_fake(root)
            logs = root / 'logs'
            (root / 'offline').touch()
            proc = subprocess.Popen(['python3', str(SCRIPT), str(logs), 'emulator-5554'], env=env)
            try:
                self.wait_for(lambda: 'offline' in (logs / 'device-liveness.txt').read_text(), 'offline state missing')
                self.assertFalse((root / 'count').exists())
                (root / 'offline').unlink()
                self.wait_for(lambda: (root / 'count').exists(), 'logcat did not start after recovery')
            finally:
                proc.send_signal(signal.SIGTERM)
                proc.wait(timeout=5)

    def test_drains_large_final_logcat_tail(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = self.setup_fake(root)
            logs = root / 'logs'
            (root / 'large-tail').touch()
            proc = subprocess.Popen(['python3', str(SCRIPT), str(logs), 'emulator-5554'], env=env)
            try:
                self.wait_for(lambda: int((root / 'count').read_text()) >= 2, 'logcat did not reconnect')
                proc.send_signal(signal.SIGTERM)
                self.assertEqual(proc.wait(timeout=5), 0)
                output = (logs / 'system-logcat.txt').read_bytes()
                self.assertIn(b'BEGIN-TAIL' + b'z' * 65536 + b'END-TAIL', output)
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait()

    def test_bounded_output_and_child_held_pipe(self):
        command = ['python3', '-c', "import subprocess,sys; subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); print('x'*1000000, flush=True)"]
        start = time.monotonic()
        code, output, error = diagnostics.run_bounded(command, timeout=.5, limit=1024)
        self.assertEqual(error, 'timeout')
        self.assertLessEqual(len(output), 1024)
        self.assertLess(time.monotonic() - start, 3)

    def test_tail_retains_only_newest_bytes(self):
        tail = diagnostics.Tail(5)
        tail.append(b'abcd')
        tail.append(b'ef')
        tail.append(b'ghijkl')
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'tail'
            tail.checkpoint(path)
            self.assertEqual(path.read_bytes(), b'hijkl')


if __name__ == '__main__':
    unittest.main()
