"""Small explicit adapter between the existing release commands and four receipts.

Plans/receipt digests are supplied by an independently trusted operator policy.
This module authenticates those inputs; it never creates owner approval itself.
"""
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import migration_receipts as receipts

PLAN_FIELDS={'schema','kind','mode','purpose','source_commit','engineering_revision','workflow_revision','package','version_code','version_name','certificate_sha256','source_receipt_sha256','build_inputs_sha256','required_test_reports'}
INPUT_FIELDS={'schema','recipe_revision','config_sha256','toolchain_sha256','dependencies_sha256','native_inputs_sha256','trust_level'}
PLAN_EVIDENCE='Approved release plan raw bytes; operator-selected inputs, not a signed attestation'


def raw(path):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):raise ValueError('release input must be a regular file')
        with os.fdopen(fd,'rb',closefd=False) as stream:return stream.read()
    finally:os.close(fd)


def document(path,expected=None):
    data=raw(path);digest=hashlib.sha256(data).hexdigest()
    if expected is not None and digest!=expected:raise ValueError('externally approved input digest differs')
    return json.loads(data,object_pairs_hook=receipts.unique),digest


def arguments(parser):
    for name in ('release-plan','source-receipt','build-inputs','build-receipt','release-receipt','test-reports','source-checkout','toolchain-input','dependency-input','native-input'):
        parser.add_argument('--'+name,type=Path)
    for name in ('approved-release-plan-sha256','approved-build-receipt-sha256','approved-release-receipt-sha256'):
        parser.add_argument('--'+name)


def plan(args,*,historical_rehearsal=False):
    if args.release_plan is None:
        if historical_rehearsal:return None
        raise ValueError('explicit independently approved release plan required; missing receipts never select legacy')
    receipts.check(args.approved_release_plan_sha256,'sha')
    data,digest=document(args.release_plan,args.approved_release_plan_sha256)
    receipts.exact(data,PLAN_FIELDS)
    if type(data['schema']) is not int or data['schema']!=1 or data['kind']!='release-plan' or data['mode'] not in ('legacy','new-source') or data['purpose'] not in ('fixture','production'):raise ValueError('unsupported release plan')
    for key in ('source_commit','engineering_revision','workflow_revision'):receipts.check(data[key],'git')
    for key in ('certificate_sha256',):receipts.check(data[key],'sha')
    receipts.check(data['package'],'package');receipts.check(data['version_code'],'code');receipts.check(data['version_name'],'text')
    if not isinstance(data['required_test_reports'],list) or any(not isinstance(x,str) or not x.strip() for x in data['required_test_reports']) or len(set(data['required_test_reports']))!=len(data['required_test_reports']):raise ValueError('required report names')
    for key in ('source_receipt_sha256','build_inputs_sha256'):
        if data['mode']=='new-source':receipts.check(data[key],'sha')
        elif data[key] is not None:raise ValueError('legacy plan must explicitly omit new-source receipts')
    if data['mode']=='legacy' and data['required_test_reports']:raise ValueError('legacy plan cannot declare unchecked required reports')
    if data['mode']=='legacy' and data['workflow_revision']!=data['source_commit']:raise ValueError('legacy workflow identity must match historical commit')
    if data['mode']=='new-source' and not data['required_test_reports']:raise ValueError('new-source plan requires actual test report evidence')
    for attr,key in [('commit','source_commit'),('approved_commit','source_commit'),('workflow_revision','workflow_revision'),('engineering_revision','engineering_revision'),('version_code','version_code'),('version_name','version_name'),('certificate_sha256','certificate_sha256')]:
        value=getattr(args,attr,None)
        if value is not None and value!=data[key]:raise ValueError('release plan differs from '+attr)
    if data['purpose']=='fixture' and getattr(args,'repo','').lower()=='0cwa/collieim-android':raise ValueError('fixture plan cannot publish to production application repository')
    return data,digest


def identities(context):
    if context and context[0]['mode']=='new-source':return {k:context[0][k] for k in ('source_commit','engineering_revision','workflow_revision')}
    return None


