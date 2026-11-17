#!/usr/bin/env python3
"""Create missing labels from the shared catalog without changing existing labels."""
import argparse
import json
from pathlib import Path
import subprocess

CATALOG = Path(__file__).with_name('catalog.json')


def sync(repo, dry_run, include_release_targets=False):
    catalog = json.loads(CATALOG.read_text())
    labels = catalog['shared'] + catalog['areas'].get(repo, [])
    if include_release_targets:
        labels += catalog.get('release_targets', [])
    existing = json.loads(subprocess.check_output(
        ['gh', 'label', 'list', '--repo', repo, '--limit', '1000',
         '--json', 'name'], text=True))
    names = {label['name'].casefold() for label in existing}
    for label in labels:
        if label['name'].casefold() in names:
            continue
        print(('Would create: ' if dry_run else 'Creating: ') + label['name'], flush=True)
        if not dry_run:
            subprocess.run(['gh', 'label', 'create', label['name'], '--repo', repo,
                            '--color', label['color'], '--description', label['description']],
                           check=True)
        names.add(label['name'].casefold())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', required=True, help='GitHub owner/repository')
    parser.add_argument('--apply', action='store_true', help='create missing labels (default: dry run)')
    parser.add_argument('--release-targets', action='store_true', help='include beta/rc scope labels explicitly')
    args = parser.parse_args()
    sync(args.repo, not args.apply, args.release_targets)


if __name__ == '__main__':
    main()
