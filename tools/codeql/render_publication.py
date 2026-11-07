#!/usr/bin/env python3
"""Render an explicit security publication layer before G, never during builds."""
import argparse,hashlib,json,re,shutil
from pathlib import Path
FILES={
    'codeql.yml.in':'.github/workflows/codeql.yml',
    'update-screenshots.yml.in':'.github/workflows/update-screenshots.yml',
    'publish-screenshots.yml.in':'.github/workflows/publish-screenshots.yml',
    'codeql-config.yml':'.github/codeql/codeql-config.yml',
    'reviewed-findings.json':'.github/codeql/reviewed-findings.json',
}

def render(revision,output,templates=None):
    if not isinstance(revision,str) or not re.fullmatch('[0-9a-f]{40}',revision):raise ValueError('immutable engineering policy commit required')
    if output.exists() or output.is_symlink():raise ValueError('new publication-layer directory required')
    templates=templates or Path(__file__).with_name('publication')
    output.mkdir(parents=True)
    written={}
    try:
        for name,target in FILES.items():
            source=templates/name
            if source.is_symlink():raise ValueError('symlink template')
            text=source.read_text().replace('__ENGINEERING_POLICY_REVISION__',revision)
            if '__ENGINEERING_POLICY_REVISION__' in text:raise ValueError('unresolved policy pin')
            path=output/target;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text)
            written[target]=hashlib.sha256(path.read_bytes()).hexdigest()
        return {'schema':1,'engineering_revision':revision,'files':written,'coverage':'Actions only; Java/Kotlin/native source NOT RUN until separately verified traced build'}
    except Exception:
        shutil.rmtree(output);raise

def main():
    p=argparse.ArgumentParser();p.add_argument('--engineering-revision',required=True);p.add_argument('--output',required=True,type=Path);a=p.parse_args()
    print(json.dumps(render(a.engineering_revision,a.output),indent=2))
if __name__=='__main__':main()
