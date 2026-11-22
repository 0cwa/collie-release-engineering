#!/usr/bin/env python3
"""Build/read back two approved Pages snapshots; never signs or deploys."""
import argparse
import hashlib
import io
import os
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import sys
import subprocess
import tarfile
import tempfile
import time
import zipfile
import urllib.request
from functools import partial
from urllib.parse import urlsplit
import binary_repo as b
import feed as feed_builder
import site_page
import retained_artifacts
import native_generations
try:
    from . import channel_profile as channels
except ImportError:
    import channel_profile as channels

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'/'release'))
import migration_receipts as receipts
ORIGIN='https://repo.collie.im/'
MAX_BYTES=950_000_000  # conservative decimal1GB minus50MB operational margin
NATIVE_POLICY=Path(__file__).with_name('native-v1.json')
NATIVE_GENERATIONS=Path(__file__).with_name('native-generations.json')
ARCHIVE_FORMAT=tarfile.USTAR_FORMAT
FIRST_PUBLICATION_RECOVERY='owner-authorized-first-publication-unverified-origin'
CACHED_NEGATIVE='cached_negative_unverified_origin'
NEGATIVE_UNVERIFIED='negative_unverified_origin'
EXACT_CONTENT_UNVERIFIED='exact_content_match_unverified_origin'

def native_files():
    return b.load(NATIVE_POLICY)['files']

INDEXES=retained_artifacts.ARCHIVE_INDEX_FILES

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
    parts=name.split('/')
    if not name or len(name)>512 or len(parts)>20 or path.is_absolute() or any(p in ('','.','..') for p in parts) or not re.fullmatch(r'[A-Za-z0-9_./=-]+',name):raise ValueError('unsafe public path')
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

def public_site_name(name, profile=channels.STABLE):
    if retained_artifacts.versioned_native_path(name) or name in site_page.STATIC:return True
    if name in {'index.html','add-repository.png','add-repository.svg','site-manifest.json','.nojekyll','CNAME','feed-state.json'}:return True
    for candidate,prefix in ((channels.STABLE,'fdroid/repo/'),(channels.BETA,'fdroid/beta/repo/')):
        if name.startswith(prefix):return b.public_path(name[len(prefix):],candidate)
    if retained_artifacts.generation_archive_path(name):return True
    return bool(re.fullmatch(r'assets/[A-Za-z0-9_.-]+\.(png|svg|css|webp)',name))

def github_read(endpoint):
    result=subprocess.run(['gh','api','--hostname','github.com','--method','GET',endpoint],
                          check=True,capture_output=True,text=True,timeout=60)
    if len(result.stdout)>1024*1024:raise ValueError('GitHub provenance response exceeds limit')
    return json.loads(result.stdout)

APP_REPOSITORY = '0cwa/collieim-android'


class ReadinessRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, archive):
        self.archive = archive

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urlsplit(newurl)
        if (not self.archive or target.scheme != 'https' or not target.hostname
                or target.username is not None or target.password is not None):
            return None
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def read_app_actions(endpoint, token, limit, *, archive=False):
    if not token or not token.strip():
        raise ValueError('COLLIE_APP_READINESS_TOKEN is required for Beta readiness')
    prefix = f'repos/{APP_REPOSITORY}/actions/'
    allowed_path = (r'artifacts/[1-9][0-9]*/zip' if archive else
                    r'(?:runs/[1-9][0-9]*/attempts/[1-9][0-9]*|artifacts/[1-9][0-9]*)')
    if not endpoint.startswith(prefix) or not re.fullmatch(allowed_path, endpoint[len(prefix):]):
        raise ValueError('unexpected Android readiness API endpoint')
    request = urllib.request.Request(
        'https://api.github.com/' + endpoint,
        headers={'Accept': 'application/vnd.github+json', 'User-Agent': 'Collie-Pages-preparer'},
    )
    # urllib does not copy unredirected headers to a new request.
    request.add_unredirected_header('Authorization', 'Bearer ' + token)
    opener = urllib.request.build_opener(ReadinessRedirect(archive))
    try:
        with opener.open(request, timeout=60) as response:
            contents = response.read(limit + 1)
    except urllib.error.HTTPError as exc:
        exc.close()
        raise ValueError(f'Android readiness request returned HTTP {exc.code}') from None
    except (OSError, ValueError):
        raise ValueError('Android readiness request failed') from None
    if not contents or len(contents) > limit:
        raise ValueError('Android readiness response is empty or exceeds its byte limit')
    return contents


def github_app_read(endpoint, *, token):
    return json.loads(read_app_actions(endpoint, token, 1024 * 1024))


