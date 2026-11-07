#!/usr/bin/env python3
"""Restrict the immediate Collie PR diff; source patches remain subject to review."""
import argparse
import json
import subprocess
import sys


def git(*args):
    return subprocess.run(['git', *args], check=True, stdout=subprocess.PIPE).stdout


def check(base):
    allowed = {'molly-build.json', 'docs/PORT-REPORT.md',
               'patches/forks/signal-foss/provenance.json'}
    prefix = 'patches/forks/signal-foss/collie-branding/'
    changed = git('diff', '--no-renames', '--name-only', '-z', base, 'HEAD').split(b'\0')
    for raw in filter(None, changed):
        path = raw.decode('utf-8', 'surrogateescape')
        if path not in allowed and not path.startswith(prefix):
            raise ValueError('path outside immediate Collie scope: ' + path)
    before = json.loads(git('show', base + ':molly-build.json'))
    after = json.loads(git('show', 'HEAD:molly-build.json'))
    for name in ('collie', 'collie-foss'):
        del before['profiles'][name]['target_tree']
        del after['profiles'][name]['target_tree']
    if before != after:
        raise ValueError('immediate Collie scope changed other profile configuration')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', required=True)
    args = parser.parse_args()
    try:
        check(args.base)
    except (KeyError, TypeError, json.JSONDecodeError, subprocess.CalledProcessError, ValueError) as exc:
        print('Collie scope check failed: ' + str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
