"""Prepare operator commands for ONE R2 bucket. Does not upload or read credentials.

The bucket must have an exclusive publication lease external to this adapter.
For production, use the signed candidate verified locally and a reviewed plan.
"""
import argparse
from pathlib import Path
import shlex
import binary_repo as b
p=argparse.ArgumentParser();p.add_argument('generation',type=Path);p.add_argument('--fingerprint',required=True);p.add_argument('--bucket',required=True);p.add_argument('--account',required=True);p.add_argument('--scratch',required=True,type=Path);p.add_argument('--approved-generation',required=True);p.add_argument('--approval',action='append',required=True,type=Path);a=p.parse_args()
b.approved_generation(a.generation,a.fingerprint,a.approved_generation,a.approval)
pinned={str(p.relative_to(a.generation)):b.sha(p) for p in b.public_files(a.generation)}
import hashlib,json
if hashlib.sha256(json.dumps(pinned,sort_keys=True).encode()).hexdigest()!=a.approved_generation:raise ValueError('generation changed after approval')
# This adapter emits commands only: approval and account access remain operator tasks.
if not all(c.isalnum() or c=='-' for c in a.bucket) or not a.account.isalnum(): raise ValueError('account/bucket')
print('# Acquire exclusive operator lease; keep this plan + approved generation digest.')
files=sorted(b.public_files(a.generation),key=lambda p:(p.suffix!='.apk',p.name=='entry.jar',str(p)))
for file in files:
    rel=file.relative_to(a.generation)
    if file.is_symlink():raise ValueError('symlink')
    url=f's3://{a.bucket}/fdroid/repo/{rel}'
    cache='public,max-age=31536000,immutable' if file.suffix=='.apk' else 'no-cache,max-age=0,must-revalidate'
    # APK URL preflight must distinguish absence from denial; never overwrite conflicts.
    if file.suffix=='.apk':
        print('# REQUIRED: head-object; existing object SHA256 must equal '+b.sha(file)+'; otherwise STOP. Skip existing identical object.')
    cmd=['python3',str(Path(__file__).with_name('upload_r2.py')),'--source',str(file),'--sha256',pinned[str(rel)],'--endpoint',f'https://{a.account}.r2.cloudflarestorage.com','--destination',url,'--cache-control',cache,'--scratch',str(a.scratch)]
    print(shlex.join(cmd))
print('# Read back every object and compare SHA256, then verify fingerprint-pinned v1/v2 indexes before committing promotion receipt.')
print('# On interruption resume SAME generation. Disable negative caching at CDN; 404 Cache-Control: no-store.')
