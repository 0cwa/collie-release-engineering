"""Emit an inert application README patch only after approved live activation."""
import argparse,difflib,hashlib,json,re,shutil
from pathlib import Path
import binary_repo as b
import pages as p
FIELDS={'schema','kind','environment','origin','policy_revision','plan_sha256','journal_sha256','repository_receipt_sha256','repository_certificate_sha256','client_acceptance_sha256','apk_sha256','checks'}

def prepare(readme,record,approved_sha256,output):
    if b.sha(record)!=approved_sha256:raise ValueError('externally approved activation record differs')
    a=b.load(record)
    if set(a)!=FIELDS or type(a['schema']) is not int or a['schema']!=1 or a['kind']!='collie-repository-link-activation' or a['environment']!='production' or a['origin']!=p.ORIGIN:raise ValueError('production activation record required')
    if a['checks']!={k:'PASS' for k in ('canonical_https_readback','official_fdroid','compatible_client')}:raise ValueError('production readback and both clients required')
    for key in ('plan_sha256','journal_sha256','repository_receipt_sha256','repository_certificate_sha256','client_acceptance_sha256','apk_sha256'):
        if not isinstance(a[key],str) or not re.fullmatch('[0-9a-f]{64}',a[key]):raise ValueError('activation digest '+key)
    if not re.fullmatch('[0-9a-f]{40}',a['policy_revision']):raise ValueError('immutable activation policy required')
    original=readme.read_text()
    pattern=r'(<a href="https://apps\.obtainium\.imranr\.dev/redirect[^\"]*"><img src="docs/assets/badge_obtainium\.png" alt="Get it on Obtainium" height="([0-9]+)"></a>)'
    matches=list(re.finditer(pattern,original))
    if len(matches)!=1 or 'badge_fdroid.svg' in original:raise ValueError('one existing Obtainium badge and no F-Droid badge required')
    item=matches[0];height=item.group(2)
    badge=f' <a href="{p.ORIGIN}" title="Our custom F-Droid repository"><img src="docs/assets/badge_fdroid.svg" alt="Our F-Droid repository" height="{height}"></a>'
    updated=original[:item.end()]+badge+original[item.end():]
    updated=updated.replace('*F-droid-compatible repo coming soon.*','Our custom F-Droid repository distributes the same signed APK.')
    if output.exists() or output.is_symlink():raise ValueError('new patch output required')
    output.mkdir(parents=True)
    (output/'README.patch').write_text(''.join(difflib.unified_diff(original.splitlines(True),updated.splitlines(True),fromfile='a/README.md',tofile='b/README.md')))
    assets=output/'docs/assets';assets.mkdir(parents=True);source=Path(__file__).with_name('site')/'publication'
    shutil.copyfile(source/'get-it-on-fdroid.svg',assets/'badge_fdroid.svg');shutil.copyfile(source/'FDROID-BADGE-LICENSE.md',assets/'badge_fdroid.LICENSE.md')
    (output/'approval-reference.json').write_text(json.dumps(dict(schema=1,activation_record_sha256=approved_sha256,source_readme_sha256=b.sha(readme),badge_sha256=b.sha(assets/'badge_fdroid.svg'),state='REVIEWABLE PATCH ONLY; NOT APPLIED OR PUBLISHED'),indent=2)+'\n')
    return output/'README.patch'

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--readme',type=Path,required=True);parser.add_argument('--record',type=Path,required=True);parser.add_argument('--approved-record-sha256',required=True);parser.add_argument('--output',type=Path,required=True);a=parser.parse_args();print(prepare(a.readme,a.record,a.approved_record_sha256,a.output))
if __name__=='__main__':main()
