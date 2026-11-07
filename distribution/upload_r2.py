"""Operator-only R2 upload of a pinned private snapshot; never called by PR jobs."""
import argparse
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import binary_repo as b

def upload(source,expected,endpoint,destination,cache,scratch):
    if not re.fullmatch('[0-9a-f]{64}',expected):raise ValueError('expected SHA256')
    if not re.fullmatch(r'https://[A-Za-z0-9]+\.r2\.cloudflarestorage\.com',endpoint):raise ValueError('R2 endpoint')
    if not destination.startswith('s3://'):raise ValueError('R2 destination')
    if source.is_symlink():raise ValueError('source symlink')
    # New private directory prevents candidate path changes from changing upload bytes.
    # Operator runs trusted code in an isolated signing/deployment account/environment.
    with tempfile.TemporaryDirectory(prefix='r2-snapshot-',dir=scratch) as private:
        snapshot=Path(private)/'approved-object'
        shutil.copyfile(source,snapshot)
        if b.sha(snapshot)!=expected:raise ValueError('approved upload bytes mismatch')
        os.chmod(snapshot,0o400)
        subprocess.run(['aws','--endpoint-url',endpoint,'s3','cp',str(snapshot),destination,'--cache-control',cache,'--metadata','sha256='+expected],check=True)
        if b.sha(snapshot)!=expected:raise ValueError('snapshot changed during upload')

def main():
    p=argparse.ArgumentParser()
    for name in ('source','sha256','endpoint','destination','cache-control','scratch'):p.add_argument('--'+name,required=True)
    a=p.parse_args();upload(Path(a.source),a.sha256,a.endpoint,a.destination,a.cache_control,Path(a.scratch))
if __name__=='__main__':main()
