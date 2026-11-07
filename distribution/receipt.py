"""Emit the shared schema-2 repository receipt after signed local read-back."""
import argparse
import json
from pathlib import Path
import re
import binary_repo as b
p=argparse.ArgumentParser();p.add_argument('repo',type=Path);p.add_argument('--approval',required=True,type=Path);p.add_argument('--metadata-revision',required=True);p.add_argument('--fingerprint',required=True);p.add_argument('--generation',required=True);a=p.parse_args()
if not re.fullmatch('[0-9a-f]{40}',a.metadata_revision):raise ValueError('immutable metadata commit required')
approval=b.approval(a.approval);generation,data=b.approved_generation(a.repo,a.fingerprint,a.generation,[a.approval])
versions=data['packages'][b.APP]['versions']
if not any(v['file']['sha256']==approval['apk_sha256'] for v in versions.values()):raise ValueError('approved APK missing')
receipt={'schema':2,'kind':'repository','payload':{'release_manifest_sha256':approval['release_manifest_sha256'],'apk_sha256':approval['apk_sha256'],'apk_certificate_sha256':approval['apk_certificate_sha256'],'repository_certificate_sha256':a.fingerprint.lower(),'fdroidserver_revision':'PyPI fdroidserver=='+b.SERVER_VERSION,'metadata_revision':a.metadata_revision,'indexes':[{'file':p.name,'sha256':b.sha(p)} for p in sorted(a.repo.glob('*')) if p.suffix in {'.jar','.json'}],'generation':generation},'evidence':[{'status':'PASS','description':'Pinned fdroidserver verifies repository v1/v2 signatures, entry digest and indexed local APK hashes','sha256':b.sha(a.repo/'entry.jar')},{'status':'NOT RUN','description':'Official F-Droid and maintained compatible client device install/update acceptance','sha256':None},{'status':'NOT RUN','description':'Production HTTPS read-back and provider cache headers','sha256':None}]}
print(json.dumps(receipt,indent=2))
