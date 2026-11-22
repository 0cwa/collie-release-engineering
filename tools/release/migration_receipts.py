#!/usr/bin/env python3
"""Versioned migration handoffs; implementation claims are not attestations."""
import hashlib
import json
import re

FIELDS = {
 'source': {'upstream_commit':'git','engineering_revision':'git','generator_revision':'git','verifier_revision':'git','layers':'digests','reconstructed_tree':'git','overlays':'digests','published_commit':'git','published_tree':'git'},
 'build': {'source_receipt_sha256':'sha','source_commit':'git','source_tree':'git','recipe_revision':'git','toolchain_sha256':'sha','dependencies_sha256':'sha','native_inputs_sha256':'sha','unsigned_apk_sha256':'sha','trust_level':'text'},
 'release': {'release_manifest_sha256':'sha','source_receipt_sha256':'sha','build_receipt_sha256':'sha','source_commit':'git','engineering_revision':'git','workflow_revision':'git','package':'package','version_code':'code','version_name':'text','apk_sha256':'sha','apk_certificate_sha256':'sha','aliases':'aliases'},
 'repository': {'release_manifest_sha256':'sha','release_receipt_sha256':'nullable_sha','apk_sha256':'sha','apk_certificate_sha256':'sha','repository_certificate_sha256':'sha','fdroidserver_revision':'text','metadata_revision':'git','indexes':'indexes','generation':'text'},
}
PROFILES = {
 'stable': {'package':'im.collie.app','apk_certificate':'f8cc0c0c052e5635c26fcd293c2b0af3e87d61ff7f2df3602a9af6e91f73c826',
            'aliases':sorted(x+'.apk' for x in ('collie','collie-foss','molly-continued','molly-foss-continued','molly-minimal'))},
 'trailblazer-beta': {'package':'im.collie.app.trailblazer','apk_certificate':'a515c03498bce8852b835544910b09ccc11a3abbed23e826e9c62c002680fe98',
                      'aliases':['trailblazer-beta.apk']},
}
ALIASES = PROFILES['stable']['aliases']

PREPUBLICATION_REPOSITORY_EVIDENCE = {
 'candidate_index': 'Pinned fdroidserver verifies repository v1/v2 signatures, entry digest and indexed local APK hashes (prepublication)',
 'client_acceptance': 'Operator-recorded isolated client add/refresh/install/upgrade evidence; not independent build reproduction',
 'production_readback': 'Production HTTPS read-back and provider cache headers (postpublication Pages journal; not run before candidate preparation)',
}


def exact(value, fields):
    if not isinstance(value, dict) or set(value) != set(fields):
        raise ValueError('unexpected or missing fields: expected '+str(sorted(fields)))


def check(value, kind, profile='stable'):
    if kind == 'nullable_sha':
        if value is not None: check(value,'sha',profile)
    elif kind in ('sha','git'):
        if not isinstance(value,str) or not re.fullmatch('[0-9a-f]{'+('64' if kind=='sha' else '40')+'}',value):
            raise ValueError('invalid '+kind+' identity')
    elif kind == 'text':
        if not isinstance(value,str) or not value.strip() or len(value)>2048 or any(ord(c)<32 for c in value):
            raise ValueError('invalid text')
    elif kind == 'code':
        if type(value) is not int or not 1 <= value <= 2100000000: raise ValueError('invalid version code')
    elif kind == 'package':
        if value != PROFILES[profile]['package']: raise ValueError('wrong package for selected channel')
    elif kind == 'aliases':
        if (not isinstance(value,list) or any(not isinstance(item,str) for item in value) or
            sorted(value) != PROFILES[profile]['aliases']): raise ValueError('wrong aliases for selected channel')
    elif kind == 'digests':
        if not isinstance(value,list): raise ValueError('expected digest list')
        for item in value: check(item,'sha')
    elif kind == 'indexes':
        if not isinstance(value,list) or not value: raise ValueError('missing indexes')
        names=[]
        for item in value:
            exact(item,('file','sha256')); check(item['sha256'],'sha')
            if not re.fullmatch(r'[A-Za-z0-9_.-]+',item['file']): raise ValueError('invalid index filename')
            names.append(item['file'])
        if len(set(names)) != len(names): raise ValueError('duplicate index')


