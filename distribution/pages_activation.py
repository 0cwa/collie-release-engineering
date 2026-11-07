"""First public add-link activation after the feed has passed production checks.

Data-only adapter over the existing Pages plan, journal and repository receipt.
No build, signing, deployment or DNS operation is performed here.
"""
import argparse,json,re,shutil
from pathlib import Path
import binary_repo as b
import pages as p
import site_page
FIELDS={'schema','kind','environment','origin','policy_revision','feed_policy_revision','feed_plan_sha256','feed_journal_sha256','repository_receipt_sha256','repository_certificate_sha256','client_acceptance_sha256','previous_site_sha256','previous_manifest_sha256','maximum_bytes','download_url'}
CLIENTS={'org.fdroid.fdroid','com.looker.droidify'}

def checked_journal(plan,journal):
    if journal['plan_sha256']!=p.digest(plan) or journal.get('previous_verified') is not True or journal.get('completed')!=['phase1','phase2']:raise ValueError('completed Pages readback required')
    observed=journal.get('readbacks',{}).get('phase2',{})
    if set(observed)!=set(plan['manifests']['phase2']):raise ValueError('complete canonical readback required')
    for name,expected in plan['manifests']['phase2'].items():
        actual=observed[name]
        if actual.get('status')!=200 or any(actual.get(k)!=expected[k] for k in ('sha256','bytes')):raise ValueError('canonical readback mismatch')

def evidence(bundle,policy):
    a=b.load(bundle/'approval.json')
    if set(a)!=FIELDS or type(a['schema']) is not int or a['schema']!=1 or a['kind']!='collie-pages-link-activation' or a['environment']!='production':raise ValueError('production activation approval required')
    if a['origin']!=p.ORIGIN or a['policy_revision']!=policy or not re.fullmatch('[0-9a-f]{40}',policy):raise ValueError('activation policy/origin differs')
    if type(a['maximum_bytes']) is not int or not 0<a['maximum_bytes']<=p.MAX_BYTES:raise ValueError('activation capacity')
    if not re.fullmatch(r'https://github\.com/0cwa/collieim-android/releases/download/[A-Za-z0-9_.-]+/collie\.apk',a['download_url']):raise ValueError('explicit canonical APK URL required')
    for key in FIELDS-{'schema','kind','environment','origin','policy_revision','feed_policy_revision','maximum_bytes','download_url'}:
        if not isinstance(a[key],str) or not re.fullmatch('[0-9a-f]{64}',a[key]):raise ValueError('activation digest '+key)
    plan=b.load(bundle/'feed-plan.json');journal=b.load(bundle/'feed-journal.json');receipt=p.receipts.load(bundle/'repository-receipt.json','repository');clients=b.load(bundle/'client-acceptance.json')
    for value,key in ((plan,'feed_plan_sha256'),(journal,'feed_journal_sha256'),(clients,'client_acceptance_sha256')):
        if p.digest(value)!=a[key]:raise ValueError('approved '+key+' differs')
    if p.receipts.digest(receipt)!=a['repository_receipt_sha256']:raise ValueError('approved repository receipt differs')
    if plan.get('mode')!='feed' or plan['origin']!=p.ORIGIN or plan['policy_revision']!=a['feed_policy_revision'] or not re.fullmatch('[0-9a-f]{40}',a['feed_policy_revision']):raise ValueError('approved completed feed plan required')
    if plan['repository_receipt_sha256']!=a['repository_receipt_sha256'] or plan['repository_certificate_sha256']!=a['repository_certificate_sha256']:raise ValueError('feed receipt/certificate differs')
    payload=receipt['payload']
    if payload['generation']!=plan['candidate_generation_sha256'] or payload['repository_certificate_sha256']!=a['repository_certificate_sha256'] or payload['apk_certificate_sha256']!=b.APK_CERTIFICATE:raise ValueError('repository receipt identity differs')
    checked_journal(plan,journal)
    expected={'schema','environment','origin','repository_certificate_sha256','apk_sha256','clients'}
    if set(clients)!=expected or type(clients['schema']) is not int or clients['schema']!=1 or clients['environment']!='production' or clients['origin']!=p.ORIGIN or clients['repository_certificate_sha256']!=a['repository_certificate_sha256'] or clients['apk_sha256']!=payload['apk_sha256'] or set(clients['clients'])!=CLIENTS:raise ValueError('production canonical client evidence required')
    for report in clients['clients'].values():
        if set(report)!={'version','evidence_sha256','tests'} or not isinstance(report['version'],str) or not report['version'] or not re.fullmatch('[0-9a-f]{64}',report['evidence_sha256']) or report['tests']!={k:'PASS' for k in ('add','refresh','install','update')}:raise ValueError('client add/refresh/install/update evidence required')
    return a,plan,journal,receipt,clients

