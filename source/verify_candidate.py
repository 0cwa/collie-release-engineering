#!/usr/bin/env python3
"""Read-only candidate audit; use a separately approved verifier revision."""
import argparse
import hashlib
import json
from pathlib import Path
import publish

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--repo', type=Path, required=True)
parser.add_argument('--plan', type=Path, required=True)
parser.add_argument('--expected-plan-sha256', required=True)
parser.add_argument('--current-main', required=True)
parser.add_argument('--expected-source', required=True)
parser.add_argument('--expected-integration', required=True)
args = parser.parse_args()
raw = args.plan.read_bytes()
if hashlib.sha256(raw).hexdigest() != args.expected_plan_sha256:
    raise ValueError('plan approval digest differs')
plan = json.loads(raw)
publish.verify(args.repo, plan, args.expected_source, args.expected_integration, args.current_main)
print(json.dumps({'status':'PASS','scope':'Complete source graph/tree identity only; no build, production authorization or signed attestation',
                  'source':args.expected_source,'integration':args.expected_integration,'base':args.current_main,
                  'plan_sha256':args.expected_plan_sha256},indent=2))
