"""Build the complete repo.collie.im Pages site from verified full releases.

Inputs (prepared by .github/workflows/feeds.yml):
  --apks DIR       collie.apk of each full release, named <tag>.apk
  --native DIR     the native-input Maven files, at their repository paths
  --native-plan F  app build-inputs/native-artifacts.json (paths, sizes, SHA-256)
  --latest TAG     newest full release; the landing page links its collie.apk
  --fastlane DIR   app fastlane/metadata/android: store listing, graphics and changelogs
  --keystore F     F-Droid repository keystore (PKCS12); password in $FDROID_KEYSTORE_PASS
Output: --site DIR, ready for upload-pages-artifact.
"""
import argparse, hashlib, json, os, re, shutil, subprocess, sys, tempfile, urllib.error, urllib.request
from pathlib import Path
from urllib.parse import unquote, urlparse

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import binary_repo, site_page  # noqa: E402
import channel_profile as channels  # noqa: E402
import retained_artifacts  # noqa: E402
import release_inputs  # noqa: E402

ORIGIN = 'https://repo.collie.im/'
REPO_URL = ORIGIN + 'fdroid/repo'
APP_RELEASES = 'https://github.com/0cwa/collieim-android/releases/download/'
SITE_LIMIT = 950_000_000
SITE_INVENTORY='site-manifest.json'
SITE_INVENTORY_LIMIT=32*1024*1024
SITE_ORIGIN='https://repo.collie.im/'


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def copy_native(native, plan, site):
    # Files sit at their site paths (native-inputs/v<N>/maven/...), as release_inputs.py native stages them.
    records = release_inputs.native_records(json.loads(Path(plan).read_text()))
    for rel, entry in sorted(records.items()):
        src = Path(native) / rel
        if src.stat().st_size != entry['bytes'] or sha256(src) != entry['sha256']:
            raise SystemExit(f'native file does not match plan: {rel}')
        dest = site / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
    return len(records)


def validate_release_identity(profile, tag, title, version_name, version_code):
    if profile.name=='stable':
        if tag != 'v' + version_name:
            raise ValueError('stable tag/version differs')
        return
    if tag != profile.tag_prefix + version_name:
        raise ValueError('Beta tag/version differs')
    if title != f'{profile.app_title} {version_name}':
        raise ValueError('Beta release title/version differs')
    if re.fullmatch(r'[0-9]+(?:\.[0-9]+)+(?:[-+][A-Za-z0-9.+-]+)?', version_name) is None:
        raise ValueError('Beta development or unvalidated version cannot be published')
    if re.search(r'(?:^|[-+.])(?:dev|development|unvalidated|port|snapshot|local)(?:$|[-+.])',version_name,re.IGNORECASE):
        raise ValueError('Beta development or unvalidated version cannot be published')
    if type(version_code) is not int or version_code<=0:
        raise ValueError('Beta versionCode must be a positive package version')

def release_identity(profile, tag, title, version_name, version_code, prior_version_code):
    validate_release_identity(profile,tag,title,version_name,version_code)
    if profile.name=='stable':return
    if type(prior_version_code) is not int or version_code <= prior_version_code:
        raise ValueError('Beta versionCode must increase within its package channel')


def apk_identity(apk, build_tools, profile=channels.STABLE):
    badging = subprocess.run([str(build_tools / 'aapt2'), 'dump', 'badging', str(apk)],
                             check=True, capture_output=True, text=True).stdout
    m = re.search(r"package: name='([^']+)' versionCode='(\d+)'(?: versionName='([^']*)')?", badging)
    certs = subprocess.run([str(build_tools / 'apksigner'), 'verify', '--print-certs', str(apk)],
                           check=True, capture_output=True, text=True).stdout
    digests = re.findall(r'certificate SHA-256 digest: ([0-9a-f]{64})', certs)
    if profile.apk_certificate is None:
        raise ValueError(f'{profile.name} APK signer pin is not configured')
    if not m or m.group(1) != profile.package or digests != [profile.apk_certificate]:
        raise SystemExit(f'{apk.name}: package or signing certificate does not match {profile.name}')
    if profile.name!='stable' and not m.group(3):
        raise ValueError(f'{profile.name} APK has no validated versionName')
    return int(m.group(2)), m.group(3) or ''


