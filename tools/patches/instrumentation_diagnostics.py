#!/usr/bin/env python3
"""Capture bounded diagnostics from the disposable synthetic CI emulator only.

The serial and emulator checks are guards, not proof of synthetic content;
the CI workflow must use a freshly created emulator without real accounts.
"""
import collections
import os
import selectors
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

SERIAL = 'emulator-5554'
LOG_LIMIT = 5 * 1024 * 1024
EVENT_LIMIT = 1024 * 1024
STATE_LIMIT = 2 * 1024 * 1024
FILTER_LIMIT = 2 * 1024 * 1024
stop = threading.Event()
offline = threading.Event()


def timestamp():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


class Tail:
    def __init__(self, limit):
        self.limit = limit
        self.chunks = collections.deque()
        self.size = 0
        self.lock = threading.Lock()

    def append(self, data):
        with self.lock:
            self.chunks.append(data)
            self.size += len(data)
            while self.size > self.limit:
                first = self.chunks.popleft()
                excess = self.size - self.limit
                self.size -= len(first)
                if len(first) > excess:
                    self.chunks.appendleft(first[excess:])
                    self.size += len(first) - excess

    def checkpoint(self, path):
        # The 8 MiB of finished tails plus one partial file peak at 13 MiB.
        temporary = path.with_name(path.name + '.tmp')
        with self.lock, temporary.open('wb') as output:
            output.writelines(self.chunks)
        os.replace(temporary, path)


def event(tail, message):
    tail.append(f'{timestamp()} {message}\n'.encode(errors='replace'))


def kill_group(proc):
    # Each diagnostic command is started in its own session. Never signal the
    # CI shell, emulator, ADB server, or another job's process group.
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        proc.wait(timeout=1)
    except subprocess.TimeoutExpired:
        pass


def run_bounded(command, timeout=.6, limit=4096):
    """Drain pipes without waiting for descendants or retaining unbounded output."""
    try:
        proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                start_new_session=True)
    except OSError as exc:
        return None, b'', str(exc)
    data = bytearray()
    deadline = time.monotonic() + timeout
    selector = selectors.DefaultSelector()
    try:
        os.set_blocking(proc.stdout.fileno(), False)
        selector.register(proc.stdout, selectors.EVENT_READ)
        while time.monotonic() < deadline:
            for key, _ in selector.select(min(.1, max(0, deadline - time.monotonic()))):
                chunk = os.read(key.fd, 4096)
                if not chunk:
                    selector.unregister(key.fileobj)
                    break
                data.extend(chunk[:max(0, limit - len(data))])
            if proc.poll() is not None and not selector.get_map():
                return proc.returncode, bytes(data), None
        return proc.poll(), bytes(data), 'timeout'
    finally:
        selector.close()
        kill_group(proc)
        proc.stdout.close()


def capture_logcat(log, events, filtered):
    while not stop.is_set():
        if offline.is_set():
            stop.wait(.2)
            continue
        code, output, error = run_bounded(
            ['adb', '-s', SERIAL, 'shell', 'getprop', 'ro.kernel.qemu'])
        if error or code != 0 or output.strip() != b'1':
            event(events, 'emulator verification unavailable; logcat not started')
            stop.wait(2)
            continue
        try:
            proc = subprocess.Popen(['adb', '-s', SERIAL, 'logcat', '-b', 'all', '-v', 'threadtime'],
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        except OSError as exc:
            event(events, f'logcat start failed: {exc}')
            stop.wait(2)
            continue
        event(events, f'logcat started pid={proc.pid}')
        selector = selectors.DefaultSelector()
        for stream, kind in ((proc.stdout, 'stdout'), (proc.stderr, 'stderr')):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, kind)
        reason = 'shutdown'
        partial = b''
        exited_at = None
        eof_at = None
        try:
            while not stop.is_set():
                if offline.is_set():
                    reason = 'transport offline'
                    break
                if proc.poll() is not None and exited_at is None:
                    exited_at = time.monotonic()
                for key, _ in selector.select(.1):
                    data = os.read(key.fd, 4096)
                    if not data:
                        selector.unregister(key.fileobj)
                        if key.data == 'stdout' and proc.poll() is None:
                            reason = 'unexpected EOF'
                            eof_at = time.monotonic()
                    elif key.data == 'stdout':
                        log.append(data)
                        # Keep a longer history of transport and test-service events.
                        # This is a line-filtered tail from the same synthetic device.
                        parts = (partial + data).split(b'\n')
                        partial = parts.pop()[-65536:]
                        for line in parts:
                            if any(tag in line for tag in (b' adbd ', b'AndroidRuntime', b'ShellCommandClient', b'ShellExecutorImpl', b'ActivityManager', b'am_crash')):
                                filtered.append(line + b'\n')
                    else:
                        event(events, 'logcat stderr: ' + data.decode(errors='replace').rstrip())
                if eof_at is not None and (not selector.get_map() or time.monotonic() - eof_at >= .5):
                    break
                if exited_at is not None and (not selector.get_map() or time.monotonic() - exited_at >= .5):
                    # Drain available final bytes, but do not wait forever for
                    # a descendant that inherited a pipe after adb exited.
                    if reason == 'shutdown':
                        reason = 'process exit'
                    break
        finally:
            code = proc.poll()
            kill_group(proc)
            selector.close()
            proc.stdout.close()
            proc.stderr.close()
            event(events, f'logcat exit={code if code is not None else proc.returncode} reason={reason}')
        if not stop.is_set():
            stop.wait(1)


