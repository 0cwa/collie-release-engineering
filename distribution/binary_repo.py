#!/usr/bin/env python3
"""Explicitly approved binary ingestion; never builds or signs an APK."""
import argparse
import contextlib
import fcntl
import hashlib
import html
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

SERVER_VERSION = '2.4.5'
APP = 'im.collie.app'
ALIASES = {'collie.apk','collie-foss.apk','molly-continued.apk','molly-foss-continued.apk','molly-minimal.apk'}

def sha(path):
    h = hashlib.sha256()
    with open(path,'rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''): h.update(block)
    return h.hexdigest()

def load(path):
    def unique(pairs):
        out={}
        for k,v in pairs:
            if k in out: raise ValueError('duplicate field '+k)
            out[k]=v
        return out
    return json.loads(Path(path).read_text(),object_pairs_hook=unique)

def approval(path):
    a=load(path)
    fields={'schema','release_id','tag','release_manifest_sha256','apk_sha256','apk_certificate_sha256','package','version_code','version_name','aliases'}
    if set(a)!=fields or a['schema']!=1: raise ValueError('approval fields/schema')
    for k in ('release_manifest_sha256','apk_sha256','apk_certificate_sha256'):
        if not re.fullmatch('[0-9a-f]{64}',a[k]): raise ValueError(k)
    if a['package']!=APP or type(a['version_code']) is not int or a['version_code']<=0: raise ValueError('package/version')
    if type(a['release_id']) is not int or a['release_id']<=0 or not a['tag'] or not a['version_name']: raise ValueError('release identity')
    if set(a['aliases'])!=ALIASES or any(v!=a['apk_sha256'] for v in a['aliases'].values()): raise ValueError('alias inequality')
    return a

def inspect(apk,aapt,apksigner):
    sig=subprocess.check_output([apksigner,'verify','--verbose','--print-certs',str(apk)],text=True)
    certs=re.findall(r'Signer #\d+ certificate SHA-256 digest: ([0-9a-fA-F]+)',sig)
    if len(certs)!=1: raise ValueError('expected one verified APK signer')
    badging=subprocess.check_output([aapt,'dump','badging',str(apk)],text=True)
    m=re.search(r"package: name='([^']+)' versionCode='([0-9]+)' versionName='([^']+)'",badging)
    if not m or 'application-debuggable' in badging: raise ValueError('APK metadata/debuggable')
    return dict(package=m[1],version_code=int(m[2]),version_name=m[3],apk_certificate_sha256=certs[0].lower())

@contextlib.contextmanager
def locked(root):
    root.mkdir(parents=True,exist_ok=True)
    with open(root/'.publish.lock','a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        yield

def immutable_copy(source,dest):
    if source.is_symlink() or not source.is_file(): raise ValueError('regular source file required')
    dest.parent.mkdir(parents=True,exist_ok=True)
    if dest.exists():
        if dest.is_symlink() or sha(dest)!=sha(source): raise ValueError('immutable URL conflict: '+str(dest))
        return
    temp=dest.with_name(dest.name+'.partial')
    shutil.copyfile(source,temp)
    if sha(source)!=sha(temp): raise ValueError('copy digest mismatch')
    os.replace(temp,dest)

def ingest(approved,manifest,apk,workspace,aapt,apksigner):
    a=approval(approved)
    if sha(manifest)!=a['release_manifest_sha256'] or sha(apk)!=a['apk_sha256']: raise ValueError('approved bytes mismatch')
    info=inspect(apk,aapt,apksigner)
    if any(a[k]!=v for k,v in info.items()): raise ValueError('APK identity substitution')
    with locked(workspace):
        target=workspace/'repo'/f"{APP}_{a['version_code']}.apk"
        immutable_copy(apk,target)
        metadata=Path(__file__).parent/'metadata'/f'{APP}.yml'
        text=metadata.read_text().replace('@APK_CERTIFICATE@',a['apk_certificate_sha256'])
        (workspace/'metadata').mkdir(exist_ok=True)
        (workspace/'metadata'/metadata.name).write_text(text)
        (workspace/'approved-release.json').write_text(json.dumps(a,indent=2)+'\n')
    return target

def verify_indexes(generation, fingerprint):
    from fdroidserver import index, common
    common.config = {"jarsigner": shutil.which("jarsigner")}
    entry, _, _ = index.get_index_from_jar(str(generation/'entry.jar'), fingerprint)
    v1, _, _ = index.get_index_from_jar(str(generation/'index-v1.jar'), fingerprint, allow_deprecated=True)
    ref=entry['index']
    name=ref['name'].lstrip('/')
    if name!='index-v2.json' or sha(generation/name)!=ref['sha256']: raise ValueError('v2 index digest mismatch')
    data=load(generation/name)
    if set(data['packages'])!={APP}: raise ValueError('unexpected indexed app')
    for version in data['packages'][APP]['versions'].values():
        file=version['file']; filename=file['name'].lstrip('/')
        if Path(filename).name!=filename or not filename.endswith('.apk'): raise ValueError('unsafe APK URL')
        if sha(generation/filename)!=file['sha256']: raise ValueError('indexed APK digest mismatch')
    if {p['packageName'] for p in v1['apps']}!={APP}: raise ValueError('v1 package mismatch')
    return data

def publish_local(generation,origin,fingerprint):
    """Publish a signed generation; entry.jar last. Readers verify referenced hashes.

    This is intentionally not called atomic: clients crossing generations retry refresh.
    Keep old immutable index payloads and APKs. Never upload private workspace/config.
    """
    verify_indexes(generation,fingerprint)
    files=[p for p in generation.rglob('*') if p.is_file()]
    if any(p.is_symlink() for p in generation.rglob('*')): raise ValueError('symlink in public generation')
    required={'entry.jar','index-v2.json','index-v1.jar'}
    if not required.issubset({p.name for p in files}): raise ValueError('missing signed indexes')
    # Only fdroid public file types; secrets/config cannot enter origin by accident.
    allowed={'.apk','.jar','.json','.xml','.png','.jpg','.webp','.svg','.css','.html'}
    if any(p.suffix not in allowed or p.name in {'config.json','approved-release.json'} for p in files): raise ValueError('unexpected public file')
    generation_id=hashlib.sha256(json.dumps({str(p.relative_to(generation)):sha(p) for p in sorted(files)},sort_keys=True).encode()).hexdigest()
    with locked(origin):
        state=origin/'.promotion.json'
        journal=load(state) if state.exists() else {'schema':1,'completed':[],'active':None}
        if journal['active'] not in (None,generation_id): raise ValueError('resume active generation first')
        journal['active']=generation_id
        state.write_text(json.dumps(journal))
        # Immutable content first; mutable index entry points last. No stale negative caching.
        ordered=sorted(files,key=lambda p:(p.name in {'index.xml','index.jar','index-v1.json','index-v1.jar','index-v2.json','entry.json','entry.jar'},p.name=='entry.jar',str(p)))
        for p in ordered:
            rel=p.relative_to(generation); dest=origin/rel
            if p.suffix=='.apk' or rel.parts[0]=='diff': immutable_copy(p,dest)
            else:
                dest.parent.mkdir(parents=True,exist_ok=True)
                tmp=dest.with_name(dest.name+'.partial');shutil.copyfile(p,tmp);os.replace(tmp,dest)
            if sha(p)!=sha(dest): raise ValueError('readback mismatch')
        journal['completed'].append(generation_id);journal['active']=None
        state.write_text(json.dumps(journal,indent=2)+'\n')
    return generation_id

def landing(output,repo_url,repo_cert,apk_cert,download):
    if not re.fullmatch('[0-9a-fA-F]{64}',repo_cert) or not re.fullmatch('[0-9a-fA-F]{64}',apk_cert): raise ValueError('certificate fingerprint')
    if not repo_url.startswith('https://') or not download.startswith('https://'): raise ValueError('HTTPS required')
    link=repo_url+'?fingerprint='+repo_cert.upper()
    import qrcode
    import qrcode.image.svg
    output.mkdir(parents=True,exist_ok=True)
    qrcode.make(link,image_factory=qrcode.image.svg.SvgPathImage).save(output/'add-repository.svg')
    e=html.escape
    (output/'index.html').write_text(f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Collie repository — staging preview</title><style>body{{font:18px/1.6 system-ui;max-width:42rem;margin:3rem auto;padding:0 1.2rem;background:#faf8f2;color:#5a4033}}a{{color:#2f4f3a}}code{{overflow-wrap:anywhere}}img{{width:180px;height:180px}}h1{{line-height:1.2}}</style><main><p>STAGING PREVIEW · Not deployed</p><h1>Collie, from the same trusted APK.</h1><p>This custom F-Droid-compatible repository distributes the same signed Collie APK as GitHub, including optional FCM support. It is separate from the official F-Droid catalogue.</p><p><a href="{e(link)}">Add repository</a> in your compatible client, then compare the repository fingerprint below before refreshing.</p><img src="add-repository.svg" alt="QR code containing the fingerprint-pinned repository link"><p>Repository index certificate<br><code>{e(repo_cert.upper())}</code></p><p>APK signing certificate<br><code>{e(apk_cert.upper())}</code></p><p><a href="{e(download)}">Canonical APK</a> · <a href="https://github.com/0cwa/collieim-android">Source</a> · <a href="https://github.com/0cwa/collie-release-engineering/tree/main/distribution">Verification</a></p></main></html>''')

def main():
    p=argparse.ArgumentParser();s=p.add_subparsers(dest='cmd',required=True)
    i=s.add_parser('ingest')
    for name in ('approval','manifest','apk','workspace'): i.add_argument('--'+name,required=True,type=Path)
    i.add_argument('--aapt',required=True);i.add_argument('--apksigner',required=True)
    x=s.add_parser('publish-local');x.add_argument('generation',type=Path);x.add_argument('origin',type=Path);x.add_argument('--fingerprint',required=True)
    a=p.parse_args()
    if a.cmd=='ingest': print(ingest(a.approval,a.manifest,a.apk,a.workspace,a.aapt,a.apksigner))
    else: print(publish_local(a.generation,a.origin,a.fingerprint))
if __name__=='__main__': main()
