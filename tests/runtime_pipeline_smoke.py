#!/usr/bin/env python3
"""Exercise actual collect/sign/publish runtime with a disposable test key/APK.

Resource-only fixture APK, synthetic Git source and typed test reports are NOT
an Android build of Collie G, production provenance, or native reproduction.
Only GitHub transport is mocked; APK metadata/alignment/signature tools run.
"""
import argparse
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import types
from unittest.mock import patch

ROOT=Path(__file__).parents[1];sys.path.insert(0,str(ROOT/'tools/patches'))
import release_artifacts as a
import release_collect as collect
import release_sign as sign
import release_publish as publish
import migration_receipts as receipts
import runtime_receipts as runtime

p=argparse.ArgumentParser();p.add_argument('--build-tools',required=True,type=Path);p.add_argument('--android-jar',required=True,type=Path);p.add_argument('--scratch',required=True,type=Path);args=p.parse_args()
args.scratch.mkdir(parents=True,exist_ok=False);root=args.scratch.resolve();tools=args.build_tools.resolve();source=root/'fixture-source';source.mkdir();unsigned=root/'unsigned';unsigned.mkdir();signed=root/'signed'
def run(cmd,**kwargs):return subprocess.run([str(x) for x in cmd],check=True,**kwargs)
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def write(name,obj):path=root/name;path.write_text(json.dumps(obj,sort_keys=True,indent=2)+'\n');return path
manifest=source/'AndroidManifest.xml';manifest.write_text('<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="im.collie.app" android:versionCode="177703" android:versionName="fixture-next"><uses-sdk android:minSdkVersion="27" android:targetSdkVersion="36"/><application android:label="Runtime fixture" android:debuggable="false"/></manifest>')
run(['git','init','-q',source]);run(['git','-C',source,'add','.']);run(['git','-C',source,'-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-qm','Resource-only runtime fixture source'])
g=subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD'],text=True).strip();tree=subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD^{tree}'],text=True).strip()
run([tools/'aapt2','link','--manifest',manifest,'-I',args.android_jar.resolve(),'-o',unsigned/'collie.apk'])
keystore=root/'test-only.p12';env=os.environ.copy();env.update(MOLLY_KEYSTORE_PASSWORD='fixture-password',MOLLY_KEY_PASSWORD='fixture-password',MOLLY_KEYSTORE=str(keystore),MOLLY_KEY_ALIAS='fixture')
run(['keytool','-genkeypair','-keystore',keystore,'-storetype','PKCS12','-storepass:env','MOLLY_KEYSTORE_PASSWORD','-keypass:env','MOLLY_KEY_PASSWORD','-alias','fixture','-keyalg','RSA','-keysize','3072','-validity','3','-dname','CN=DISPOSABLE RUNTIME FIXTURE'],env=env,stdout=subprocess.DEVNULL)
cert=subprocess.check_output(['keytool','-exportcert','-keystore',str(keystore),'-storepass:env','MOLLY_KEYSTORE_PASSWORD','-alias','fixture'],env=env);certsha=hashlib.sha256(cert).hexdigest()
config=write('config.json',{'android':{'build_tools':tools.name}})
source_receipt={'schema':2,'kind':'source','payload':{'upstream_commit':g,'engineering_revision':'b'*40,'generator_revision':'d'*40,'verifier_revision':'e'*40,'layers':[],'reconstructed_tree':tree,'overlays':[],'published_commit':g,'published_tree':tree},'evidence':[{'status':'PASS','description':'Synthetic resource-only fixture source identity, not Collie G/Android evidence','sha256':sha(manifest)}]}
src=write('source.json',source_receipt)
tool=write('toolchain.json',{'fixture_aapt2':str(tools/'aapt2'),'java':subprocess.run(['java','-version'],capture_output=True,text=True,check=True).stderr})
deps=write('dependencies.json',{'scope':'resource-only fixture','android_jar_sha256':sha(args.android_jar)})
native=write('native.json',{'scope':'fixture APK has no native libraries; not native acceptance'})
inputs=write('build-inputs.json',{'schema':1,'recipe_revision':'f'*40,'config_sha256':sha(config),'toolchain_sha256':sha(tool),'dependencies_sha256':sha(deps),'native_inputs_sha256':sha(native),'trust_level':'fixture only; not Collie G Android compilation or independent native reproduction'})
test=root/'executed-test.txt';test.write_text('Fixture integrity check executed: resource-only APK created and source tracked tree checked.\n')
reports=write('reports.json',{'schema':1,'reports':[{'name':'fixture-integrity','status':'PASS','description':'Actual fixture report bytes, not application tests','file':test.name,'sha256':sha(test)}]})
plan=write('release-plan.json',{'schema':1,'kind':'release-plan','mode':'new-source','purpose':'fixture','source_commit':g,'engineering_revision':'b'*40,'workflow_revision':'c'*40,'package':'im.collie.app','version_code':177703,'version_name':'fixture-next','certificate_sha256':certsha,'source_receipt_sha256':receipts.digest(source_receipt),'build_inputs_sha256':sha(inputs),'required_test_reports':['fixture-integrity']})
common=['--release-plan',str(plan),'--approved-release-plan-sha256',sha(plan),'--source-receipt',str(src),'--build-inputs',str(inputs)]
results=[]
def invoke(module,argv,**mocks):
    with patch('sys.argv',[module.__name__]+[str(x) for x in argv]),patch.dict(os.environ,env),contextlib.redirect_stdout(io.StringIO()):return module.main()