def stage_fastlane(fastlane, work, profile=channels.STABLE):
    # fdroid update reads build/<app>/fastlane and fills the listing, app icon and changelog.
    # The same icon is the repository's own icon, which fdroid would otherwise generate.
    source = Path(fastlane)
    if any(p.is_symlink() for p in source.rglob('*')):
        raise SystemExit('symlink in Fastlane metadata')
    shutil.copytree(source, work / 'build' / profile.package / 'fastlane/metadata/android')
    shutil.copyfile(source / 'en-US/images/icon.png', work / 'collie-icon.png')

def validate_generated_index(work,profile):
    if profile.name=='stable':return
    index_path=Path(work)/'repo/index-v2.json'
    data=json.loads(index_path.read_text())
    packages=data.get('packages')
    if not isinstance(packages,dict) or set(packages)!={profile.package}:
        raise ValueError('Beta F-Droid index must contain only the Beta package')
    if data.get('repo',{}).get('address','').rstrip('/')!=profile.repository_url.rstrip('/'):
        raise ValueError('Beta generated index address differs from channel route')
    package=packages[profile.package]
    if not isinstance(package,dict) or package.get('releaseChannels') not in (None,[]):
        raise ValueError('Beta package index releaseChannels must be absent or empty')


def channel_repository_config(config,profile):
    """Set F-Droid's signed repo address before update; stable source stays byte-identical."""
    if profile.name=='stable':return config
    lines=config.splitlines()
    wanted={'repo_url':profile.repository_url.rstrip('/'),
            'repo_name':'Collie TrailBlazer Beta',
            'repo_description':"Collie's dedicated TrailBlazer Beta F-Droid repository."}
    for key,value in wanted.items():
        prefix=key+':'
        matches=[i for i,line in enumerate(lines) if line.startswith(prefix)]
        if len(matches)!=1:raise ValueError('F-Droid public config must define '+key+' exactly once')
        lines[matches[0]]=prefix+' '+json.dumps(value)
    return '\n'.join(lines)+'\n'


def _read_origin(path, *, max_bytes):
    """Fetch only canonical HTTPS Pages URLs; return (status, bytes)."""
    url=SITE_ORIGIN+path
    request=urllib.request.Request(url,headers={'Cache-Control':'no-cache, no-store','Pragma':'no-cache'})
    try:
        response=urllib.request.urlopen(request,timeout=60)
    except urllib.error.HTTPError as error:
        if error.code==404:
            final=urlparse(error.geturl())
            if final.scheme!='https' or final.netloc!='repo.collie.im':
                error.close()
                raise ValueError('canonical Pages absence check left its fixed HTTPS origin')
            error.close()
            return 404,b''
        raise
    with response:
        final=urlparse(response.geturl())
        if final.scheme!='https' or final.netloc!='repo.collie.im' or response.status!=200:
            raise ValueError('canonical Pages readback left its fixed HTTPS origin')
        chunks=[];size=0
        while True:
            block=response.read(min(1024*1024,max_bytes-size+1))
            if not block:break
            size+=len(block)
            if size>max_bytes:raise ValueError('canonical Pages readback exceeds byte limit')
            chunks.append(block)
        return 200,b''.join(chunks)


def _inventory_files(raw):
    def unique(pairs):
        result={}
        for key,value in pairs:
            if key in result:raise ValueError('duplicate served inventory field')
            result[key]=value
        return result
    try:data=json.loads(raw,object_pairs_hook=unique)
    except (ValueError,UnicodeDecodeError) as exc:raise ValueError('invalid served site inventory') from exc
    if (not isinstance(data,dict) or set(data)!={'schema','kind','origin','files'} or
            type(data.get('schema')) is not int or data['schema']!=1 or
            data['kind']!='collie-pages-public-inventory' or data['origin']!=SITE_ORIGIN or
            not isinstance(data['files'],dict)):
        raise ValueError('served site inventory identity/schema differs')
    for name,entry in data['files'].items():
        if (not isinstance(name,str) or not isinstance(entry,dict) or set(entry)!={'sha256','bytes'} or
                type(entry.get('bytes')) is not int or entry['bytes']<0 or
                not re.fullmatch('[0-9a-f]{64}',str(entry.get('sha256')))):
            raise ValueError('served site inventory contains malformed file record')
        if name.startswith('fdroid/beta/repo/') and not binary_repo.public_path(name[len('fdroid/beta/repo/'):],channels.BETA):
            raise ValueError('served site inventory contains an unexpected Beta route path')
    return data['files']


