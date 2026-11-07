#!/usr/bin/env python3
"""Run traced ADB server for a fresh synthetic CI emulator, keeping a bounded tail."""
import os
from pathlib import Path
import selectors
import signal
import socket
import subprocess
import sys
import time

from instrumentation_diagnostics import Tail, kill_group

LIMIT = 2 * 1024 * 1024


def main():
    if len(sys.argv) != 2:
        print('usage: adb_trace_server.py REPORT_DIR', file=sys.stderr)
        return 2
    directory = Path(sys.argv[1])
    directory.mkdir(parents=True, exist_ok=True)
    # Never take over an existing server (and never run kill-server).
    with socket.socket() as probe:
        probe.settimeout(.2)
        if probe.connect_ex(('127.0.0.1', 5037)) == 0:
            print('ADB port 5037 already in use; refusing to alter existing server', file=sys.stderr)
            return 1
    env = dict(os.environ, ADB_TRACE='transport')
    proc = subprocess.Popen(['adb', '-L', 'tcp:localhost:5037', 'nodaemon', 'server'],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env,
                            start_new_session=True)
    stopping = False
    def shutdown(*_):
        nonlocal stopping
        stopping = True
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    selector = selectors.DefaultSelector()
    selector.register(proc.stdout, selectors.EVENT_READ)
    os.set_blocking(proc.stdout.fileno(), False)
    tail = Tail(LIMIT)
    checkpoint_at = time.monotonic()
    try:
        while not stopping:
            for key, _ in selector.select(.2):
                chunk = os.read(key.fd, 65536)
                if chunk:
                    tail.append(chunk)
                else:
                    selector.unregister(key.fileobj)
            if time.monotonic() >= checkpoint_at:
                tail.checkpoint(directory / 'adb-transport.txt')
                checkpoint_at = time.monotonic() + 2
            if proc.poll() is not None:
                print(f'traced ADB server exited: {proc.returncode}', file=sys.stderr)
                return 1
    finally:
        kill_group(proc)
        # Drain buffered output after the child has stopped so its final trace
        # messages are included even when it exits between selector reads.
        while True:
            try:
                chunk = os.read(proc.stdout.fileno(), 65536)
            except BlockingIOError:
                break
            if not chunk:
                break
            tail.append(chunk)
        selector.close()
        proc.stdout.close()
        tail.checkpoint(directory / 'adb-transport.txt')
    return 0

if __name__ == '__main__':
    sys.exit(main())