def check_manifest(context,manifest):
    if not context:
        if manifest.get('schema')!=1 or manifest.get('release_mode')=='new-source' or any(k in manifest for k in ('source_receipt_sha256','build_receipt_sha256','release_plan_sha256')):raise ValueError('new-source evidence requires an approved plan even for rehearsal/dry-run')
        return
    p,plan_sha=context
    if p['mode']=='legacy':
        if manifest.get('schema')!=1 or manifest.get('release_mode')=='new-source' or any(k in manifest for k in ('source_receipt_sha256','build_receipt_sha256','release_plan_sha256')):
            raise ValueError('new-source evidence cannot select legacy route')
    for field,key in [('commit','source_commit'),('package','package'),('version_code','version_code'),('version_name','version_name'),('certificate_sha256','certificate_sha256')]:
        if field in manifest and manifest[field]!=p[key]:raise ValueError('manifest differs from approved release plan: '+field)


def source_and_inputs(args,context):
    p,_=context
    if args.source_receipt is None or args.build_inputs is None:raise ValueError('new-source requires source receipt and approved stable build inputs')
    source,_=document(args.source_receipt);receipts.validate(source,'source')
    if receipts.digest(source)!=p['source_receipt_sha256']:raise ValueError('source receipt differs from approved plan')
    s=source['payload']
    if s['published_commit']!=p['source_commit'] or s['engineering_revision']!=p['engineering_revision']:raise ValueError('source/engineering identity differs')
    inputs,_=document(args.build_inputs,p['build_inputs_sha256']);receipts.exact(inputs,INPUT_FIELDS)
    if type(inputs['schema']) is not int or inputs['schema']!=1:raise ValueError('build input schema')
    receipts.check(inputs['recipe_revision'],'git')
    for k in ('config_sha256','toolchain_sha256','dependencies_sha256','native_inputs_sha256'):receipts.check(inputs[k],'sha')
    receipts.check(inputs['trust_level'],'text')
    return source,inputs


def source_unchanged(checkout,commit,tree):
    """Compare every tracked raw blob/type/Git executable bit, without filters.

    Read the original index only for staged/hidden-entry checks. Never refresh
    its stat cache or let local Git conversion rules stand in for source bytes.
    """
    return source_worktree_matches_index(checkout,commit,tree,require_head_tree=True)


