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
from dataclasses import replace
from pathlib import Path
from urllib.parse import unquote, urlparse

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import beta_inputs, binary_repo, site_page  # noqa: E402
import channel_profile as channels  # noqa: E402
import retained_artifacts  # noqa: E402
import release_inputs  # noqa: E402

ORIGIN = 'https://repo.collie.im/'
REPO_URL = channels.REPOSITORY_URL
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
    staging = work / 'build' / profile.package / 'fastlane/metadata/android'
    shutil.copytree(source, staging)
    if profile == channels.STABLE:
        shutil.copyfile(source / 'en-US/images/icon.png', work / 'collie-icon.png')
    else:
        # Older immutable TrailBlazer tags have an icon and title but no store descriptions.
        for fallback in sorted((HERE / 'beta-metadata/en-US').glob('*.txt')):
            target = staging / 'en-US' / fallback.name
            if not target.exists():
                shutil.copyfile(fallback, target)


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


def verify_inventory_is_current(expected, *, fetch=None):
    """Compare the whole-site inventory with the one frozen before the build."""
    if fetch is None:
        fetch = _read_origin
    status, raw = fetch(SITE_INVENTORY, max_bytes=SITE_INVENTORY_LIMIT)
    if status != 200 or hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError('served site changed after capture; refusing stale whole-site publication')


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
    for route in (retained_artifacts.STABLE_ROUTE, retained_artifacts.BETA_ROUTE):
        route_files = {name[len(route):] for name in files if name.startswith(route)}
        if route_files:
            if 'entry.jar' not in route_files:
                raise ValueError(route + ' served route lacks signed entry index')
            retained_artifacts.verify_route(destination, files, route)
    return {'inventory_sha256':hashlib.sha256(raw).hexdigest(),'files':files}


def verify_live_site(expected_site, *, fetch=None):
    """Verify the deployed whole-site inventory and signed repository."""
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
        legacy = retained_artifacts.BETA_ROUTE
        if expected_state.get('beta') and not any(name.startswith(legacy) for name in live['files']):
            for name in ('entry.jar', 'index-v1.jar', 'index-v2.json'):
                status, _ = fetch(legacy + name, max_bytes=8 * 1024 * 1024)
                if status != 404:
                    raise ValueError('retired Beta index is still served: ' + name)
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


def stage_app(work, fastlane, profile, builds):
    metadata_path = HERE / ('metadata' if profile == channels.STABLE else 'beta-metadata')
    metadata_path = metadata_path / f'{profile.package}.yml'
    text = metadata_path.read_text()
    channels.require_apk_trusted(profile)
    if f"  - '{profile.apk_certificate}'" not in text:
        raise ValueError(f'{profile.name} metadata APK signer pin differs from channel profile')
    stage_fastlane(fastlane, work, profile)
    # F-Droid includes changelogs only for versions with a Builds entry.
    with (work / 'metadata' / metadata_path.name).open('w') as metadata:
        metadata.write(text + 'Builds:\n')
        for code, version, tag, _apk in sorted(builds):
            metadata.write(f"  - versionName: '{version}'\n    versionCode: {code}\n    commit: {tag}\n")
    for code, _version, _tag, apk in builds:
        shutil.copyfile(apk, work / 'repo' / f'{profile.package}_{code}.apk')