def github_artifact_bytes(artifact_id, *, token):
    return read_app_actions(
        f'repos/{APP_REPOSITORY}/actions/artifacts/{artifact_id}/zip',
        token, MAX_READINESS_ARTIFACT_BYTES, archive=True,
    )


READINESS_MANIFEST='trailblazer-beta-readiness.json'
MAX_READINESS_ARTIFACT_BYTES=8*1024*1024
READINESS_FIELDS={'schema','kind','repository','branch','workflow_path','run_id','run_attempt','head_sha',
    'package','version_name','version_code','tag','title','apk_asset','source_receipt_sha256',
    'build_receipt_sha256','release_receipt_sha256','repository_receipt_sha256',
    'release_manifest_sha256','apk_sha256','apk_certificate_sha256'}

def parse_readiness_archive(archive,approval,artifact_digest):
    actual=hashlib.sha256(archive).hexdigest()
    if artifact_digest not in (actual,'sha256:'+actual) or actual!=approval['readiness_artifact_sha256']:
        raise ValueError('downloaded Beta readiness artifact digest differs')
    try:
        with zipfile.ZipFile(io.BytesIO(archive)) as zf:
            infos=zf.infolist()
            if len(infos)!=1 or infos[0].filename!=READINESS_MANIFEST or infos[0].file_size>64*1024:
                raise ValueError('Beta readiness artifact must contain only the fixed manifest')
            mode=(infos[0].external_attr >> 16) & 0o170000
            if mode not in (0,0o100000):raise ValueError('Beta readiness manifest must be a regular file')
            raw=zf.read(infos[0])
    except (zipfile.BadZipFile,RuntimeError,OSError) as exc:
        raise ValueError('invalid Beta readiness artifact archive') from exc
    def unique(pairs):
        values={}
        for key,value in pairs:
            if key in values:raise ValueError('duplicate readiness manifest field')
            values[key]=value
        return values
    try: manifest=json.loads(raw.decode('utf-8'),object_pairs_hook=unique)
    except (json.JSONDecodeError,UnicodeDecodeError) as exc:raise ValueError('invalid Beta readiness manifest JSON') from exc
    if not isinstance(manifest,dict) or set(manifest)!=READINESS_FIELDS:raise ValueError('Beta readiness manifest fields/schema')
    if (type(manifest['schema']) is not int or manifest['schema']!=1 or manifest['kind']!='collie-trailblazer-readiness' or
        manifest['repository']!='0cwa/collieim-android' or manifest['branch']!=channels.BETA.source_branch or
        manifest['workflow_path']!=channels.BETA.readiness_workflow or
        type(manifest['run_id']) is not int or type(manifest['run_attempt']) is not int or
        manifest['run_id']!=approval['readiness_run_id'] or manifest['run_attempt']!=approval['readiness_attempt'] or
        not re.fullmatch('[0-9a-f]{40}',manifest['head_sha']) or manifest['head_sha']!=approval['source_commit']):
        raise ValueError('Beta readiness manifest provenance differs')
    for key in ('source_receipt_sha256','build_receipt_sha256','release_receipt_sha256','repository_receipt_sha256',
                'release_manifest_sha256','apk_sha256','apk_certificate_sha256'):
        if not isinstance(manifest[key],str) or not re.fullmatch('[0-9a-f]{64}',manifest[key]):
            raise ValueError('invalid readiness digest '+key)
    if type(manifest['version_code']) is not int or manifest['version_code']<=0:
        raise ValueError('invalid readiness version code')
    for key in ('package','version_name','tag','title','apk_asset'):
        if not isinstance(manifest[key],str) or not manifest[key] or len(manifest[key])>256:
            raise ValueError('invalid readiness identity '+key)
    return manifest

