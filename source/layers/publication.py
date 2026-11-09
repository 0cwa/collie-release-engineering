#!/usr/bin/env python3
"""Materialize reviewed publication/build inputs before source publication."""
from pathlib import Path
import json
import re
import shutil
import subprocess

RECIPE_REVISION = '50ba71e8304af241568c845dbd8b44b03ee1552e'
RECONSTRUCTED_TREE = 'da5c03ff20ae2a672329eb8257bc33949845b268'


def git_environment():
    import os
    env = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
    env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
               GIT_NO_REPLACE_OBJECTS='1', GIT_NO_LAZY_FETCH='1', LC_ALL='C')
    return env


def _asset_source_path(previous_application, application_base):
    trees = {}
    for name in ('docs/assets', 'assets'):
        result = subprocess.run(
            ['git', '-C', str(previous_application), 'ls-tree', '-r', '-z',
             '--full-tree', application_base, '--', name],
            env=git_environment(), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        if result.returncode != 0:
            raise ValueError('cannot inspect approved application assets')
        trees[name] = bool(result.stdout)
    if all(trees.values()):
        raise ValueError('approved application contains both old and grouped assets')
    if trees['docs/assets']:
        return 'docs/assets'
    if trees['assets']:
        return 'assets'
    raise ValueError('approved application assets are missing')


def _published_source_dates(previous_application, application_base):
    """Retain the trusted publication-date note when regenerating the build guide."""
    locations = {}
    for path in ('BUILD.md', 'docs/BUILD.md'):
        result = subprocess.run(
            ['git', '-C', str(previous_application), 'ls-tree', '-r', '-z',
             '--full-tree', application_base, '--', path],
            env=git_environment(), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        if result.returncode != 0:
            raise ValueError('cannot inspect approved build guide')
        if result.stdout:
            locations[path] = result.stdout
    if len(locations) != 1:
        raise ValueError('approved application must contain one build guide')
    path = next(iter(locations))
    result = subprocess.run(
        ['git', '-C', str(previous_application), 'show', f'{application_base}:{path}'],
        env=git_environment(), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != 0:
        raise ValueError('cannot read approved build guide')
    try:
        text = result.stdout.decode()
    except UnicodeDecodeError as exc:
        raise ValueError('approved build guide is not UTF-8') from exc
    marker = '## Published source dates\n'
    if marker not in text:
        return ''
    if text.count(marker) != 1:
        raise ValueError('approved build guide has ambiguous published-source-date sections')
    section = text[text.index(marker):].rstrip()
    if '\n## ' in section[len(marker):]:
        raise ValueError('published-source-date section is not the final build-guide section')
    return section


def preserve_user_files(app, previous_application, application_base, publication_plan):
    """Copy only the explicitly pinned user-facing base; approval is external."""
    if not isinstance(application_base,str) or not re.fullmatch('[0-9a-f]{40}',application_base):
        raise ValueError('explicit immutable application base required')
    if not isinstance(publication_plan,dict) or publication_plan.get('old_main')!=application_base:
        raise ValueError('application base differs from publication plan old_main')
    app, previous_application = map(Path,(app,previous_application))
    resolved=subprocess.check_output(['git','-C',str(previous_application),'rev-parse','--verify',application_base+'^{commit}'],text=True,env=git_environment()).strip()
    if resolved!=application_base:raise ValueError('application base must name the exact commit')
    # The final publisher independently compares these complete owner-file
    # blobs/modes with plan.old_main, in addition to checking the entire tree.
    asset_source = _asset_source_path(previous_application, application_base)
    for name in ('README.md','LICENSE','NOTICE','assets','docs/assets'):
        target=app/name
        if target.is_symlink() or target.is_file():target.unlink()
        elif target.exists():shutil.rmtree(target)
    for name in ('README.md','LICENSE','NOTICE',asset_source):
        data=subprocess.check_output(['git','-C',str(previous_application),'archive',application_base,name],env=git_environment())
        import io,tarfile
        with tarfile.open(fileobj=io.BytesIO(data)) as archive:archive.extractall(app,filter='data')


def _replace_path(text, old, new):
    if old == 'assets/':
        pattern = re.compile(r'(?<!docs/)assets/')
        if not pattern.search(text):
            if 'docs/assets/' in text:
                return text
            raise ValueError(f'expected path reference is missing: {old}')
        return pattern.sub(new, text)
    if new == 'tooling/' + old:
        pattern = re.compile(r'(?<!tooling/)' + re.escape(old))
        if not pattern.search(text):
            if new in text:
                return text
            raise ValueError(f'expected path reference is missing: {old}')
        return pattern.sub(new, text)
    if new in text and old not in text:
        return text
    count = text.count(old)
    if count == 0 or new in text:
        raise ValueError(f'expected path reference is missing: {old}')
    return text.replace(old, new)


def apply_root_layout(app):
    """Group guides, assets, and build helpers while keeping Gradle IDs stable."""
    app = Path(app)
    docs = app / 'docs'
    tooling = app / 'tooling'
    docs.mkdir(exist_ok=True)
    tooling.mkdir(exist_ok=True)

    for name in ('BUILD.md', 'BUILDING.md', 'LEGAL.md', 'VERIFY.md'):
        source, destination = app / name, docs / name
        if source.exists() and destination.exists():
            raise ValueError(f'both old and new documentation paths exist: {name}')
        if source.exists():
            source.rename(destination)
        elif not destination.is_file():
            raise ValueError(f'documentation path is missing: {name}')

    if (app / 'assets').exists() and (docs / 'assets').exists():
        raise ValueError('both old and new asset directories exist')
    if (app / 'assets').exists():
        (app / 'assets').rename(docs / 'assets')
    elif not (docs / 'assets').is_dir():
        raise ValueError('approved asset directory is missing')

    for name in ('baseline-profile', 'fast-lint', 'lintchecks', 'screener', 'mk',
                 'reproducible-builds', 'gradle.properties.docker'):
        source, destination = app / name, tooling / name
        if source.exists() and destination.exists():
            raise ValueError(f'both old and new tooling paths exist: {name}')
        if source.exists():
            source.rename(destination)
        elif not destination.exists():
            raise ValueError(f'tooling path is missing: {name}')

    source, destination = app / 'core-gms', app / 'core/gms'
    if source.exists() and destination.exists():
        raise ValueError('both old and new core Google services paths exist')
    if source.exists():
        destination.parent.mkdir(exist_ok=True)
        source.rename(destination)
    elif not destination.is_dir():
        raise ValueError('core Google services path is missing')

    readme = app / 'README.md'
    text = readme.read_text()
    text = _replace_path(text, 'assets/', 'docs/assets/')
    text = _replace_path(text, '[LEGAL.md](LEGAL.md)', '[LEGAL.md](docs/LEGAL.md)')
    readme.write_text(text)

    building = docs / 'BUILDING.md'
    text = building.read_text()
    text = _replace_path(text, '(reproducible-builds/README.md)',
                         '(../tooling/reproducible-builds/README.md)')
    text = _replace_path(text, 'mollyim-android/reproducible-builds',
                         'mollyim-android/tooling/reproducible-builds')
    building.write_text(text)

    reproducible_readme = tooling / 'reproducible-builds/README.md'
    text = reproducible_readme.read_text()
    text = _replace_path(text, 'mollyim-android/reproducible-builds',
                         'mollyim-android/tooling/reproducible-builds')
    reproducible_readme.write_text(text)

    makefile = app / 'Makefile'
    text = makefile.read_text()
    text = _replace_path(text, 'mk/<platform>.mk', 'tooling/mk/<platform>.mk')
    text = _replace_path(text, 'include mk/android.mk', 'include tooling/mk/android.mk')
    makefile.write_text(text)

    dockerfile = app / 'Dockerfile'
    text = dockerfile.read_text()
    text = _replace_path(text, 'COPY gradle.properties.docker ',
                         'COPY tooling/gradle.properties.docker ')
    dockerfile.write_text(text)

    for name in ('.gitignore', '.dockerignore'):
        path = app / name
        text = path.read_text()
        for leaf in ('certs', 'outputs'):
            text = _replace_path(text, f'reproducible-builds/{leaf}',
                                 f'tooling/reproducible-builds/{leaf}')
        path.write_text(text)

    settings = app / 'settings.gradle.kts'
    text = settings.read_text()
    marker = '// Stable Gradle project IDs backed by grouped source directories.'
    mappings = '''// Stable Gradle project IDs backed by grouped source directories.
project(":core-gms").projectDir = file("core/gms")
project(":core-gms:base").projectDir = file("core/gms/base")
project(":core-gms:cloud-messaging").projectDir = file("core/gms/cloud-messaging")
project(":core-gms:safeparcel").projectDir = file("core/gms/safeparcel")
project(":core-gms:safeparcel-processor").projectDir = file("core/gms/safeparcel-processor")
project(":core-gms:tasks").projectDir = file("core/gms/tasks")
project(":screener").projectDir = file("tooling/screener")
project(":lintchecks").projectDir = file("tooling/lintchecks")
project(":fast-lint").projectDir = file("tooling/fast-lint")
'''
    if marker in text:
        if mappings not in text:
            raise ValueError('stable Gradle path mapping differs')
    else:
        anchor = 'include(":fast-lint")\n'
        if text.count(anchor) != 1:
            raise ValueError('expected Gradle tooling module includes')
        text = text.replace(anchor, anchor + '\n' + mappings, 1)
    settings.write_text(text)

    gradle = app / 'app/build.gradle.kts'
    text = gradle.read_text()
    text = _replace_path(text, 'see BUILD.md', 'see docs/BUILD.md')
    gradle.write_text(text)


SOURCE_WORKFLOW = '''name: Direct source checks
on: [push, pull_request, workflow_dispatch]
permissions:
  contents: read
jobs:
  source:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683
        with:
          ref: ${{ github.event.pull_request.head.sha || github.sha }}
          persist-credentials: false
      - run: git diff --check && test -f app/build.gradle.kts && test ! -d tools/patches
      - name: Report direct-build gate
        run: python3 -c 'import json; x=json.load(open("build-inputs/native-publication.json")); assert x["status"] == "BLOCKED"; print("Direct Android build acceptance BLOCKED pending public immutable native inputs; this source check is not build approval")'
'''


def replace_application_workflows(app):
    """Replace legacy workflows with the reviewed source-only workflow."""
    workflows = Path(app) / ".github/workflows"
    shutil.rmtree(workflows)
    workflows.mkdir()
    (workflows / "source.yml").write_text(SOURCE_WORKFLOW)


def apply(app, engineering, previous_application, *, application_base, publication_plan):
    app, engineering, previous_application = map(Path, (app, engineering, previous_application))
    if subprocess.check_output(['git','-C',str(app),'rev-parse','HEAD^{tree}'],text=True,env=git_environment()).strip() != RECONSTRUCTED_TREE:
        raise ValueError('publication layer requires exact merged-main reconstruction')
    if subprocess.check_output(['git','-C',str(app),'status','--porcelain'], text=True,env=git_environment()).strip():
        raise ValueError('publication layer requires a clean reconstructed checkout')
    apply_outputs(app, engineering, previous_application,
                  application_base=application_base, publication_plan=publication_plan)


def apply_outputs(app, engineering, previous_application, *, application_base, publication_plan):
    """Apply current publication outputs after the caller verified the source tree.

    The tree-only draft composer verifies its private index is at
    RECONSTRUCTED_TREE before calling this helper. Normal callers should use
    apply(), which also enforces HEAD and clean-worktree checks.
    """
    app, engineering, previous_application = map(Path, (app, engineering, previous_application))
    preserve_user_files(app, previous_application, application_base, publication_plan)
    replace_application_workflows(app)
    (app / 'build-inputs').mkdir(exist_ok=True)
    (app / 'build-inputs/engineering.json').write_text(json.dumps({'schema': 1, 'origin': 'https://github.com/0cwa/collie-release-engineering.git', 'recipe_revision': RECIPE_REVISION, 'native_recipe_path': 'tools/native', 'trust': 'trusted-local native outputs; independent reproduction not established'}, indent=2)+'\n')
    shutil.copyfile(engineering / 'tools/native/native-lock.json', app / 'build-inputs/native-lock.json')
    (app / 'build-inputs/native-publication.json').write_text(json.dumps({'schema':1,'status':'BLOCKED','repository':None,
        'reason':'Accepted trusted-local native artifacts have locked hashes but no verified public immutable origin. Release assembly remains blocked.',
        'lock':'native-lock.json','production_version_floor':177702},indent=2)+'\n')
    props=app/'gradle.properties'
    props.write_text(props.read_text()+ '\n# Canonical Collie identity: no legacy product matrix.\nmollyFirebaseFree=false\nbaseAppTitle=Collie\nbaseAppFileName=Collie\nbasePackageId=im.collie.app\ncollieBuildTimestamp=1794023956\ncollieBuildRevision=da5c03ff20ae\n\n# DRAFT proposal only; not reserved. Direct Android builds remain blocked.\nreleaseVersionCode=177703\nreleaseVersionName=8.35.1-2-rc1\n')
    gradle=app/'app/build.gradle.kts'
    text=gradle.read_text()
    text=text.replace('val baseAppTitle = getCiEnv("CI_APP_TITLE") ?: properties["baseAppTitle"] as String','val baseAppTitle = "Collie"')
    text=text.replace('val baseAppFileName = getCiEnv("CI_APP_FILENAME") ?: properties["baseAppFileName"] as String','val baseAppFileName = "Collie"')
    text=text.replace('val basePackageId = getCiEnv("CI_PACKAGE_ID") ?: properties["basePackageId"] as String','val basePackageId = "im.collie.app"')
    # Git history is publication metadata, not an undeclared build input.
    start=text.index('fun getLastCommitTimestamp(): String {')
    expected_tail = '''fun getLastCommitTimestamp(): String {
  return try {
    git("log", "-1", "--pretty=format:%ct000")
  } catch (e: Exception) {
    logger.warn("Failed to get Git commit timestamp: ${e.message}. Using mtime of current build script.")
    buildFile.lastModified().toString()
  }
}

fun getGitHash(): String {
  return try {
    git("rev-parse", "--short=12", "HEAD")
  } catch (e: Exception) {
    logger.warn("Failed to get Git commit hash: ${e.message}. Using default value.")
    "abc123def456"
  }
}

fun requireCommitTag(): String {
  val tag = git("describe", "--tags", "--exact-match")
  return tag.takeIf { it.isNotEmpty() } ?: "untagged"
}

private fun git(vararg args: String): String =
  providers.exec {
    commandLine("git", *args)
  }.standardOutput.asText.get().trim()
'''
    if text[start:] != expected_tail:
        raise ValueError('metadata tail changed; refusing to truncate unrelated build logic')
    text=text[:start]+'''fun getLastCommitTimestamp(): String = providers.gradleProperty("collieBuildTimestamp").get() + "000"

fun getGitHash(): String = providers.gradleProperty("collieBuildRevision").get()

fun requireCommitTag(): String = providers.gradleProperty("releaseVersionName").getOrElse("8.35.1-1-port-unvalidated")
'''
    text = text.replace('versionCode = (canonicalVersionCode * maxHotfixVersions) + mollyRevision + currentHotfixVersion', 'versionCode = providers.gradleProperty("releaseVersionCode").orNull?.toInt()?.also { require(it > 177702) { "Release versionCode must exceed published RC1 and all later reservations" } } ?: ((canonicalVersionCode * maxHotfixVersions) + mollyRevision + currentHotfixVersion)')
    text = text.replace('versionName = if (ciEnabled) requireCommitTag() else sourceVersionNameWithRevision', 'versionName = providers.gradleProperty("releaseVersionName").orNull ?: (if (ciEnabled) requireCommitTag() else sourceVersionNameWithRevision)')
    # A release cannot accidentally use the old unvalidated source default version.
    text += '''
// Publication migration gate: remove only with reviewed public native inputs and
// direct-source acceptance. The legacy operator route is retained in engineering.
throw GradleException("Direct Android builds BLOCKED: publish and verify locked native inputs first; see BUILD.md")
'''
    gradle.write_text(text)
    build_guide = '''# Building Collie

Reconstruction and release engineering live in [collie-release-engineering](https://github.com/0cwa/collie-release-engineering). See [VERIFY.md](VERIFY.md) for Git-only source inspection.

This checkout contains the reconstructed application source; Gradle does not run
an assembler or inject source from another worktree. JDK/Gradle/SDK/NDK and strict
dependency inputs are declared in this tree. Canonical identity is im.collie.app,
Collie backup names and optional FCM; legacy filename aliases are one product.

**Direct build readiness: BLOCKED.** `build-inputs/native-lock.json` identifies
accepted native bytes. Their public immutable repository has not been supplied
or independently downloaded. `native-publication.json` records this gap. Do not
substitute upstream native libraries or disable strict dependency verification.
Every direct Android Gradle configuration deliberately fails until the required publication layer
and clean-checkout build acceptance are reviewed. This draft is not releasable.

The retained local operator route is in collie-release-engineering and still
requires its explicit trusted-local native acceptance. That is not independent
native reproduction. Obtain the three locked artifacts and POMs from approved
public immutable URLs, verify all hashes, materialize their exclusive repository
and verification entries in source, then run a fresh controlled Gradle build.
After reviewed gate removal, releaseVersionCode and releaseVersionName are normal source-defined Gradle properties. Final values must come from serialized external reservation and be recorded in the build receipt. The expected canonical task is `./gradlew --dependency-verification=strict
:app:assembleProdWebsiteRelease`; it is currently BLOCKED as described above.

Release versions must be externally reserved above 177702 and all later published
or reserved codes using engineering's serialized version policy. RC1 ingestion
reuses its existing signed bytes/code. No build has been performed from this G.
**DRAFT candidate proposal:** 8.35.1-2-rc1 / 177703. This value is not allocated
or reserved. The direct-build gate remains closed until the owner refreshes the
inventory and confirms this proposal through the serialized reservation policy.
No parity with historical APK bytes is claimed after changing build metadata.
Instrumentation remains opt-in. Physical-device/account/attachment/database
upgrade acceptance and independently reproduced native outputs are NOT RUN.
'''
    source_dates = _published_source_dates(previous_application, application_base)
    if source_dates:
        build_guide = build_guide.rstrip() + '\n\n' + source_dates + '\n'
    (app/'BUILD.md').write_text(build_guide)
    (app/'VERIFY.md').write_text('''# Git-only source inspection

Use a clean Git environment with replace refs and grafts disabled. Review the
exact Signal commit `a74583b01e260944f699c4da03ba4e8ce04de32b`, generated endpoint G,
and integration I from the externally approved source receipt. Do not substitute
GitHub's synthetic pull-request merge. `git merge-base --is-ancestor SIGNAL G`
checks upstream ancestry; `git diff SIGNAL G` reviews every changed path.
`git diff OLD_MAIN I` exposes the complete cutover. `git rev-parse G^{tree}
I^{tree}` must print identical complete trees, and `git show -s --format=%P I`
must name OLD_MAIN and G, in that order. Clear GIT_* overrides and reject
.git/info/grafts, refs/replace, alternates and shallow history before checking.

Reconstruction lives in the separately reviewed collie-release-engineering
recipe. Its receipt records inputs/layers and final trees after G exists; G does
not contain its own hash. Source inspection is not signed provenance or proof
that native binaries were independently rebuilt. Historical RC1's tag is retained
unchanged; this source checkpoint is not retroactively its build input.

Contribute normal application changes here. Maintainers export a full binary
Git delta, commit it as an engineering layer, regenerate and compare the whole
reviewed tree before release (source/PUBLICATION.md in engineering). The two
representations may not silently diverge.
''')
    apply_root_layout(app)
