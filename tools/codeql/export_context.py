#!/usr/bin/env python3
"""Export source-bound diagnostic hashes without changing review policy."""
import argparse
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

import report


MANIFESTS = {f'app/build/intermediates/{kind}/prodWebsiteDebug/{task}/AndroidManifest.xml' for kind, task in (
    ('merged_manifest', 'processProdWebsiteDebugMainManifest'),
    ('merged_manifests', 'processProdWebsiteDebugManifest'),
    ('packaged_manifests', 'processProdWebsiteDebugManifestForPackage'))}
RECEIVER = 'org.thoughtcrime.securesms.notifications.LocaleChangedReceiver'
ANDROID = '{http://schemas.android.com/apk/res/android}'


def receiver_metadata(root, path, expected_hash):
    try:
        source = root / path
        if source.is_symlink() or not source.resolve().is_relative_to(root.resolve()):
            return {'status': 'error', 'error': 'manifest escapes checkout or is a symlink'}
        data = source.read_bytes()
        if hashlib.sha256(data).hexdigest() != expected_hash:
            return {'status': 'error', 'error': 'manifest changed while exporting'}
        xml = ET.fromstring(data)
        matches = [item for item in xml.iter('receiver') if item.get(ANDROID + 'name') == RECEIVER]
        if len(matches) != 1:
            return {'status': 'error', 'error': 'expected exactly one known receiver'}
        exported = matches[0].get(ANDROID + 'exported')
        if exported is None:
            return {'status': 'error', 'error': 'known receiver has no explicit exported attribute', 'name': RECEIVER, 'exported': None}
        return {'status': 'ok', 'name': RECEIVER, 'exported': exported}
    except (OSError, ET.ParseError) as error:
        return {'status': 'error', 'error': str(error)}


def bound_hashes(finding, root):
    try:
        paths = report.context_source_hashes(finding, root)
        digest = report.context_sha256(finding, root)
        if paths != report.context_source_hashes(finding, root):
            raise ValueError('context sources changed while exporting')
        receivers = {path: receiver_metadata(root, path, paths[path]) for path in paths if path in MANIFESTS}
        return {'status': 'ok', 'sha256': digest, 'paths': paths, 'receivers': receivers}
    except (OSError, ValueError, KeyError, TypeError, UnicodeError) as error:
        return {'status': 'error', 'error': str(error)}


def export(sarif, root, language):
    audit = report.audit(sorted(sarif.rglob('*.sarif')) if sarif.is_dir() else [sarif], language)
    rows = []
    fields = ('rule', 'level', 'security_severity', 'query_kind', 'query_precision', 'message', 'locations')
    for run in audit['runs']:
        for finding in run['findings']:
            row = {key: finding[key] for key in fields}
            row.update(category=run['category'], tool_version=run['tool_version'])
            locs = finding['locations']
            if len(locs) != 1:
                row['primary'] = {'status': 'error', 'error': 'Expected one primary location'}
            else:
                synthetic = {'context_locations': locs, 'related_locations': [], 'code_flows': [], 'review_context': {}}
                try:
                    paths = report.context_source_hashes(synthetic, root)
                    row['primary'] = {'status': 'ok', 'sha256': next(iter(paths.values()))}
                except (OSError, ValueError, KeyError, TypeError, UnicodeError) as error:
                    row['primary'] = {'status': 'error', 'error': str(error)}
            if (finding['query_kind'] == 'path-problem' or finding['review_context']['codeFlows'] or finding['review_context']['relatedLocations']):
                row['context'] = bound_hashes(finding, root)
            else:
                row['context'] = {'status': 'not-required'}
            rows.append(row)
    return {'language': language, 'audit_errors': audit['errors'],
            'run_errors': [{'source': run['source'], 'errors': run['errors']} for run in audit['runs']],
            'findings': rows}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--sarif', type=Path, required=True)
    parser.add_argument('--source-root', type=Path, required=True)
    parser.add_argument('--language', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = export(args.sarif, args.source_root.resolve(), args.language)
    args.output.write_text(json.dumps(result, separators=(',', ':')) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
