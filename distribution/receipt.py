#!/usr/bin/env python3
"""Emit a repository receipt for one explicitly selected package channel."""
import argparse
import json
from pathlib import Path
import re
import binary_repo as b
import channel_profile as channels

p=argparse.ArgumentParser()
p.add_argument('repo',type=Path)
p.add_argument('--approval',required=True,type=Path)
p.add_argument('--client-evidence',type=Path)
p.add_argument('--receipt-bundle',type=Path)
p.add_argument('--metadata-revision',required=True)
p.add_argument('--fingerprint',required=True)
p.add_argument('--generation',required=True)
p.add_argument('--channel',choices=('stable','trailblazer-beta'),default='stable')
p.add_argument('--repository-stage',choices=('prepublication',),help='explicitly produce a Beta candidate receipt before Pages publication')
a=p.parse_args()
if not re.fullmatch('[0-9a-f]{40}',a.metadata_revision):raise ValueError('immutable metadata commit required')
profile=channels.STABLE if a.channel=='stable' else channels.BETA
if profile.name=='trailblazer-beta' and a.repository_stage!='prepublication':
    raise ValueError('Beta repository receipt requires --repository-stage prepublication')
if profile.name=='stable' and a.repository_stage is not None:
    raise ValueError('repository stage is available only for the Beta channel')
approval=b.approval(a.approval,profile,allow_canonical_stable=True)
b.verify_release_approval(approval,a.receipt_bundle,profile)
generation,data=b.approved_generation(a.repo,a.fingerprint,a.generation,[a.approval],profile,allow_canonical_stable=True)
if profile.name!='stable' and data['repo']['address'].rstrip('/')!=profile.repository_url.rstrip('/'):
    raise ValueError('signed index origin differs from selected channel')
versions=data['packages'][profile.package]['versions']
if not any(v['file']['sha256']==approval['apk_sha256'] for v in versions.values()):
    raise ValueError('approved APK missing')
if profile.name=='trailblazer-beta':
    evidence=[{'status':'PASS','description':'Pinned fdroidserver verifies repository v1/v2 signatures, entry digest and indexed local APK hashes (prepublication)','sha256':b.sha(a.repo/'entry.jar')},
              {'status':'NOT RUN','description':'Operator-recorded isolated client add/refresh/install/upgrade evidence; not independent build reproduction','sha256':None},
              {'status':'NOT RUN','description':'Production HTTPS read-back and provider cache headers (postpublication Pages journal; not run before candidate preparation)','sha256':None}]
else:
    evidence=[{'status':'PASS','description':'Pinned fdroidserver verifies repository v1/v2 signatures, entry digest and indexed local APK hashes','sha256':b.sha(a.repo/'entry.jar')},
              {'status':'NOT RUN','description':'Official F-Droid and maintained compatible client device install/update acceptance','sha256':None},
              {'status':'NOT RUN','description':'Production HTTPS read-back and provider cache headers','sha256':None}]
if a.client_evidence:
    evidence[1]={'status':'PASS','description':'Operator-recorded isolated client add/refresh/install/upgrade evidence; not independent build reproduction','sha256':b.sha(a.client_evidence)}
receipt={'schema':2,'kind':'repository','payload':{
    'release_manifest_sha256':approval['release_manifest_sha256'],
    'release_receipt_sha256':approval.get('release_receipt_sha256'),
    'apk_sha256':approval['apk_sha256'],'apk_certificate_sha256':approval['apk_certificate_sha256'],
    'repository_certificate_sha256':a.fingerprint.lower(),
    'fdroidserver_revision':'PyPI fdroidserver=='+b.SERVER_VERSION,
    'metadata_revision':a.metadata_revision,
    'indexes':[{'file':item.name,'sha256':b.sha(item)} for item in sorted(a.repo.glob('*'))
               if item.suffix in {'.jar','.json'}],
    'generation':generation},'evidence':evidence}
print(json.dumps(receipt,indent=2))