def validate_beta_provenance(approval,profile=channels.BETA,api=None,download=None,*,readiness_token=None):
    if api is None:api=partial(github_app_read,token=readiness_token)
    if (approval.get('source_branch')!=profile.source_branch or approval.get('readiness_workflow')!=profile.readiness_workflow or
        type(approval.get('readiness_run_id')) is not int or approval['readiness_run_id']<=0 or
        type(approval.get('readiness_attempt')) is not int or approval['readiness_attempt']<=0 or
        type(approval.get('readiness_artifact_id')) is not int or approval['readiness_artifact_id']<=0 or
        not re.fullmatch('[0-9a-f]{64}',approval.get('readiness_artifact_sha256','')) or
        not re.fullmatch('[0-9a-f]{40}',approval.get('source_commit','')) or
        approval.get('readiness_commit')!=approval.get('source_commit')):
        raise ValueError('exact allowlisted Beta source and successful readiness provenance required')
    repository=APP_REPOSITORY;run_id=approval['readiness_run_id'];attempt=approval['readiness_attempt']
    run=api(f'repos/{repository}/actions/runs/{run_id}/attempts/{attempt}')
    if (run.get('id')!=run_id or run.get('run_attempt')!=attempt or run.get('head_repository',{}).get('full_name')!=repository or
        run.get('head_branch')!=profile.source_branch or run.get('head_sha')!=approval['source_commit'] or
        run.get('path')!=profile.readiness_workflow or run.get('status')!='completed' or run.get('conclusion')!='success' or
        run.get('event') not in ('push','workflow_dispatch')):
        raise ValueError('authoritative GitHub Beta readiness run differs')
    artifact=api(f"repos/{repository}/actions/artifacts/{approval['readiness_artifact_id']}")
    if (artifact.get('id')!=approval['readiness_artifact_id'] or
        artifact.get('name')!=f'trailblazer-beta-readiness-{run_id}-{attempt}' or
        artifact.get('expired') is not False or artifact.get('workflow_run',{}).get('id')!=run_id or
        type(artifact.get('size_in_bytes')) is not int or not 0<artifact['size_in_bytes']<=MAX_READINESS_ARTIFACT_BYTES or
        artifact.get('digest') not in (approval['readiness_artifact_sha256'],'sha256:'+approval['readiness_artifact_sha256'])):
        raise ValueError('authoritative GitHub Beta readiness artifact differs')
    if download is None:download=partial(github_artifact_bytes,token=readiness_token)
    contents=download(approval['readiness_artifact_id'])
    if len(contents)!=artifact['size_in_bytes']:raise ValueError('downloaded Beta readiness artifact size differs')
    return parse_readiness_archive(contents,approval,artifact['digest'])

def immutable(name):
    return retained_artifacts.immutable_public_path(name)

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