def source_worktree_matches_index(checkout,expected_head,expected_tree,*,require_head_tree=False):
    """Prove raw worktree bytes equal a pinned index tree at an explicit HEAD.

    This supports prepublication staging where HEAD deliberately remains the
    approved application base while the index/worktree are reset to the
    verified reconstruction tree. It rejects staged/hidden entries, all
    untracked paths, local conversion rules, file-mode disguises and unsafe
    filesystem substitutions without refreshing the index.
    """
    env={k:v for k,v in os.environ.items() if not k.startswith('GIT_')}
    env.update(GIT_CONFIG_NOSYSTEM='1',GIT_CONFIG_GLOBAL=os.devnull,GIT_NO_REPLACE_OBJECTS='1',GIT_ATTR_NOSYSTEM='1',GIT_NO_LAZY_FETCH='1')
    def git(*argv):
        return subprocess.check_output(['git','-C',str(checkout),'-c','core.hooksPath=/dev/null',
            '-c','core.fsmonitor=false','-c','core.attributesFile=/dev/null',*argv],env=env)
    common=Path(os.fsdecode(git('rev-parse','--path-format=absolute','--git-common-dir').strip()))
    if any((common/name).exists() for name in ('info/grafts','objects/info/alternates','shallow')) or git('for-each-ref','--format=%(refname)','refs/replace').strip():
        raise ValueError('hostile or incomplete source Git store')
    if any((common/'objects/pack').glob('*.promisor')):
        raise ValueError('promisor object packs are forbidden')
    for args in (('config','--local','--get','extensions.partialClone'),
                 ('config','--local','--get-regexp',r'^remote\..*\.promisor$')):
        result=subprocess.run(['git','-C',str(checkout),*args],env=env,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
        if result.returncode==0 and result.stdout.strip():raise ValueError('partial or promisor Git object store is forbidden')
        if result.returncode not in (0,1):raise ValueError('cannot verify Git object store configuration')
    if git('rev-parse','HEAD').decode().strip()!=expected_head:
        raise ValueError('actual checkout HEAD differs from pinned base')
    if require_head_tree and git('rev-parse','HEAD^{tree}').decode().strip()!=expected_tree:
        raise ValueError('actual checkout tree differs from approved source')
    untracked=git('ls-files','--others','-z','--directory')
    if untracked:
        raise ValueError('untracked source paths are forbidden: '+os.fsdecode(untracked.split(b'\0',1)[0]))
    entries=git('ls-files','-v','-z').split(b'\0')
    if any(line.startswith(b'S ') or line[:1].islower() for line in entries):
        raise ValueError('hidden tracked source entries')
    tracked=list(filter(None,git('ls-tree','-r','-z','--full-tree',expected_tree).split(b'\0')))
    approved_index=[]
    for entry in tracked:
        metadata,path=entry.split(b'\t',1);mode,kind,expected=metadata.split()
        approved_index.append(mode+b' '+expected+b' 0\t'+path)
    if sorted(filter(None,git('ls-files','--stage','-z').split(b'\0')))!=sorted(approved_index):
        raise ValueError('tracked source index entries differ from expected tree')
    root=os.open(checkout,os.O_RDONLY|os.O_DIRECTORY)
    try:
        for entry in tracked:
            metadata,path=entry.split(b'\t',1);mode,kind,expected=metadata.split()
            parts=path.split(b'/')
            if mode not in (b'100644',b'100755',b'120000') or kind!=b'blob' or any(x in (b'',b'.',b'..') for x in parts):
                raise ValueError('unsupported tracked source entry')
            directory=os.dup(root)
            try:
                for part in parts[:-1]:
                    child=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=directory)
                    os.close(directory);directory=child
                name=parts[-1];info=os.stat(name,dir_fd=directory,follow_symlinks=False)
                if mode==b'120000':
                    if not stat.S_ISLNK(info.st_mode):raise ValueError('tracked source symlink changed')
                    content=os.fsencode(os.readlink(name,dir_fd=directory))
                    actual=hashlib.sha1(b'blob '+str(len(content)).encode()+b'\0'+content).hexdigest()
                else:
                    if not stat.S_ISREG(info.st_mode) or bool(info.st_mode&stat.S_IXUSR)!=(mode==b'100755'):
                        raise ValueError('tracked source file type or executable mode changed')
                    descriptor=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=directory)
                    with os.fdopen(descriptor,'rb') as stream:
                        before=os.fstat(stream.fileno())
                        if bool(before.st_mode&stat.S_IXUSR)!=(mode==b'100755'):
                            raise ValueError('tracked source executable mode changed while opening')
                        if not stat.S_ISREG(before.st_mode) or (before.st_dev,before.st_ino)!=(info.st_dev,info.st_ino):
                            raise ValueError('tracked source file changed while checking')
                        digest=hashlib.sha1(b'blob '+str(before.st_size).encode()+b'\0');size=0
                        for chunk in iter(lambda:stream.read(1024*1024),b''):digest.update(chunk);size+=len(chunk)
                        after=os.fstat(stream.fileno())
                        if size!=before.st_size or (before.st_size,before.st_mtime_ns,before.st_ctime_ns)!=(after.st_size,after.st_mtime_ns,after.st_ctime_ns):
                            raise ValueError('tracked source file changed while checking')
                        actual=digest.hexdigest()
                if actual.encode()!=expected:raise ValueError('tracked source bytes changed during build')
            except OSError as error:
                raise ValueError('tracked source file is missing or unsafe') from error
            finally:os.close(directory)
    finally:os.close(root)