def capture_beta_route(snapshot, *, fetch=None, expected_inventory_sha256=None):
    """Verify and copy the currently served Beta route; never trust inventory alone."""
    snapshot=Path(snapshot)
    if snapshot.exists():raise ValueError('new Beta snapshot directory required')
    snapshot.mkdir(parents=True)
    if fetch is None:fetch=_read_origin
    status,raw=fetch(SITE_INVENTORY,max_bytes=SITE_INVENTORY_LIMIT)
    if status==404:
        entry_status,_=fetch('fdroid/beta/repo/entry.jar',max_bytes=8*1024*1024)
        if entry_status!=404:raise ValueError('served Beta index exists without the canonical site inventory')
        if expected_inventory_sha256 is not None:raise ValueError('served inventory disappeared after capture')
        return {'inventory_sha256':None,'files':{},'release':None}
    if status!=200:raise ValueError('canonical site inventory readback failed')
    inventory_sha=hashlib.sha256(raw).hexdigest()
    if expected_inventory_sha256 is not None and inventory_sha!=expected_inventory_sha256:
        raise ValueError('canonical site inventory changed after Beta capture')
    files=_inventory_files(raw)
    prefix='fdroid/beta/repo/'
    beta={name[len(prefix):]:value for name,value in files.items() if name.startswith(prefix)}
    if not beta:
        entry_status,_=fetch(prefix+'entry.jar',max_bytes=8*1024*1024)
        if entry_status!=404:raise ValueError('served Beta bytes are absent from canonical inventory')
        return {'inventory_sha256':inventory_sha,'files':{},'release':None}
    if 'entry.jar' not in beta:
        raise ValueError('served Beta inventory lacks its signed entry index')
    if len(beta)>40000 or sum(item['bytes'] for item in beta.values())>SITE_LIMIT:
        raise ValueError('served Beta route exceeds retained file or byte budget')
    root=snapshot/'fdroid/beta/repo'
    for rel,record in sorted(beta.items()):
        status,contents=fetch(prefix+rel,max_bytes=record['bytes'])
        if status!=200 or len(contents)!=record['bytes'] or hashlib.sha256(contents).hexdigest()!=record['sha256']:
            raise ValueError('served Beta bytes differ from canonical inventory: '+rel)
        target=root/rel
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes(contents)
    data=binary_repo.verify_indexes(root,channels.BETA.repository_certificate,channels.BETA)
    if data.get('repo',{}).get('address','').rstrip('/')!=channels.BETA.repository_url.rstrip('/'):
        raise ValueError('served Beta index address differs from fixed route')
    tuples=binary_repo.release_tuples(data,channels.BETA)
    newest=max(tuples,key=lambda row:row[0]) if tuples else None
    if newest is None:raise ValueError('served Beta index has no APK version')
    return {'inventory_sha256':inventory_sha,'files':beta,'release':{'version_name':newest[1],'version_code':newest[0]}}


def verify_beta_snapshot_is_current(snapshot_info, *, fetch=None):
    """Compare-and-swap guard immediately before assembling a new whole-site snapshot."""
    if fetch is None:fetch=_read_origin
    status,raw=fetch(SITE_INVENTORY,max_bytes=SITE_INVENTORY_LIMIT)
    expected=snapshot_info['inventory_sha256']
    if expected is None:
        if status==200 and any(name.startswith('fdroid/beta/repo/') for name in _inventory_files(raw)):
            raise ValueError('Beta route appeared after Stable site capture')
        entry_status,_=fetch('fdroid/beta/repo/entry.jar',max_bytes=8*1024*1024)
        if entry_status!=404:raise ValueError('Beta route appeared after Stable site capture')
        return
    if status!=200 or hashlib.sha256(raw).hexdigest()!=expected:
        raise ValueError('served site changed after Beta capture; refusing stale whole-site publication')