def prepare(bundle,output,expected_policy,expected_channel=None,profile_override=None,*,readiness_token=None):
    a=b.load(bundle/'approval.json')
    channel=a.get('channel','stable')
    if expected_channel is not None and channel!=expected_channel:raise ValueError('approval channel differs from selected channel')
    if channel not in ('stable','trailblazer-beta'):raise ValueError('unknown distribution channel')
    profile=profile_override or (channels.STABLE if channel=='stable' else channels.BETA)
    if profile.name!=channel:raise ValueError('selected channel profile differs from approval')
    if profile.name=='trailblazer-beta':
        channels.require_trusted(profile)
        channels.require_apk_trusted(profile)
    native_only=a.get('kind')=='collie-pages-native-input'
    fields={'schema','kind','origin','previous_site_sha256','previous_manifest_sha256','candidate_generation_sha256','repository_certificate_sha256','repository_receipt_sha256','release_approvals','landing_sha256','maximum_bytes','policy_revision'}
    if channel=='trailblazer-beta':
        fields.update({'channel','source_branch','source_commit','readiness_workflow','readiness_run_id','readiness_attempt','readiness_artifact_id','readiness_artifact_sha256','readiness_commit'})
    if native_only:fields={'schema','kind','origin','previous_site_sha256','previous_manifest_sha256','native_manifest_sha256','maximum_bytes','policy_revision'}
    elif a.get('schema')==2 and profile.name=='stable':fields.add('native_manifest_sha256')
    elif a.get('schema')==2 and 'native_manifest_sha256' in a:fields.add('native_manifest_sha256')
    if native_only and 'previous_deployment_guard_id' in a:
        fields.add('previous_deployment_guard_id')
    allowed_schema=(1,) if native_only else ((1,2) if profile.name=='stable' else (2,))
    if set(a)!=fields or type(a['schema']) is not int or a['schema'] not in allowed_schema or a['kind'] not in ('collie-pages-input','collie-pages-native-input'):raise ValueError('Pages input fields/schema')
    if a['origin']!=ORIGIN or a['policy_revision']!=expected_policy or not re.fullmatch('[0-9a-f]{40}',expected_policy):raise ValueError('origin/policy approval differs')
    if profile.name=='trailblazer-beta' and (profile.source_branch!='trailblazer-beta' or not profile.readiness_workflow):raise ValueError('Beta source readiness profile is not configured')
    readiness=None
    if profile.name=='trailblazer-beta':
        readiness=validate_beta_provenance(a,profile,readiness_token=readiness_token)
    if type(a['maximum_bytes']) is not int or not 0<a['maximum_bytes']<=MAX_BYTES:raise ValueError('Pages budget')
    if a['previous_manifest_sha256'] is not None and not re.fullmatch('[0-9a-f]{64}',a['previous_manifest_sha256']):raise ValueError('previous manifest digest')
    keys=('previous_site_sha256',) if native_only else ('previous_site_sha256','candidate_generation_sha256','repository_certificate_sha256','repository_receipt_sha256','landing_sha256')
    for key in keys:
        if not re.fullmatch('[0-9a-f]{64}',a[key]):raise ValueError('Pages digest '+key)
    old=bundle/'previous';candidate=bundle/'candidate';landing=bundle/'landing'
    old_files=inventory(old);landing_files={} if native_only else inventory(landing)
    if profile.name=='trailblazer-beta' and any(name.startswith('fdroid/beta/repo/') for name in old_files) and 'fdroid/beta/repo/entry.jar' not in old_files:
        raise ValueError('previous Beta feed lacks its signed entry index')
    pinned_native=native_files()
    supplied_native=inventory(bundle/'native')
    native_digest=a.get('native_manifest_sha256')
    if native_only and native_digest is None:raise ValueError('native manifest approval required')
    if native_digest is not None:
        if not isinstance(native_digest,str) or not re.fullmatch('[0-9a-f]{64}',native_digest) or b.sha(bundle/'native-manifest.json')!=native_digest:raise ValueError('native manifest digest differs')
        approved_native=native_generations.approved_files(b.load(bundle/'native-manifest.json'),NATIVE_POLICY,NATIVE_GENERATIONS)
        if supplied_native!=approved_native:
            raise ValueError('native inputs differ from approved six files' if approved_native==pinned_native else
                             'native inputs differ from the exact approved file set')
    elif supplied_native:raise ValueError('native inputs lack approval')
    retained_native={n:e for n,e in old_files.items() if n.startswith('native-inputs/')}
    # Only already published, externally approved native URLs may extend this
    # set. New native input must match a separately registered immutable policy;
    # every such policy retains the fixed native-v1 set without changes.
    if retained_native and (any(retained_native.get(name)!=entry for name,entry in pinned_native.items()) or
            any(not retained_artifacts.versioned_native_path(name) for name in retained_native) or
            (profile.name=='stable' and native_digest is None)):
        raise ValueError('published native v1 must remain complete and approved')
    for name in old_files:
        if not public_site_name(name,profile):raise ValueError('unexpected previous public file')
    if 'CNAME' in old_files:
        hostname=b'repo.collie.im'
        if old_files['CNAME']['bytes'] not in (len(hostname),len(hostname)+1) or (old/'CNAME').read_bytes() not in (hostname,hostname+b'\n'):
            raise ValueError('previous CNAME must name exactly repo.collie.im')
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
        if a['repository_certificate_sha256'].upper() not in text or profile.apk_certificate.upper() not in text:raise ValueError('landing fingerprints differ')
        if not isinstance(a['release_approvals'],list) or not a['release_approvals']:raise ValueError('release approvals missing')
        approvals=[];approved_records=[]
        for name in a['release_approvals']:
            if not re.fullmatch(r'[A-Za-z0-9_-]+\.json',name):raise ValueError('release approval filename')
            path=bundle/'releases'/name;approval=b.approval(path,profile)
            if profile.name=='trailblazer-beta' and approval['schema']!=2:raise ValueError('Beta publication requires schema2 receipt graph')
            release=b.verify_release_approval(approval,bundle/'bundles'/str(approval['release_id']) if approval['schema']==2 else None,profile)
            approvals.append(path);approved_records.append((approval,release))
        if profile.name=='trailblazer-beta':
            if not approved_records or any(a['tag']!=profile.tag_prefix+a['version_name'] or
                a['version_code']<=0 or a['package']!=profile.package for a,_ in approved_records):
                raise ValueError('Beta release approval channel identity differs')
            if readiness is not None and len(approved_records)==1:
                feed_builder.validate_release_identity(profile,approved_records[0][0]['tag'],readiness['title'],
                    approved_records[0][0]['version_name'],approved_records[0][0]['version_code'])
            if any(release is not None and release['payload']['source_commit']!=a['source_commit']
                   for approval,release in approved_records):
                raise ValueError('Beta readiness source commit differs from release receipt')
        _,data=b.approved_generation(candidate,a['repository_certificate_sha256'],a['candidate_generation_sha256'],approvals,profile)
        repo_path='fdroid/repo' if profile.name=='stable' else 'fdroid/beta/repo'
        expected_address=profile.repository_url.rstrip('/')
        if data['repo']['address'].rstrip('/')!=expected_address:raise ValueError('signed index origin differs')
        previous_data=None
        if repo_path+'/entry.jar' in old_files:
            previous_data=b.verify_indexes(old/repo_path,a['repository_certificate_sha256'],profile)
            if previous_data['repo']['address'].rstrip('/')!=expected_address:raise ValueError('previous signed origin differs')
        receipt=receipts.load(bundle/'repository-receipt.json','repository',profile=profile.name)
        if receipts.digest(receipt,profile=profile.name)!=a['repository_receipt_sha256']:raise ValueError('repository receipt unapproved')
        payload=receipt['payload']
        if payload['generation']!=a['candidate_generation_sha256'] or payload['repository_certificate_sha256']!=a['repository_certificate_sha256']:raise ValueError('repository receipt identity differs')
        selected=[b.approval(p,profile) for p in approvals]
        if not any(x['apk_sha256']==payload['apk_sha256'] and x['apk_certificate_sha256']==payload['apk_certificate_sha256'] and x['release_manifest_sha256']==payload['release_manifest_sha256'] and x.get('release_receipt_sha256')==payload['release_receipt_sha256'] for x in selected):raise ValueError('repository receipt release differs')
        if profile.name=='trailblazer-beta':
            if len(selected)!=1 or readiness is None:raise ValueError('Beta readiness must bind exactly one schema2 release')
            release_bundle=bundle/'bundles'/str(selected[0]['release_id'])
            graph={kind:receipts.load(release_bundle/(kind+'.json'),kind,profile=profile.name)
                   for kind in ('source','build','release')}
            repository_digest=a['repository_receipt_sha256']
            receipts.verify_graph(**graph,repository=receipt,
                approved={**selected[0]['receipt_approvals'],'repository':repository_digest},
                promotion=True,profile=profile.name,repository_stage='prepublication')
            source_payload=graph['source']['payload'];release_payload=graph['release']['payload']
            expected_readiness={
                'package':profile.package,'version_name':release_payload['version_name'],
                'version_code':release_payload['version_code'],
                'tag':profile.tag_prefix+release_payload['version_name'],
                'title':profile.app_title+' '+release_payload['version_name'],
                'apk_asset':profile.apk_asset,
                'source_receipt_sha256':selected[0]['receipt_approvals']['source'],
                'build_receipt_sha256':selected[0]['receipt_approvals']['build'],
                'release_receipt_sha256':selected[0]['receipt_approvals']['release'],
                'repository_receipt_sha256':repository_digest,
                'release_manifest_sha256':release_payload['release_manifest_sha256'],
                'apk_sha256':release_payload['apk_sha256'],
                'apk_certificate_sha256':release_payload['apk_certificate_sha256']}
            if any(readiness.get(k)!=v for k,v in expected_readiness.items()):
                raise ValueError('Beta readiness manifest release/receipt identity differs')
            if source_payload['published_commit']!=a['source_commit'] or release_payload['source_commit']!=a['source_commit']:
                raise ValueError('Beta source commit differs from full receipt graph')
            apk_path=candidate/f"{profile.package}_{release_payload['version_code']}.apk"
            if not apk_path.is_file() or b.sha(apk_path)!=readiness['apk_sha256']:
                raise ValueError('Beta readiness APK bytes differ from candidate bundle')
            if previous_data is not None:
                previous_codes=[item[0] for item in b.release_tuples(previous_data,profile)]
                candidate_codes=[item[0] for item in b.release_tuples(data,profile)]
                if not candidate_codes or (previous_codes and min(candidate_codes)<=max(previous_codes)):
                    raise ValueError('Beta versionCode must exceed verified previous signed index')
        for entry in payload['indexes']:
            if b.sha(candidate/entry['file'])!=entry['sha256']:raise ValueError('repository receipt index differs')
        candidate_files=inventory(candidate)
        additions.update({(repo_path+'/'+name):(candidate/name,entry) for name,entry in candidate_files.items()})
    # Retain old immutable URLs. Replacing an APK/native URL is never retention.
    active_route='fdroid/repo/' if profile.name=='stable' else 'fdroid/beta/repo/'
    other_route='fdroid/beta/repo/' if profile.name=='stable' else 'fdroid/repo/'
    for name,(path,entry) in additions.items():
        if name in old_files and immutable(name) and entry!=old_files[name]:raise ValueError('immutable URL overwrite')
    for name,entry in old_files.items():
        if ((name.startswith(other_route) or (name.startswith(active_route) and immutable(name))) and name not in additions):
            additions[name]=(old/name,entry)
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
                if phase=='phase2' or immutable(name) or name.startswith(other_route):mapping[name]=(path,entry)
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
        plan={'schema':1,'kind':'collie-pages-promotion','channel':channel,'input_approval_sha256':digest(a),'origin':ORIGIN,'policy_revision':expected_policy,'mode':'native-only' if native_only else 'feed','native_manifest_sha256':native_digest,'repository_receipt_sha256':a.get('repository_receipt_sha256'),'repository_certificate_sha256':a.get('repository_certificate_sha256'),'candidate_generation_sha256':a.get('candidate_generation_sha256'),'maximum_bytes':a['maximum_bytes'],'previous_manifest_sha256':a['previous_manifest_sha256'],'previous_site_sha256':a['previous_site_sha256'],'previous_files':old_files,'manifests':manifests,'artifacts':artifacts}
        if 'previous_deployment_guard_id' in a:
            plan['previous_deployment_guard_id'] = a['previous_deployment_guard_id']
            native_deployment_guard(plan)
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