def collect(args,context,unsigned_sha):
    source,inputs=source_and_inputs(args,context);p,plan_sha=context
    if args.source_checkout is None:raise ValueError('new-source collection requires actual source checkout')
    source_unchanged(args.source_checkout,p['source_commit'],source['payload']['published_tree'])
    for attr,key in [('toolchain_input','toolchain_sha256'),('dependency_input','dependencies_sha256'),('native_input','native_inputs_sha256')]:
        path=getattr(args,attr,None)
        if path is None or hashlib.sha256(raw(path)).hexdigest()!=inputs[key]:raise ValueError('actual '+attr+' bytes differ from approved build inputs')
    if hashlib.sha256(raw(args.config)).hexdigest()!=inputs['config_sha256']:raise ValueError('actual build configuration differs from approved inputs')
    if args.test_reports is None:raise ValueError('actual test report descriptor required')
    descriptor,_=document(args.test_reports);receipts.exact(descriptor,('schema','reports'))
    if type(descriptor['schema']) is not int or descriptor['schema']!=1 or not isinstance(descriptor['reports'],list):raise ValueError('test report descriptor')
    names=[];evidence=[{'status':'PASS','description':PLAN_EVIDENCE,'sha256':plan_sha}]
    for entry in descriptor['reports']:
        receipts.exact(entry,('name','status','description','file','sha256'));receipts.check(entry['name'],'text');receipts.check(entry['description'],'text')
        names.append(entry['name'])
        if entry['status'] not in ('PASS','FAIL','NOT RUN','BLOCKED','UNKNOWN'):raise ValueError('test report status')
        if entry['file'] is None:
            if entry['sha256'] is not None or entry['status']=='PASS':raise ValueError('passed test report needs actual bytes')
        else:
            receipts.check(entry['sha256'],'sha')
            receipts.check(entry['file'],'text')
            path=Path(entry['file'])
            if path.is_absolute() or '..' in path.parts:raise ValueError('test report path must remain relative')
            target=args.test_reports.parent/path
            for component in (target,*target.parents):
                if component.is_symlink():raise ValueError('test report symlink')
                if component==args.test_reports.parent:break
            if hashlib.sha256(raw(target)).hexdigest()!=entry['sha256']:raise ValueError('actual test report bytes differ')
        evidence.append({'status':entry['status'],'description':entry['name']+': '+entry['description'],'sha256':entry['sha256']})
    if len(names)!=len(set(names)) or set(names)!=set(p['required_test_reports']):raise ValueError('missing or substituted required test reports')
    payload={'source_receipt_sha256':receipts.digest(source),'source_commit':p['source_commit'],'source_tree':source['payload']['published_tree'],'unsigned_apk_sha256':unsigned_sha,**{k:inputs[k] for k in ('recipe_revision','toolchain_sha256','dependencies_sha256','native_inputs_sha256','trust_level')}}
    build={'schema':2,'kind':'build','payload':payload,'evidence':evidence};receipts.validate(build,'build')
    return source,build


def signing(args,context,manifest):
    source,inputs=source_and_inputs(args,context);p,plan_sha=context
    if args.build_receipt is None or args.approved_build_receipt_sha256 is None:raise ValueError('new-source signing requires externally approved build receipt')
    receipts.check(args.approved_build_receipt_sha256,'sha')
    build,_=document(args.build_receipt);receipts.validate(build,'build')
    if receipts.digest(build)!=args.approved_build_receipt_sha256:raise ValueError('unapproved build receipt')
    b=build['payload'];s=source['payload']
    if any(b[k]!=inputs[k] for k in ('recipe_revision','toolchain_sha256','dependencies_sha256','native_inputs_sha256','trust_level')):raise ValueError('build recipe/toolchain/native/dependency identity differs')
    if b['source_receipt_sha256']!=receipts.digest(source) or b['source_commit']!=p['source_commit'] or b['source_tree']!=s['published_tree']:raise ValueError('build source binding differs')
    if not any(e['description']==PLAN_EVIDENCE and e['sha256']==plan_sha and e['status']=='PASS' for e in build['evidence']):raise ValueError('build receipt does not name approved plan')
    for kind,doc in [('source',source),('build',build)]:
        if not doc['evidence'] or any(e['status']!='PASS' for e in doc['evidence']):raise ValueError('new-source signing requires passing scoped evidence for '+kind)
    for k,expected in [('release_plan_sha256',plan_sha),('source_receipt_sha256',receipts.digest(source)),('build_receipt_sha256',receipts.digest(build))]:
        if manifest.get(k)!=expected:raise ValueError('unsigned manifest receipt/plan substitution')
    if manifest.get('schema')!=2 or {e['sha256'] for e in manifest['apks']}!={b['unsigned_apk_sha256']}:raise ValueError('unsigned APK/build receipt mismatch')
    return source,build


