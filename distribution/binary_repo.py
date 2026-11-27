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
try:
    from . import migration_receipts as receipts
    from .channel_profile import STABLE, ChannelProfile, require_trusted
except ImportError:
    import migration_receipts as receipts
    from channel_profile import STABLE, ChannelProfile, require_trusted

SERVER_VERSION = '2.4.5'
APP = 'im.collie.app'
APK_CERTIFICATE = 'f8cc0c0c052e5635c26fcd293c2b0af3e87d61ff7f2df3602a9af6e91f73c826'
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

def approval(path, profile=STABLE, *, allow_canonical_stable=False):
    a=load(path)
    fields={'schema','release_id','tag','release_manifest_sha256','apk_sha256','apk_certificate_sha256','package','version_code','version_name','aliases'}
    if a.get('schema')==2:fields|={'release_receipt_sha256','receipt_approvals'}
    if set(a)!=fields or type(a.get('schema')) is not int or a['schema'] not in (1,2): raise ValueError('approval fields/schema')
    if a['schema']==2:
        if not re.fullmatch('[0-9a-f]{64}',a['release_receipt_sha256']):raise ValueError('canonical release receipt digest')
        if set(a['receipt_approvals'])!={'source','build','release'} or any(not re.fullmatch('[0-9a-f]{64}',v) for v in a['receipt_approvals'].values()):raise ValueError('receipt approvals')
        if a['receipt_approvals']['release']!=a['release_receipt_sha256']:raise ValueError('release receipt approval differs')
    for k in ('release_manifest_sha256','apk_sha256','apk_certificate_sha256'):
        if not re.fullmatch('[0-9a-f]{64}',a[k]): raise ValueError(k)
    if profile.apk_certificate is None:raise ValueError(f'{profile.name} APK signer pin is not configured')
    if a['apk_certificate_sha256']!=profile.apk_certificate:raise ValueError(f'{profile.name} APK signing certificate differs')
    if a['package']!=profile.package or type(a['version_code']) is not int or a['version_code']<=0: raise ValueError('package/version')
    if type(a['release_id']) is not int or a['release_id']<=0 or not a['tag'] or not a['version_name']: raise ValueError('release identity')
    if profile.name=='stable':
        historical=dict.fromkeys(ALIASES,a['apk_sha256'])
        canonical={profile.apk_asset:a['apk_sha256']}
        if a['aliases']!=historical and not (allow_canonical_stable and a['aliases']==canonical):
            raise ValueError('alias inequality')
    elif a['aliases']!={profile.apk_asset:a['apk_sha256']} or a['tag']!=profile.tag_prefix+a['version_name']:
        raise ValueError('Beta asset/tag identity differs')
    return a

def verify_release_approval(a,bundle,profile=STABLE):
    if a['schema']==1:
        if bundle is not None:raise ValueError('legacy approval cannot claim a complete receipt graph')
        return None
    if bundle is None:raise ValueError('schema2 requires externally approved receipt bundle')
    documents={kind:receipts.load(bundle/(kind+'.json'),kind,profile=profile.name) for kind in ('source','build','release')}
    receipts.verify_release_inputs(**documents,approved=a['receipt_approvals'],raw_manifest_sha256=a['release_manifest_sha256'],promotion=True,profile=profile.name)
    release=documents['release']['payload']
    links={'package':'package','version_code':'version_code','version_name':'version_name','apk_sha256':'apk_sha256','apk_certificate_sha256':'apk_certificate_sha256'}
    if any(a[left]!=release[right] for left,right in links.items()):raise ValueError('approved release receipt APK identity differs')
    return documents['release']

def inspect(apk,aapt,apksigner,profile=STABLE):
    sig=subprocess.check_output([apksigner,'verify','--verbose','--print-certs',str(apk)],text=True)
    certs=[value.replace(':','').lower() for value in
           re.findall(r'Signer #\d+ certificate SHA-256 digest: ([0-9a-fA-F:]+)',sig)]
    if len(certs)!=1 or not re.fullmatch('[0-9a-f]{64}',certs[0]):
        raise ValueError('expected one verified APK signer')
    badging=subprocess.check_output([aapt,'dump','badging',str(apk)],text=True)
    m=re.search(r"package: name='([^']+)' versionCode='([0-9]+)' versionName='([^']+)'",badging)
    if not m or 'application-debuggable' in badging: raise ValueError('APK metadata/debuggable')
    return dict(package=m[1],version_code=int(m[2]),version_name=m[3],apk_certificate_sha256=certs[0])

