import importlib.util
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).with_name('adb_trace_server.py')
spec = importlib.util.spec_from_file_location('adb_trace_server', SCRIPT)
trace = importlib.util.module_from_spec(spec)
spec.loader.exec_module(trace)

class OccupiedSocket:
    def __enter__(self): return self
    def __exit__(self, *_): pass
    def settimeout(self, _): pass
    def connect_ex(self, _): return 0

class FreeSocket(OccupiedSocket):
    def connect_ex(self, _): return 111

class AdbTraceServerTest(unittest.TestCase):
    def test_starts_traced_server_and_captures_its_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            adb = root / 'adb'
            adb.write_text('#!/bin/sh\nprintf "trace=%s args=%s\\n" "$ADB_TRACE" "$*"\nexit 7\n')
            adb.chmod(0o755)
            with patch.object(trace.socket, 'socket', FreeSocket), \
                 patch.dict(os.environ, {'PATH': str(root) + ':' + os.environ['PATH']}), \
                 patch.object(trace.sys, 'argv', ['adb_trace_server.py', tmp]):
                self.assertEqual(trace.main(), 1)
            output = (root / 'adb-transport.txt').read_text()
            self.assertIn('trace=transport', output)
            self.assertIn('nodaemon server', output)

    def test_refuses_to_replace_existing_server(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(trace.socket, 'socket', OccupiedSocket), \
             patch.object(trace.subprocess, 'Popen') as popen, \
             patch.object(trace.sys, 'argv', ['adb_trace_server.py', tmp]):
            self.assertEqual(trace.main(), 1)
            popen.assert_not_called()

    def test_preserves_last_bytes_when_server_exits_after_large_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            adb = root / 'adb'
            adb.write_text('#!/bin/sh\nhead -c 200000 /dev/zero\nprintf "LAST-TRACE-MARKER"\n')
            adb.chmod(0o755)
            with patch.object(trace.socket, 'socket', FreeSocket), \
                 patch.dict(os.environ, {'PATH': str(root) + ':' + os.environ['PATH']}), \
                 patch.object(trace.sys, 'argv', ['adb_trace_server.py', tmp]):
                self.assertEqual(trace.main(), 1)
            self.assertTrue((root / 'adb-transport.txt').read_bytes().endswith(b'LAST-TRACE-MARKER'))

    def test_tail_is_bounded(self):
        tail = trace.Tail(trace.LIMIT)
        tail.append(b'x' * (trace.LIMIT + 1024))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'host'
            tail.checkpoint(path)
            self.assertEqual(path.stat().st_size, trace.LIMIT)

if __name__ == '__main__': unittest.main()
