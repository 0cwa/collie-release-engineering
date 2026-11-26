# Collie products, release stages and source policy

This is the maintained policy for the Android app, release engineering and
CollieSocket repositories. [DEVELOPMENT-CYCLE.md](DEVELOPMENT-CYCLE.md) governs
ordinary changes. Engineering owns distribution and release evidence; app source
changes go through app PRs. The ruleset requests below are proposals for a
separately reviewed administration change, not live repository settings.

## Two products, with a separate publication stage

| Identity | Collie Stable | Collie TrailBlazer Beta |
| --- | --- | --- |
| Android package | `im.collie.app` | `im.collie.app.trailblazer` |
| GitHub tag | `v<version>` | `trailblazer-v<version>` |
| Primary APK | `collie.apk` (five Stable APKs) | `trailblazer-beta.apk` |
| APK signing identity | Existing Stable key | Separate pinned Beta key |
| F-Droid feed | `https://repo.collie.im/fdroid/repo/` | `https://repo.collie.im/fdroid/beta/repo/` |
| GitHub publication | Draft → verification prerelease → verified full release | Immutable Beta prerelease |
| Current source contract | `main`, exact-commit `pr.yml` readiness | `trailblazer-beta`; the APK must equal a `build-release.yml` build of the signed tag's commit |

The complete identities and certificate pins are in
[`distribution/channel_profile.py`](../distribution/channel_profile.py).
The products have separate installation identities and version-code histories.
Each feed uses its own default F-Droid channel (`releaseChannels` absent or empty).
Changing product requires a deliberate installation choice; an in-app channel
toggle must not substitute one package or signer for the other.

When a Stable prerelease and a TrailBlazer Beta ship together, the Stable
`v<version>` release keeps its five Stable APKs and `publication.json` and also
carries the signed Beta APK as `collie-beta.apk`.
The separate `trailblazer-v<version>` release with `trailblazer-beta.apk` may
still exist. `collie-beta.apk` is a convenience copy: it stays Beta-signed,
keeps package `im.collie.app.trailblazer` and the Beta version-code history, and
does not make the Stable release a Beta release or put Beta in the Stable feed.
The app's `tooling/release/publication.py` verifies the copy against the pinned
Beta certificate.

GitHub's `prerelease` flag describes publication eligibility within these routes.
A Stable `-betaN` or `-rcN` version suffix does not select the TrailBlazer product.
Product selection must use the complete package/tag/asset/signer/feed
identity. Reproducibility establishes artifact identity; product readiness also
needs the approved feature scope and applicable behavior evidence.

## Stable publication and promotion

