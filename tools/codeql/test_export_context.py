import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import export_context as exporter
import report


def finding(path):
    loc = {'uri': path, 'uri_base_id': '%SRCROOT%', 'start_line': 1, 'start_column': 1, 'end_line': None, 'end_column': None}
    return {'rule': 'rule', 'level': 'warning', 'security_severity': '1', 'query_kind': 'path-problem', 'query_precision': 'high', 'message': 'message', 'locations': [loc], 'related_locations': [], 'code_flows': [], 'context_locations': [loc], 'review_context': {'codeFlows': [], 'relatedLocations': []}}


class ExportTests(unittest.TestCase):
    def test_parity_and_rebinding(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'caller').write_text('one')
            f = finding('caller')
            first = exporter.bound_hashes(f, root)
            self.assertEqual(first['sha256'], report.context_sha256(f, root))
            self.assertEqual(first['paths']['caller'], hashlib.sha256(b'one').hexdigest())
            (root / 'caller').write_text('two')
            self.assertNotEqual(first['sha256'], exporter.bound_hashes(f, root)['sha256'])
            (root / 'other').write_text('one')
            self.assertNotEqual(first['sha256'], exporter.bound_hashes(finding('other'), root)['sha256'])

    def test_missing_and_escape(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(exporter.bound_hashes(finding(next(iter(exporter.MANIFESTS))), root)['status'], 'error')
            self.assertEqual(exporter.bound_hashes(finding('../outside'), root)['status'], 'error')
            (root / 'link').symlink_to(root.parent)
            self.assertEqual(exporter.bound_hashes(finding('link/outside'), root)['status'], 'error')

    def test_manifest_metadata_exact_receiver_and_ambiguity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = sorted(exporter.MANIFESTS)[0]
            manifest = root / path
            manifest.parent.mkdir(parents=True)
            def write(receivers):
                manifest.write_text('<manifest xmlns:android="http://schemas.android.com/apk/res/android"><application>' + receivers + '</application></manifest>')
            wrong = '<receiver android:name="org.example.LocaleChangedReceiver" android:exported="true"/>'
            right = '<receiver android:name="' + exporter.RECEIVER + '" android:exported="false"/>'
            write(wrong)
            metadata = exporter.bound_hashes(finding(path), root)['receivers'][path]
            self.assertEqual(metadata['status'], 'error')
            self.assertNotIn('org.example', str(metadata))
            write(wrong + right)
            metadata = exporter.bound_hashes(finding(path), root)['receivers'][path]
            self.assertEqual(metadata, {'status': 'ok', 'name': exporter.RECEIVER, 'exported': 'false'})
            write('<receiver android:name="' + exporter.RECEIVER + '"/>')
            self.assertEqual(exporter.bound_hashes(finding(path), root)['receivers'][path]['status'], 'error')
            write(right + right)
            self.assertEqual(exporter.bound_hashes(finding(path), root)['receivers'][path]['status'], 'error')

    def test_primary_run_errors_and_policy_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'caller').write_text('primary')
            policy = root / 'policy.json'
            policy.write_text('{"reviews":[]}')
            audit = {'errors': ['top error'], 'runs': [{'source': 'a.sarif', 'errors': ['run error'], 'category': 'c', 'tool_version': 'v', 'findings': [finding('caller')]}]}
            with patch.object(report, 'audit', return_value=audit):
                result = exporter.export(root / 'a.sarif', root, 'java-kotlin')
            self.assertEqual(result['findings'][0]['primary']['sha256'], hashlib.sha256(b'primary').hexdigest())
            self.assertEqual(result['audit_errors'], ['top error'])
            self.assertEqual(result['run_errors'], [{'source': 'a.sarif', 'errors': ['run error']}])
            self.assertEqual(policy.read_text(), '{"reviews":[]}')
            self.assertNotIn('disposition', result['findings'][0])


if __name__ == '__main__':
    unittest.main()