def _verify_observation_url(actual, expected, require_canonical):
    if not actual.startswith('https://'):
        raise ValueError('HTTPS readback required')
    if require_canonical and actual != expected:
        raise ValueError('canonical readback URL differs')


def observe_head(url,*,allow_unverified=False,require_canonical=False):
    import urllib.error
    request=urllib.request.Request(url,headers={'Cache-Control':'no-cache, no-store','Pragma':'no-cache'},method='HEAD')
    try:
        response=urllib.request.urlopen(request,timeout=60)
    except urllib.error.HTTPError as error:
        if error.code==404:
            try:
                _verify_observation_url(error.geturl(),url,require_canonical)
                headers=_cache_headers(error.headers) if allow_unverified else _fresh_cache_headers(error.headers)
            finally:error.close()
            return {'status':404,'headers':headers}
        raise
    with response:
        _verify_observation_url(response.geturl(),url,require_canonical)
        if response.status!=200:raise ValueError('canonical inventory HEAD was not 200 or 404')
        headers=_cache_headers(response.headers) if allow_unverified else _fresh_cache_headers(response.headers)
        return {'status':200,'headers':headers}

def observe(url,*,allow_unverified=False,max_bytes=None,require_canonical=False):
    import urllib.error
    if max_bytes is None:max_bytes=MAX_BYTES
    if type(max_bytes) is not int or not 0<=max_bytes<=MAX_BYTES:raise ValueError('invalid readback byte limit')
    request=urllib.request.Request(url,headers={'Cache-Control':'no-cache, no-store','Pragma':'no-cache'})

    # Guarded additive publication checks may retry an unavailable CDN, but
    # still need a real canonical response. Other publication modes fail fast.
    delays = (5, 15, 35, None) if require_canonical and allow_unverified else (None,)
    for delay in delays:
        try:
            response = urllib.request.urlopen(request, timeout=60)
            break
        except urllib.error.HTTPError as error:
            if error.code == 503 and delay is not None:
                with error:
                    _verify_observation_url(error.geturl(), url, require_canonical)
                print(f'Pages GET unavailable for {url}; retrying in {delay}s', file=sys.stderr)
                time.sleep(delay)
                continue
            if error.code == 404:
                with error:
                    _verify_observation_url(error.geturl(), url, require_canonical)
                    headers = _cache_headers(error.headers) if allow_unverified else _fresh_cache_headers(error.headers)
                return {'status':404,'headers':headers}
            error.add_note(f'Pages GET failed for {url}')
            raise
    with response:
        _verify_observation_url(response.geturl(),url,require_canonical)
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