def release(context,source,build,final,raw_final):
    p,plan_sha=context
    for key,expected in [('schema',2),('release_mode','new-source'),('release_plan_sha256',plan_sha),('source_receipt_sha256',receipts.digest(source)),('build_receipt_sha256',receipts.digest(build)),*identities(context).items()]:
        if final.get(key)!=expected:raise ValueError('signed final plan/identity differs: '+key)
    for key,field in [('commit','source_commit'),('version_code','version_code'),('version_name','version_name'),('certificate_sha256','certificate_sha256'),('package','package')]:
        if final.get(key)!=p[field]:raise ValueError('signed final manifest differs from plan: '+key)
    digests={e['signed_sha256'] for e in final['apks']}
    if len(digests)!=1 or any(e['certificate_sha256']!=p['certificate_sha256'] for e in final['apks']):raise ValueError('signed aliases/signer differ')
    payload={'release_manifest_sha256':hashlib.sha256(raw_final).hexdigest(),'source_receipt_sha256':receipts.digest(source),'build_receipt_sha256':receipts.digest(build),'source_commit':p['source_commit'],'engineering_revision':p['engineering_revision'],'workflow_revision':p['workflow_revision'],'package':p['package'],'version_code':p['version_code'],'version_name':p['version_name'],'apk_sha256':next(iter(digests)),'apk_certificate_sha256':p['certificate_sha256'],'aliases':receipts.ALIASES}
    result={'schema':2,'kind':'release','payload':payload,'evidence':[{'status':'PASS','description':'Actual signed alias bytes and v2/v3 signer checked by release_sign; '+p['purpose']+' plan, not independent native reproduction','sha256':payload['release_manifest_sha256']}]}
    receipts.validate(result,'release');return result


def publication(args,context,unsigned,final,raw_final):
    source,build=signing(args,context,unsigned)
    if args.release_receipt is None or args.approved_release_receipt_sha256 is None:raise ValueError('new-source publication requires externally approved release receipt')
    receipts.check(args.approved_release_receipt_sha256,'sha')
    supplied,_=document(args.release_receipt);receipts.validate(supplied,'release')
    if receipts.digest(supplied)!=args.approved_release_receipt_sha256:raise ValueError('unapproved release receipt')
    expected=release(context,source,build,final,raw_final)
    if supplied['payload']!=expected['payload']:raise ValueError('release/raw-final/signed identity substitution')
    receipts.verify_release_inputs(source,build,supplied,{'source':context[0]['source_receipt_sha256'],'build':args.approved_build_receipt_sha256,'release':args.approved_release_receipt_sha256},raw_manifest_sha256=hashlib.sha256(raw_final).hexdigest(),promotion=True)
    return source,build,supplied


def write(path,value):
    Path(path).write_text(json.dumps(value,sort_keys=True,indent=2)+'\n')


def import_legacy(manifest,engineering_revision,purpose):
    """Propose explicit legacy inputs; never infer approval or new evidence."""
    if manifest.get('schema')!=1 or manifest.get('workflow_revision')!=manifest.get('commit') or manifest.get('release_mode')=='new-source' or any(k in manifest for k in ('source_receipt_sha256','build_receipt_sha256','release_plan_sha256')):
        raise ValueError('only genuine legacy identity manifests can be imported')
    receipts.check(engineering_revision,'git')
    if purpose not in ('production','fixture'):raise ValueError('explicit plan purpose required')
    result={'schema':1,'kind':'release-plan','mode':'legacy','purpose':purpose,
            'source_commit':manifest['commit'],'engineering_revision':engineering_revision,
            'workflow_revision':manifest['workflow_revision'],'package':manifest['package'],
            'version_code':manifest['version_code'],'version_name':manifest['version_name'],
            'certificate_sha256':manifest['certificate_sha256'],'source_receipt_sha256':None,
            'build_inputs_sha256':None,'required_test_reports':[]}
    for key in ('source_commit','workflow_revision'):receipts.check(result[key],'git')
    for key,kind in [('package','package'),('version_code','code'),('version_name','text'),('certificate_sha256','sha')]:receipts.check(result[key],kind)
    return result


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description='Propose an explicit legacy plan without approving it; stdout contains JSON only.')
    parser.add_argument('action',choices=['import-legacy']);parser.add_argument('--manifest',required=True,type=Path)
    parser.add_argument('--engineering-revision',required=True);parser.add_argument('--purpose',required=True,choices=['production','fixture'])
    options=parser.parse_args();value,_=document(options.manifest)
    print(json.dumps(import_legacy(value,options.engineering_revision,options.purpose),indent=2,sort_keys=True))