def capture_live_site(destination, *, fetch=None):
    """Capture the exact Pages-served inventory for an approved promotion input."""
    destination=Path(destination)
    if destination.exists():raise ValueError('new served site snapshot directory required')
    destination.mkdir(parents=True)
    if fetch is None:fetch=_read_origin
    status,raw=fetch(SITE_INVENTORY,max_bytes=SITE_INVENTORY_LIMIT)
    if status!=200:raise ValueError('canonical site inventory must exist before snapshot capture')
    files=_inventory_files(raw)
    if len(files)>40000:raise ValueError('served inventory has too many files')
    total=0
    for name,record in sorted(files.items()):
        safe=Path(name)
        if safe.is_absolute() or '..' in safe.parts or not re.fullmatch(r'[A-Za-z0-9_./=-]+',name):
            raise ValueError('unsafe served inventory path')
        total+=record['bytes']
        if total>SITE_LIMIT:raise ValueError('served inventory exceeds site byte limit')
        status,contents=fetch(name,max_bytes=record['bytes'])
        if status!=200 or len(contents)!=record['bytes'] or hashlib.sha256(contents).hexdigest()!=record['sha256']:
            raise ValueError('served site bytes differ from inventory: '+name)
        target=destination/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(contents)
    (destination/SITE_INVENTORY).write_bytes(raw)
    for profile,route in ((channels.STABLE,'fdroid/repo'),(channels.BETA,'fdroid/beta/repo')):
        route_files={name[len(route)+1:] for name in files if name.startswith(route+'/')}
        if route_files:
            if 'entry.jar' not in route_files:raise ValueError(profile.name+' served route lacks signed entry index')
            index=binary_repo.verify_indexes(destination/route,profile.repository_certificate,profile)
            if index.get('repo',{}).get('address','').rstrip('/')!=profile.repository_url.rstrip('/'):
                raise ValueError(profile.name+' served index address differs from fixed route')
    return {'inventory_sha256':hashlib.sha256(raw).hexdigest(),'files':files}


def verify_live_site(expected_site, *, fetch=None):
    """Verify the deployed whole-site inventory and both repository routes."""
    expected_site=Path(expected_site)
    if fetch is None:fetch=_read_origin
    with tempfile.TemporaryDirectory(prefix='collie-pages-live-') as temporary:
        captured=Path(temporary)/'site'
        live=capture_live_site(captured,fetch=fetch)
        expected=(expected_site/SITE_INVENTORY).read_bytes()
        actual=(captured/SITE_INVENTORY).read_bytes()
        if expected!=actual:
            raise ValueError('deployed site inventory differs from uploaded whole-site candidate')
        expected_state=json.loads((expected_site/'feed-state.json').read_text())
        actual_state=json.loads((captured/'feed-state.json').read_text())
        if expected_state!=actual_state:
            raise ValueError('deployed feed state differs from uploaded whole-site candidate')
        for route in ('fdroid/repo','fdroid/beta/repo'):
            prefix=route+'/'
            expected_names={n for n in live['files'] if n.startswith(prefix)}
            if expected_names and 'entry.jar' not in {n[len(prefix):] for n in expected_names}:
                raise ValueError(route+' deployed route lacks an index')
        return live


def capture_deploy_baseline(destination, *, fetch=None, retained_site=None):
    """Capture a verified whole-site inventory before a scheduled Stable build."""
    destination=Path(destination)
    if destination.exists():raise ValueError('new deploy baseline path required')
    if retained_site is not None:
        retained_site=Path(retained_site)
        if destination.resolve().is_relative_to(retained_site.resolve()):
            raise ValueError('deploy baseline must be outside the retained site snapshot')
        captured=capture_live_site(retained_site,fetch=fetch)
    else:
        with tempfile.TemporaryDirectory(prefix='collie-feed-baseline-') as temporary:
            captured=capture_live_site(Path(temporary)/'site',fetch=fetch)
    value={'schema':1,'kind':'collie-pages-deploy-baseline','origin':SITE_ORIGIN,
           'inventory_sha256':captured['inventory_sha256']}
    destination.parent.mkdir(parents=True,exist_ok=True)
    destination.write_text(json.dumps(value,sort_keys=True,separators=(',',':'))+'\n')
    return value