cargs=['--directory',unsigned,'--build-tools',tools,'--config',config,'--commit',g,'--workflow-revision','c'*40,'--run-id','1','--run-attempt','1','--version-name','fixture-next','--version-code','177703','--certificate-sha256',certsha,'--single-collie','--source-checkout',source,'--toolchain-input',tool,'--dependency-input',deps,'--native-input',native,'--test-reports',reports]+common
assert invoke(collect,cargs)==0
build=unsigned/'build-receipt.json';b=receipts.load(build,'build');assert b['payload']['unsigned_apk_sha256']==sha(unsigned/'collie.apk');results.append('PASS actual collector binds source checkout/input/report bytes and unsigned fixture APK')
checklist=write('instrumentation.json',{'commit':g,'status':'not run','evidence':'','reason':'resource-only runtime fixture; no device/account/native proof'})
inventory=write('inventory.json',[{'release':'fixture-old','code':177702,'sha256':'a'*64}])
sargs=['--manifest',unsigned/'manifest.json','--manifest-sha256',sha(unsigned/'manifest.json'),'--approved-commit',g,'--approved-run','1','--certificate-sha256',certsha,'--apks',unsigned,'--output',signed,'--build-tools',tools,'--scratch',root,'--instrumentation-checklist',checklist,'--version-ledger',root/'versions.json','--published-inventory',inventory,'--published-inventory-sha256',sha(inventory),'--build-receipt',build,'--approved-build-receipt-sha256',receipts.digest(b)]+common
with patch.object(sign,'run',wraps=sign.run) as real_tools:
 assert invoke(sign,sargs)==0
 assert sum(len(call.args[0])>1 and str(call.args[0][0]).endswith('/apksigner') and call.args[0][1]=='sign' for call in real_tools.call_args_list)==1
assert len({sha(signed/(name+'.apk')) for name in a.PROFILES})==1
release=signed/'release-receipt.json';r=receipts.load(release,'release');assert r['payload']['release_manifest_sha256']==sha(signed/'final-manifest.json');results.append('PASS actual test-key signing once, five identical aliases, post-final raw/canonical release receipt')
# Missing/substituted graph inputs must fail before any key/sign tool invocation.
for label,path,change in [('source substitution',src,lambda d:d['payload'].update(published_tree='a'*40)),('build substitution',build,lambda d:d['payload'].update(native_inputs_sha256='a'*64))]:
 original=path.read_bytes();d=json.loads(original);change(d);path.write_text(json.dumps(d))
 try:
  argv=list(sargs);argv[argv.index('--output')+1]=root/('rejected-'+label.replace(' ','-'))
  with patch.object(sign,'run') as key_access:assert invoke(sign,argv)==1;key_access.assert_not_called()
 finally:path.write_bytes(original)
for flag in ('--source-receipt','--build-receipt','--release-plan'):
 argv=list(sargs);i=argv.index(flag);del argv[i:i+2];argv[argv.index('--output')+1]=root/('missing-'+flag[2:])
 with patch.object(sign,'run') as key_access:assert invoke(sign,argv)==1;key_access.assert_not_called()
# Valid-looking replacement approvals cannot substitute plan/build/APK identities.
for label,flag,value in [('fake-plan-approval','--approved-release-plan-sha256','0'*64),('fake-build-approval','--approved-build-receipt-sha256','0'*64)]:
 argv=list(sargs);argv[argv.index(flag)+1]=value;argv[argv.index('--output')+1]=root/label
 with patch.object(sign,'run') as key_access:assert invoke(sign,argv)==1;key_access.assert_not_called()
apk=unsigned/'collie.apk';original=apk.read_bytes();apk.write_bytes(original+b'substitution')
try:
 argv=list(sargs);argv[argv.index('--output')+1]=root/'unsigned-substitution'
 with patch.object(sign,'run') as key_access:assert invoke(sign,argv)==1;key_access.assert_not_called()