def validate(value, kind=None, *, profile='stable'):
    if profile not in PROFILES: raise ValueError('unknown receipt channel')
    exact(value,('schema','kind','payload','evidence'))
    if type(value['schema']) is not int or value['schema'] != 2 or value['kind'] not in FIELDS or (kind and value['kind'] != kind):
        raise ValueError('wrong receipt schema or kind')
    fields=FIELDS[value['kind']]; exact(value['payload'],fields)
    for name, fieldtype in fields.items(): check(value['payload'][name],fieldtype,profile)
    payload=value['payload']
    if value['kind'] in ('release','repository'):
        cert=payload['apk_certificate_sha256']
        if profile=='trailblazer-beta' and cert!=PROFILES[profile]['apk_certificate']:
            raise ValueError('APK signer differs from selected channel')
        if profile=='stable' and cert==PROFILES['trailblazer-beta']['apk_certificate']:
            raise ValueError('Beta APK signer cannot be used by stable receipt channel')
    if not isinstance(value['evidence'],list): raise ValueError('invalid evidence')
    for entry in value['evidence']:
        exact(entry,('status','description','sha256'))
        if entry['status'] not in ('PASS','FAIL','NOT RUN','BLOCKED','UNKNOWN'): raise ValueError('invalid evidence status')
        check(entry['description'],'text')
        if entry['sha256'] is not None: check(entry['sha256'],'sha')
        if entry['status']=='PASS' and entry['sha256'] is None: raise ValueError('passed evidence needs digest')
    return value


def unique(pairs):
    result={}
    for key,value in pairs:
        if key in result: raise ValueError('duplicate JSON field')
        result[key]=value
    return result


def load(path, kind=None, *, profile='stable'):
    return validate(json.loads(path.read_text(),object_pairs_hook=unique),kind,profile=profile)


def digest(value, *, profile='stable'):
    validate(value,profile=profile)
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode()).hexdigest()


def import_legacy(final, provenance):
    """Import historical evidence without manufacturing source/build receipts."""
    import release_artifacts
    release_artifacts.validate_alias_provenance(final,provenance,final['commit'])
    if final.get('schema') != 1: raise ValueError('not a historical schema1 release')
    return {'schema':1,'commit':final['commit'],'version_code':final['version_code'],
            'source_receipt':'UNKNOWN','build_receipt':'UNKNOWN','independent_reproduction':'UNKNOWN',
            'instrumentation':final.get('instrumentation_checklist',{'status':'unknown'})}


def verify_release_inputs(source, build, release, approved, *, raw_manifest_sha256, promotion=True, profile='stable'):
    """Verify independently approved source/build/release before binary ingestion."""
    documents=dict(source=source,build=build,release=release)
    exact(approved,documents); check(raw_manifest_sha256,'sha')
    for kind,document in documents.items():
        validate(document,kind,profile=profile); check(approved[kind],'sha',profile)
        if digest(document,profile=profile)!=approved[kind]: raise ValueError('unapproved '+kind+' receipt')
        if promotion and (not document['evidence'] or any(e['status']!='PASS' for e in document['evidence'])):
            raise ValueError('promotion requires passed evidence for '+kind)
    s,b,r=(documents[k]['payload'] for k in ('source','build','release'))
    links=((b['source_receipt_sha256'],digest(source,profile=profile),'build source receipt'),
           (r['source_receipt_sha256'],digest(source,profile=profile),'release source receipt'),
           (r['build_receipt_sha256'],digest(build,profile=profile),'release build receipt'),
           (r['release_manifest_sha256'],raw_manifest_sha256,'raw final manifest'),
           (b['source_commit'],s['published_commit'],'build source commit'),
           (b['source_tree'],s['published_tree'],'build source tree'),
           (r['source_commit'],s['published_commit'],'release source commit'),
           (r['engineering_revision'],s['engineering_revision'],'release engineering revision'))
    for actual,expected,label in links:
        if actual!=expected:raise ValueError(label+' differs')
    return {kind:digest(document,profile=profile) for kind,document in documents.items()}