def verify_deploy_baseline(path, *, fetch=None):
    """Require the live site to equal the exact verified inventory captured pre-build."""
    value=json.loads(Path(path).read_text())
    if (not isinstance(value,dict) or set(value)!={'schema','kind','origin','inventory_sha256'} or
            type(value.get('schema')) is not int or value['schema']!=1 or
            value['kind']!='collie-pages-deploy-baseline' or value['origin']!=SITE_ORIGIN or
            not re.fullmatch('[0-9a-f]{64}',str(value.get('inventory_sha256')))):
        raise ValueError('invalid captured Stable deploy baseline')
    with tempfile.TemporaryDirectory(prefix='collie-feed-deploy-check-') as temporary:
        current=capture_live_site(Path(temporary)/'site',fetch=fetch)
    if current['inventory_sha256']!=value['inventory_sha256']:
        raise ValueError('served site changed after Stable candidate capture; refusing stale deployment')
    return True


def build_fdroid(apks, fastlane, keystore, build_tools, work, site, profile=channels.STABLE,
                 tag=None, title=None, prior_version_code=None, prerelease=False):
    if profile.name!='stable':
        channels.require_trusted(profile)
        channels.require_apk_trusted(profile)
    candidates=sorted(Path(apks).glob('*.apk'))
    if profile.name!='stable' and (len(candidates)!=1 or candidates[0].name!=profile.apk_asset):
        raise ValueError(f'{profile.name} feed requires exactly {profile.apk_asset}')
    if profile.name=='stable' and not candidates:
        raise ValueError('stable feed requires at least one APK')
    work.mkdir(parents=True)
    shutil.copytree(HERE / ('metadata' if profile.name=='stable' else 'beta-metadata'), work / 'metadata')
    stage_fastlane(fastlane, work, profile)
    (work / 'repo').mkdir()
    builds = []
    if profile.name=='stable':
        for apk in candidates:
            code,version_name=apk_identity(apk,build_tools,profile)
            validate_release_identity(profile,apk.stem,None,version_name,code)
            if type(code) is not int or code<=0 or any(previous[0]==code for previous in builds):
                raise ValueError('stable APK versionCode must be positive and unique')
            shutil.copyfile(apk,work/'repo'/f'{profile.package}_{code}.apk')
            builds.append((code,version_name,apk.stem))
        if tag is not None:
            selected=[code for code,_name,source_tag in builds if source_tag==tag]
            if len(selected)!=1 or any(code>selected[0] for code,_name,_tag in builds):
                raise ValueError('selected full release must be present and have the highest indexed versionCode')
    else:
        if not prerelease:raise ValueError('Beta feed accepts prerelease releases only')
        apk=candidates[0]
        code,version_name=apk_identity(apk,build_tools,profile)
        release_identity(profile,tag,title,version_name,code,prior_version_code)
        shutil.copyfile(apk, work / 'repo' / f'{profile.package}_{code}.apk')
        builds.append((code, version_name, tag))
    # index-v2 only carries a changelog for versions that have a Builds entry. Nothing is built.
    with open(work / 'metadata' / f'{profile.package}.yml', 'a') as metadata:
        metadata.write('Builds:\n')
        for code, version, commit in sorted(builds):
            metadata.write(f"  - versionName: '{version}'\n    versionCode: {code}\n    commit: {commit}\n")
    if profile.name!='stable':
        metadata_path=work/'metadata'/f'{profile.package}.yml'
        text=metadata_path.read_text()
        if f"  - '{profile.apk_certificate}'" not in text:
            raise ValueError('Beta metadata APK signer pin differs from channel profile')
    config = channel_repository_config((HERE / 'config.public.yml').read_text(),profile)
    config += (f'\nkeystore: {Path(keystore).resolve()}\nrepo_keyalias: collie-repo\n'
               'keystorepass: {env: FDROID_KEYSTORE_PASS}\nkeypass: {env: FDROID_KEYSTORE_PASS}\n'
               f'sdk_path: {build_tools.parent.parent}\n')
    (work / 'config.yml').write_text(config)
    (work / 'config.yml').chmod(0o600)
    subprocess.run(['fdroid', 'update', '--pretty'], cwd=work, check=True)
    validate_generated_index(work,profile)
    repo_path='fdroid/repo' if profile.name=='stable' else 'fdroid/beta/repo'
    shutil.copytree(work / 'repo', site / repo_path,
                    ignore=shutil.ignore_patterns('*.asc', 'status'))


