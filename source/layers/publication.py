#!/usr/bin/env python3
"""Materialize reviewed publication/build inputs before source publication."""
from pathlib import Path
import json
import re
import shutil
import subprocess

RECIPE_REVISION = '36779887ffc0bb1ecff55a87c350740662570039'
RECONSTRUCTED_TREE = 'da5c03ff20ae2a672329eb8257bc33949845b268'


def git_environment():
    import os
    env = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
    env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
               GIT_NO_REPLACE_OBJECTS='1', GIT_NO_LAZY_FETCH='1', LC_ALL='C')
    return env


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
    for name in ('README.md','LICENSE','NOTICE','assets'):
        data=subprocess.check_output(['git','-C',str(previous_application),'archive',application_base,name],env=git_environment())
        target=app/name
        if target.is_symlink() or target.is_file():target.unlink()
        elif target.exists():shutil.rmtree(target)
        import io,tarfile
        with tarfile.open(fileobj=io.BytesIO(data)) as archive:archive.extractall(app,filter='data')


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
    if subprocess.check_output(['git','-C',str(app),'rev-parse','HEAD^{tree}'],text=True).strip() != RECONSTRUCTED_TREE:
        raise ValueError('publication layer requires exact merged-main reconstruction')
    if subprocess.check_output(['git','-C',str(app),'status','--porcelain'], text=True).strip():
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
    props.write_text(props.read_text()+ '\n# Canonical Collie identity: no legacy product matrix.\nmollyFirebaseFree=false\nbaseAppTitle=Collie\nbaseAppFileName=Collie\nbasePackageId=im.collie.app\ncollieBuildTimestamp=1794023956\ncollieBuildRevision=da5c03ff20ae\n')
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
    (app/'BUILD.md').write_text('''# Building Collie

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
No parity with historical APK bytes is claimed after changing build metadata.
Instrumentation remains opt-in. Physical-device/account/attachment/database
upgrade acceptance and independently reproduced native outputs are NOT RUN.
''')
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
