#!/usr/bin/env python3
"""Fetch the exact historical profile review needed by fork-scope checks."""
import argparse
from pathlib import Path
import subprocess

import scope


def fetch(repo: Path, remote: str, commit: str) -> None:
    # A temporary promisor remote lets Git fetch only the reviewed blobs;
    # do not use --depth, which could alter ancestry checks for the main repo.
    git = ['git', '-C', str(repo), '-c', f'remote.reviewed-profile.url={remote}',
           '-c', 'remote.reviewed-profile.promisor=true',
           '-c', 'remote.reviewed-profile.partialclonefilter=blob:none']
    subprocess.run(git + ['fetch', '--filter=blob:none', '--no-tags', '--no-recurse-submodules', 'reviewed-profile', commit], check=True)
    actual = subprocess.check_output(git + ['rev-parse', '--verify', 'FETCH_HEAD^{commit}'], text=True).strip()
    if actual != commit:
        raise ValueError('reviewed profile fetch did not return the pinned commit')
    for path in ('tools/patches/release_profiles.py', 'molly-build.json',
                 'patches/ports/signal-8.33.2/provenance.json'):
        subprocess.run(git + ['cat-file', '-e', f'{commit}:{path}'], check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path('.'))
    parser.add_argument('remote', help='trusted repository URL')
    args = parser.parse_args()
    fetch(args.repo, args.remote, scope.REVIEWED_PROFILES)


if __name__ == '__main__':
    main()
