#!/usr/bin/env python3
"""Reinstall the exact unfiltered APK outputs of the just-finished Gradle task."""
import json
from pathlib import Path
import re
import subprocess
import sys


def output(directory, variant):
    metadata = json.loads((directory / 'output-metadata.json').read_text())
    if metadata.get('variantName', '').lower() != variant.lower():
        raise ValueError(f'unexpected variant in {directory}')
    package = metadata.get('applicationId', '')
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+', package):
        raise ValueError(f'invalid application ID in {directory}')
    elements = metadata.get('elements', [])
    if len(elements) != 1 or elements[0].get('filters'):
        raise ValueError(f'expected one unfiltered APK in {directory}')
    filename = elements[0].get('outputFile', '')
    if not filename.endswith('.apk') or Path(filename).name != filename:
        raise ValueError(f'invalid APK filename in {directory}')
    apk = directory / filename
    if not apk.is_file():
        raise ValueError(f'APK missing: {apk}')
    return package, apk


def main():
    root, suite, runner = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
    if suite not in ('default', 'imo'):
        raise ValueError(f'unknown suite: {suite}')
    base = root / 'app/build/outputs/apk'
    app_id, app = output(base / 'prodWebsite/debug', 'prodWebsiteDebug')
    test_id, test = output(base / 'androidTest/prodWebsite/debug', 'prodWebsiteDebugAndroidTest')
    expected_id = 'im.collie.app' + ('.imo' if suite == 'imo' else '')
    if app_id != expected_id or test_id != expected_id + '.test':
        raise ValueError(f'unexpected package IDs for {suite}: {app_id}, {test_id}')
    for apk in (app, test):
        subprocess.run(['adb', 'install', '-r', str(apk)], check=True, stdout=sys.stderr)
    listing = subprocess.run(['adb', 'shell', 'pm', 'list', 'instrumentation'], check=True, capture_output=True, text=True).stdout
    component = f'{test_id}/{runner}'
    matches = [line for line in listing.splitlines() if line.strip() == f'instrumentation:{component} (target={app_id})']
    if len(matches) != 1:
        raise ValueError(f'could not uniquely identify {component} targeting {app_id}')
    print(component)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
        print(f'APK reinstall or runner lookup failed: {exc}', file=sys.stderr)
        sys.exit(1)