def build_beta_route(apks, fastlane, tag, keystore, build_tools, work, site, previous_release, *, preview=False):
    """Rebuild fdroid/beta/repo from one verified TrailBlazer prerelease APK.

    The release itself (immutable prerelease, signed tag, byte match with its CI
    build) is checked by beta_inputs.py before this runs. Here the APK must carry
    the Beta package and signer and a versionCode above the served Beta index.
    """
    profile=channels.BETA
    version_name=tag[len(profile.tag_prefix):]
    prior=previous_release['version_code'] if previous_release else 0
    build_fdroid(apks,fastlane,keystore,Path(build_tools),Path(work),Path(site),profile,tag=tag,
                 title=f'{profile.app_title} {version_name}',prior_version_code=prior,prerelease=True)
    route=Path(site)/'fdroid/beta/repo'
    if preview:
        data=json.loads((route/'index-v2.json').read_text())
    else:
        data=binary_repo.verify_indexes(route,profile.repository_certificate,profile)
    tuples=binary_repo.release_tuples(data,profile)
    if len(tuples)!=1 or next(iter(tuples))[1]!=version_name:
        raise ValueError('generated Beta index must contain exactly the selected release')
    return {'version_name':version_name,'version_code':next(iter(tuples))[0]}


def keystore_fingerprint(keystore):
    der = subprocess.run(['keytool', '-exportcert', '-keystore', str(keystore), '-storetype', 'PKCS12',
                          '-alias', 'collie-repo', '-storepass:env', 'FDROID_KEYSTORE_PASS'],
                         check=True, capture_output=True).stdout
    return hashlib.sha256(der).hexdigest()


