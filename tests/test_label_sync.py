import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('label_sync', Path(__file__).resolve().parents[1] / 'tools/labels/sync.py')
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)


class LabelSyncTest(unittest.TestCase):
    def test_default_dry_run_excludes_release_targets(self):
        catalog = json.loads(sync.CATALOG.read_text())
        existing = [{'name': item['name']} for item in catalog['shared']]
        with patch.object(sync.subprocess, 'check_output', return_value=json.dumps(existing)), \
                patch.object(sync.subprocess, 'run') as run, \
                patch('builtins.print') as output:
            sync.sync('example/repo', True)
            run.assert_not_called()
        printed = '\n'.join(str(call.args[0]) for call in output.call_args_list)
        self.assertNotIn('target: beta', printed)
        self.assertNotIn('target: rc', printed)

    def test_release_targets_are_opt_in_and_existing_labels_are_untouched(self):
        catalog = json.loads(sync.CATALOG.read_text())
        existing = [{'name': item['name']} for item in catalog['shared']]
        existing_target = {
            'name': 'target: beta',
            'color': 'FF00FF',
            'description': 'Existing metadata must be left alone.',
        }
        existing.extend([
            existing_target,
            {'name': 'no log entry'},
            {'name': 'custom label'},
        ])
        with patch.object(sync.subprocess, 'check_output', return_value=json.dumps(existing)) as listing, \
                patch.object(sync.subprocess, 'run') as run:
            sync.sync('example/repo', False, include_release_targets=True)

        self.assertEqual(run.call_count, 1)
        command = run.call_args.args[0]
        self.assertEqual(command[:4], ['gh', 'label', 'create', 'target: rc'])
        rc = next(label for label in catalog['release_targets'] if label['name'] == 'target: rc')
        self.assertEqual(command[4:], [
            '--repo', 'example/repo', '--color', rc['color'],
            '--description', rc['description'],
        ])
        self.assertTrue(run.call_args.kwargs['check'])
        list_command = listing.call_args.args[0]
        self.assertEqual(list_command[-2:], ['--json', 'name'])
        self.assertEqual(existing_target, {
            'name': 'target: beta',
            'color': 'FF00FF',
            'description': 'Existing metadata must be left alone.',
        })

    def test_existing_release_target_name_is_case_insensitive(self):
        catalog = json.loads(sync.CATALOG.read_text())
        existing = [{'name': item['name']} for item in catalog['shared']]
        existing.extend([{'name': 'TARGET: BETA'}, {'name': 'target: rc'}])
        with patch.object(sync.subprocess, 'check_output', return_value=json.dumps(existing)), \
                patch.object(sync.subprocess, 'run') as run:
            sync.sync('example/repo', False, include_release_targets=True)
        run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
