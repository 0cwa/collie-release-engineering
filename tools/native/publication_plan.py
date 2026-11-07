#!/usr/bin/env python3
"""Describe verified existing Maven bytes for operator-only immutable publication.

No artifact is copied, uploaded or rebuilt. Reuse stage_artifacts stage/verify-stage
before calling; no production capability is granted by an author-created plan.
"""
import argparse
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit
import stage_artifacts


def plan(lock_path, stage, verification, public_repository):
    url = urlsplit(public_repository)
    if (url.scheme != 'https' or not url.hostname or url.username or url.password
            or url.query or url.fragment or not public_repository.endswith('/')):
        raise ValueError('public repository must be a plain HTTPS directory URL without credentials')
    lock_path, stage, verification = (p.resolve() for p in (lock_path, stage, verification))
    lock = stage_artifacts.load_lock(lock_path)
    receipt = stage_artifacts.verify_stage(lock_path, stage, verification)
    files = []
    for module in lock['modules']:
        directory = Path(lock['output_group'].replace('.', '/')) / module['name'] / module['output_version']
        for filename, digest in receipt['artifacts'][module['name']].items():
            relative = (directory / filename).as_posix()
            file = stage / 'maven' / relative
            files.append({'path': relative, 'url': public_repository + relative,
                          'bytes': file.stat().st_size, 'sha256': digest})
    return {'schema': 1, 'kind': 'native-publication-plan',
            'status': 'NOT PUBLISHED', 'operator_approval': 'REQUIRED',
            'trust': 'accepted trusted-local binary inputs; independent native reproduction NOT RUN',
            'repository': public_repository, 'lock_sha256': stage_artifacts.digest(lock_path),
            'stage_receipt_sha256': stage_artifacts.digest(stage / 'receipt.json'),
            'verification_sha256': receipt['verification_sha256'], 'files': files}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lock', required=True, type=Path)
    parser.add_argument('--stage', required=True, type=Path)
    parser.add_argument('--original-verification', required=True, type=Path)
    parser.add_argument('--public-repository', required=True)
    args = parser.parse_args()
    print(json.dumps(plan(args.lock, args.stage, args.original_verification, args.public_repository), indent=2))
