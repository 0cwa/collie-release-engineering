#!/usr/bin/env python3
"""Materialize native dependencies as ordinary tracked build inputs before compilation.

The proposed immutable repository is NOT PUBLISHED. This layer never activates
Android builds, fetches native binaries, uploads anything, or uses an init script.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import tomllib
import sys
import subprocess
import xml.etree.ElementTree as ET
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'tools/native'))
import publication_plan
import stage_artifacts

ORIGIN='https://repo.collie.im/native-inputs/v1/maven/'
SETTINGS_MARKER='// Collie native repository: reviewed source input, NOT PUBLISHED.'
BUILD_MARKER='// Collie native substitutions: ordinary tracked project configuration.'


def repository_block(lock):
    modules='\n'.join(f'        includeModule("{lock["output_group"]}", "{m["name"]}")' for m in lock['modules'])
    return f'''    {SETTINGS_MARKER}
    exclusiveContent {{
      forRepository {{
        maven {{
          name = "CollieNativeInputs"
          url = uri("{ORIGIN}")
          metadataSources {{ mavenPom(); ignoreGradleMetadataRedirection() }}
        }}
      }}
      filter {{
{modules}
      }}
    }}
'''


def substitution_block(lock):
    lines='\n'.join(f'        substitute(module("{group}:{m["name"]}")).using(module("{lock["output_group"]}:{m["name"]}:{m["output_version"]}"))'
                    for group in ('im.molly','org.signal') for m in lock['modules'])
    return f'''\n{BUILD_MARKER}
allprojects {{
  configurations.configureEach {{
    resolutionStrategy.dependencySubstitution {{
{lines}
    }}
  }}
}}
'''


def validate_plan(lock,plan,metadata):
    if plan.get('schema')!=1 or plan.get('kind')!='native-publication-plan' or plan.get('status')!='NOT PUBLISHED' or plan.get('repository')!=ORIGIN:
        raise ValueError('native plan must retain the reviewed NOT PUBLISHED origin')
    if hashlib.sha256(metadata).hexdigest()!=plan.get('verification_sha256'):
        raise ValueError('strict native verification metadata differs from plan')
    by_path={f['path']:f for f in plan['files']}
    expected={}
    for m in lock['modules']:
        for ext in (m['extension'],'pom'):
            name=f'{m["name"]}-{m["output_version"]}.{ext}'
            path=f'{lock["output_group"].replace(".","/")}/{m["name"]}/{m["output_version"]}/{name}'
            expected[path]=(m,ext)
    if len(plan['files'])!=6 or set(by_path)!=set(expected):raise ValueError('exact six native files required')
    for path,(module,ext) in expected.items():
        f=by_path[path]
        if f.get('url')!=ORIGIN+path or type(f.get('bytes')) is not int or f['bytes']<=0 or not re.fullmatch('[0-9a-f]{64}',f.get('sha256','')):
            raise ValueError('native coordinate/URL/hash/size differs')
        if ext!='pom' and f['sha256']!=module['binary_sha256']:raise ValueError('native binary substitution')
    xml=ET.fromstring(metadata)
    ns=stage_artifacts.GRADLE
    if xml.findtext(ns+'configuration/'+ns+'verify-metadata')!='true':raise ValueError('POM verification must remain enabled')
    for module in lock['modules']:
        component=[c for c in xml.findall(ns+'components/'+ns+'component') if (c.get('group'),c.get('name'),c.get('version'))==(lock['output_group'],module['name'],module['output_version'])]
        if len(component)!=1:raise ValueError('native verification component missing/duplicate')
        for ext in (module['extension'],'pom'):
            filename=f'{module["name"]}-{module["output_version"]}.{ext}'
            values=[a for a in component[0].findall(ns+'artifact') if a.get('name')==filename]
            if len(values)!=1:raise ValueError('native artifact verification missing/duplicate')
            digests=[x.get('value') for x in values[0].findall(ns+'sha256')]
            expected_hash=next(f['sha256'] for path,f in by_path.items() if path.endswith('/'+filename))
            if digests!=[expected_hash]:raise ValueError('native binary/POM hash differs')


def materialize(files,lock,plan,metadata):
    validate_plan(lock,plan,metadata)
    result=dict(files)
    settings=files['settings.gradle.kts'].decode()
    if SETTINGS_MARKER in settings or BUILD_MARKER in files['build.gradle.kts'].decode():raise ValueError('native layer already present')
    local='''    mavenLocal {
      content {
        includeGroup("im.molly")
        includeGroup("org.signal")
      }
    }
'''
    if settings.count(local)!=1:raise ValueError('reviewed mavenLocal block changed')
    settings=settings.replace(local,'')
    anchor='''dependencyResolutionManagement {
  repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
  repositories {
'''
    if settings.count(anchor)!=1:raise ValueError('repository ownership changed')
    settings=settings.replace(anchor,anchor+repository_block(lock))
    start=settings.index('// To build libsignal from source,')
    end=settings.index('// Main app',start)
    settings=settings[:start]+'// Native inputs use the pinned public repository; no external source includeBuild.\n\n'+settings[end:]
    if 'mavenLocal' in settings or 'libsignalClientPath' in settings:raise ValueError('ambient native input remains')
    result['settings.gradle.kts']=settings.encode()
    result['build.gradle.kts']=files['build.gradle.kts'].decode().rstrip().encode()+b'\n'+substitution_block(lock).encode()
    catalog=tomllib.loads(files['gradle/libs.versions.toml'].decode())
    if catalog.get('versions',{}).get('libsignal-client')!='0.106.1-collie.1':raise ValueError('canonical native version pin differs')
    for name in ('libsignal-client','libsignal-android'):
        if catalog.get('libraries',{}).get(name)!={'module':'im.collie.native:'+name,'version':{'ref':'libsignal-client'}}:raise ValueError('canonical native catalog pin differs')
    if catalog.get('libraries',{}).get('signal-ringrtc')!='im.collie.native:ringrtc-android:2.74.0-collie.1':raise ValueError('canonical RingRTC catalog pin differs')
    result['gradle/verification-metadata.xml']=metadata
    state=json.loads(files['build-inputs/native-publication.json'])
    if state['status']!='BLOCKED':raise ValueError('native acceptance gate unexpectedly changed')
    state.update(repository=ORIGIN,publication_status='NOT PUBLISHED',
                 reason='Tracked native coordinates/repository/substitutions/strict verification are materialized. Exact public bytes and direct Android acceptance remain unverified; gate stays unconditional.')
    result['build-inputs/native-publication.json']=(json.dumps(state,indent=2)+'\n').encode()
    result['build-inputs/native-artifacts.json']=(json.dumps(plan,indent=2)+'\n').encode()
    result['build-inputs/host-test-inputs.json']=(json.dumps({
        'schema':1,'status':'NOT RUN','test_only':True,'never_package_in_apk':True,
        'platform':'Linux x86-64','resource':'libsignal_jni_testing_amd64.so',
        'public_url':None,'complete_host_build_recipe':None,
        'historical_local_candidate_sha256':'b3f94489c68433abfb00883c9a4579565cfd6db820fcbd006b1ca581beaf9d8e',
        'historical_receipt_lock_sha256':'9866196dec36f096d2757bbf9e9d774c7147548542dd2a041e6438f79dbd73e4',
        'prepared_libsignal_tree':'de930179286b4fe68875475c1b0678615e180908',
        'staging_guard':{'origin':'https://github.com/0cwa/collie-release-engineering.git',
                        'revision':'808ee6357a9227f780faa605eddad48b44136c70',
                        'path':'tools/native/host_jni_guard.py',
                        'sha256':'038f9553916d93baf2a6919d19c9146f6486ccfec1dacf8eee78b64d595db538'},
        'limitation':'Historical local test input uses an older lock. Not current accepted host-test evidence; no public host binary or complete build recipe. Six production Maven files do not establish native JVM regression readiness.'},indent=2)+'\n').encode()

    props=files['gradle.properties'].decode()
    props=props.replace('# Uncomment these to build libsignal from source.\n# libsignalClientPath=../libsignal\n# org.gradle.dependency.verification=lenient\n','')
    result['gradle.properties']=props.encode()
    if b'throw GradleException("Direct Android builds BLOCKED:' not in files['app/build.gradle.kts']:
        raise ValueError('unconditional Android build gate missing')
    # This layer intentionally never edits app/build.gradle.kts or the gate.
    build=files['BUILD.md'].decode()
    build=build.replace('Obtain the three locked artifacts and POMs from approved\npublic immutable URLs, verify all hashes, materialize their exclusive repository\nand verification entries in source, then run a fresh controlled Gradle build.',
        'The three locked binaries and transformed POMs now have tracked exclusive repository, catalog, substitutions and strict verification entries. The proposed origin in build-inputs/native-artifacts.json is NOT PUBLISHED. Obtain operator publication approval, independently read back all six exact files, review a separate acceptance/gate layer, then run a fresh controlled Gradle build. No native init script, local Maven lookup or external native source includeBuild is part of this direct-source recipe.')
    build+='\nNative JVM regression readiness is separate: build-inputs/host-test-inputs.json records the historical Linux testing-JNI candidate and immutable staging-guard recipe. No public host-test input or complete host build recipe is supplied; those tests are NOT RUN, not skipped/passed. Do not package test JNI in a release or treat the six production Maven files as all-test readiness.\n'
    result['BUILD.md']=build.encode()
    return {path:data for path,data in result.items() if files.get(path)!=data}


def verified_stage(engineering,stage,original_verification):
    lock_path=engineering/'tools/native/native-lock.json'
    lock=stage_artifacts.load_lock(lock_path.resolve())
    actual=publication_plan.plan(lock_path,stage,original_verification,ORIGIN)
    frozen=json.loads((engineering/'source/native-publication-candidate.json').read_text())
    # The old init-script receipt depends on its local path; it is historical
    # evidence, not a public build input. All actual public inputs must match.
    for key in ('repository','lock_sha256','verification_sha256','files'):
        if actual[key]!=frozen[key]:raise ValueError('verified native stage differs from frozen publication plan: '+key)
    return lock,frozen,(stage/'verification-metadata.xml').read_bytes()


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--app',required=True,type=Path);p.add_argument('--engineering',type=Path,default=Path('.'))
    p.add_argument('--verified-stage',required=True,type=Path)
    a=p.parse_args();app=a.app.resolve();engineering=a.engineering.resolve()
    if subprocess.check_output(['git','-C',str(app),'rev-parse','HEAD^{tree}'],text=True).strip()!='50f5c32b8dd11cac12520bcacb9e7dbb5e2ea74d':raise ValueError('native layer requires exact reviewed G2 tree')
    if subprocess.check_output(['git','-C',str(app),'status','--porcelain'],text=True).strip():raise ValueError('native layer requires a clean source checkout')
    names=['settings.gradle.kts','build.gradle.kts','gradle/libs.versions.toml','gradle/verification-metadata.xml','gradle.properties','build-inputs/native-publication.json','app/build.gradle.kts','BUILD.md']
    files={name:(app/name).read_bytes() for name in names}
    lock,plan,metadata=verified_stage(engineering,a.verified_stage.resolve(),app/'gradle/verification-metadata.xml')
    for path,data in materialize(files,lock,plan,metadata).items():
        (app/path).parent.mkdir(parents=True,exist_ok=True);(app/path).write_bytes(data)
