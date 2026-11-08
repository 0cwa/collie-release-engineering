#!/usr/bin/env python3
"""Render an explicit security publication layer before G, never during builds."""
import argparse,hashlib,json,re,shutil,subprocess
from pathlib import Path
FILES={
    'codeql.yml.in':'.github/workflows/codeql.yml',
    'update-screenshots.yml.in':'.github/workflows/update-screenshots.yml',
    'publish-screenshots.yml.in':'.github/workflows/publish-screenshots.yml',
    'codeql-config.yml':'.github/codeql/codeql-config.yml',
    'reviewed-findings.json':'.github/codeql/reviewed-findings.json',
}

def render(revision,output,repository=None):
    if not isinstance(revision,str) or not re.fullmatch('[0-9a-f]{40}',revision):raise ValueError('immutable engineering policy commit required')
    if output.exists() or output.is_symlink():raise ValueError('new publication-layer directory required')
    repository=Path(repository or Path(__file__).resolve().parents[2])
    def committed_template(name):
        source=f'tools/codeql/publication/{name}'
        tree=subprocess.check_output(['git','-C',str(repository),'ls-tree','-z',revision,'--',source])
        try:
            metadata,path=tree.rstrip(b'\0').split(b'\t',1)
            mode,kind,_=metadata.decode().split()
        except ValueError as error:
            raise ValueError('template is missing from policy commit') from error
        if path.decode()!=source or mode!='100644' or kind!='blob':
            raise ValueError('policy template must be a regular Git blob')
        return subprocess.check_output(['git','-C',str(repository),'show',f'{revision}:{source}']).decode('utf-8')
    output.mkdir(parents=True)
    written={}
    try:
        for name,target in FILES.items():
            text=committed_template(name).replace('__ENGINEERING_POLICY_REVISION__',revision)
            if '__ENGINEERING_POLICY_REVISION__' in text:raise ValueError('unresolved policy pin')
            path=output/target;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text)
            written[target]=hashlib.sha256(path.read_bytes()).hexdigest()
        return {'schema':1,'engineering_revision':revision,'files':written,'coverage':'Actions only; Java/Kotlin/native source NOT RUN until separately verified traced build'}
    except Exception:
        shutil.rmtree(output);raise

def main():
    p=argparse.ArgumentParser();p.add_argument('--engineering-revision',required=True);p.add_argument('--output',required=True,type=Path);p.add_argument('--repository',type=Path);a=p.parse_args()
    print(json.dumps(render(a.engineering_revision,a.output,a.repository),indent=2))
if __name__=='__main__':main()