def native_deployment_guard(plan):
    """Return the explicitly approved predecessor for an additive native-only update."""
    key = 'previous_deployment_guard_id'
    if key not in plan:
        return None
    guard = plan[key]
    previous = plan['previous_files']
    candidate = plan['manifests']['phase1']
    inventory = 'site-manifest.json'
    if (plan['mode'] != 'native-only' or plan['channel'] != 'stable' or
            type(guard) is not int or guard <= 0 or not plan['previous_manifest_sha256'] or
            previous.get(inventory, {}).get('sha256') != plan['previous_manifest_sha256'] or
            candidate != plan['manifests']['phase2'] or inventory not in candidate or
            any(candidate.get(name) != entry for name, entry in previous.items() if name != inventory)):
        raise ValueError('cached readback requires an approved additive native deployment')
    added = candidate.keys() - previous.keys()
    if not added or any(not retained_artifacts.versioned_native_path(name) for name in added):
        raise ValueError('guarded deployment may add only versioned native files')
    return guard


def preflight(plan,journal_path,*,fetch=observe,head=None,expected_channel='stable'):
    if plan.get('channel','stable')!=expected_channel:raise ValueError('plan channel differs from selected channel')
    if plan['origin']!=ORIGIN:raise ValueError('unapproved origin')
    if head is None:head=observe_head if fetch is observe else fetch
    recovery=_authorized_empty_native_plan(plan)
    guarded=native_deployment_guard(plan)
    with b.locked(journal_path.parent):
        journal=b.load(journal_path)
        if journal['plan_sha256']!=digest(plan):raise ValueError('journal belongs to another plan')
        if (journal.get('previous_verified') is not False or journal.get('completed')!=[] or
            journal.get('readbacks')!={} or 'previous_readback' in journal or
            'previous_state' in journal or 'previous_state_classification' in journal):
            raise ValueError('fresh preflight journal required')
        observations={}
        def inspect_head(url):
            actual=(head(url,allow_unverified=True,require_canonical=bool(guarded)) if (recovery or guarded) and head is observe_head else head(url))
            if actual.get('status') not in (200,404):raise ValueError('canonical inventory HEAD was not 200 or 404')
            headers=actual.get('headers',{})
            if not (recovery or guarded) and not _fresh_headers(headers):
                raise ValueError('cached response is not a fresh canonical inventory')
            return actual
        def exact_get(url,name,expected,*,allow_absent=False):
            actual=(fetch(url,allow_unverified=bool(recovery or guarded),max_bytes=expected['bytes'],require_canonical=bool(guarded))
                    if fetch is observe else fetch(url))
            if allow_absent and actual.get('status')==404:
                return actual
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
                actual=(fetch(plan['origin']+name,allow_unverified=bool(guarded),max_bytes=expected['bytes'],require_canonical=bool(guarded))
                        if fetch is observe else fetch(plan['origin']+name))
                if actual.get('status',200)!=200 or actual.get('sha256')!=expected['sha256'] or actual.get('bytes')!=expected['bytes']:raise ValueError('live previous site differs from approved snapshot')
                observations[name]=actual
        # A retained feed can predate native hosting. Also inspect every NEW
        # native URL: absence from its inventory does not prove the URL is empty.
        for name,expected in plan['manifests']['phase1'].items():
            if not name.startswith('native-inputs/') or name in plan['previous_files']:continue
            if guarded:
                # Pages has returned 503 on HEAD for these unpublished paths.
                # A canonical GET must prove 404 or return the exact approved file.
                observations[name]=exact_get(plan['origin']+name,name,expected,allow_absent=True)
                continue
            actual=inspect_head(plan['origin']+name)
            if actual.get('status')==404:record_negative(name,actual)
            else:observations[name]=exact_get(plan['origin']+name,name,expected)
        if recovery:
            journal['previous_verified']=False
            journal['previous_state_classification']=FIRST_PUBLICATION_RECOVERY
            journal['previous_state']={'plan_sha256':digest(plan),'input_approval_sha256':plan['input_approval_sha256'],'observations':observations}
        else:
            journal['previous_verified']=True;journal['previous_readback']=observations
            if guarded:
                journal['previous_deployment_guard_id'] = guarded
        b.atomic_json(journal_path,journal)
        return journal

