import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('label_sync', Path(__file__).resolve().parents[1] / 'tools/labels/sync.py')
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)


class LabelSyncTest(unittest.TestCase):
    def test_dry_run_and_repeated_apply_preserve_existing(self):
        catalog = json.loads(sync.CATALOG.read_text())
        existing = [{'name': item['name']} for item in catalog['shared'][1:]]
        existing.append({'name': 'no log entry'})
        with patch.object(sync.subprocess, 'check_output', return_value=json.dumps(existing)), patch.object(sync.subprocess, 'run') as run:
            sync.sync('example/repo', True)
            run.assert_not_called()
            sync.sync('example/repo', False)
            self.assertEqual(run.call_count, 1)
            self.assertEqual(run.call_args.args[0][3], catalog['shared'][0]['name'])
        existing.append({'name': catalog['shared'][0]['name'].upper()})
        with patch.object(sync.subprocess, 'check_output', return_value=json.dumps(existing)), patch.object(sync.subprocess, 'run') as run:
            sync.sync('example/repo', False)
            run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
