#!/usr/bin/env python3
"""Build/read back two approved Pages snapshots; never signs or deploys."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import sys
import tarfile
import urllib.request
import binary_repo as b
import site_page

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'/'patches'))
import migration_receipts as receipts
ORIGIN='https://repo.collie.im/'
MAX_BYTES=950_000_000  # conservative decimal1GB minus50MB operational margin
NATIVE_POLICY=Path(__file__).with_name('native-v1.json')
ARCHIVE_FORMAT=tarfile.USTAR_FORMAT
FIRST_PUBLICATION_RECOVERY='owner-authorized-first-publication-unverified-origin'
CACHED_NEGATIVE='cached_negative_unverified_origin'
NEGATIVE_UNVERIFIED='negative_unverified_origin'
EXACT_CONTENT_UNVERIFIED='exact_content_match_unverified_origin'

def native_files():
    return b.load(NATIVE_POLICY)['files']

INDEXES={'entry.jar','entry.json','index-v1.jar','index-v1.json','index-v2.json','index.jar','index.xml'}

def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def _cache_headers(headers):
    return {k:headers.get(k) for k in ('Cache-Control','ETag','Last-Modified','Age','X-Cache')}

def _fresh_headers(values):
    age=values.get('Age')
    if age is not None:
        try:age_value=int(age)
        except (TypeError,ValueError):return False
        if age_value!=0:return False
    return not re.search(r'\bhit\b',values.get('X-Cache') or '',re.IGNORECASE)

def safe_name(name):
    path=PurePosixPath(name)
    if not name or len(name)>512 or len(path.parts)>20 or path.is_absolute() or any(p in ('','.','..') for p in path.parts) or not re.fullmatch(r'[A-Za-z0-9_./-]+',name):raise ValueError('unsafe public path')
    return name

def inventory(root):
    if root.is_symlink():raise ValueError('symlink input root')
    result={}
    for path in sorted(root.rglob('*')):
        if path.is_symlink():raise ValueError('symlink input')
        if not path.is_file():continue
        if path.stat().st_nlink!=1:raise ValueError('hardlinked input')
        name=safe_name(path.relative_to(root).as_posix())
        result[name]={'sha256':b.sha(path),'bytes':path.stat().st_size}
    if len(result)>20000:raise ValueError('too many public files')
    return result

def public_site_name(name):
    if name in native_files() or name in site_page.STATIC:return True
    if name in {'index.html','add-repository.png','add-repository.svg','site-manifest.json','.nojekyll'}:return True
    if name.startswith('fdroid/repo/'):return b.public_path(name[len('fdroid/repo/'):])
    if re.fullmatch(r'_generations/[0-9a-f]{64}/[A-Za-z0-9_.-]+',name):return name.rsplit('/',1)[1] in INDEXES|{'repository-receipt.json'}
    return bool(re.fullmatch(r'assets/[A-Za-z0-9_.-]+\.(png|svg|css|webp)',name))

def immutable(name):
    return name.endswith('.apk') or name.startswith(('native-inputs/','_generations/')) or name.startswith('fdroid/repo/diff/') or (name.startswith('fdroid/repo/icons') and not name.endswith('/icon.png'))

def copy_pinned(source,destination,expected):
    destination.parent.mkdir(parents=True,exist_ok=True)
    b.immutable_copy(source,destination,expected['sha256'])
    if destination.stat().st_size!=expected['bytes']:raise ValueError('size differs')

def archive(site,output,manifest):
    # Match the official upload-pages-artifact action: artifact.tar containing only
    # regular files/directories. Avoid its nested floating upload-artifact ref.
    with tarfile.open(output,'w',format=ARCHIVE_FORMAT) as tar:
        for name,entry in sorted(manifest.items()):
            info=tarfile.TarInfo(name);info.size=entry['bytes'];info.mode=0o644;info.mtime=0
            with open(site/name,'rb') as stream:tar.addfile(info,stream)
    with tarfile.open(output) as tar:
        members=tar.getmembers()
        if {m.name for m in members}!=set(manifest):raise ValueError('artifact membership mismatch')
        for member in members:
            if not member.isfile() or member.issym() or member.islnk():raise ValueError('artifact links/special files')
            expected=manifest[member.name];h=hashlib.sha256()
            with tar.extractfile(member) as stream:
                for block in iter(lambda:stream.read(1024*1024),b''):h.update(block)
            if h.hexdigest()!=expected['sha256']:raise ValueError('artifact changed during archiving')
    return {'sha256':b.sha(output),'bytes':output.stat().st_size}

def prepare(bundle,output,expected_policy):
    a=b.load(bundle/'approval.json')
    native_only=a.get('kind')=='collie-pages-native-input'
    fields={'schema','kind','origin','previous_site_sha256','previous_manifest_sha256','candidate_generation_sha256','repository_certificate_sha256','repository_receipt_sha256','release_approvals','landing_sha256','maximum_bytes','policy_revision'}
    if native_only:fields={'schema','kind','origin','previous_site_sha256','previous_manifest_sha256','native_manifest_sha256','maximum_bytes','policy_revision'}
    elif a.get('schema')==2:fields.add('native_manifest_sha256')
    if set(a)!=fields or type(a['schema']) is not int or a['schema'] not in ((1,) if native_only else (1,2)) or a['kind'] not in ('collie-pages-input','collie-pages-native-input'):raise ValueError('Pages input fields/schema')
    if a['origin']!=ORIGIN or a['policy_revision']!=expected_policy or not re.fullmatch('[0-9a-f]{40}',expected_policy):raise ValueError('origin/policy approval differs')
    if type(a['maximum_bytes']) is not int or not 0<a['maximum_bytes']<=MAX_BYTES:raise ValueError('Pages budget')
    if a['previous_manifest_sha256'] is not None and not re.fullmatch('[0-9a-f]{64}',a['previous_manifest_sha256']):raise ValueError('previous manifest digest')
    keys=('previous_site_sha256',) if native_only else ('previous_site_sha256','candidate_generation_sha256','repository_certificate_sha256','repository_receipt_sha256','landing_sha256')
    for key in keys:
        if not re.fullmatch('[0-9a-f]{64}',a[key]):raise ValueError('Pages digest '+key)
    old=bundle/'previous';candidate=bundle/'candidate';landing=bundle/'landing'
    old_files=inventory(old);landing_files={} if native_only else inventory(landing)
    pinned_native=native_files()
    supplied_native=inventory(bundle/'native')
    native_digest=a.get('native_manifest_sha256')
    if native_only and native_digest is None:raise ValueError('native manifest approval required')
    if native_digest is not None:
        if not isinstance(native_digest,str) or not re.fullmatch('[0-9a-f]{64}',native_digest) or b.sha(bundle/'native-manifest.json')!=native_digest:raise ValueError('native manifest digest differs')
        if b.load(bundle/'native-manifest.json')!=b.load(NATIVE_POLICY):raise ValueError('native manifest differs from fixed six-file policy')
        if supplied_native!=pinned_native:raise ValueError('native inputs differ from approved six files')
    elif supplied_native:raise ValueError('native inputs lack approval')
    retained_native={n:e for n,e in old_files.items() if n.startswith('native-inputs/')}
    if retained_native and (retained_native!=pinned_native or native_digest is None):raise ValueError('published native v1 must remain complete and approved')
    for name in old_files:
        if not public_site_name(name):raise ValueError('unexpected previous public file')
    if old_files:
        prior=b.load(old/'site-manifest.json')
        if set(prior)!={'schema','kind','origin','files'} or type(prior['schema']) is not int or prior['schema']!=1 or prior['kind']!='collie-pages-public-inventory' or prior['origin']!=ORIGIN:raise ValueError('previous manifest format')
        if prior['files']!={k:v for k,v in old_files.items() if k!='site-manifest.json'} or b.sha(old/'site-manifest.json')!=a['previous_manifest_sha256']:raise ValueError('previous manifest does not cover full retained site')
    elif a['previous_manifest_sha256'] is not None:raise ValueError('initial publication requires explicit null previous manifest')
    if digest(old_files)!=a['previous_site_sha256'] or (not native_only and digest(landing_files)!=a['landing_sha256']):raise ValueError('previous site/landing changed')
    additions={name:(bundle/'native'/name,entry) for name,entry in supplied_native.items()}
    receipt_bytes=None;prefix=None
    if not native_only:
        if set(landing_files) not in ({'index.html','add-repository.svg'},{'index.html'}|site_page.STATIC,{'index.html','add-repository.png'}|site_page.STATIC):raise ValueError('unexpected landing files')
        # This checks strings as binding hints, not HTML correctness; operator reviews
        # the exact landing digest. Never execute a candidate build or script.
        text=(landing/'index.html').read_text()
        if 'data-repository-state="preview"' in text:raise ValueError('test preview cannot be a production landing')
        prior_active=(old/'index.html').is_file() and site_page.has_add_link((old/'index.html').read_text(),ORIGIN)
        new_qr_assets={name for name in {'add-repository.png','add-repository.svg'} & set(landing_files) if landing_files[name]!=old_files.get(name)}
        if new_qr_assets and not prior_active:raise ValueError('new repository QR requires completed production activation evidence')
        if site_page.has_add_link(text,ORIGIN) and not (old/'index.html').is_file():raise ValueError('first repository link requires completed production activation evidence')
        if site_page.has_add_link(text,ORIGIN) and not site_page.has_add_link((old/'index.html').read_text(),ORIGIN):raise ValueError('first repository link requires completed production activation evidence')
        if (old/'index.html').is_file() and site_page.has_add_link((old/'index.html').read_text(),ORIGIN) and not site_page.has_add_link(text,ORIGIN):raise ValueError('an active repository landing must remain active during updates')
        if a['repository_certificate_sha256'].upper() not in text or b.APK_CERTIFICATE.upper() not in text:raise ValueError('landing fingerprints differ')
        if not isinstance(a['release_approvals'],list) or not a['release_approvals']:raise ValueError('release approvals missing')
        approvals=[]
        for name in a['release_approvals']:
            if not re.fullmatch(r'[A-Za-z0-9_-]+\.json',name):raise ValueError('release approval filename')
            path=bundle/'releases'/name;approval=b.approval(path)
            b.verify_release_approval(approval,bundle/'bundles'/str(approval['release_id']) if approval['schema']==2 else None)
            approvals.append(path)
        _,data=b.approved_generation(candidate,a['repository_certificate_sha256'],a['candidate_generation_sha256'],approvals)
        if data['repo']['address'].rstrip('/')!=ORIGIN+'fdroid/repo':raise ValueError('signed index origin differs')
        if 'fdroid/repo/entry.jar' in old_files:
            previous_data=b.verify_indexes(old/'fdroid/repo',a['repository_certificate_sha256'])
            if previous_data['repo']['address'].rstrip('/')!=ORIGIN+'fdroid/repo':raise ValueError('previous signed origin differs')
        receipt=receipts.load(bundle/'repository-receipt.json','repository')
        if receipts.digest(receipt)!=a['repository_receipt_sha256']:raise ValueError('repository receipt unapproved')
        payload=receipt['payload']
        if payload['generation']!=a['candidate_generation_sha256'] or payload['repository_certificate_sha256']!=a['repository_certificate_sha256']:raise ValueError('repository receipt identity differs')
        selected=[b.approval(p) for p in approvals]
        if not any(x['apk_sha256']==payload['apk_sha256'] and x['apk_certificate_sha256']==payload['apk_certificate_sha256'] and x['release_manifest_sha256']==payload['release_manifest_sha256'] and x.get('release_receipt_sha256')==payload['release_receipt_sha256'] for x in selected):raise ValueError('repository receipt release differs')
        for entry in payload['indexes']:
            if b.sha(candidate/entry['file'])!=entry['sha256']:raise ValueError('repository receipt index differs')
        candidate_files=inventory(candidate)
        additions.update({('fdroid/repo/'+name):(candidate/name,entry) for name,entry in candidate_files.items()})
    # Retain old immutable URLs. Replacing an APK/native URL is never retention.
    for name,(path,entry) in additions.items():
        if name in old_files and immutable(name) and entry!=old_files[name]:raise ValueError('immutable URL overwrite')
    for name,entry in old_files.items():
        if name.startswith('fdroid/repo/') and immutable(name) and name not in additions:additions[name]=(old/name,entry)
    if not native_only:
        prefix='_generations/'+a['candidate_generation_sha256']+'/'
        for name in INDEXES:
            if name in candidate_files:additions[prefix+name]=(candidate/name,candidate_files[name])
        for name,(path,entry) in additions.items():
            if name in old_files and immutable(name) and entry!=old_files[name]:raise ValueError('immutable generation archive overwrite')
        # Signed index previews are immutable archive evidence, never an active feed.
        receipt_bytes=(bundle/'repository-receipt.json').read_bytes()
        receipt_name=prefix+'repository-receipt.json'
        if receipt_name in old_files and old_files[receipt_name]['sha256']!=hashlib.sha256(receipt_bytes).hexdigest():raise ValueError('immutable receipt archive overwrite')
    if output.exists():raise ValueError('new output directory required; reuse prepared artifacts on retry')
    output.mkdir(parents=True)
    try:
        manifests={};artifacts={}
        for phase in ('phase1','phase2'):
            site=output/phase/'site';site.mkdir(parents=True)
            mapping={name:(old/name,entry) for name,entry in old_files.items() if name!='site-manifest.json'}
            for name,(path,entry) in additions.items():
                if phase=='phase2' or immutable(name):mapping[name]=(path,entry)
            if phase=='phase2' and not native_only:mapping.update({name:(landing/name,entry) for name,entry in landing_files.items()})
            if not old_files and (phase=='phase1' or native_only):
                if native_only:site_page.render(site,state='native')
                else:(site/'index.html').write_text('<!doctype html><html lang="en"><title>Collie repository preparation</title><h1>Repository preparation</h1><p>The feed is not active. Approved assets and signed index previews are being verified.</p></html>')
            for name,(path,entry) in mapping.items():copy_pinned(path,site/name,entry)
            if receipt_bytes is not None:
                archive_dir=site/prefix;archive_dir.mkdir(parents=True,exist_ok=True)
                (archive_dir/'repository-receipt.json').write_bytes(receipt_bytes)
            public_inventory={'schema':1,'kind':'collie-pages-public-inventory','origin':ORIGIN,'files':inventory(site)}
            b.atomic_json(site/'site-manifest.json',public_inventory)
            manifest=inventory(site);size=sum(e['bytes'] for e in manifest.values())
            if size>a['maximum_bytes']:raise ValueError('Pages snapshot exceeds approved budget')
            # Tar overhead counts toward the operational budget, too.
            manifests[phase]=manifest
            artifacts[phase]=archive(site,output/phase/'artifact.tar',manifest)
            if artifacts[phase]['bytes']>a['maximum_bytes']:raise ValueError('Pages archive exceeds approved budget')
        immutable1={n:e for n,e in manifests['phase1'].items() if immutable(n)}
        immutable2={n:e for n,e in manifests['phase2'].items() if immutable(n)}
        if immutable1!=immutable2:raise ValueError('phase immutable asset sets differ')
        for name in old_files:
            if name not in manifests['phase2']:raise ValueError('retained URL removed')
        plan={'schema':1,'kind':'collie-pages-promotion','input_approval_sha256':digest(a),'origin':ORIGIN,'policy_revision':expected_policy,'mode':'native-only' if native_only else 'feed','native_manifest_sha256':native_digest,'repository_receipt_sha256':a.get('repository_receipt_sha256'),'repository_certificate_sha256':a.get('repository_certificate_sha256'),'candidate_generation_sha256':a.get('candidate_generation_sha256'),'maximum_bytes':a['maximum_bytes'],'previous_manifest_sha256':a['previous_manifest_sha256'],'previous_site_sha256':a['previous_site_sha256'],'previous_files':old_files,'manifests':manifests,'artifacts':artifacts}
        b.atomic_json(output/'plan.json',plan)
        b.atomic_json(output/'journal.json',{'schema':1,'plan_sha256':digest(plan),'completed':[],'previous_verified':False,'readbacks':{}})
        return plan
    except Exception:
        shutil.rmtree(output)
        raise

def _fresh_cache_headers(headers):
    values=_cache_headers(headers)
    age=values['Age']
    if age is not None and not re.fullmatch(r'\s*\d+\s*',age):
        raise ValueError('invalid Age header; canonical inventory freshness is unknown')
    if not _fresh_headers(values):raise ValueError('cached response is not a fresh canonical inventory')
    return values

def observe_head(url,*,allow_unverified=False):
    import urllib.error
    request=urllib.request.Request(url,headers={'Cache-Control':'no-cache, no-store','Pragma':'no-cache'},method='HEAD')
    try:
        response=urllib.request.urlopen(request,timeout=60)
    except urllib.error.HTTPError as error:
        if error.code==404:
            try:
                if not error.geturl().startswith('https://'):raise ValueError('HTTPS readback required')
                headers=_cache_headers(error.headers) if allow_unverified else _fresh_cache_headers(error.headers)
            finally:error.close()
            return {'status':404,'headers':headers}
        raise
    with response:
        if not response.geturl().startswith('https://'):raise ValueError('HTTPS readback required')
        if response.status!=200:raise ValueError('canonical inventory HEAD was not 200 or 404')
        headers=_cache_headers(response.headers) if allow_unverified else _fresh_cache_headers(response.headers)
        return {'status':200,'headers':headers}

def observe(url,*,allow_unverified=False,max_bytes=None):
    import urllib.error
    if max_bytes is None:max_bytes=MAX_BYTES
    if type(max_bytes) is not int or not 0<=max_bytes<=MAX_BYTES:raise ValueError('invalid readback byte limit')
    request=urllib.request.Request(url,headers={'Cache-Control':'no-cache, no-store','Pragma':'no-cache'})

    try:
        response=urllib.request.urlopen(request,timeout=60)
    except urllib.error.HTTPError as error:
        if error.code==404:
            try:
                if not error.geturl().startswith('https://'):raise ValueError('HTTPS readback required')
                headers=_cache_headers(error.headers) if allow_unverified else _fresh_cache_headers(error.headers)
            finally:error.close()
            return {'status':404,'headers':headers}
        raise
    with response:
        if not response.geturl().startswith('https://'):raise ValueError('HTTPS readback required')
        if response.status!=200:raise ValueError('readback not200')
        headers=_cache_headers(response.headers) if allow_unverified else _fresh_cache_headers(response.headers)
        h=hashlib.sha256();size=0
        while True:
            block=response.read(min(1024*1024,max_bytes-size+1))
            if not block:break
            size+=len(block)
            if size>max_bytes:raise ValueError('readback exceeds maximum byte budget')
            h.update(block)
        return {'status':200,'sha256':h.hexdigest(),'bytes':size,'headers':headers}

def _authorized_empty_native_plan(plan):
    expected_paths=set(native_files())|set(site_page.STATIC)|{'index.html','site-manifest.json'}
    try:
        if (plan.get('schema')!=1 or plan.get('kind')!='collie-pages-promotion' or
            plan.get('mode')!='native-only' or plan.get('origin')!=ORIGIN or
            plan.get('previous_files')!={} or plan.get('previous_manifest_sha256') is not None or
            plan.get('previous_site_sha256')!=digest({}) or
            plan.get('native_manifest_sha256')!=b.sha(NATIVE_POLICY) or
            type(plan.get('maximum_bytes')) is not int or plan.get('maximum_bytes')!=MAX_BYTES or
            not re.fullmatch(r'[0-9a-f]{64}',plan.get('input_approval_sha256','')) or
            not re.fullmatch(r'[0-9a-f]{40}',plan.get('policy_revision','')) or
            plan.get('repository_receipt_sha256') is not None or
            plan.get('repository_certificate_sha256') is not None or
            plan.get('candidate_generation_sha256') is not None):return False
        phase1=plan['manifests']['phase1'];phase2=plan['manifests']['phase2']
        if set(phase1)!=expected_paths or phase2!=phase1:return False
        if plan['artifacts']['phase1']!=plan['artifacts']['phase2']:return False
        if any(type(e.get('bytes')) is not int or e['bytes']<=0 or
               not re.fullmatch(r'[0-9a-f]{64}',e.get('sha256','')) for e in phase1.values()):return False
        return all(phase1[name]==entry for name,entry in native_files().items())
    except (KeyError,TypeError,AttributeError):
        return False

def _recovery_journal_authorized(plan,journal):
    if (journal.get('previous_verified') is not False or
        journal.get('previous_state_classification')!=FIRST_PUBLICATION_RECOVERY or
        not _authorized_empty_native_plan(plan)):
        return False
    state=journal.get('previous_state')
    if (not isinstance(state,dict) or state.get('plan_sha256')!=digest(plan) or
        state.get('input_approval_sha256')!=plan['input_approval_sha256']):
        return False
    expected={'site-manifest.json','fdroid/repo/entry.jar'}|{
        n for n in plan['manifests']['phase1'] if n.startswith('native-inputs/')}
    observations=state.get('observations')
    if not isinstance(observations,dict) or set(observations)!=expected:return False
    for name,item in observations.items():
        if not isinstance(item,dict):return False
        if item.get('status')==404:
            if item.get('classification') not in (CACHED_NEGATIVE,NEGATIVE_UNVERIFIED):return False
        elif item.get('status')==200 and name in plan['manifests']['phase1']:
            expected_entry=plan['manifests']['phase1'][name]
            if (item.get('classification')!=EXACT_CONTENT_UNVERIFIED or
                item.get('sha256')!=expected_entry['sha256'] or item.get('bytes')!=expected_entry['bytes']):return False
        else:return False
    return True

def verify_terminal_operation_inventory(runs,deployments,expected_run_ids,expected_deployment_ids):
    if not all(isinstance(value,list) for value in (runs,deployments)):
        raise ValueError('operation inventory must be lists')
    if not expected_run_ids or not expected_deployment_ids:
        raise ValueError('known operation IDs are required')
    if any(type(value) is not int or value<=0 for value in (*expected_run_ids,*expected_deployment_ids)):
        raise ValueError('known operation IDs are invalid')
    if (len(set(expected_run_ids))!=len(expected_run_ids) or
        len(set(expected_deployment_ids))!=len(expected_deployment_ids)):
        raise ValueError('known operation IDs are duplicated')
    run_ids=[run.get('id') for run in runs if isinstance(run,dict)]
    deployment_ids=[item.get('id') for item in deployments if isinstance(item,dict)]
    if (len(run_ids)!=len(runs) or len(deployment_ids)!=len(deployments) or
        any(type(value) is not int or value<=0 for value in (*run_ids,*deployment_ids)) or
        len(set(run_ids))!=len(run_ids) or len(set(deployment_ids))!=len(deployment_ids) or
        set(run_ids)!=set(expected_run_ids) or set(deployment_ids)!=set(expected_deployment_ids)):
        raise ValueError('unexpected or missing workflow run/deployment')
    if any(run.get('status')!='completed' or run.get('conclusion')!='failure' for run in runs):
        raise ValueError('workflow run is active or not a known terminal failure')
    if any(item.get('state') not in ('failure','success') for item in deployments):
        raise ValueError('deployment is active or not a known terminal state')
    return True

def preflight(plan,journal_path,*,fetch=observe,head=None):
    if plan['origin']!=ORIGIN:raise ValueError('unapproved origin')
    if head is None:head=observe_head if fetch is observe else fetch
    recovery=_authorized_empty_native_plan(plan)
    with b.locked(journal_path.parent):
        journal=b.load(journal_path)
        if journal['plan_sha256']!=digest(plan):raise ValueError('journal belongs to another plan')
        if (journal.get('previous_verified') is not False or journal.get('completed')!=[] or
            journal.get('readbacks')!={} or 'previous_readback' in journal or
            'previous_state' in journal or 'previous_state_classification' in journal):
            raise ValueError('fresh preflight journal required')
        observations={}
        def inspect_head(url):
            actual=(head(url,allow_unverified=True) if recovery and head is observe_head else head(url))
            if actual.get('status') not in (200,404):raise ValueError('canonical inventory HEAD was not 200 or 404')
            headers=actual.get('headers',{})
            if not recovery and not _fresh_headers(headers):
                raise ValueError('cached response is not a fresh canonical inventory')
            return actual
        def exact_get(url,name,expected):
            actual=(fetch(url,allow_unverified=recovery,max_bytes=expected['bytes'])
                    if fetch is observe else fetch(url))
            if (actual.get('status',200)!=200 or actual.get('sha256')!=expected['sha256'] or
                actual.get('bytes')!=expected['bytes']):
                message='native URL differs before initial publication' if name.startswith('native-inputs/') else 'existing URL differs before initial publication'
                raise ValueError(message+': '+name)
            actual=dict(actual);actual['classification']=EXACT_CONTENT_UNVERIFIED
            return actual
        def record_negative(name,actual):
            if recovery:
                item=dict(actual);item['classification']=CACHED_NEGATIVE if not _fresh_headers(actual.get('headers',{})) else NEGATIVE_UNVERIFIED
                observations[name]=item
            else:observations[name]=actual
        if not plan['previous_files']:
            for name in ('site-manifest.json','fdroid/repo/entry.jar'):
                actual=inspect_head(plan['origin']+name)
                if actual.get('status')==404:
                    record_negative(name,actual);continue
                if not recovery or name not in plan['manifests']['phase1']:
                    raise ValueError('initial origin is not the explicitly approved empty feed')
                observations[name]=exact_get(plan['origin']+name,name,plan['manifests']['phase1'][name])
        else:
            for name,expected in plan['previous_files'].items():
                safe_name(name)
                actual=(fetch(plan['origin']+name,max_bytes=expected['bytes'])
                        if fetch is observe else fetch(plan['origin']+name))
                if actual.get('status',200)!=200 or actual.get('sha256')!=expected['sha256'] or actual.get('bytes')!=expected['bytes']:raise ValueError('live previous site differs from approved snapshot')
                observations[name]=actual
        # A retained feed can predate native hosting. Also inspect every NEW
        # native URL: absence from its inventory does not prove the URL is empty.
        for name,expected in plan['manifests']['phase1'].items():
            if not name.startswith('native-inputs/') or name in plan['previous_files']:continue
            actual=inspect_head(plan['origin']+name)
            if actual.get('status')==404:record_negative(name,actual)
            else:observations[name]=exact_get(plan['origin']+name,name,expected)
        if recovery:
            journal['previous_verified']=False
            journal['previous_state_classification']=FIRST_PUBLICATION_RECOVERY
            journal['previous_state']={'plan_sha256':digest(plan),'input_approval_sha256':plan['input_approval_sha256'],'observations':observations}
        else:
            journal['previous_verified']=True;journal['previous_readback']=observations
        b.atomic_json(journal_path,journal)
        return journal

def readback(plan,journal_path,phase,*,fetch=None):
    if phase not in ('phase1','phase2'):raise ValueError('phase')
    if plan['origin']!=ORIGIN:raise ValueError('unapproved origin')
    if fetch is None:fetch=observe
    with b.locked(journal_path.parent):
        journal=b.load(journal_path)
        if journal['plan_sha256']!=digest(plan):raise ValueError('journal belongs to another plan')
        recovery=not journal.get('previous_verified') and _recovery_journal_authorized(plan,journal)
        if not journal.get('previous_verified') and not recovery:
            raise ValueError('live previous preflight or owner-authorized first-publication recovery required')
        if phase=='phase2' and 'phase1' not in journal['completed']:raise ValueError('phase1 readback required before phase2')
        observed={}
        for name,expected in plan['manifests'][phase].items():
            safe_name(name)
            # In the exact empty-native recovery, cache metadata is not origin
            # proof. Still GET every URL and require the exact planned bytes/hash.
            actual=(fetch(plan['origin']+name,allow_unverified=recovery,max_bytes=expected['bytes'])
                    if fetch is observe else fetch(plan['origin']+name))
            if actual.get('status',200)!=200 or actual.get('sha256')!=expected['sha256'] or actual.get('bytes')!=expected['bytes']:raise ValueError('readback mismatch: '+name)
            observed[name]=actual
        journal['readbacks'][phase]=observed
        if phase not in journal['completed']:journal['completed'].append(phase)
        b.atomic_json(journal_path,journal)
        return journal

def verify_completed_readbacks(plan,journal):
    if journal.get('plan_sha256')!=digest(plan):raise ValueError('journal belongs to another plan')
    if not journal.get('previous_verified') and not _recovery_journal_authorized(plan,journal):
        raise ValueError('live previous preflight or owner-authorized first-publication recovery required')
    if journal.get('completed')!=['phase1','phase2'] or set(journal.get('readbacks',{}))!={'phase1','phase2'}:
        raise ValueError('both canonical readbacks are required')
    for phase in ('phase1','phase2'):
        observed=journal['readbacks'][phase];expected=plan['manifests'][phase]
        if set(observed)!=set(expected):raise ValueError('canonical readback inventory differs')
        for name,entry in expected.items():
            item=observed[name]
            if (item.get('status')!=200 or item.get('sha256')!=entry['sha256'] or
                item.get('bytes')!=entry['bytes']):raise ValueError('canonical readback differs: '+name)
    return True

def unpack(archive_path,destination):
    if destination.exists():raise ValueError('new bundle directory required')
    with tarfile.open(archive_path,'r:*') as tar:
        members=[];total=0;names=set()
        for member in tar:
            safe_name(member.name)
            if member.name in names or not(member.isfile() or member.isdir()):raise ValueError('bundle duplicate/link/special member')
            names.add(member.name);members.append(member);total+=member.size
            if total>2*MAX_BYTES or len(names)>40000:raise ValueError('input bundle budget')
        destination.mkdir(parents=True)
        for member in members:
            path=destination/member.name
            if member.isdir():path.mkdir(parents=True,exist_ok=True)
            else:
                path.parent.mkdir(parents=True,exist_ok=True)
                with tar.extractfile(member) as source,open(path,'xb') as target:shutil.copyfileobj(source,target)

def main():
    p=argparse.ArgumentParser();s=p.add_subparsers(dest='cmd',required=True)
    a=s.add_parser('prepare');a.add_argument('bundle',type=Path);a.add_argument('output',type=Path);a.add_argument('--policy',required=True)
    a=s.add_parser('preflight');a.add_argument('plan',type=Path);a.add_argument('journal',type=Path)
    a=s.add_parser('readback');a.add_argument('plan',type=Path);a.add_argument('journal',type=Path);a.add_argument('--phase',choices=['phase1','phase2'],required=True)
    a=s.add_parser('unpack');a.add_argument('archive',type=Path);a.add_argument('destination',type=Path)
    args=p.parse_args()
    if args.cmd=='prepare':print(json.dumps(prepare(args.bundle,args.output,args.policy)['artifacts'],indent=2))
    elif args.cmd=='preflight':print(json.dumps(preflight(b.load(args.plan),args.journal),indent=2))
    elif args.cmd=='readback':print(json.dumps(readback(b.load(args.plan),args.journal,args.phase),indent=2))
    else:unpack(args.archive,args.destination)
if __name__=='__main__':main()
