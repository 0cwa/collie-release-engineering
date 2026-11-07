"""Explicit legacy plans for historical runtime fixtures; never production inputs."""
import hashlib
import json
from pathlib import Path


def legacy_args(argv,root):
    values={}
    for index,value in enumerate(argv[:-1]):
        if isinstance(value,str) and value.startswith('--'):values[value]=argv[index+1]
    manifest={}
    for option in ('--manifest','--final-manifest'):
        if option in values:
            try:manifest=json.loads(Path(values[option]).read_text())
            except (OSError,ValueError):pass
    commit=str(values.get('--commit',values.get('--approved-commit','a'*40)))
    code=int(values.get('--version-code',manifest.get('version_code',177704)))
    version=str(values.get('--version-name',manifest.get('version_name','fixture')))
    cert=str(values.get('--certificate-sha256',manifest.get('certificate_sha256','b'*64)))
    plan={'schema':1,'kind':'release-plan','mode':'legacy','purpose':'fixture','source_commit':commit,'engineering_revision':commit,'workflow_revision':commit,'package':'im.collie.app','version_code':code,'version_name':version,'certificate_sha256':cert,'source_receipt_sha256':None,'build_inputs_sha256':None,'required_test_reports':[]}
    path=Path(root)/'explicit-legacy-plan.json';path.write_text(json.dumps(plan))
    return argv+['--release-plan',str(path),'--approved-release-plan-sha256',hashlib.sha256(path.read_bytes()).hexdigest()]
