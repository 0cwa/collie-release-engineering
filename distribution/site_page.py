"""Small data-only repository page renderer; never activates a public endpoint."""
import html,re,shutil,json,hashlib
from pathlib import Path
from string import Template
from urllib.parse import urlsplit,urlunsplit
ROOT=Path(__file__).with_name('site')
STATIC=frozenset('assets/'+name for name in ('site.css','manrope.ttf','sora.ttf','manrope-OFL.txt','sora-OFL.txt','collie-icon.svg'))

def write_assets(output):
    lock=json.loads((ROOT/'ASSETS.json').read_text())
    if lock['schema']!=1 or set(lock['files'])!=STATIC:raise ValueError('static asset lock differs')
    for name in STATIC:
        if hashlib.sha256((ROOT/name).read_bytes()).hexdigest()!=lock['files'][name]['sha256']:raise ValueError('pinned static asset changed: '+name)
        target=output/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/name,target)

def links(repo_url,certificate):
    parsed=urlsplit(repo_url)
    if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:raise ValueError('clean HTTPS repository URL required')
    if not isinstance(certificate,str) or not re.fullmatch('[0-9a-fA-F]{64}',certificate):raise ValueError('repository certificate fingerprint')
    pinned=repo_url+'?fingerprint='+certificate.upper()
    deep=urlunsplit(('fdroidrepos',parsed.netloc,parsed.path,'fingerprint='+certificate.upper(),''))
    return {'https':pinned,'deep':deep,'universal':'https://fdroid.link/#'+pinned}

def render(output,repo_url=None,repo_cert=None,apk_cert=None,download=None,*,state='inactive'):
    if state not in ('inactive','native','preview','active'):raise ValueError('page state')
    output.mkdir(parents=True,exist_ok=True);write_assets(output);e=html.escape
    if state=='native':
        content='<section class="inactive"><p>Collie native inputs are being prepared.</p><p class="note">The F-Droid feed is not active.</p></section>'
    elif state=='inactive':
        if not download or urlsplit(download).scheme!='https':raise ValueError('HTTPS download required')
        content=f'<section class="inactive"><p>The repository is not published yet.</p><div class="actions"><a class="button" href="{e(download)}">Download APK</a></div><p class="note">Collie is available on GitHub.</p></section>'
    else:
        urls=links(repo_url,repo_cert)
        if not isinstance(apk_cert,str) or not re.fullmatch('[0-9a-fA-F]{64}',apk_cert) or not download or urlsplit(download).scheme!='https':raise ValueError('APK certificate / HTTPS download required')
        import qrcode
        qr=qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M,box_size=8,border=4);qr.add_data(urls['deep']);qr.make(fit=True)
        qr.make_image(fill_color='#000000',back_color='#ffffff').convert('RGB').save(output/'add-repository.png')
        notice='<p class="status">Local preview. This repository is not published.</p>' if state=='preview' else ''
        content=f'''{notice}<section class="content"><div><p class="intro">Add Collie to F-Droid or a compatible client.</p><div class="actions"><a class="button" href="{e(urls['deep'])}">Add repository</a><a class="button secondary" href="{e(download)}">Download APK</a></div><p class="manual">If the app does not open, <a href="{e(urls['universal'])}">try the F-Droid link</a>, or copy this address into your client:<br><a href="{e(urls['https'])}">{e(repo_url)}</a></p></div><div class="qr"><div class="qr-island"><img src="add-repository.png" width="240" height="240" alt="QR code to add the fingerprint-pinned Collie repository"></div><p class="scan">Scan to add the repository</p></div></section><section class="fingerprints"><div><h2>Repository fingerprint</h2><code>{e(repo_cert.upper())}</code></div><div><h2>APK fingerprint</h2><code>{e(apk_cert.upper())}</code></div></section><p class="note">The same signed APK as GitHub, with optional FCM support. This is Collie’s custom repository, separate from the official F-Droid catalogue.</p>'''
    metadata=''
    if state!='native' and repo_cert and apk_cert:
        links(repo_url,repo_cert)
        if not re.fullmatch('[0-9a-fA-F]{64}',apk_cert):raise ValueError('APK certificate fingerprint')
        metadata=f'<meta name="collie-repository-certificate" content="{repo_cert.upper()}"><meta name="collie-apk-certificate" content="{apk_cert.upper()}">'
    text=Template((ROOT/'landing.html').read_text()).substitute(CONTENT=content,STATE=state,METADATA=metadata)
    (output/'index.html').write_text(text)
    return {'state':state,'files':sorted(STATIC|{'index.html'}|({'add-repository.png'} if state in ('preview','active') else set()))}

def has_add_link(text):
    from html.parser import HTMLParser
    class Links(HTMLParser):
        active=False
        def handle_starttag(self,tag,attrs):
            attrs=dict(attrs)
            if tag=='body' and attrs.get('data-repository-state') in ('active','preview'):self.active=True
            if tag=='a':
                href=attrs.get('href','').strip().lower()
                if href.startswith(('fdroidrepos:','fdroidrepo:','https://fdroid.link/#','https://repo.collie.im/fdroid/repo')):self.active=True
    parser=Links();parser.feed(text);return parser.active

def main():
    import argparse
    import binary_repo
    parser=argparse.ArgumentParser();parser.add_argument('output',type=Path);parser.add_argument('--state',choices=['inactive','native','preview'],default='inactive');parser.add_argument('--repo-url');parser.add_argument('--repository-fingerprint');parser.add_argument('--download');args=parser.parse_args()
    print(json.dumps(render(args.output,args.repo_url,args.repository_fingerprint,binary_repo.APK_CERTIFICATE,args.download,state=args.state),indent=2))
if __name__=='__main__':main()
