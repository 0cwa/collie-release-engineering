#!/usr/bin/env python3
"""Versioned migration handoffs; implementation claims are not attestations."""
import hashlib
import json
import re

FIELDS = {
 'source': {'upstream_commit':'git','engineering_revision':'git','generator_revision':'git','verifier_revision':'git','layers':'digests','reconstructed_tree':'git','overlays':'digests','published_commit':'git','published_tree':'git'},
 'build': {'source_receipt_sha256':'sha','source_commit':'git','source_tree':'git','recipe_revision':'git','toolchain_sha256':'sha','dependencies_sha256':'sha','native_inputs_sha256':'sha','unsigned_apk_sha256':'sha','trust_level':'text'},
 'release': {'source_receipt_sha256':'sha','build_receipt_sha256':'sha','source_commit':'git','engineering_revision':'git','workflow_revision':'git','package':'package','version_code':'code','version_name':'text','apk_sha256':'sha','apk_certificate_sha256':'sha','aliases':'aliases'},
 'repository': {'release_manifest_sha256':'sha','apk_sha256':'sha','apk_certificate_sha256':'sha','repository_certificate_sha256':'sha','fdroidserver_revision':'text','metadata_revision':'git','indexes':'indexes','generation':'text'},
}
ALIASES = sorted(x+'.apk' for x in ('collie','collie-foss','molly-continued','molly-foss-continued','molly-minimal'))


def exact(value, fields):
    if not isinstance(value, dict) or set(value) != set(fields):
        raise ValueError('unexpected or missing fields: expected '+str(sorted(fields)))


def check(value, kind):
    if kind in ('sha','git'):
        if not isinstance(value,str) or not re.fullmatch('[0-9a-f]{'+('64' if kind=='sha' else '40')+'}',value):
            raise ValueError('invalid '+kind+' identity')
    elif kind == 'text':
        if not isinstance(value,str) or not value.strip() or len(value)>2048 or any(ord(c)<32 for c in value):
            raise ValueError('invalid text')
    elif kind == 'code':
        if type(value) is not int or not 1 <= value <= 2100000000: raise ValueError('invalid version code')
    elif kind == 'package':
        if value != 'im.collie.app': raise ValueError('wrong package')
    elif kind == 'aliases':
        if not isinstance(value,list) or sorted(value) != ALIASES: raise ValueError('wrong aliases')
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


def validate(value, kind=None):
    exact(value,('schema','kind','payload','evidence'))
    if type(value['schema']) is not int or value['schema'] != 2 or value['kind'] not in FIELDS or (kind and value['kind'] != kind):
        raise ValueError('wrong receipt schema or kind')
    fields=FIELDS[value['kind']]; exact(value['payload'],fields)
    for name, fieldtype in fields.items(): check(value['payload'][name],fieldtype)
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


def load(path, kind=None):
    return validate(json.loads(path.read_text(),object_pairs_hook=unique),kind)


def digest(value):
    validate(value)
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode()).hexdigest()


def import_legacy(final, provenance):
    """Import historical evidence without manufacturing source/build receipts."""
    import release_artifacts
    release_artifacts.validate_alias_provenance(final,provenance,final['commit'])
    if final.get('schema') != 1: raise ValueError('not a historical schema1 release')
    return {'schema':1,'commit':final['commit'],'version_code':final['version_code'],
            'source_receipt':'UNKNOWN','build_receipt':'UNKNOWN','independent_reproduction':'UNKNOWN',
            'instrumentation':final.get('instrumentation_checklist',{'status':'unknown'})}


def verify_graph(source, build, release, repository, approved, *, promotion=False):
    """Bind supplied receipts to external digest approvals and each other.

    PASS claims are not attestations: an operator still authenticates evidence and
    signer authorization separately. Historical UNKNOWN evidence cannot promote.
    """
    documents=dict(source=source,build=build,release=release,repository=repository)
    exact(approved,documents)
    for kind,document in documents.items():
        validate(document,kind); check(approved[kind],'sha')
        if digest(document) != approved[kind]:
            raise ValueError('unapproved '+kind+' receipt')
        if promotion and (not document['evidence'] or any(e['status']!='PASS' for e in document['evidence'])):
            raise ValueError('promotion requires passed evidence for '+kind)
    s,b,r,p=(documents[k]['payload'] for k in ('source','build','release','repository'))
    links=(
        (b['source_receipt_sha256'],digest(source),'build source receipt'),
        (r['source_receipt_sha256'],digest(source),'release source receipt'),
        (r['build_receipt_sha256'],digest(build),'release build receipt'),
        (p['release_manifest_sha256'],digest(release),'repository release receipt'),
        (b['source_commit'],s['published_commit'],'build source commit'),
        (b['source_tree'],s['published_tree'],'build source tree'),
        (r['source_commit'],s['published_commit'],'release source commit'),
        (r['engineering_revision'],s['engineering_revision'],'release engineering revision'),
        (p['apk_sha256'],r['apk_sha256'],'repository APK'),
        (p['apk_certificate_sha256'],r['apk_certificate_sha256'],'repository APK signer'),
    )
    for actual,expected,label in links:
        if actual != expected: raise ValueError(label+' differs')
    return {kind:digest(document) for kind,document in documents.items()}


def main():
    import argparse
    from pathlib import Path
    parser=argparse.ArgumentParser(description='Verify approved Collie receipt graph; no keys or deployment.')
    for kind in FIELDS: parser.add_argument('--'+kind,type=Path,required=True)
    parser.add_argument('--approvals',type=Path,required=True,help='independently authenticated kind-to-digest JSON')
    parser.add_argument('--promotion',action='store_true',help='reject empty or incomplete evidence')
    args=parser.parse_args()
    try:
        documents={kind:load(getattr(args,kind),kind) for kind in FIELDS}
        approved=json.loads(args.approvals.read_text(),object_pairs_hook=unique)
        print(json.dumps(verify_graph(**documents,approved=approved,promotion=args.promotion),sort_keys=True))
    except (ValueError,OSError) as exc:
        parser.exit(1,str(exc)+'\n')
    return 0


if __name__=='__main__': raise SystemExit(main())