def process_summary():
    lines = []
    for path in Path('/proc').iterdir():
        if not path.name.isdigit():
            continue
        try:
            name = (path / 'comm').read_text().strip()
            if name not in ('adb', 'emulator', 'emulator64-x86', 'qemu-system-x86', 'qemu-system-x86_64'):
                continue
            status = (path / 'status').read_text().splitlines()
            fields = [line for line in status if line.startswith(('State:', 'PPid:', 'VmRSS:', 'Threads:'))]
            lines.append(f'pid={path.name} name={name} ' + ' '.join(fields))
        except (OSError, UnicodeError):
            continue
    return '\n'.join(lines[:100]) or '(none found)'


def sample_state(tail, directory):
    lines = [timestamp()]
    code, output, error = run_bounded(['adb', 'devices'])
    # Do not include identifiers of other attached devices in the artifact.
    devices = output.decode(errors='replace').splitlines()[1:]
    target = [line.split() for line in devices if line.split() and line.split()[0] == SERIAL]
    target_state = target[0][1] if target and len(target[0]) > 1 else 'absent'
    if target_state not in ('device', 'offline', 'unauthorized', 'absent'):
        target_state = 'unrecognized'
    lines.append(f'adb devices exit={code} error={error} target={target_state} other_count={sum(bool(line.strip()) for line in devices) - len(target)}')
    code, output, error = run_bounded(['adb', '-s', SERIAL, 'get-state'])
    state = output.decode(errors='replace').strip()[:100]
    # Only a definite offline response triggers a restart, never silence or a timeout.
    if (target and len(target[0]) > 1 and target[0][1] == 'offline') or (error is None and state == 'offline'):
        offline.set()
    elif error is None and code == 0 and state == 'device':
        offline.clear()
    safe_state = state if state in ('device', 'offline', 'unknown', '') else 'unrecognized'
    lines.append(f'adb get-state exit={code} error={error} state={safe_state}')
    try:
        memory = Path('/proc/meminfo').read_text().splitlines()
        lines.extend(line for line in memory if line.startswith(('MemTotal:', 'MemAvailable:', 'SwapTotal:', 'SwapFree:')))
        disk = shutil.disk_usage(directory)
        lines.append(f'disk bytes total={disk.total} free={disk.free}')
        lines.append('processes:\n' + process_summary())
    except OSError as exc:
        lines.append(f'host state failed: {exc}')
    tail.append(('\n'.join(lines) + '\n\n').encode(errors='replace'))


def main():
    if len(sys.argv) != 3 or sys.argv[2] != SERIAL:
        print('diagnostics require the disposable CI emulator serial emulator-5554', file=sys.stderr)
        return 2
    directory = Path(sys.argv[1])
    directory.mkdir(parents=True, exist_ok=True)
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    log, events, state, filtered = Tail(LOG_LIMIT), Tail(EVENT_LIMIT), Tail(STATE_LIMIT), Tail(FILTER_LIMIT)
    thread = threading.Thread(target=capture_logcat, args=(log, events, filtered), daemon=True)
    thread.start()

    def checkpoint():
        for tail, name in ((log, 'system-logcat.txt'), (events, 'logcat-events.txt'), (state, 'device-liveness.txt'), (filtered, 'filtered-lifecycle.txt')):
            tail.checkpoint(directory / name)

    try:
        while not stop.is_set():
            started = time.monotonic()
            sample_state(state, directory)
            checkpoint()
            stop.wait(max(0, 2 - (time.monotonic() - started)))
    finally:
        stop.set()
        thread.join(timeout=3)
        checkpoint()
    return 0


if __name__ == '__main__':
    sys.exit(main())