def verify_deploy_baseline(plan,phase,journal_path=None,*,expected_channel='stable',fetch=observe,head=None):
    """Recheck the exact captured prior state immediately before a deploy reservation.

    Phase 1 is compared with the prepared previous-site snapshot. Phase 2 is
    compared with the immutable phase-1 manifest already read back by the run.
    """
    if phase not in ('phase1','phase2'):raise ValueError('deploy baseline phase')
    if plan.get('channel','stable')!=expected_channel:raise ValueError('plan channel differs from selected channel')
    if phase=='phase2':
        if journal_path is None:raise ValueError('phase2 deploy baseline requires the phase1 journal')
        readback(plan,journal_path,'phase1',fetch=fetch,expected_channel=expected_channel,
                 require_recorded=True)
        return True
    with tempfile.TemporaryDirectory(prefix='collie-pages-deploy-baseline-') as temporary:
        baseline_journal=Path(temporary)/'journal.json'
        b.atomic_json(baseline_journal,{'schema':1,'plan_sha256':digest(plan),'completed':[],
                                       'previous_verified':False,'readbacks':{}})
        preflight(plan,baseline_journal,fetch=fetch,head=head,expected_channel=expected_channel)
    return True

def readback(plan,journal_path,phase,*,fetch=None,expected_channel='stable',require_recorded=False, deployment_guard_id=None):
    if plan.get('channel','stable')!=expected_channel:raise ValueError('plan channel differs from selected channel')
    if phase not in ('phase1','phase2'):raise ValueError('phase')
    if plan['origin']!=ORIGIN:raise ValueError('unapproved origin')
    guarded = native_deployment_guard(plan)
    if guarded and not require_recorded and (type(deployment_guard_id) is not int or deployment_guard_id <= 0):
        raise ValueError('successful deployment guard output is required')
    live_fetch = fetch is None or fetch is observe
    if fetch is None:fetch=observe
    with b.locked(journal_path.parent):
        journal=b.load(journal_path)
        if journal['plan_sha256']!=digest(plan):raise ValueError('journal belongs to another plan')
        if require_recorded:
            recorded=journal.get('readbacks',{}).get(phase)
            expected=plan['manifests'][phase]
            if (phase not in journal.get('completed',[]) or not isinstance(recorded,dict) or
                    set(recorded)!=set(expected)):
                raise ValueError('recorded completed '+phase+' readback required for deployment baseline')
            for name,entry in expected.items():
                item=recorded[name]
                if (not isinstance(item,dict) or item.get('status')!=200 or
                        item.get('sha256')!=entry['sha256'] or item.get('bytes')!=entry['bytes']):
                    raise ValueError('recorded '+phase+' readback differs from deployment baseline')
        recovery=not journal.get('previous_verified') and _recovery_journal_authorized(plan,journal)
        if not journal.get('previous_verified') and not recovery:
            raise ValueError('live previous preflight or owner-authorized first-publication recovery required')
        if phase=='phase2' and 'phase1' not in journal['completed']:raise ValueError('phase1 readback required before phase2')
        if guarded and journal.get('previous_deployment_guard_id') != guarded:
            raise ValueError('journal lacks the approved previous deployment guard')
        observed={}
        deadline = time.monotonic() + 660
        for name,expected in plan['manifests'][phase].items():
            safe_name(name)
            # In the exact empty-native recovery, cache metadata is not origin
            # proof. Still GET every URL and require the exact planned bytes/hash.
            while True:
                actual=(fetch(plan['origin']+name,allow_unverified=bool(recovery or guarded),max_bytes=expected['bytes'],require_canonical=bool(guarded))
                        if fetch is observe else fetch(plan['origin']+name))
                if (actual.get('status')==200 and actual.get('sha256')==expected['sha256'] and
                        actual.get('bytes')==expected['bytes']):
                    break
                # A cached old manifest or negative can lag an already successful
                # deployment. Every positive result must still match the new bytes.
                if not (guarded and live_fetch and not require_recorded and
                        (name == 'site-manifest.json' or name not in plan['previous_files']) and
                        not _fresh_headers(actual.get('headers', {})) and time.monotonic() < deadline):
                    raise ValueError('readback mismatch: '+name)
                time.sleep(min(15, max(0, deadline - time.monotonic())))
            observed[name]=actual
        if guarded and not require_recorded:
            journal.setdefault('deployment_guards', {})[phase] = deployment_guard_id
        journal['readbacks'][phase]=observed
        if phase not in journal['completed']:journal['completed'].append(phase)
        b.atomic_json(journal_path,journal)
        return journal

