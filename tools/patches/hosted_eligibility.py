#!/usr/bin/env python3
"""Select supported hosted phases; this report never proves native acceptance."""
import argparse
import hashlib
import json
from pathlib import Path
from build import BuildError,native_artifact_lock


def eligibility(repo,config):
    lock=native_artifact_lock(repo,config)
    android=isinstance(config.get('android'),dict) and bool(config['android'])
    enabled=android and lock is None
    reason=('Declared multi-artifact native integration requires separately supplied immutable native inputs and acceptance; the hosted native/Android/instrumentation route is not operational.' if lock else
            'Historical hosted route is eligible; this selection is not native, Android or instrumentation execution evidence.' if enabled else
            'This configuration declares no hosted Android build.')
    return {'schema':1,'status':'NOT RUN','android':enabled,'native_prepare':enabled,
            'native_lock_sha256':hashlib.sha256(lock.read_bytes()).hexdigest() if lock else None,'reason':reason}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--repo',type=Path,default=Path('.'));p.add_argument('--report',type=Path)
    p.add_argument('--github-output',type=Path);a=p.parse_args();repo=a.repo.resolve()
    try:
        result=eligibility(repo,json.loads((repo/'molly-build.json').read_text()))
        text=json.dumps(result,indent=2)+'\n'
        if a.report:
            a.report.parent.mkdir(parents=True,exist_ok=True)
            with a.report.open('x',encoding='utf-8') as out:out.write(text)
        if a.github_output:
            with a.github_output.open('a',encoding='utf-8') as out:out.write('android='+str(result['android']).lower()+'\n')
        print(text,end='')
    except (BuildError,ValueError,OSError,KeyError) as error:
        p.exit(1,'hosted phase selection failed: '+str(error)+'\n')


if __name__=='__main__':main()
