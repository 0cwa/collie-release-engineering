"""Small data-only repository page renderer; never activates a public endpoint."""
import html,re,shutil,json,hashlib
from pathlib import Path
from string import Template
from urllib.parse import urlsplit,urlunsplit,urljoin
ROOT=Path(__file__).with_name('site')
STATIC=frozenset('assets/'+name for name in ('site.css','site.js','manrope.ttf','sora.ttf','manrope-OFL.txt','sora-OFL.txt','collie-icon.svg'))

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
    if state in ('inactive','native') and any((output/name).exists() or (output/name).is_symlink() for name in ('add-repository.png','add-repository.svg')):raise ValueError('inactive/native output contains an existing QR; use a fresh directory')
    import binary_repo
    if apk_cert is None:apk_cert=binary_repo.APK_CERTIFICATE
    if not isinstance(apk_cert,str) or not re.fullmatch('[0-9a-fA-F]{64}',apk_cert):raise ValueError('APK certificate fingerprint')
    ICON='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="8" y="4" width="13" height="16" rx="2"></rect><path d="M16 4V3a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h3"></path></svg>'
    package=html.escape(binary_repo.APP);signer=':'.join(apk_cert.upper()[i:i+2] for i in range(0,64,2))
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
        content=f'''{notice}<section class="content"><div><p class="intro">Add Collie to F-Droid or a compatible client.</p><div class="actions"><a class="button" href="{e(urls['deep'])}">Add repository</a><a class="button secondary" href="{e(download)}">Download APK</a></div><p class="manual">If the app does not open, <a href="{e(urls['universal'])}">try the F-Droid link</a>, or add the repository manually in your client.</p><div class="repo-address" id="repo-address"><dl class="repo-values"><div><dt>Repository address (with fingerprint)</dt><dd class="copy-row"><code id="repo-url"><a href="{e(urls['https'])}">{e(urls['https'])}</a></code><button class="copy-icon" type="button" data-copy-source="repo-url" data-copy-label="Copied repository address." data-copy-status="repo-copy-status" data-copy-fallback="repo-copy-fallback" aria-label="Copy repository address with fingerprint" aria-describedby="repo-copy-status" title="Copy repository address" hidden>{ICON}</button></dd></div><div><dt>F-Droid link</dt><dd><code id="repo-deep"><a href="{e(urls['deep'])}">{e(urls['deep'])}</a></code></dd></div></dl><p class="copy-status" id="repo-copy-status" role="status" aria-live="polite"></p><textarea class="copy-fallback" id="repo-copy-fallback" aria-label="Repository address selected for manual copying" rows="3" readonly hidden></textarea></div></div><div class="qr"><div class="qr-island"><img src="add-repository.png" width="240" height="240" alt="QR code to add the fingerprint-pinned Collie repository"></div><p class="scan">Scan to add the repository</p></div></section><section class="fingerprints"><div><h2>Repository fingerprint</h2><code>{e(repo_cert.upper())}</code></div></section><p class="note">The same signed APK as GitHub, with optional FCM support. This is Collie’s custom repository, separate from the official F-Droid catalogue.</p>'''
    verification=f'''<section class="verification" id="verification" aria-labelledby="verification-title"><div class="verification-heading"><h2 id="verification-title">Verification</h2><button class="copy-icon" type="button" data-copy-verification aria-label="Copy package and APK signing SHA256" aria-describedby="verification-copy-status" title="Copy verification details" hidden>{ICON}</button></div><dl class="verification-values"><div><dt>Package</dt><dd><code id="verification-package">{package}</code></dd></div><div><dt>APK signing SHA256</dt><dd><code id="verification-signer">{signer}</code></dd></div></dl><p class="copy-status" id="verification-copy-status" role="status" aria-live="polite"></p><textarea class="copy-fallback" id="verification-copy-fallback" aria-label="Verification details selected for manual copying" rows="2" readonly hidden></textarea><noscript><p class="copy-status">Select the package and APK signing SHA256 above to copy them.</p></noscript><p class="verification-docs"><a href="https://github.com/0cwa/collie-release-engineering/tree/main/distribution">Verification details</a></p></section>'''
    content+=verification
    metadata=''
    if state!='native' and repo_cert and apk_cert:
        links(repo_url,repo_cert)
        if not re.fullmatch('[0-9a-fA-F]{64}',apk_cert):raise ValueError('APK certificate fingerprint')
        metadata=f'<meta name="collie-repository-certificate" content="{repo_cert.upper()}"><meta name="collie-apk-certificate" content="{apk_cert.upper()}">'
    text=Template((ROOT/'landing.html').read_text()).substitute(CONTENT=content,STATE=state,METADATA=metadata)
    (output/'index.html').write_text(text)
    return {'state':state,'files':sorted(STATIC|{'index.html'}|({'add-repository.png'} if state in ('preview','active') else set()))}

def has_add_link(text,origin='https://repo.collie.im/'):
    from html.parser import HTMLParser
    approved=urlsplit(origin)
    class Links(HTMLParser):
        active=False
        def handle_starttag(self,tag,attrs):
            attrs=dict(attrs)
            if tag=='base':raise ValueError('generated static page must use the approved base origin')
            if tag=='body' and attrs.get('data-repository-state') in ('active','preview'):self.active=True
            if tag=='a':
                # Resolve root-relative and scheme-relative URLs as the browser
                # does; a literal-prefix check misses both forms.
                href=attrs.get('href','')
                # These templates emit canonical URLs. Refuse browser-specific
                # rewrites instead of approximating a general WHATWG parser.
                if chr(92) in href or any(ord(c)<32 or ord(c)==127 for c in href):raise ValueError('noncanonical link URL in generated static page')
                href=href.strip()
                try:target=urlsplit(urljoin(origin,href))
                except ValueError:self.active=True;return
                if target.scheme.lower() in ('fdroidrepos','fdroidrepo'):self.active=True
                if target.scheme.lower() in ('https','http'):
                    if target.hostname==approved.hostname and target.path.rstrip('/')=='/fdroid/repo':self.active=True
                    if target.hostname=='fdroid.link' and target.fragment:self.active=True
    parser=Links();parser.feed(text);return parser.active

def main():
    import argparse
    import binary_repo
    parser=argparse.ArgumentParser();parser.add_argument('output',type=Path);parser.add_argument('--state',choices=['inactive','native','preview'],default='inactive');parser.add_argument('--repo-url');parser.add_argument('--repository-fingerprint');parser.add_argument('--download');args=parser.parse_args()
    print(json.dumps(render(args.output,args.repo_url,args.repository_fingerprint,binary_repo.APK_CERTIFICATE,args.download,state=args.state),indent=2))
if __name__=='__main__':main()
