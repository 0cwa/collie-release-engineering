#!/usr/bin/env python3
"""Execute actual emitted metadata expressions/gate with Gradle, without Android inputs."""
import argparse
import json
import os
from pathlib import Path
import subprocess

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--gradle',required=True,type=Path)
parser.add_argument('--app-build-script',required=True,type=Path)
parser.add_argument('--scratch',required=True,type=Path)
a=parser.parse_args();a.scratch.mkdir(parents=True,exist_ok=False)
script=a.app_build_script.read_text()
gate=script[script.index('// Publication migration gate:'):]
project=a.scratch/'project';project.mkdir()
(project/'settings.gradle.kts').write_text('rootProject.name="publication-gate-fixture"\n')
command=[str(a.gradle),'--offline','--no-daemon','--no-configuration-cache','--max-workers=1','-g',str(a.scratch/'gradle-home'),'-p',str(project)]
result=[]
# Actual emitted Kotlin gate runs before any real task regardless CLI aliases/aggregates.
(project/'build.gradle.kts').write_text('tasks.register("assembleProdWebsiteRelease")\ntasks.register("assemble") { dependsOn("assembleProdWebsiteRelease") }\ntasks.register("build") { dependsOn("assemble") }\n'+gate)
for task in ['assembleProdWebsiteRelease','assemble','build']:
 p=subprocess.run(command+[task],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
 if p.returncode==0 or 'Direct Android builds BLOCKED:' not in p.stdout:raise RuntimeError(p.stdout)
 result.append({'task':task,'result':'PASS rejected actual Kotlin configuration'})
# Evaluate actual emitted defaultConfig RHS without Android plugin or compiling app code.
code=next(x.strip().removeprefix('versionCode = ') for x in script.splitlines() if x.strip().startswith('versionCode = '))
name=next(x.strip().removeprefix('versionName = ') for x in script.splitlines() if x.strip().startswith('versionName = '))
(project/'build.gradle.kts').write_text('val canonicalVersionCode=1777\nval maxHotfixVersions=100\nval mollyRevision=1\nval currentHotfixVersion=0\nval ciEnabled=false\nval sourceVersionNameWithRevision="fixture"\nfun requireCommitTag()="fixture"\nval versionCode = '+code+'\nval versionName = '+name+'\ntasks.register("metadata") { doLast { println("FIXTURE_VERSION=$versionCode/$versionName") } }\n')
p=subprocess.run(command+['metadata','-PreleaseVersionCode=177703','-PreleaseVersionName=fixture-next'],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
if p.returncode or 'FIXTURE_VERSION=177703/fixture-next' not in p.stdout:raise RuntimeError(p.stdout)
result.append({'metadata':'PASS actual DSL applies explicit code/name'})
p=subprocess.run(command+['metadata','-PreleaseVersionCode=177702'],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
if p.returncode==0 or 'Release versionCode must exceed published RC1' not in p.stdout:raise RuntimeError(p.stdout)
result.append({'floor':'PASS published code rejected; external inventory/reservation still required'})
print(json.dumps({'scope':'Synthetic Gradle fixture; not Android build/device evidence','checks':result},indent=2))
