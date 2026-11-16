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
import argparse, hashlib, json, os, re, shutil, subprocess, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import binary_repo, site_page  # noqa: E402
import channel_profile as channels  # noqa: E402

ORIGIN = 'https://repo.collie.im/'
REPO_URL = ORIGIN + 'fdroid/repo'
APP_RELEASES = 'https://github.com/0cwa/collieim-android/releases/download/'
SITE_LIMIT = 950_000_000


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def copy_native(native, plan, site):
    files = json.loads(Path(plan).read_text())['files']
    for entry in files:
        rel = entry['path']
        if rel.startswith('/') or '..' in Path(rel).parts:
            raise SystemExit(f'bad native path {rel}')
        src = Path(native) / rel
        if src.stat().st_size != entry['bytes'] or sha256(src) != entry['sha256']:
            raise SystemExit(f'native file does not match plan: {rel}')
        dest = site / 'native-inputs/v1/maven' / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
    return len(files)


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
            code,_version_name=apk_identity(apk,build_tools,profile)
            version_name=apk.stem.removeprefix('v')
            shutil.copyfile(apk,work/'repo'/f'{profile.package}_{code}.apk')
            builds.append((code,version_name,apk.stem))
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


def keystore_fingerprint(keystore):
    der = subprocess.run(['keytool', '-exportcert', '-keystore', str(keystore), '-storetype', 'PKCS12',
                          '-alias', 'collie-repo', '-storepass:env', 'FDROID_KEYSTORE_PASS'],
                         check=True, capture_output=True).stdout
    return hashlib.sha256(der).hexdigest()


def main():
    p = argparse.ArgumentParser()
    for name in ('site', 'apks', 'native', 'native-plan', 'latest', 'fastlane', 'keystore', 'build-tools'):
        p.add_argument('--' + name, required=True)
    p.add_argument('--channel', choices=('stable','trailblazer-beta'), default='stable')
    p.add_argument('--title')
    p.add_argument('--prior-version-code', type=int)
    p.add_argument('--prerelease', action='store_true')
    a = p.parse_args()
    profile=channels.STABLE if a.channel=='stable' else channels.BETA
    site, work = Path(a.site), Path(a.site).parent / 'fdroid-work'
    if site.exists() or work.exists():
        raise SystemExit('site and work directories must not exist yet')
    if not os.environ.get('FDROID_KEYSTORE_PASS'):
        raise SystemExit('FDROID_KEYSTORE_PASS is not set')
    build_tools = Path(a.build_tools)
    site.mkdir(parents=True)
    native_count = copy_native(a.native, a.native_plan, site)
    build_fdroid(a.apks, a.fastlane, a.keystore, build_tools, work, site, profile,
                 tag=a.latest, title=a.title, prior_version_code=a.prior_version_code,
                 prerelease=a.prerelease)
    fingerprint = keystore_fingerprint(a.keystore)
    if profile.repository_certificate is None or fingerprint != profile.repository_certificate:
        raise ValueError(f'{profile.name} repository signer pin is unset or differs')
    if profile.name=='stable':
        site_page.render(site, REPO_URL, fingerprint, profile.apk_certificate,
                         f'{APP_RELEASES}{a.latest}/collie.apk', state='active')
    (site / 'CNAME').write_text('repo.collie.im\n')
    repo_path='fdroid/repo' if profile.name=='stable' else 'fdroid/beta/repo'
    state = {'schema': 1, 'latest': a.latest, 'repository_fingerprint': fingerprint,
             'apks': sorted(p.name for p in (site / repo_path).glob('*.apk')),
             'native_files': native_count}
    if profile.name!='stable':state['channel']=profile.name
    (site / 'feed-state.json').write_text(json.dumps(state, indent=1) + '\n')
    total = sum(f.stat().st_size for f in site.rglob('*') if f.is_file())
    if total > SITE_LIMIT:
        raise SystemExit(f'site is {total} bytes, over the {SITE_LIMIT} byte limit; keep fewer releases')
    print(json.dumps({**state, 'site_bytes': total}, indent=1))


if __name__ == '__main__':
    main()