finally:apk.write_bytes(original)
results.append('PASS missing/substituted source/build/plan/APK and fake approval digests rejected before key access; no legacy fallback')
# Mock only GitHub transport; real publication code verifies exact assets/readback.
remote={};calls=[];interrupt=True
pcommon=['--build-receipt',build,'--approved-build-receipt-sha256',receipts.digest(b),'--release-receipt',release,'--approved-release-receipt-sha256',receipts.digest(r)]+common
pargs=['publish','--repo','fixture/runtime','--tag','vfixture','--approved-commit',g,'--final-manifest',signed/'final-manifest.json','--final-manifest-sha256',sha(signed/'final-manifest.json'),'--provenance',unsigned/'manifest.json','--notes',write('notes.json',{'scope':'fixture only'}),'--scratch',root]+pcommon
# Create only in mocked transport through real draft path; signed tag verification
# is also mocked by its underlying GH API responses, never by the verifier.
def transport(cmd,dry=False,**kwargs):
 global interrupt
 cmd=[str(x) for x in cmd];calls.append(cmd)
 if cmd[1]=='api':
  if '/git/ref/tags/' in cmd[2]:return types.SimpleNamespace(stdout=json.dumps({'object':{'type':'tag','sha':'1'*40}}))
  return types.SimpleNamespace(stdout=json.dumps({'tag':'vfixture','object':{'type':'commit','sha':g},'verification':{'verified':True}}))
 action=cmd[2]
 if action=='view':
  if not remote:raise subprocess.CalledProcessError(1,cmd,stderr='release not found')
  return types.SimpleNamespace(stdout=json.dumps({'isDraft':remote['draft'],'isPrerelease':False,'assets':[{'name':n} for n in remote['assets']]}))
 if action=='create':
  paths=[Path(x) for x in cmd if Path(x).is_file()];remote.update(draft=True,assets={p.name:p.read_bytes() for p in paths if p.name!='notes.json'})
 if action=='download':
  dest=Path(cmd[cmd.index('--dir')+1])
  for name,data in remote['assets'].items():(dest/name).write_bytes(data)
 if action=='edit':
  remote['draft']=False
  if interrupt:interrupt=False;raise subprocess.CalledProcessError(1,cmd,stderr='fixture lost response after publication')
 return types.SimpleNamespace(stdout='')
with patch.object(publish,'command',side_effect=transport):
 draft=list(pargs);draft[0]='draft';assert invoke(publish,draft)==0
 assert invoke(publish,pargs)==1
 assert invoke(publish,pargs)==0
 edits=sum(c[1:3]==['release','edit'] for c in calls);assert invoke(publish,pargs)==0;assert sum(c[1:3]==['release','edit'] for c in calls)==edits
 for label,path in [('release',release),('raw final',signed/'final-manifest.json')]:
  original=path.read_bytes();path.write_bytes(original+b' ')
  argv=list(pargs)
  # A raw final substitution with an updated raw approval still fails the release link.
  if label=='raw final':argv[argv.index('--final-manifest-sha256')+1]=sha(path)
  if label=='release':
   changed=json.loads(original);changed['payload']['release_manifest_sha256']='a'*64;path.write_text(json.dumps(changed));argv[argv.index('--approved-release-receipt-sha256')+1]=receipts.digest(changed)
  try:
   with patch.object(publish,'command') as api:assert invoke(publish,argv)==1;api.assert_not_called()
  finally:path.write_bytes(original)
for flag in ('--source-receipt','--build-receipt','--release-receipt'):
 argv=list(pargs);i=argv.index(flag);del argv[i:i+2]
 with patch.object(publish,'command') as api:assert invoke(publish,argv)==1;api.assert_not_called()
results.append('PASS actual publication graph checks before API, raw/release substitutions fail, interrupted published response resumes exact bytes')
approved={'source':receipts.digest(source_receipt),'build':receipts.digest(b),'release':receipts.digest(r)}
receipts.verify_release_inputs(source_receipt,b,r,approved,raw_manifest_sha256=sha(signed/'final-manifest.json'),promotion=True)
for name,doc in [('source.json',source_receipt),('build.json',b),('release.json',r)]:
 assert receipts.digest(json.loads(remote['assets'][name]))==receipts.digest(doc)
results.append('PASS same canonical source/build/release graph exported for existing distribution ingestion')
print(json.dumps({'scope':'Actual runtime functions, tiny resource-only fixture APK, disposable test key and mocked GH transport; NOT a Collie G Android build/native/device proof','source_commit':g,'engineering_revision':'b'*40,'workflow_revision':'c'*40,'build_receipt_sha256':receipts.digest(b),'release_receipt_sha256':receipts.digest(r),'checks':results},indent=2))