def build_fdroid(apks, fastlane, keystore, build_tools, work, site, *, tag, fingerprint,
                 beta_apk=None, beta_fastlane=None, beta_tag=None, previous_beta=None):
    stable, beta = channels.STABLE, channels.BETA
    builds = []
    for apk in sorted(Path(apks).glob('*.apk')):
        code, version = apk_identity(apk, build_tools, stable)
        validate_release_identity(stable, apk.stem, None, version, code)
        if type(code) is not int or code <= 0 or any(row[0] == code for row in builds):
            raise ValueError('stable APK versionCode must be positive and unique')
        builds.append((code, version, apk.stem, apk))
    if not builds:
        raise ValueError('stable feed requires at least one APK')
    if max(builds)[2] != tag:
        raise ValueError('selected full release must be present and have the highest indexed versionCode')
    (work / 'repo').mkdir(parents=True)
    (work / 'metadata').mkdir()
    stage_app(work, fastlane, stable, builds)
    beta_builds = []
    if beta_apk is not None:
        code, version = apk_identity(beta_apk, build_tools, beta)
        title = f'{beta.app_title} {version}'
        prior_code = previous_beta['version_code'] if previous_beta else 0
        if code == prior_code and previous_beta and sha256(beta_apk) == previous_beta['sha256']:
            validate_release_identity(beta, beta_tag, title, version, code)
            if version != previous_beta['version_name']:
                raise ValueError('carried Beta version differs from the signed previous index')
        else:
            release_identity(beta, beta_tag, title, version, code, prior_code)
        beta_builds = [(code, version, beta_tag, beta_apk)]
        stage_app(work, beta_fastlane, beta, beta_builds)

    config = (HERE / 'config.public.yml').read_text()
    config += (f'\nkeystore: {Path(keystore).resolve()}\nrepo_keyalias: collie-repo\n'
               'keystorepass: {env: FDROID_KEYSTORE_PASS}\nkeypass: {env: FDROID_KEYSTORE_PASS}\n'
               f'sdk_path: {build_tools.parent.parent}\n')
    (work / 'config.yml').write_text(config)
    (work / 'config.yml').chmod(0o600)
    subprocess.run(['fdroid', 'update', '--pretty'], cwd=work, check=True, stdout=sys.stderr)

    # The caller checks the production pin before building. Preview uses its disposable
    # fingerprint and still verifies both index signatures and each real APK signer.
    signing_profile = replace(stable, repository_certificate=fingerprint)
    additional = (replace(beta, repository_certificate=fingerprint),) if beta_builds else ()
    repo = work / 'repo'
    index = binary_repo.verify_indexes(repo, fingerprint, signing_profile, additional_profiles=additional)
    if index['repo']['address'].rstrip('/') != REPO_URL.rstrip('/'):
        raise ValueError('generated index address differs from the canonical route')
    for profile, selected in [(stable, builds)] + ([(beta, beta_builds)] if beta_builds else []):
        expected = {(code, version, sha256(apk), f'{profile.package}_{code}.apk', profile.apk_certificate)
                    for code, version, _tag, apk in selected}
        if binary_repo.release_tuples(index, profile) != expected:
            raise ValueError(f'generated index differs from the selected {profile.name} releases')
        metadata = index['packages'][profile.package]['metadata']
        if not metadata.get('categories') or any(
                not metadata.get(field, {}).get('en-US') for field in ('name', 'summary', 'description', 'icon')):
            raise ValueError(f'generated {profile.name} index lacks store listing metadata')
    shutil.copytree(repo, site / retained_artifacts.STABLE_ROUTE,
                    ignore=shutil.ignore_patterns('*.asc', 'status'))
    if beta_builds:
        code, version, _, _ = beta_builds[0]
        return {'version_name': version, 'version_code': code}


def carried_beta_inputs(previous, work):
    """Reuse the signed, previously verified APK and export listing inputs from its immutable tag."""
    carried = previous.beta_release
    tag = channels.BETA.tag_prefix + carried['version_name']
    release = beta_inputs.fetch_release(tag)
    record = previous.files[carried['path']]
    if (release['sha256'], release['bytes']) != (record['sha256'], record['bytes']):
        raise ValueError('served Beta APK differs from the immutable GitHub release')
    commit, _ = beta_inputs.tag_commit(tag)
    work.mkdir()
    listing = work / 'fastlane'
    evidence = beta_inputs.export_metadata(
        {'kind': 'collie-beta-feed-input', 'repository': beta_inputs.APP, 'tag': tag,
         'source_commit': commit}, listing)
    native_plan = work / 'native-artifacts.json'
    native_evidence = beta_inputs.export_native_plan(tag, native_plan)
    if native_evidence['source_commit'] != commit:
        raise ValueError('served Beta tag changed during metadata export')
    (work / 'metadata-inputs.json').write_text(json.dumps(evidence, indent=2) + '\n')
    return previous.root / carried['path'], listing, tag, native_plan