@contextlib.contextmanager
def locked(root):
    root.mkdir(parents=True,exist_ok=True)
    with open(root/'.publish.lock','a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        yield

def immutable_copy(source,dest,expected=None):
    if expected is None:expected=sha(source)
    if source.is_symlink() or not source.is_file(): raise ValueError('regular source file required')
    dest.parent.mkdir(parents=True,exist_ok=True)
    if dest.exists():
        if dest.is_symlink() or sha(dest)!=expected: raise ValueError('immutable URL conflict: '+str(dest))
        return
    temp=dest.with_name(dest.name+'.partial')
    shutil.copyfile(source,temp)
    if sha(temp)!=expected: raise ValueError('copy digest mismatch')
    os.replace(temp,dest)

def ingest(approved,manifest,apk,workspace,aapt,apksigner,receipt_bundle=None,profile=STABLE):
    a=approval(approved,profile)
    if sha(manifest)!=a['release_manifest_sha256'] or sha(apk)!=a['apk_sha256']: raise ValueError('approved bytes mismatch')
    verify_release_approval(a,receipt_bundle,profile)
    info=inspect(apk,aapt,apksigner,profile)
    if any(a[k]!=v for k,v in info.items()): raise ValueError('APK identity substitution')
    with locked(workspace):
        target=workspace/'repo'/f"{profile.package}_{a['version_code']}.apk"
        immutable_copy(apk,target,a['apk_sha256'])
        metadata=Path(__file__).parent/('metadata' if profile.name=='stable' else 'beta-metadata')/f'{profile.package}.yml'
        text=metadata.read_text()
        (workspace/'metadata').mkdir(exist_ok=True)
        (workspace/'metadata'/metadata.name).write_text(text)
        (workspace/'approved-release.json').write_text(json.dumps(a,indent=2)+'\n')
    return target

def verify_indexes(generation, fingerprint, profile=STABLE):
    if profile.repository_certificate is None:
        raise ValueError(f'{profile.name} repository signer pin is not configured')
    if fingerprint.lower() != profile.repository_certificate.lower():
        raise ValueError(f'{profile.name} repository signer differs')
    from fdroidserver import index, common
    common.config = {"jarsigner": shutil.which("jarsigner")}
    entry, _, _ = index.get_index_from_jar(str(generation/'entry.jar'), fingerprint)
    v1, _, _ = index.get_index_from_jar(str(generation/'index-v1.jar'), fingerprint, allow_deprecated=True)
    ref=entry['index']
    name=ref['name'].lstrip('/')
    if name!='index-v2.json' or sha(generation/name)!=ref['sha256']: raise ValueError('v2 index digest mismatch')
    data=load(generation/name)
    if set(data['packages'])!={profile.package}: raise ValueError('unexpected indexed app/channel package')
    if profile.name!='stable' and data['packages'][profile.package].get('releaseChannels') not in (None,[]):
        raise ValueError('Beta package index releaseChannels must be absent or empty')
    for version in data['packages'][profile.package]['versions'].values():
        file=version['file']; filename=file['name'].lstrip('/')
        if Path(filename).name!=filename or not filename.endswith('.apk'): raise ValueError('unsafe APK URL')
        if sha(generation/filename)!=file['sha256']: raise ValueError('indexed APK digest mismatch')
    if profile.name!='stable':
        for item in release_tuples(data,profile):
            if type(item[0]) is not int or item[0]<=0:
                raise ValueError('indexed Beta versionCode must be a positive integer')
            if profile.apk_certificate is None or item[4]!=profile.apk_certificate.lower():
                raise ValueError('indexed Beta APK signer differs from channel pin')
    validate_index_parity(data,v1,profile)
    return data

def release_tuples(data, profile=STABLE):
    tuples=[]
    for version in data['packages'][profile.package]['versions'].values():
        manifest=version['manifest'];file=version['file']
        signers=manifest['signer']['sha256']
        if len(signers)!=1:raise ValueError('one APK signer required')
        tuples.append((manifest['versionCode'],manifest['versionName'],file['sha256'],file['name'].lstrip('/'),signers[0].lower()))
    if len({v[0] for v in tuples})!=len(tuples):raise ValueError('duplicate versionCode')
    return set(tuples)

def validate_index_parity(data,v1,profile=STABLE):
    if {p['packageName'] for p in v1['apps']}!={profile.package} or set(v1['packages'])!={profile.package}:raise ValueError('v1 package mismatch')
    versions=v1['packages'][profile.package]
    legacy={(v['versionCode'],v['versionName'],v['hash'],v['apkName'],v['signer'].lower()) for v in versions if v['hashType']=='sha256'}
    if len(legacy)!=len(versions) or legacy!=release_tuples(data,profile):raise ValueError('v1/v2 release mismatch')

def public_path(rel, profile=STABLE):
    root_files={'entry.jar','entry.json','index-v2.json','index-v1.jar','index-v1.json','index.jar','index.xml','index.html','index.css','index.png'}
    pkg=re.escape(profile.package)
    return bool(rel in root_files or re.fullmatch(pkg+r'_[1-9][0-9]*\.apk',rel) or re.fullmatch(r'diff/[0-9]+\.json',rel) or re.fullmatch(r'(icons(?:-[0-9]+)?)/[A-Za-z0-9_.-]+\.(png|jpg|webp|svg)',rel) or re.fullmatch(pkg+r'/[A-Za-z-]+/(?:[A-Za-z]+Screenshots/)?[A-Za-z0-9_.=-]+\.(png|jpg)',rel) or rel in {'status/running.json','status/update.json'})

def public_files(generation, profile=STABLE):
    if generation.is_symlink():raise ValueError('symlink generation')
    files=[]
    for p in generation.rglob('*'):
        if p.is_symlink():raise ValueError('symlink in public generation')
        if not p.is_file():continue
        rel=p.relative_to(generation).as_posix()
        valid=public_path(rel,profile)
        if not valid:raise ValueError('unexpected public file: '+rel)
        files.append(p)
    if not {'entry.jar','index-v2.json','index-v1.jar'}.issubset({p.name for p in files}):raise ValueError('missing signed indexes')
    return sorted(files)

def generation_digest(generation,profile=STABLE):
    return hashlib.sha256(json.dumps({str(p.relative_to(generation)):sha(p) for p in public_files(generation,profile)},sort_keys=True).encode()).hexdigest()

def approved_generation(generation,fingerprint,expected_generation,approvals,profile=STABLE, *, allow_canonical_stable=False):
    # Both approvals and generation digest come from the operator's reviewed record,
    # outside the candidate. A repository signature alone is not promotion authority.
    if profile.name!='stable':
        require_trusted(profile)
    files=public_files(generation,profile)
    actual=generation_digest(generation,profile)
    if not re.fullmatch('[0-9a-f]{64}',expected_generation) or actual!=expected_generation:raise ValueError('approved generation mismatch')
    data=verify_indexes(generation,fingerprint,profile)
    approved=[]
    for path in approvals:
        a=approval(path,profile,allow_canonical_stable=allow_canonical_stable)
        approved.append((a['version_code'],a['version_name'],a['apk_sha256'],f"{profile.package}_{a['version_code']}.apk",a['apk_certificate_sha256']))
    if not approved or len(set(approved))!=len(approved) or set(approved)!=release_tuples(data,profile):raise ValueError('unapproved indexed release')
    apks={p.name for p in files if p.suffix=='.apk'}
    if apks!={v[3] for v in approved}:raise ValueError('unapproved APK file')
    return actual,data

def atomic_json(path,data):
    temporary=path.with_name(path.name+'.partial')
    with open(temporary,'w') as stream:
        json.dump(data,stream,indent=2);stream.write('\n');stream.flush();os.fsync(stream.fileno())
    os.replace(temporary,path)
    directory=os.open(path.parent,os.O_RDONLY)
    try:os.fsync(directory)
    finally:os.close(directory)

def publish_local(generation,origin,fingerprint,expected_generation,approvals,profile=STABLE):
    """Publish a signed generation; entry.jar last. Readers verify referenced hashes.

    This is intentionally not called atomic: clients crossing generations retry refresh.
    Keep old immutable index payloads and APKs. Never upload private workspace/config.
    """
    if profile.name!='stable':
        raise ValueError('Beta publication requires approved Pages snapshots')
    generation_id,data=approved_generation(generation,fingerprint,expected_generation,approvals,profile,allow_canonical_stable=True)
    if profile.name!='stable' and data['repo']['address'].rstrip('/')!=profile.repository_url.rstrip('/'):
        raise ValueError('signed index origin differs from selected channel')
    files=public_files(generation,profile)
    pinned={str(p.relative_to(generation)):sha(p) for p in files}
    if hashlib.sha256(json.dumps(pinned,sort_keys=True).encode()).hexdigest()!=expected_generation:raise ValueError('generation changed after approval')
    control=origin.parent/(origin.name+'.control')
    origin.mkdir(parents=True,exist_ok=True)
    with locked(control):
        state=control/'promotion.json'
        journal=load(state) if state.exists() else {'schema':1,'completed':[],'active':None}
        if journal['active'] not in (None,generation_id): raise ValueError('resume active generation first')
        journal['active']=generation_id
        atomic_json(state,journal)
        # Immutable content first; mutable index entry points last. No stale negative caching.
        ordered=sorted(files,key=lambda p:(p.name in {'index.xml','index.jar','index-v1.json','index-v1.jar','index-v2.json','entry.json','entry.jar'},p.name=='entry.jar',str(p)))
        for p in ordered:
            rel=p.relative_to(generation); dest=origin/rel
            if p.suffix=='.apk' or rel.parts[0]=='diff': immutable_copy(p,dest,pinned[str(rel)])
            else:
                dest.parent.mkdir(parents=True,exist_ok=True)
                tmp=dest.with_name(dest.name+'.partial');shutil.copyfile(p,tmp)
                if sha(tmp)!=pinned[str(rel)]:raise ValueError('approved file changed during copy')
                os.replace(tmp,dest)
            if pinned[str(rel)]!=sha(dest): raise ValueError('readback mismatch')
        for rel,expected in pinned.items():
            if sha(origin/rel)!=expected:raise ValueError('approved readback mismatch')
        verify_indexes(origin,fingerprint,profile)
        if generation_id not in journal['completed']:journal['completed'].append(generation_id)
        journal['active']=None
        atomic_json(state,journal)
    return generation_id

def landing(output,repo_url,repo_cert,apk_cert,download,*,state='inactive'):
    # Rendering data grants no publication authority. First-link activation is
    # separately verified by pages_activation against actual production evidence.
    import site_page
    return site_page.render(output,repo_url,repo_cert,apk_cert,download,state=state)

def main():
    p=argparse.ArgumentParser();s=p.add_subparsers(dest='cmd',required=True)
    i=s.add_parser('ingest')
    for name in ('approval','manifest','apk','workspace'): i.add_argument('--'+name,required=True,type=Path)
    i.add_argument('--receipt-bundle',type=Path);i.add_argument('--aapt',required=True);i.add_argument('--apksigner',required=True)
    i.add_argument('--channel',choices=('stable','trailblazer-beta'),default='stable')
    x=s.add_parser('publish-local');x.add_argument('generation',type=Path);x.add_argument('origin',type=Path);x.add_argument('--fingerprint',required=True);x.add_argument('--approved-generation',required=True);x.add_argument('--approval',action='append',required=True,type=Path);x.add_argument('--channel',choices=('stable',),default='stable')
    d=s.add_parser('generation-digest');d.add_argument('generation',type=Path);d.add_argument('--channel',choices=('stable','trailblazer-beta'),default='stable')
    a=p.parse_args()
    profile=STABLE if a.channel=='stable' else __import__('channel_profile').BETA
    if a.cmd=='generation-digest':print(generation_digest(a.generation,profile));return
    if a.cmd=='ingest': print(ingest(a.approval,a.manifest,a.apk,a.workspace,a.aapt,a.apksigner,a.receipt_bundle,profile))
    else: print(publish_local(a.generation,a.origin,a.fingerprint,a.approved_generation,a.approval,profile))
if __name__=='__main__': main()
