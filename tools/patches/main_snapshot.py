#!/usr/bin/env python3
"""Identify the source proof for a trusted main snapshot."""

import json
import subprocess
import sys

from scope import INITIAL_MAIN


def git(*args):
    return subprocess.check_output(['git', *args], text=True).strip()


def main():
    blob = subprocess.run(['git', 'show', 'HEAD:molly-build.json'], text=True, capture_output=True)
    if blob.returncode:
        print('main-snapshot-transition ' + INITIAL_MAIN)
        return
    json.loads(blob.stdout)  # Reject a broken profile instead of skipping proof.
    print('main-maintenance ' + git('rev-parse', 'HEAD'))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, json.JSONDecodeError, subprocess.CalledProcessError) as exc:
        print('main snapshot base failed: ' + str(exc), file=sys.stderr)
        sys.exit(1)