def keystore_fingerprint(keystore):
    der = subprocess.run(['keytool', '-exportcert', '-keystore', str(keystore), '-storetype', 'PKCS12',
                          '-alias', 'collie-repo', '-storepass:env', 'FDROID_KEYSTORE_PASS'],
                         check=True, capture_output=True).stdout
    return hashlib.sha256(der).hexdigest()


def main():
    if len(sys.argv)>1 and sys.argv[1]=='prepare-stable-previous-withdrawal':
        parser=argparse.ArgumentParser(description='Prepare an offline Pages candidate omitting only the previous unindexed Stable APK and updating the captured feed state')
        for name in ('previous-site', 'previous-baseline', 'output'):
            parser.add_argument('--'+name,type=Path,required=True)
        parser.add_argument('--inventory-sha256',required=True)
        parser.add_argument('--apk-version-code',type=int,required=True)
        parser.add_argument('--apk-sha256',required=True)
        parser.add_argument('--apk-bytes',type=int,required=True)
        args=parser.parse_args(sys.argv[2:])
        previous=retained_artifacts.load_previous_site(
            args.previous_site,args.previous_baseline,parse_inventory=_inventory_files)
        result=retained_artifacts.prepare_stable_previous_withdrawal(
            args.output,previous,inventory_sha256=args.inventory_sha256,
            apk_version_code=args.apk_version_code,apk_sha256=args.apk_sha256,apk_bytes=args.apk_bytes)
        print(json.dumps(result))
        return
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
    p.add_argument('--preview', action='store_true',
                   help='render an unpublished preview with a disposable repository key')
    p.add_argument('--beta-apks', type=Path,
                   help='directory holding the new verified trailblazer-beta.apk')
    p.add_argument('--beta-fastlane', type=Path, help='listing exported from the new Beta release tag')
    p.add_argument('--beta-tag', help='new verified immutable TrailBlazer prerelease tag')
    p.add_argument('--migration-beta-sha256', help='approved digest of the new updater migration APK')
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
    if bool(a.previous_site) != bool(a.previous_baseline):
        p.error('--previous-site and --previous-baseline are required together')
    required = ('site', 'apks', 'fastlane', 'keystore', 'build_tools', 'native', 'native_plan', 'latest')
    missing = [name for name in required if not getattr(a, name)]
    if missing:
        p.error('required for site generation: ' + ', '.join('--' + key.replace('_', '-') for key in missing))
    if any((a.beta_apks, a.beta_fastlane, a.beta_tag)):
        if not all((a.beta_apks, a.beta_fastlane, a.beta_tag)):
            p.error('--beta-apks, --beta-fastlane and --beta-tag are required together')
        if not a.beta_tag.startswith(channels.BETA.tag_prefix):
            p.error('--beta-tag must be a TrailBlazer tag')
        if not a.preview and not a.previous_site:
            p.error('a production Beta update needs --previous-site for its versionCode floor')

    site = Path(a.site)
    work = site.parent / 'fdroid-work'
    if site.exists() or work.exists():
        raise SystemExit('site and work directories must not exist yet')
    if not os.environ.get('FDROID_KEYSTORE_PASS'):
        raise SystemExit('FDROID_KEYSTORE_PASS is not set')
    fingerprint = keystore_fingerprint(a.keystore)
    if not a.preview and fingerprint != channels.REPOSITORY_CERTIFICATE:
        raise ValueError('Collie repository signer pin differs')
    previous = None
    if a.previous_site:
        previous = retained_artifacts.load_previous_site(
            a.previous_site, a.previous_baseline, parse_inventory=_inventory_files, byte_limit=SITE_LIMIT)

    beta_apk = None
    beta_fastlane, beta_tag = a.beta_fastlane, a.beta_tag
    if a.beta_apks:
        beta_apk = a.beta_apks / channels.BETA.apk_asset
        if sorted(a.beta_apks.glob('*.apk')) != [beta_apk]:
            raise ValueError(f'Beta feed requires exactly {channels.BETA.apk_asset}')
    elif previous and previous.beta_release:
        beta_apk, beta_fastlane, beta_tag, native_plan = carried_beta_inputs(
            previous, site.parent / 'served-beta-inputs')
        a.keep_native_plan.append(native_plan)
    if a.migration_beta_sha256:
        previous_beta = previous.beta_release if previous else None
        if (not a.beta_apks or not previous_beta or
                previous_beta['sha256'] == a.migration_beta_sha256 or
                sha256(beta_apk) != a.migration_beta_sha256):
            raise ValueError('migration requires the approved new Beta APK, distinct from the served signed release')

    site.mkdir(parents=True)
    copy_native(a.native, a.native_plan, site)
    beta_release = build_fdroid(
        a.apks, a.fastlane, a.keystore, Path(a.build_tools), work, site, tag=a.latest,
        fingerprint=fingerprint, beta_apk=beta_apk, beta_fastlane=beta_fastlane, beta_tag=beta_tag,
        previous_beta=previous.beta_release if previous else None)
    site_page.render(site, REPO_URL, fingerprint, channels.STABLE.apk_certificate,
                     f'{APP_RELEASES}{a.latest}/collie.apk', state='preview' if a.preview else 'active',
                     include_trailblazer=beta_release is not None)
    (site / 'CNAME').write_text('repo.collie.im\n')
    apks = sorted(p.name for p in (site / retained_artifacts.STABLE_ROUTE).glob('*.apk'))
    native_paths = {p.relative_to(site).as_posix() for p in (site / 'native-inputs').rglob('*') if p.is_file()}
    state = {'schema': 1, 'latest': a.latest, 'repository_fingerprint': fingerprint,
             'apks': apks, 'native_files': len(native_paths)}
    if a.preview:
        state['preview'] = True
    if beta_release is not None:
        state['beta'] = beta_release
    if previous:
        native_keep = set(native_paths)
        for plan in a.keep_native_plan:
            records = release_inputs.native_records(json.loads(plan.read_text()))
            for name, record in records.items():
                if name in native_paths:
                    candidate = site / name
                    actual = {'bytes': candidate.stat().st_size, 'sha256': sha256(candidate)}
                else:
                    actual = previous.files.get(name)
                if actual != {key: record[key] for key in ('bytes', 'sha256')}:
                    raise ValueError('Beta native file differs from the signed tag plan: ' + name)
            native_keep.update(records)
        current = {path.relative_to(site).as_posix() for path in site.rglob('*') if path.is_file()}
        retained = previous.retained_files(current=current, native_keep=native_keep)
        native_paths.update(name for name in retained if name.startswith('native-inputs/'))
        state['native_files'] = len(native_paths)
        state['retained_apks'] = sorted(Path(name).name for name in retained
                                        if name.endswith('.apk') and Path(name).name not in apks)
    (site / 'feed-state.json').write_text(json.dumps(state, indent=1) + '\n')
    if previous:
        retained_artifacts.retain(site, previous, byte_limit=SITE_LIMIT, native_keep=native_keep)
        verify_inventory_is_current(previous.inventory_record['sha256'])
    public_files={p.relative_to(site).as_posix():{'sha256':sha256(p),'bytes':p.stat().st_size}
                  for p in sorted(site.rglob('*')) if p.is_file() and p.name!=SITE_INVENTORY}
    (site/SITE_INVENTORY).write_text(json.dumps({'schema':1,'kind':'collie-pages-public-inventory',
        'origin':SITE_ORIGIN,'files':public_files},sort_keys=True,separators=(',',':'))+'\n')
    total = sum(f.stat().st_size for f in site.rglob('*') if f.is_file())
    if total >= SITE_LIMIT:
        raise SystemExit(f'site is {total} bytes, over the {SITE_LIMIT} byte limit; '
                         'a separately reviewed retirement or storage migration is required')
    print(json.dumps({**state, 'site_bytes': total}, indent=1))


if __name__ == '__main__':
    main()
