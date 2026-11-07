"""Staging harness. Disposable index key only; no APK signing or production access."""
import argparse
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import binary_repo as b

p=argparse.ArgumentParser();p.add_argument('--approval',type=Path,required=True);p.add_argument('--manifest',type=Path,required=True);p.add_argument('--apk',type=Path,required=True);p.add_argument('--workspace',type=Path,required=True);p.add_argument('--sdk',type=Path,required=True);a=p.parse_args()
if importlib.metadata.version('fdroidserver')!=b.SERVER_VERSION: raise ValueError('fdroidserver pin mismatch')
w=a.workspace;w.mkdir(parents=True,exist_ok=True);os.chmod(w,0o700)
b.ingest(a.approval,a.manifest,a.apk,w,str(a.sdk/'aapt'),str(a.sdk/'apksigner'))
key=w/'staging-only.p12'
if not key.exists():
    subprocess.run(['keytool','-genkeypair','-keystore',str(key),'-storetype','PKCS12','-alias','staging','-storepass','staging-test-only','-keypass','staging-test-only','-keyalg','RSA','-keysize','3072','-validity','30','-dname','CN=Collie disposable staging key'],check=True)
public=subprocess.check_output(['keytool','-exportcert','-keystore',str(key),'-storepass','staging-test-only','-alias','staging'])
import hashlib
fp=hashlib.sha256(public).hexdigest()
# Fixed localhost staging identity, never production endpoint.
(w/'config.yml').write_text(f'''repo_url: http://127.0.0.1:8765/fdroid/repo
repo_name: Collie disposable staging
repo_description: Local test only, not a production repository.
archive_older: 0
make_current_version_link: false
sdk_path: {a.sdk.parent.parent}
aapt: {a.sdk/'aapt'}
apksigner: {a.sdk/'apksigner'}
keystore: {key}
repo_keyalias: staging
keystorepass: staging-test-only
keypass: staging-test-only
''')
os.chmod(w/'config.yml',0o600)
subprocess.run([str(Path(sys.executable).parent/'fdroid'),'update','--pretty'],cwd=w,check=True)
b.verify_indexes(w/'repo',fp)
print(json.dumps({'fdroidserver':b.SERVER_VERSION,'repository_certificate_sha256':fp,'apk_sha256':b.sha(a.apk),'indexes':{p.name:b.sha(p) for p in (w/'repo').glob('*') if p.suffix in ('.jar','.json') }},indent=2))