Use the app's reviewed
[`docs/RELEASING.md`](https://github.com/0cwa/collieim-android/blob/main/docs/RELEASING.md)
and workflows at the chosen immutable revision. The release candidate retains
`im.collie.app`, its existing signing key, the canonical APK and its four
compatibility names; a joint round may add `collie-beta.apk` as described above.
New version codes must exceed the published history for that package, including
published candidates. The Stable feed admits verified full releases; Stable
Obtainium configuration continues to exclude prereleases.

The publisher reads `docs/release-notes/<version>.md` from the exact signed tag
commit in the app. Its schema 1 `release.json` asset carries the canonical
channel, tag, source commit, package and version, APK name and SHA-256, and
native artifacts path. Feed staging validates the asset against the release
API, resolved tag and downloaded APK. Older releases without the asset use
tag and APK inspection.

The schema 1 `publication.json` record binds the product (`edition=collie`),
repository and release ID, signed annotated tag object,
source commit, package/version/signer, `built_on=local|github`, and every APK's
asset ID, name, size and SHA256. It is uploaded to the draft after all five
identical APKs exist and before publishing the candidate with `prerelease=true`
and `make_latest=false`.

Promotion preserves those APKs and the record. The hosted promotion route accepts
`built_on=local`, rebuilds the immutable source, compares the real APK contents,
and rechecks the same release/tag/record/assets before changing the release to
full and Latest. A GitHub-built candidate needs the separately approved local
reproduction path; recording `built_on=github` does not prove that reproduction.
Do not manufacture records for old published prereleases or replace their assets
to make them pass a new verifier. Resolve any legacy candidate through a reviewed
migration or a fresh candidate with a new version code.

Publication and promotion dispatch on trusted app `main`, the only Stable
source branch. `release/stable/8.35` is retired.

## Source verification

The Stable publication verifier must match the owning Android repository,
`.github/workflows/pr.yml`, the exact selected source ref and commit, and the
latest successful matching run ID and positive attempt. Only `push` and
`workflow_dispatch` runs qualify. Require successful `Unit tests`,
`Unsigned production build` and `Security readiness` jobs from that attempt.
The workflow and executable verification inputs must match reviewed trusted
`main` policy. Ancestry, a PR run or a matching workflow filename alone does
not establish the source proof. Record the selected source branch in Android
publication evidence and retain same-byte promotion. Existing engineering feed
receipts keep their immutable release, tag, APK and source bindings.

For TrailBlazer (decided 2026-11-22), publishing the GitHub prerelease is the
approval. The scheduled feed workflow takes the newest immutable TrailBlazer
prerelease when its tag is a verified signed tag, its APK equals the successful
`trailblazer-beta` `build-release.yml` build of that commit plus its signature, the
APK carries the pinned Beta package and signer, and its versionCode exceeds the
served Beta index. [`BETA-FEED-OPERATIONS.md`](../distribution/BETA-FEED-OPERATIONS.md)
has the details. The Android release no longer has to stay a draft: the app
repository has GitHub immutable releases enabled, so a published release's tag and
assets are frozen, and the feed checks those frozen bytes against CI.

Keep the app's fixed profile contract and engineering's `channel_profile.py`
consistent through reviewed source changes;
do not fetch release policy dynamically at runtime.

## Repository protection proposals and drift report

[`repository-policy.json`](../.github/repository-policy.json) contains exact GitHub
ruleset request bodies under each `request` key, plus activation notes outside the
request. Applying rulesets requires a separate authorized review of current
settings and dependencies.
The [GitHub rulesets API](https://docs.github.com/en/rest/repos/rules) defines the
request format.

The desired rules for app and engineering main require PRs, code owner review,
signed commits, the current named required checks, and deletion/force-push
restrictions. The desired app main rules allow merge and squash. Changes covered
by engineering's [`.github/CODEOWNERS`](../.github/CODEOWNERS) require maintainer
review under the development policy. The proposed Beta source rules retain the
app check names.
Source readiness requires the additional workflow evidence described above.
The CollieSocket main proposal uses
the contract, server, Signal and two platform-image job names from its `test.yml`;
confirm those exact checks on a successful current PR before applying it.

Protect both `refs/tags/v*` and `refs/tags/trailblazer-v*`. Keep tag creation in its
own ruleset with the existing `RepositoryRole` ID `5`, `always` bypass. Put tag
update/deletion in a separate ruleset with **no bypass actors**. In the reviewed
settings change, install the unbypassable immutability rule first, then narrow
the existing rule to creation
and extend it to both tag namespaces. Preserve unrelated source/signature rules.

Run the advisory checker from this repository:

```sh
python3 tools/repository_policy.py
python3 tools/repository_policy.py --snapshot /path/to/get-snapshot.json --json
python3 -m unittest discover -s tests -p 'test_repository_policy.py'
```

The live command uses only `gh api --hostname github.com --method GET`, paginates
branches and rulesets, and fetches full ruleset details including inherited rules.
Offline input makes no network requests. Its shape is a schema 1 object with
`repositories` keyed by the three repository names; each value has
`default_branch` (string), `branches` (name strings), `rulesets` (full GET response
objects), and `errors` (empty only after a complete read). Missing or incomplete
data or duplicate paginated identities is `UNKNOWN`. Omitted bypass actors,
unrecognized rule details and unrecognized wildcard coverage need
manual review; they are not assumed safe.

`OK` means the requested rule has the reviewed ruleset coverage. `DRIFT` reports
missing or different coverage. Planned source refs remain `GATED` even when a
matching branch exists. The checker covers rulesets only; classic branch
protection, environments, signer ownership, hosted test results and release
approvals require their own review. An empty ruleset list does not prove the
absence of all other protections. Known gaps return a successful advisory report,
not a failing CI gate; invalid input returns exit code 2. No command in this tool
applies rules, creates branches or dispatches a workflow.

## CollieSocket compatibility and independent versions

CollieSocket keeps its own version/tag sequence and source history. An app release
does not advance the server version, and a server release does not promote either
Android product. The inherited Socket release workflow is restricted to
`mollyim/mollysocket`; this policy does not enable publication in the Collie fork.
Define the fork's reviewed release and immutable-tag route separately before
advertising a released server artifact.

Every claimed supported app/server pair must have an explicit tested compatibility
reference in its release notes or linked release evidence:

| Record | Required identity/evidence |
| --- | --- |
| Socket | Repository, full source commit, independent release tag/version, and immutable binary or image digest |
| App | Product/package, full source commit, release tag/version and tested APK digest |
| Protocol | Contract commit pin and copied-fixture manifest identity for the versions actually negotiated |
| Tests | Exact workflow run/attempt or independently reviewed test record for both sources; results and the tested scenarios |
| Runtime claim | Applicable app/server integration evidence, with any untested deployment or device behavior stated explicitly |

Read the app's v3 fixture
[`PIN.txt`](https://github.com/0cwa/collieim-android/blob/main/app/src/test/resources/colliesocket-contract-v3/PIN.txt)
at the tested app revision for its contract commit and source tree. A contract pin
does not establish a tested release pair or prove that shipped clients implement
v3. Preserve the fixture ownership/manifest checks and the advisory scope of
[SECURITY-FUZZING.md](SECURITY-FUZZING.md). Record an actual tested pair before
making a release compatibility claim; do not infer it from similar version names
or passing schema fixtures.