def prepare(bundle,output,policy):
    a,feed,_,receipt,_=evidence(bundle,policy);old=bundle/'previous';files=p.inventory(old)
    if p.digest(files)!=a['previous_site_sha256'] or files!=feed['manifests']['phase2']:raise ValueError('complete published feed snapshot differs')
    prior=b.load(old/'site-manifest.json')
    if b.sha(old/'site-manifest.json')!=a['previous_manifest_sha256'] or prior!={'schema':1,'kind':'collie-pages-public-inventory','origin':p.ORIGIN,'files':{k:v for k,v in files.items() if k!='site-manifest.json'}}:raise ValueError('authoritative previous manifest differs')
    if site_page.has_add_link((old/'index.html').read_text()):raise ValueError('first-link activation requires the inactive landing')
    data=b.verify_indexes(old/'fdroid/repo',a['repository_certificate_sha256']);payload=receipt['payload']
    for index in payload['indexes']:
        if b.sha(old/'fdroid/repo'/index['file'])!=index['sha256']:raise ValueError('approved signed index differs')
    selected=[v for v in data['packages'][b.APP]['versions'].values() if v['file']['sha256']==payload['apk_sha256']]
    if len(selected)!=1 or selected[0]['manifest']['signer']['sha256']!=[b.APK_CERTIFICATE]:raise ValueError('approved canonical production APK differs')
    if output.exists() or output.is_symlink():raise ValueError('new activation output required')
    output.mkdir(parents=True)
    try:
        site=output/'phase2/site';site.mkdir(parents=True)
        for name,entry in files.items():
            if name!='site-manifest.json':p.copy_pinned(old/name,site/name,entry)
        site_page.render(site,p.ORIGIN+'fdroid/repo/',a['repository_certificate_sha256'],b.APK_CERTIFICATE,a['download_url'],state='active')
        current=p.inventory(site)
        if any(current.get(name)!=entry for name,entry in files.items() if name not in {'index.html','site-manifest.json','add-repository.png'}):raise ValueError('link activation changed existing assets/index/APK/native bytes')
        if set(current)-set(files)-{'add-repository.png'}:raise ValueError('unexpected activation assets')
        b.atomic_json(site/'site-manifest.json',dict(schema=1,kind='collie-pages-public-inventory',origin=p.ORIGIN,files=current));current=p.inventory(site)
        if sum(x['bytes'] for x in current.values())>a['maximum_bytes']:raise ValueError('activation site capacity')
        artifact=p.archive(site,output/'phase2/artifact.tar',current)
        if artifact['bytes']>a['maximum_bytes']:raise ValueError('activation archive capacity')
        plan=dict(schema=1,kind='collie-pages-promotion',mode='link-activation',origin=p.ORIGIN,policy_revision=policy,input_approval_sha256=p.digest(a),maximum_bytes=a['maximum_bytes'],previous_manifest_sha256=a['previous_manifest_sha256'],previous_files=files,manifests={'phase1':files,'phase2':current},artifacts={'phase2':artifact},repository_receipt_sha256=a['repository_receipt_sha256'],repository_certificate_sha256=a['repository_certificate_sha256'],candidate_generation_sha256=feed['candidate_generation_sha256'],client_acceptance_sha256=a['client_acceptance_sha256'])
        b.atomic_json(output/'plan.json',plan);b.atomic_json(output/'journal.json',dict(schema=1,plan_sha256=p.digest(plan),completed=[],previous_verified=False,readbacks={}))
        return plan
    except Exception:shutil.rmtree(output);raise

def record(bundle,plan_path,journal_path):
    plan=b.load(plan_path);journal=b.load(journal_path);a,feed,_,receipt,_=evidence(bundle,plan['policy_revision'])
    if plan.get('mode')!='link-activation' or plan['input_approval_sha256']!=p.digest(a):raise ValueError('approved link activation plan required')
    if plan['origin']!=p.ORIGIN or plan['repository_receipt_sha256']!=a['repository_receipt_sha256'] or plan['repository_certificate_sha256']!=a['repository_certificate_sha256'] or plan['client_acceptance_sha256']!=a['client_acceptance_sha256'] or plan['previous_files']!=feed['manifests']['phase2'] or plan['manifests']['phase1']!=plan['previous_files']:raise ValueError('activation plan evidence substituted')
    changed={name for name,entry in plan['manifests']['phase2'].items() if plan['previous_files'].get(name)!=entry}
    if changed-{'index.html','add-repository.png','site-manifest.json'} or set(plan['previous_files'])-set(plan['manifests']['phase2']):raise ValueError('activation changed existing bytes')
    checked_journal(plan,journal)
    return dict(schema=1,kind='collie-repository-link-activation',environment='production',origin=p.ORIGIN,policy_revision=plan['policy_revision'],plan_sha256=p.digest(plan),journal_sha256=p.digest(journal),repository_receipt_sha256=p.receipts.digest(receipt),repository_certificate_sha256=a['repository_certificate_sha256'],client_acceptance_sha256=a['client_acceptance_sha256'],apk_sha256=receipt['payload']['apk_sha256'],checks={k:'PASS' for k in ('canonical_https_readback','official_fdroid','compatible_client')})

def main():
    parser=argparse.ArgumentParser();sub=parser.add_subparsers(dest='cmd',required=True)
    cli=sub.add_parser('prepare');cli.add_argument('bundle',type=Path);cli.add_argument('output',type=Path);cli.add_argument('--policy',required=True)
    cli=sub.add_parser('record');cli.add_argument('bundle',type=Path);cli.add_argument('plan',type=Path);cli.add_argument('journal',type=Path)
    args=parser.parse_args()
    print(json.dumps(prepare(args.bundle,args.output,args.policy) if args.cmd=='prepare' else record(args.bundle,args.plan,args.journal),indent=2))
if __name__=='__main__':main()
