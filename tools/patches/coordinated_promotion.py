#!/usr/bin/env python3
"""Durable operator journal binding GitHub and repository promotion to same bytes.

This command never signs, builds, uploads, publishes or reads credentials. Endpoint
read-back is executed separately using approved verifiers; externally reviewed
report digests are required. Missing/incomplete read-back never advances a phase.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re

HASH=re.compile(r'[0-9a-f]{64}\Z')
PLAN={'schema','release_id','tag','release_manifest_sha256','apk_sha256','apk_certificate_sha256','package','version_code','repository_generation_sha256'}
BINDINGS=('release_manifest_sha256','apk_sha256','apk_certificate_sha256','package','version_code')


def unique(pairs):
    out={}
    for k,v in pairs:
        if k in out:raise ValueError('duplicate field')
        out[k]=v
    return out


def checked(path,digest):
    raw=Path(path).read_bytes()
    if not HASH.fullmatch(digest) or hashlib.sha256(raw).hexdigest()!=digest:raise ValueError('externally approved digest differs')
    return json.loads(raw,object_pairs_hook=unique)


def validate_plan(plan):
    if set(plan)!=PLAN or type(plan['schema']) is not int or plan['schema']!=1:raise ValueError('promotion plan fields/schema')
    for k in ('release_manifest_sha256','apk_sha256','apk_certificate_sha256','repository_generation_sha256'):
        if not isinstance(plan[k],str) or not HASH.fullmatch(plan[k]):raise ValueError('promotion identity')
    if plan['package']!='im.collie.app' or type(plan['version_code']) is not int or type(plan['release_id']) is not int or plan['version_code']<1 or plan['release_id']<1:raise ValueError('package/version/release identity')
    if not isinstance(plan['tag'],str) or not re.fullmatch(r'[A-Za-z0-9._+-]+',plan['tag']):raise ValueError('release tag')
    return plan


def observe(state_path,plan_path,plan_sha256,endpoint,report_path,report_sha256):
    plan=validate_plan(checked(plan_path,plan_sha256));report=checked(report_path,report_sha256)
    if endpoint not in ('github','repository'):raise ValueError('unknown endpoint')
    keys={'schema','endpoint','status',*BINDINGS,'release_id','tag'}
    if endpoint=='repository':keys|={'repository_generation_sha256','index_signatures_verified'}
    else:keys|={'aliases','published'}
    if set(report)!=keys or type(report['schema']) is not int or report['schema']!=1 or report['endpoint']!=endpoint or report['status']!='PASS':raise ValueError('complete passing endpoint readback required')
    for key in (*BINDINGS,'release_id','tag'):
        if report[key]!=plan[key] or type(report[key]) is not type(plan[key]):raise ValueError('endpoint substituted release input: '+key)
    if endpoint=='github':
        aliases={'collie.apk','collie-foss.apk','molly-continued.apk','molly-foss-continued.apk','molly-minimal.apk'}
        if report['published'] is not True or not isinstance(report['aliases'],dict) or set(report['aliases'])!=aliases or set(report['aliases'].values())!={plan['apk_sha256']}:raise ValueError('published alias readback differs')
    elif report['repository_generation_sha256']!=plan['repository_generation_sha256'] or report['index_signatures_verified'] is not True:raise ValueError('repository generation/signature readback differs')
    state_path=Path(state_path);state_path.parent.mkdir(parents=True,exist_ok=True)
    with open(str(state_path)+'.lock','a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        state=json.loads(state_path.read_text(),object_pairs_hook=unique) if state_path.exists() else {'schema':1,'plan_sha256':plan_sha256,'verified':{},'phase':'prepared'}
        if state.get('plan_sha256')!=plan_sha256:raise ValueError('resume original immutable plan; input substitution refused')
        if endpoint=='repository' and 'github' not in state['verified']:raise ValueError('verify existing GitHub published bytes first')
        previous=state['verified'].get(endpoint)
        if previous is not None:
            if previous!=report_sha256:raise ValueError('endpoint already bound to different approved readback')
            return state
        state['verified'][endpoint]=report_sha256
        state['phase']='complete' if len(state['verified'])==2 else 'github-verified'
        temporary=state_path.with_name(state_path.name+'.partial')
        with temporary.open('w') as out:
            json.dump(state,out,sort_keys=True,indent=2);out.write('\n');out.flush();os.fsync(out.fileno())
        os.replace(temporary,state_path)
        fd=os.open(state_path.parent,os.O_RDONLY)
        try:os.fsync(fd)
        finally:os.close(fd)
    return state


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for field in ('state','plan','report'):p.add_argument('--'+field,type=Path,required=True)
    p.add_argument('--approved-plan-sha256',required=True);p.add_argument('--approved-report-sha256',required=True)
    p.add_argument('--endpoint',choices=('github','repository'),required=True)
    a=p.parse_args();print(json.dumps(observe(a.state,a.plan,a.approved_plan_sha256,a.endpoint,a.report,a.approved_report_sha256),indent=2))