def verify_completed_readbacks(plan,journal):
    if journal.get('plan_sha256')!=digest(plan):raise ValueError('journal belongs to another plan')
    if not journal.get('previous_verified') and not _recovery_journal_authorized(plan,journal):
        raise ValueError('live previous preflight or owner-authorized first-publication recovery required')
    guarded = native_deployment_guard(plan)
    if guarded:
        guards = journal.get('deployment_guards', {})
        if (journal.get('previous_deployment_guard_id') != guarded or set(guards) != {'phase1', 'phase2'} or
                any(type(value) is not int or value <= 0 for value in guards.values()) or
                len({guarded, *guards.values()}) != 3):
            raise ValueError('guarded readbacks require both successful deployment identities')
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
    a=s.add_parser('prepare');a.add_argument('bundle',type=Path);a.add_argument('output',type=Path);a.add_argument('--policy',required=True);a.add_argument('--channel',choices=('stable','trailblazer-beta'),default='stable')
    a=s.add_parser('preflight');a.add_argument('plan',type=Path);a.add_argument('journal',type=Path);a.add_argument('--channel',choices=('stable','trailblazer-beta'),default='stable')
    a=s.add_parser('readback');a.add_argument('plan',type=Path);a.add_argument('journal',type=Path);a.add_argument('--phase',choices=['phase1','phase2'],required=True);a.add_argument('--channel',choices=('stable','trailblazer-beta'),default='stable')
    a=s.add_parser('unpack');a.add_argument('archive',type=Path);a.add_argument('destination',type=Path)
    args=p.parse_args()
    readiness_token = os.environ.pop('COLLIE_APP_READINESS_TOKEN', None)
    if args.cmd=='prepare':
        print(json.dumps(prepare(args.bundle,args.output,args.policy,args.channel,
                                 readiness_token=readiness_token)['artifacts'],indent=2))
    elif args.cmd=='preflight':print(json.dumps(preflight(b.load(args.plan),args.journal,expected_channel=args.channel),indent=2))
    elif args.cmd=='readback':
        guard_id = os.environ.get('COLLIE_DEPLOYED_GUARD_ID')
        print(json.dumps(readback(b.load(args.plan),args.journal,args.phase,expected_channel=args.channel,
                     deployment_guard_id=int(guard_id) if guard_id else None),indent=2))
    else:unpack(args.archive,args.destination)
if __name__=='__main__':main()
