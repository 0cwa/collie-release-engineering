#!/usr/bin/env python3
"""Stage exact verified preparation bytes in one Beta release bundle."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

import migration_receipts as receipts
import runtime_receipts as runtime
import trusted_directories


def stage(args):
    context = runtime.plan(args)
    plan, _plan_sha = context
    if args.channel != 'trailblazer-beta' or plan['purpose'] != 'production' or plan['mode'] != 'new-source':
        raise ValueError('preparation bundle staging requires a production Beta new-source plan')
    if type(args.release_id) is not int or args.release_id <= 0:
        raise ValueError('positive selected Beta release ID required')

    source, _ = runtime.document(args.source_receipt)
    receipts.validate(source, 'source', profile=args.channel)
    source_sha = receipts.digest(source, profile=args.channel)
    if (source_sha != plan['source_receipt_sha256'] or
            source['payload']['published_commit'] != plan['source_commit'] or
            source['payload']['engineering_revision'] != plan['engineering_revision']):
        raise ValueError('source receipt differs from approved Beta release plan')

    build, _ = runtime.document(args.build_receipt)
    receipts.validate(build, 'build', profile=args.channel)
    receipts.check(args.approved_build_receipt_sha256, 'sha', args.channel)
    if receipts.digest(build, profile=args.channel) != args.approved_build_receipt_sha256:
        raise ValueError('build receipt differs from independently approved digest')
    payload = build['payload']
    if (payload['source_receipt_sha256'] != source_sha or
            payload['source_commit'] != source['payload']['published_commit'] or
            payload['source_tree'] != source['payload']['published_tree']):
        raise ValueError('build receipt source binding differs')
    if (not source['evidence'] or any(item['status'] != 'PASS' for item in source['evidence']) or
            not build['evidence'] or any(item['status'] != 'PASS' for item in build['evidence'])):
        raise ValueError('staging requires passing source and build evidence')

    preparation, preparation_sha = runtime.preparation_document(args.preparation)
    marker_sha = runtime.verify_preparation_record(preparation, context, source, build)
    if marker_sha != preparation_sha:
        raise ValueError('preparation bytes differ from approved build evidence marker')
    preparation_bytes = (json.dumps(preparation, sort_keys=True, indent=2) + '\n').encode('utf-8')
    if hashlib.sha256(preparation_bytes).hexdigest() != preparation_sha:
        raise ValueError('preparation input changed while staging')

    root = Path(args.bundle_root)
    try:
        root_fd, root = trusted_directories.open_trusted_directory(root)
    except (OSError, ValueError) as error:
        raise ValueError('bundle root must be a trusted existing directory') from error
    parent_fd = root_fd
    opened = []
    temp_name = None
    try:
        bundles_fd = trusted_directories.open_private_directory_at(parent_fd, 'bundles', create=True)
        opened.append(bundles_fd)
        release_fd = trusted_directories.open_private_directory_at(bundles_fd, str(args.release_id), create=True)
        opened.append(release_fd)
        target = 'preparation.json'
        try:
            os.stat(target, dir_fd=release_fd, follow_symlinks=False)
            raise ValueError('new preparation bundle path required')
        except FileNotFoundError:
            pass
        trusted_directories.publish_exclusive_at(release_fd, target, preparation_bytes, mode=0o644)
        return root / 'bundles' / str(args.release_id) / target, preparation_sha
    finally:
        if temp_name is not None and opened:
            try:
                os.unlink(temp_name, dir_fd=opened[-1])
            except FileNotFoundError:
                pass
        for descriptor in reversed(opened):
            os.close(descriptor)
        os.close(root_fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release-id', type=int, required=True)
    parser.add_argument('--bundle-root', type=Path, required=True,
                        help='existing release-output directory containing the selected release bundle')
    runtime.arguments(parser)
    args = parser.parse_args()
    try:
        path, digest = stage(args)
        print('Staged exact preparation evidence.')
        print(digest)
    except (ValueError, KeyError, OSError, TypeError) as error:
        print('preparation staging failed:', error, file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
