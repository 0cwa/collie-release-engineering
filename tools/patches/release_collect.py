#!/usr/bin/env python3
"""Check and record unsigned APK inputs from an approved release build."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

import release_artifacts as a
import runtime_receipts as runtime
import migration_receipts as receipts


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--directory', type=Path, required=True)
    p.add_argument('--build-tools', type=Path, required=True)
    p.add_argument('--config', type=Path, required=True)
    p.add_argument('--commit', required=True)
    p.add_argument('--workflow-revision', required=True)
    p.add_argument('--run-id', required=True)
    p.add_argument('--run-attempt', required=True)
    p.add_argument('--version-name', required=True)
    p.add_argument('--version-code', type=int, required=True)
    p.add_argument('--certificate-sha256', required=True)
    p.add_argument('--tag', help='exact prerelease tag (required for Beta)')
    p.add_argument('--title', help='exact prerelease title (required for Beta)')
    p.add_argument('--single-collie', action='store_true', help='one Collie APK supplies all five compatibility filenames')
    p.add_argument('--preparation-baseline-commit',help='full commit pinned by the operator before preparing this source')
    runtime.arguments(p)
    args = p.parse_args()
    try:
        context=runtime.plan(args)
        channel=args.channel
        if channel=='trailblazer-beta':
            if args.single_collie:raise ValueError('Beta cannot use Stable compatibility aliases')
            apk_names=sorted(path.name for path in args.directory.glob('*.apk'))
            if apk_names!=['trailblazer-beta.apk']:
                raise ValueError('Beta collection accepts only trailblazer-beta.apk')
            runtime.verify_beta_ci_run(args.run_id,args.run_attempt,args.commit)
            a.validate_beta_identity(args.version_name,args.version_code,args.tag,args.title,True)
        profile=runtime.CHANNELS[channel]
        config = json.loads(args.config.read_text())
        if args.build_tools.name != config['android']['build_tools']:
            raise ValueError('build-tools directory does not match the build pin')
        manifest = {'schema': 1, 'commit': args.commit, 'workflow_revision': args.workflow_revision,
                    'run_id': args.run_id, 'run_attempt': args.run_attempt,
                    'version_name': args.version_name, 'version_code': args.version_code,
                    'certificate_sha256': args.certificate_sha256, 'package': profile['package'],
                    'input_pins': config, 'build_tools': args.build_tools.name,
                    'java': subprocess.run(['java', '-version'], check=True, capture_output=True, text=True).stderr.strip(),
                    'apks': []}
        if channel=='trailblazer-beta':
            manifest.update(schema=2,channel=channel,tag=args.tag,title=args.title,prerelease=True,
                            artifact_mode='single-trailblazer-beta',source_profile=channel,
                            **runtime.identities(context))
            path=args.directory/'trailblazer-beta.apk'
            a.check_unsigned(path)
            a.check_metadata(args.build_tools/'aapt2',path,args.version_code,args.version_name,channel)
            manifest['apks']=[{'profile':channel,'file':'trailblazer-beta.apk','sha256':a.sha256(path)}]
        elif args.single_collie:
            manifest['artifact_mode'] = 'single-collie'
            manifest['source_profile'] = 'collie'
            path = args.directory / 'collie.apk'
            a.check_unsigned(path)
            a.check_metadata(args.build_tools / 'aapt2', path, args.version_code, args.version_name)
            digest = a.sha256(path)
            manifest['apks'] = [{'profile': profile, 'file': profile + '.apk', 'sha256': digest}
                                for profile in sorted(a.PROFILES)]
        else:
            for profile in sorted(a.PROFILES):
                file = profile + '.apk'
                path = args.directory / file
                a.check_unsigned(path)
                a.check_metadata(args.build_tools / 'aapt2', path, args.version_code, args.version_name)
                manifest['apks'].append({'profile': profile, 'file': file, 'sha256': a.sha256(path)})
        if context[0]['mode']=='new-source':
            if not args.single_collie and channel!='trailblazer-beta':raise ValueError('new-source collection requires one canonical Stable input')
            manifest.update(schema=2,**runtime.identities(context))
            runtime.open_collection_output(args,context)
            source,build=runtime.collect(args,context,manifest['apks'][0]['sha256'])
            manifest.update(release_mode='new-source',release_plan_sha256=context[1],source_receipt_sha256=receipts.digest(source),build_receipt_sha256=receipts.digest(build))
            runtime.write_collection_file(args,'build-receipt.json',build)
        a.validate_manifest(manifest, args.commit, args.run_id, args.certificate_sha256,
                            runtime.identities(context),channel)
        if not re.fullmatch(r'[0-9a-f]{40}', args.workflow_revision):
            raise ValueError('invalid workflow revision')
        runtime.write_collection_file(args,'manifest.json',manifest)
    except (ValueError, KeyError, OSError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        print('release collection failed:', exc, file=sys.stderr)
        return 1
    finally:
        runtime.close_collection_output(args)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
