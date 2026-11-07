"""Actual signed-index tamper and crossing-generation checks, no device claim."""
import argparse
from pathlib import Path
import binary_repo as b
p=argparse.ArgumentParser();p.add_argument('repo',type=Path);p.add_argument('origin',type=Path);p.add_argument('--fingerprint',required=True);a=p.parse_args()
b.verify_indexes(a.repo,a.fingerprint)
try:b.verify_indexes(a.repo,'0'*64)
except Exception:pass
else:raise AssertionError('wrong repository certificate accepted')
original=(a.repo/'index-v2.json').read_bytes()
try:
    (a.repo/'index-v2.json').write_bytes(original+b' ')
    try:b.verify_indexes(a.repo,a.fingerprint)
    except ValueError as error:
        if 'digest mismatch' not in str(error):raise
    else:raise AssertionError('crossed/tampered index accepted')
finally:(a.repo/'index-v2.json').write_bytes(original)
g=b.publish_local(a.repo,a.origin,a.fingerprint)
assert b.publish_local(a.repo,a.origin,a.fingerprint)==g
b.verify_indexes(a.origin,a.fingerprint)
print('PASS: actual v1/v2 signatures; wrong repository key rejected; crossed v2 digest rejected; resumable local promotion/readback '+g)
