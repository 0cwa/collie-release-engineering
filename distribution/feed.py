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


def apk_identity(apk, build_tools):
    badging = subprocess.run([str(build_tools / 'aapt2'), 'dump', 'badging', str(apk)],
                             check=True, capture_output=True, text=True).stdout
    m = re.search(r"package: name='([^']+)' versionCode='(\d+)'", badging)
    certs = subprocess.run([str(build_tools / 'apksigner'), 'verify', '--print-certs', str(apk)],
                           check=True, capture_output=True, text=True).stdout
    digests = re.findall(r'certificate SHA-256 digest: ([0-9a-f]{64})', certs)
    if not m or m.group(1) != binary_repo.APP or digests != [binary_repo.APK_CERTIFICATE]:
        raise SystemExit(f'{apk.name}: not a Collie APK signed with the release certificate')
    return int(m.group(2))


def stage_fastlane(fastlane, work):
    # fdroid update reads build/<app>/fastlane and fills the listing, app icon and changelog.
    # The same icon is the repository's own icon, which fdroid would otherwise generate.
    source = Path(fastlane)
    if any(p.is_symlink() for p in source.rglob('*')):
        raise SystemExit('symlink in Fastlane metadata')
    shutil.copytree(source, work / 'build' / binary_repo.APP / 'fastlane/metadata/android')
    shutil.copyfile(source / 'en-US/images/icon.png', work / 'collie-icon.png')


def build_fdroid(apks, fastlane, keystore, build_tools, work, site):
    work.mkdir(parents=True)
    shutil.copytree(HERE / 'metadata', work / 'metadata')
    stage_fastlane(fastlane, work)
    (work / 'repo').mkdir()
    builds = []
    for apk in sorted(Path(apks).glob('*.apk')):
        code = apk_identity(apk, build_tools)
        shutil.copyfile(apk, work / 'repo' / f'{binary_repo.APP}_{code}.apk')
        builds.append((code, apk.stem))
    # index-v2 only carries a changelog for versions that have a Builds entry. Nothing is built.
    with open(work / 'metadata' / f'{binary_repo.APP}.yml', 'a') as metadata:
        metadata.write('Builds:\n')
        for code, tag in sorted(builds):
            metadata.write(f"  - versionName: '{tag.removeprefix('v')}'\n    versionCode: {code}\n    commit: {tag}\n")
    config = (HERE / 'config.public.yml').read_text()
    config += (f'\nkeystore: {Path(keystore).resolve()}\nrepo_keyalias: collie-repo\n'
               'keystorepass: {env: FDROID_KEYSTORE_PASS}\nkeypass: {env: FDROID_KEYSTORE_PASS}\n'
               f'sdk_path: {build_tools.parent.parent}\n')
    (work / 'config.yml').write_text(config)
    (work / 'config.yml').chmod(0o600)
    subprocess.run(['fdroid', 'update', '--pretty'], cwd=work, check=True)
    shutil.copytree(work / 'repo', site / 'fdroid/repo',
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
    a = p.parse_args()
    site, work = Path(a.site), Path(a.site).parent / 'fdroid-work'
    if site.exists() or work.exists():
        raise SystemExit('site and work directories must not exist yet')
    if not os.environ.get('FDROID_KEYSTORE_PASS'):
        raise SystemExit('FDROID_KEYSTORE_PASS is not set')
    build_tools = Path(a.build_tools)
    site.mkdir(parents=True)
    native_count = copy_native(a.native, a.native_plan, site)
    build_fdroid(a.apks, a.fastlane, a.keystore, build_tools, work, site)
    fingerprint = keystore_fingerprint(a.keystore)
    site_page.render(site, REPO_URL, fingerprint, binary_repo.APK_CERTIFICATE,
                     f'{APP_RELEASES}{a.latest}/collie.apk', state='active')
    (site / 'CNAME').write_text('repo.collie.im\n')
    state = {'schema': 1, 'latest': a.latest, 'repository_fingerprint': fingerprint,
             'apks': sorted(p.name for p in (site / 'fdroid/repo').glob('*.apk')),
             'native_files': native_count}
    (site / 'feed-state.json').write_text(json.dumps(state, indent=1) + '\n')
    total = sum(f.stat().st_size for f in site.rglob('*') if f.is_file())
    if total > SITE_LIMIT:
        raise SystemExit(f'site is {total} bytes, over the {SITE_LIMIT} byte limit; keep fewer releases')
    print(json.dumps({**state, 'site_bytes': total}, indent=1))


if __name__ == '__main__':
    main()
