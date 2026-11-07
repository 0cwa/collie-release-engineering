#!/usr/bin/env python3
"""Resolve the six actual accepted files using emitted source DSL, no init script.

This is an isolated dependency-resolution fixture, not the blocked Android build.
The HTTPS origin is replaced only in this throwaway fixture with a file URI.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--gradle',required=True,type=Path);p.add_argument('--emitted',required=True,type=Path)
p.add_argument('--accepted-stage',required=True,type=Path);p.add_argument('--scratch',required=True,type=Path)
a=p.parse_args();a.scratch.mkdir(parents=True,exist_ok=False)
root=a.scratch.resolve();emitted=a.emitted.resolve();stage=a.accepted_stage.resolve()
spec=importlib.util.spec_from_file_location('native_layer',Path(__file__).parents[1]/'source/layers/native_repository.py');layer=importlib.util.module_from_spec(spec);spec.loader.exec_module(layer)
plan=json.loads((emitted/'build-inputs/native-artifacts.json').read_text());repo=root/'maven';repo.mkdir()
for item in plan['files']:
 src=stage/'maven'/item['path'];dst=repo/item['path'];dst.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(src,dst)
 if hashlib.sha256(dst.read_bytes()).hexdigest()!=item['sha256']:raise ValueError('fixture input differs')
# A second repository has matching bytes; exclusiveContent must still refuse it
# if the approved native repository has no matching module.
fallback=root/'fallback';shutil.copytree(repo/'im/collie/native/libsignal-client',fallback/'im/collie/native/libsignal-client')
upstream=root/'upstream';holder=upstream/'test/holder/1/holder-1.pom';holder.parent.mkdir(parents=True)
mods=[('libsignal-android','0.106.1','aar'),('libsignal-client','0.106.1','jar'),('ringrtc-android','2.74.0','aar')]
deps=''.join(f'<dependency><groupId>{g}</groupId><artifactId>{n}</artifactId><version>{v}</version><type>{ext}</type></dependency>' for g in ('org.signal','im.molly') for n,v,ext in mods)
holder.write_text('<project xmlns="http://maven.apache.org/POM/4.0.0"><modelVersion>4.0.0</modelVersion><groupId>test</groupId><artifactId>holder</artifactId><version>1</version><packaging>pom</packaging><dependencies>'+deps+'</dependencies></project>')
project=root/'project';(project/'gradle').mkdir(parents=True)
metadata=(emitted/'gradle/verification-metadata.xml').read_text()
metadata=metadata.replace('   </components>',f'      <component group="test" name="holder" version="1"><artifact name="holder-1.pom"><sha256 value="{hashlib.sha256(holder.read_bytes()).hexdigest()}"/></artifact></component>\n   </components>')
(project/'gradle/verification-metadata.xml').write_text(metadata)
# Extract the exact emitted exclusive block and project substitution block.
settings=(emitted/'settings.gradle.kts').read_text()
start=settings.index('    '+layer.SETTINGS_MARKER);end=settings.index('    google {',start)
block=settings[start:end]
if block.count(layer.ORIGIN)!=1:raise ValueError('fixture must change one exact proposed origin only')
block=block.replace(layer.ORIGIN,repo.as_uri()+'/')
(project/'settings.gradle.kts').write_text('dependencyResolutionManagement { repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)\n repositories {\n'+block+f'    maven {{ url=uri("{upstream.as_uri()}") }}\n    maven {{ url=uri("{fallback.as_uri()}") }}\n }}\n}}\nrootProject.name="native-source-fixture"\n')
build=(emitted/'build.gradle.kts').read_text();block=build[build.index(layer.BUILD_MARKER):]
configs=['signal','molly','declared','transitive']
body='\n'.join(f'val {c} = configurations.create("{c}") {{ exclude(group="org.jetbrains.kotlin"); exclude(group="org.jetbrains.kotlinx"); exclude(group="androidx.annotation") }}' for c in configs)
body+='\ndependencies {\n'
for c,group in [('signal','org.signal'),('molly','im.molly'),('declared','im.collie.native')]:
 for name,version,ext in mods:
  if group=='im.collie.native':version=version+'-collie.1'
  body+=f'  add("{c}", "{group}:{name}:{version}")\n'
body+='  add("transitive", "test:holder:1")\n}\n'
expected={Path(f['path']).name:f['sha256'] for f in plan['files'] if not f['path'].endswith('.pom')}
body+='val expected = mapOf('+','.join(json.dumps(k)+' to '+json.dumps(v) for k,v in expected.items())+')\n'
body+='''tasks.register("verifyPinnedNative") { doLast {
 listOf(signal,molly,declared,transitive).forEach { configuration ->
  val artifacts=configuration.incoming.artifactView { }.artifacts.artifacts.filter { (it.id.componentIdentifier as org.gradle.api.artifacts.component.ModuleComponentIdentifier).group != "test" }
  require(artifacts.size==3) { "native artifact count differs" }
  require(artifacts.all { (it.id.componentIdentifier as org.gradle.api.artifacts.component.ModuleComponentIdentifier).group=="im.collie.native" }) { "native fallback" }
  artifacts.forEach { artifact ->
   val digest=java.security.MessageDigest.getInstance("SHA-256")
   artifact.file.inputStream().use { input -> val bytes=ByteArray(1048576); var size=input.read(bytes); while(size>=0) { digest.update(bytes,0,size);size=input.read(bytes) } }
   require(digest.digest().joinToString("") { "%02x".format(it) } == expected[artifact.file.name]) { "native bytes differ" }
  }
  println("PINNED_NATIVE="+configuration.name+":"+artifacts.map { it.id.componentIdentifier.displayName }.sorted())
 }
} }
'''
(project/'build.gradle.kts').write_text(block+'\n'+body)
env={k:v for k,v in os.environ.items() if k not in ('GRADLE_OPTS','JAVA_TOOL_OPTIONS','JDK_JAVA_OPTIONS','_JAVA_OPTIONS')}
env['GRADLE_USER_HOME']=str(root/'gradle-home');env['TMPDIR']=str(root/'tmp');(root/'tmp').mkdir()
command=[str(a.gradle.resolve()),'--offline','--no-daemon','--no-configuration-cache','--no-build-cache','--max-workers=1','--dependency-verification=strict','-p',str(project),'verifyPinnedNative']
checks=[]
def execute(name,passing):
 # Refresh only this fixture's native metadata cache; preserve one build directory.
 cache=root/'gradle-home/caches/modules-2'
 if cache.exists():shutil.rmtree(cache)
 r=subprocess.run(command,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
 (root/(name+'.log')).write_text(r.stdout)
 if passing:
  if r.returncode or r.stdout.count('PINNED_NATIVE=')!=4:raise RuntimeError(r.stdout)
 else:
  if r.returncode==0:raise RuntimeError('tampered/missing native input unexpectedly resolved')
  if 'verification failed' not in r.stdout.lower() and 'could not find' not in r.stdout.lower() and 'could not resolve' not in r.stdout.lower():raise RuntimeError(r.stdout)
 checks.append({'check':name,'status':'PASS','scope':'isolated dependency resolver; no Android/gate activation'})
execute('exact-six-files-direct-and-transitive',True)
client_dir=repo/'im/collie/native/libsignal-client/0.106.1-collie.1';missing=client_dir.with_name(client_dir.name+'.absent')
client_dir.rename(missing)
try:execute('missing-approved-module-does-not-fallback',False)
finally:missing.rename(client_dir)
pom=next(client_dir.glob('*.pom'));original=pom.read_bytes()
pom.rename(pom.with_suffix('.pom.absent'))
try:execute('missing-pom-with-binary-present-rejected',False)
finally:pom.with_suffix('.pom.absent').rename(pom)
module=pom.with_suffix('.module');module.write_text('{"formatVersion":"1.1","component":{"group":"im.collie.native","module":"libsignal-client","version":"0.106.1-collie.1"},"variants":[]}')
pom.rename(pom.with_suffix('.pom.absent'))
try:execute('gradle-module-without-required-pom-rejected',False)
finally:pom.with_suffix('.pom.absent').rename(pom)
pom.write_bytes(original.replace(b'<project',b'<!-- do_not_remove: published-with-gradle-metadata -->\n<project'))
try:execute('pom-marker-cannot-redirect-to-gradle-module',False)
finally:pom.write_bytes(original);module.unlink()
pom.write_bytes(original+b'\n')
try:execute('wrong-pom-rejected',False)
finally:pom.write_bytes(original)
jar=next(client_dir.glob('*.jar'));size=jar.stat().st_size
with jar.open('ab') as out:out.write(b'tampered')
try:execute('wrong-binary-rejected',False)
finally:
 with jar.open('r+b') as out:out.truncate(size)
print(json.dumps({'scope':'Actual accepted binary/POM bytes; production origin replaced with fixture-local file URI only; no init script/Android compilation/public readback','checks':checks,'production_metadata_sha256':hashlib.sha256((emitted/'gradle/verification-metadata.xml').read_bytes()).hexdigest()},indent=2))