def main():
    if len(sys.argv)>1 and sys.argv[1]=='verify-uploaded-archive':
        parser=argparse.ArgumentParser();parser.add_argument('archive',type=Path)
        args=parser.parse_args(sys.argv[2:])
        size=retained_artifacts.verify_pages_archive(args.archive,byte_limit=SITE_LIMIT)
        print(json.dumps({'pages_archive_bytes':size,'maximum_bytes':SITE_LIMIT}))
        return
    if len(sys.argv)>1 and sys.argv[1]=='capture-deploy-baseline':
        parser=argparse.ArgumentParser();parser.add_argument('output',type=Path)
        parser.add_argument('--retain-site',type=Path,
                            help='keep the same verified snapshot for immutable artifact retention')
        args=parser.parse_args(sys.argv[2:])
        capture_deploy_baseline(args.output,retained_site=args.retain_site)
        print('captured verified canonical Stable/Beta site baseline')
        return
    p = argparse.ArgumentParser()
    for name in ('site', 'apks', 'native', 'native-plan', 'latest', 'fastlane', 'keystore', 'build-tools',
                 'capture-live-site'):
        p.add_argument('--' + name, required=False)
    p.add_argument('--channel', choices=('stable',), default='stable')
    p.add_argument('--title')
    p.add_argument('--prior-version-code', type=int)
    p.add_argument('--prerelease', action='store_true')
    p.add_argument('--preview', action='store_true',
                   help='render an unpublished Stable preview with a disposable repository key')
    p.add_argument('--beta-apks',type=Path,
                   help='directory holding the verified trailblazer-beta.apk: rebuild the Beta route in this site')
    p.add_argument('--beta-fastlane',type=Path,help='Beta listing exported from the Beta release tag')
    p.add_argument('--beta-tag',help='verified immutable TrailBlazer prerelease tag')
    p.add_argument('--preserve-beta',action='store_true',
                   help='retain the current verified Beta route in a Stable whole-site build')
    p.add_argument('--previous-site',type=Path,
                   help='retain published immutable URLs from this frozen pre-build site snapshot')
    p.add_argument('--previous-baseline',type=Path,
                   help='the deploy baseline captured together with --previous-site')
    p.add_argument('--keep-native-plan',type=Path,action='append',default=[],
                   help='native plan of another current release (the served Beta); its files stay with the plan copied into the site')
    p.add_argument('--verify-live-site',action='store_true',
                   help='verify the uploaded candidate inventory and both signed routes against Pages')
    a = p.parse_args()
    if a.capture_live_site:
        capture_live_site(a.capture_live_site)
        print('captured exact canonical site inventory and verified signed Stable/Beta indexes')
        return
    if a.verify_live_site:
        if not a.site:p.error('--site candidate directory is required with --verify-live-site')
        verify_live_site(a.site)
        print('deployed site inventory and signed Stable/Beta indexes match candidate')
        return
    profile=channels.STABLE if a.channel=='stable' else channels.BETA
    if bool(a.previous_site)!=bool(a.previous_baseline):
        p.error('--previous-site and --previous-baseline are required together')
    if a.previous_site and (profile.name!='stable' or a.preview):
        raise ValueError('immutable artifact retention is only for a production Stable site build')
    if a.preview and profile.name!='stable':
        raise ValueError('Preview generation supports Stable only')
    required=('apks','fastlane','keystore','build_tools')
    if profile.name=='stable':required+=('native','native_plan','latest')
    missing=[name for name in required if not getattr(a,name)]
    if missing:p.error('required for '+profile.name+' generation: '+', '.join('--'+x.replace('_','-') for x in missing))
    rebuild_beta=a.beta_apks is not None
    if rebuild_beta:
        if profile.name!='stable' or a.preserve_beta or not (a.beta_fastlane and a.beta_tag):
            p.error('--beta-apks rebuilds the Beta route in a Stable site build and needs --beta-fastlane and --beta-tag')
        if not a.preview and not a.previous_site:
            p.error('a production Beta rebuild needs --previous-site for its versionCode floor')
        if not a.beta_tag.startswith(channels.BETA.tag_prefix):
            p.error('--beta-tag must be a TrailBlazer tag')
    site, work = Path(a.site), Path(a.site).parent / 'fdroid-work'
    beta_work=Path(a.site).parent/'fdroid-beta-work'
    if site.exists() or work.exists() or beta_work.exists():
        raise SystemExit('site and work directories must not exist yet')
    if not os.environ.get('FDROID_KEYSTORE_PASS'):
        raise SystemExit('FDROID_KEYSTORE_PASS is not set')
    previous=None;native_keep=None
    if a.previous_site:
        previous=retained_artifacts.load_previous_site(a.previous_site,a.previous_baseline,
            parse_inventory=_inventory_files,byte_limit=SITE_LIMIT)
        previous.retained_files(a.preserve_beta,rebuild_beta)
    build_tools = Path(a.build_tools)
    site.mkdir(parents=True)
    native_count = copy_native(a.native, a.native_plan, site) if a.native and a.native_plan else 0
    if previous is not None:
        # Native files stay only while a current release plan names them: the one just copied
        # into the site, and the plan of the served Beta (--keep-native-plan).
        native_keep={p.relative_to(site).as_posix() for p in (site/'native-inputs').rglob('*') if p.is_file()}
        for plan in a.keep_native_plan:
            native_keep.update(release_inputs.native_records(json.loads(plan.read_text())))
    build_fdroid(a.apks, a.fastlane, a.keystore, build_tools, work, site, profile,
                 tag=a.latest, title=a.title, prior_version_code=a.prior_version_code,
                 prerelease=a.prerelease)
    retained_beta=None
    built_beta=None
    if rebuild_beta:
        built_beta=build_beta_route(a.beta_apks,a.beta_fastlane,a.beta_tag,a.keystore,build_tools,beta_work,site,
                                    previous.beta_release if previous is not None else None,preview=a.preview)
    if a.preserve_beta:
        if profile.name!='stable' or a.preview:
            raise ValueError('Beta retention is only for a production Stable site build')
        if previous is not None:
            retained_beta=previous.beta
        else:
            retained_beta=capture_beta_route(work.parent/'verified-beta')
            for rel in retained_beta['files']:
                source=work.parent/'verified-beta'/'fdroid/beta/repo'/rel
                dest=site/'fdroid/beta/repo'/rel
                dest.parent.mkdir(parents=True,exist_ok=True)
                shutil.copyfile(source,dest)
            verify_beta_snapshot_is_current(retained_beta)
    fingerprint = keystore_fingerprint(a.keystore)
    if not a.preview and (profile.repository_certificate is None or fingerprint != profile.repository_certificate):
        raise ValueError(f'{profile.name} repository signer pin is unset or differs')
    if profile.name=='stable':
        site_page.render(site, REPO_URL, fingerprint, profile.apk_certificate,
                         f'{APP_RELEASES}{a.latest}/collie.apk', state='preview' if a.preview else 'active')
    (site / 'CNAME').write_text('repo.collie.im\n')
    repo_path='fdroid/repo' if profile.name=='stable' else 'fdroid/beta/repo'
    site_names=lambda: {p.relative_to(site).as_posix() for p in site.rglob('*') if p.is_file()}
    if previous is not None:
        native_paths={p.relative_to(site).as_posix() for p in (site/'native-inputs').rglob('*') if p.is_file()}
        native_paths.update(name for name in previous.retained_files(a.preserve_beta,rebuild_beta,site_names(),native_keep)
                            if name.startswith('native-inputs/'))
        native_count=len(native_paths)
    state = {'schema': 1, 'latest': a.latest, 'repository_fingerprint': fingerprint,
             'apks': sorted(p.name for p in (site / repo_path).glob('*.apk')),
             'native_files': native_count}
    if previous is not None:
        # The selected APKs alone enter the new signed index. Older URLs remain
        # downloadable for clients using a previously cached signed index.
        state['retained_apks']=sorted(Path(name).name for name in previous.retained_files(a.preserve_beta,rebuild_beta,site_names(),native_keep)
            if name.startswith('fdroid/repo/') and name.endswith('.apk') and Path(name).name not in state['apks'])
    if profile.name!='stable':state['channel']=profile.name
    if a.preview:state['preview']=True
    (site / 'feed-state.json').write_text(json.dumps(state, indent=1) + '\n')
    beta_release=built_beta or (retained_beta['release'] if retained_beta is not None else None)
    if beta_release is not None:
        state['beta']={'version_name':beta_release['version_name'],'version_code':beta_release['version_code']}
        (site/'feed-state.json').write_text(json.dumps(state,indent=1)+'\n')
    if previous is not None:
        retained_artifacts.retain(site,previous,preserve_beta=a.preserve_beta,rebuild_beta=rebuild_beta,
                                  byte_limit=SITE_LIMIT,native_keep=native_keep)
        # This compares the live manifest with the original frozen capture. It
        # never substitutes a newly captured site or a newer Beta generation.
        verify_beta_snapshot_is_current(previous.beta)
    public_files={p.relative_to(site).as_posix():{'sha256':sha256(p),'bytes':p.stat().st_size}
                  for p in sorted(site.rglob('*')) if p.is_file() and p.name!=SITE_INVENTORY}
    (site/SITE_INVENTORY).write_text(json.dumps({'schema':1,'kind':'collie-pages-public-inventory',
        'origin':SITE_ORIGIN,'files':public_files},sort_keys=True,separators=(',',':'))+'\n')
    total = sum(f.stat().st_size for f in site.rglob('*') if f.is_file())
    if total > SITE_LIMIT:
        raise SystemExit(f'site is {total} bytes, over the {SITE_LIMIT} byte limit; '
                         'a separately reviewed retirement or storage migration is required')
    print(json.dumps({**state, 'site_bytes': total}, indent=1))


if __name__ == '__main__':
    main()