def verify_prepublication_repository_evidence(repository):
    """Require completed local-index and client gates, while recording HTTPS as postpublication."""
    evidence=repository['evidence']
    by_description={entry['description']:entry for entry in evidence}
    if (len(evidence)!=3 or len(by_description)!=3 or
        set(by_description)!=set(PREPUBLICATION_REPOSITORY_EVIDENCE.values())):
        raise ValueError('Beta prepublication repository evidence set differs')
    candidate=by_description[PREPUBLICATION_REPOSITORY_EVIDENCE['candidate_index']]
    client=by_description[PREPUBLICATION_REPOSITORY_EVIDENCE['client_acceptance']]
    production=by_description[PREPUBLICATION_REPOSITORY_EVIDENCE['production_readback']]
    if candidate['status']!='PASS' or candidate['sha256'] is None:
        raise ValueError('completed signed-index verification required before Beta preparation')
    if client['status']!='PASS' or client['sha256'] is None:
        raise ValueError('completed isolated client acceptance required before Beta preparation')
    if production['status']!='NOT RUN' or production['sha256'] is not None:
        raise ValueError('production HTTPS read-back belongs to the postpublication Pages journal')


def verify_graph(source, build, release, repository, approved, *, promotion=False, profile='stable', repository_stage=None):
    """Bind raw final-manifest bytes and canonical release-receipt identity separately.

    A legacy repository receipt may parse with null release_receipt_sha256, but
    cannot pass this complete-graph verifier or promote as new source evidence.
    """
    exact(approved,('source','build','release','repository'))
    validate(repository,'repository',profile=profile);check(approved['repository'],'sha',profile)
    if digest(repository,profile=profile)!=approved['repository']:raise ValueError('unapproved repository receipt')
    if repository_stage not in (None,'prepublication'):
        raise ValueError('unknown repository evidence stage')
    if profile=='trailblazer-beta' and promotion and repository_stage is None:
        raise ValueError('Beta promotion requires explicit prepublication repository evidence stage')
    if repository_stage=='prepublication':
        if profile!='trailblazer-beta' or not promotion:
            raise ValueError('prepublication repository stage is Beta promotion-only')
        verify_prepublication_repository_evidence(repository)
    elif promotion and (not repository['evidence'] or any(e['status']!='PASS' for e in repository['evidence'])):
        raise ValueError('promotion requires passed evidence for repository')
    p=repository['payload'];r=release['payload']
    result=verify_release_inputs(source,build,release,{k:approved[k] for k in ('source','build','release')},raw_manifest_sha256=p['release_manifest_sha256'],promotion=promotion,profile=profile)
    for actual,expected,label in ((p['release_receipt_sha256'],digest(release,profile=profile),'repository canonical release receipt'),
                                  (p['apk_sha256'],r['apk_sha256'],'repository APK'),
                                  (p['apk_certificate_sha256'],r['apk_certificate_sha256'],'repository APK signer')):
        if actual!=expected:raise ValueError(label+' differs')
    result['repository']=digest(repository,profile=profile)
    return result


def main():
    import argparse
    from pathlib import Path
    parser=argparse.ArgumentParser(description='Verify approved Collie receipt graph; no keys or deployment.')
    for kind in FIELDS: parser.add_argument('--'+kind,type=Path,required=True)
    parser.add_argument('--approvals',type=Path,required=True,help='independently authenticated kind-to-digest JSON')
    parser.add_argument('--promotion',action='store_true',help='reject empty or incomplete evidence')
    parser.add_argument('--channel',choices=tuple(PROFILES),default='stable',help='select one exact receipt identity profile; default preserves stable behavior')
    parser.add_argument('--repository-stage',choices=('prepublication',),help='allow only the explicit Beta prepublication evidence boundary')
    args=parser.parse_args()
    try:
        documents={kind:load(getattr(args,kind),kind,profile=args.channel) for kind in FIELDS}
        approved=json.loads(args.approvals.read_text(),object_pairs_hook=unique)
        print(json.dumps(verify_graph(**documents,approved=approved,promotion=args.promotion,profile=args.channel,
                                      repository_stage=args.repository_stage),sort_keys=True))
    except (ValueError,OSError) as exc:
        parser.exit(1,str(exc)+'\n')
    return 0


if __name__=='__main__': raise SystemExit(main())
