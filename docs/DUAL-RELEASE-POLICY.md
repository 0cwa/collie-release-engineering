# Collie products, release stages and source policy

This is the maintained policy for the Android app, release engineering and
CollieSocket repositories. [DEVELOPMENT-CYCLE.md](DEVELOPMENT-CYCLE.md) governs
ordinary changes. Engineering owns distribution and release evidence; app source
changes go through app PRs. Ruleset requests in this change are proposals for a
separately reviewed administration change, not live repository settings.

## Two products, with a separate publication stage

| Identity | Collie Stable | Collie TrailBlazer Beta |
| --- | --- | --- |
| Android package | `im.collie.app` | `im.collie.app.trailblazer` |
| GitHub tag | `v<version>` | `trailblazer-v<version>` |
| Primary APK | `collie.apk` (five Stable APKs) | `trailblazer-beta.apk` |
| APK signing identity | Existing Stable key | Separate pinned Beta key |
| F-Droid feed | `https://repo.collie.im/fdroid/repo/` | `https://repo.collie.im/fdroid/beta/repo/` |
| GitHub publication | Draft → verification prerelease → verified full release | Reviewed Beta prerelease |
| Current source contract | `main`, exact-commit `pr.yml` readiness | Intended `trailblazer-beta`, exact approved build and Beta readiness evidence; activation gated |

The complete identities and certificate pins are in
[`distribution/channel_profile.py`](../distribution/channel_profile.py).
The products have separate installation identities and version-code histories.
Each feed uses its own default F-Droid channel (`releaseChannels` absent or empty).
Changing product requires a deliberate installation choice; an in-app channel
toggle must not substitute one package or signer for the other.

When a Stable prerelease and a TrailBlazer Beta ship together (decided
2026-11-21), the Stable `v<version>` release keeps its five Stable APKs and
`publication.json` and also carries the signed Beta APK as `collie-beta.apk`.
The separate `trailblazer-v<version>` release with `trailblazer-beta.apk` may
still exist. `collie-beta.apk` is a convenience copy: it stays Beta-signed,
keeps package `im.collie.app.trailblazer` and the Beta version-code history, and
does not make the Stable release a Beta release or put Beta in the Stable feed.
The app's `tooling/release/publication.py`
([app #224](https://github.com/0cwa/collieim-android/pull/224)) accepts it and
verifies it against the pinned Beta certificate. First shipped in
[`v8.36.3-1-rc2`](https://github.com/0cwa/collieim-android/releases/tag/v8.36.3-1-rc2).

GitHub's `prerelease` flag describes publication eligibility within these routes.
A Stable `-betaN` or `-rcN` version suffix does not select the TrailBlazer product.
The published Stable
[`v8.35.1-3-rc2`](https://github.com/0cwa/collieim-android/releases/tag/v8.35.1-3-rc2),
for example, is a full release despite its suffix. Product selection must use the complete package/tag/asset/signer/feed
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

The accompanying [app publication-policy change (#186)](https://github.com/0cwa/collieim-android/pull/186)
introduces `publication.json`.
Use that record-based workflow only after it has merged; this engineering policy
does not make an unmerged workflow available. The schema 1 record binds the
product (`edition=collie`), repository and release ID, signed annotated tag object,
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

Publication and promotion dispatch on trusted app `main`. The Stable source
allowlist is exactly `refs/heads/main` and `refs/heads/release/stable/8.35`.
The maintenance source becomes eligible once its ref protections and matching
Android verifier have landed and the selected source passes the exact-run gate.
Historical verify-only use retains the `main` contract.

## Source activation gates

App `main` remains the development source. The proposed protection below
targets the exact `release/stable/8.35` ref. It creates no branch and applies no
settings. Approve the selected exact source and scope, and verify the named
ref's signed-commit, PR and check protections before activation.

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

For TrailBlazer, approve the complete source and build identity first, including
package, signer, branding, version-code floor and successful exact-source build.
Activate the reviewed `trailblazer-beta` protection and source route only with
those changes. Then use the active plan, preparation and source/build/release
receipts; the signed candidate index and isolated client test; the successful
exact readiness run/artifact; and the separate Pages approval described in
[`BETA-FEED-OPERATIONS.md`](../distribution/BETA-FEED-OPERATIONS.md). The first
publication also needs a verified complete served-site inventory. The candidate,
readiness and Pages steps each retain their existing independent approval inputs.

The following are **status observations from 2026-11-17**, to recheck before any
activation. They are not standing approvals or permanent branch state:

| Dependency | Observed state and remaining gate |
| --- | --- |
| [App #177](https://github.com/0cwa/collieim-android/pull/177) | Draft conservative 8.35.2 RC, based on `agent/rc-8351-published-base` rather than `main`. Its exact candidate analysis, preparation/provenance and applicable runtime evidence remain gates. Resolve its feature/source scope before choosing a Stable route. |
| [App #184](https://github.com/0cwa/collieim-android/pull/184) → [#183](https://github.com/0cwa/collieim-android/pull/183) | Both merged: reviewed Actions bindings, then the runner-context correction. Before analysis, verify the corrected workflow's current hosted audit and obtain its new exact execution approval. Merging these corrections does not authorize the candidate dispatch. |
| [App #182](https://github.com/0cwa/collieim-android/pull/182) | Draft preparation-aware Beta verifier pin, held until the exact RC analysis approval/dispatch dependency is resolved. Its presence does not approve a build, signing, readiness or publication run. |
| Beta source and feed | The intended `trailblazer-beta` branch and live Beta index were absent. Existing engineering checks also name held branding/source-workflow commits; confirm the complete approved source is reachable and passes those checks. Do not weaken the checks to accept unrelated source. |
| Beta readiness | The app workflow exists, but its current engineering pin and the absent intended source route do not establish usable preparation-aware readiness. Complete the held pin/source work and a real successful run before publication. |
| Updater | Current app source contains the signed-feed updater. The published `v8.35.1-3-rc2` came from `cc55c02bef64b2980c5f384a6a04f52ffb7bef92`, with automatic app updates disabled. Source changes require a new verified APK before they reach users. |

The accompanying [app updater-profile change (#185)](https://github.com/0cwa/collieim-android/pull/185)
selects the fixed profile by exact
application ID and keeps Stable defaults. It is source preparation until merged,
compiled, tested and released. It does not add a Beta Gradle flavor, activate the
held Beta source, or publish a feed. Keep the app's fixed profile contract and
engineering's `channel_profile.py` consistent through reviewed source changes;
do not fetch release policy dynamically at runtime.

## Repository protection proposals and drift report

[`repository-policy.json`](../.github/repository-policy.json) contains exact GitHub
ruleset request bodies under each `request` key, plus activation notes outside the
request. It describes current main coverage and gated source/tag changes. Applying
it requires a separate authorized review of current settings and dependencies.
The [GitHub rulesets API](https://docs.github.com/en/rest/repos/rules) defines the
request format.

App and engineering main require PRs, signed commits, the current named required
checks, and deletion/force-push restrictions. Proposed Beta and Stable maintenance
source rules retain the app check names; source readiness still requires the
additional workflow evidence described above. The CollieSocket main proposal uses
the contract, server, Signal and two platform-image job names from its `test.yml`;
confirm those exact checks on a successful current PR before applying it.

Protect both `refs/tags/v*` and `refs/tags/trailblazer-v*`. Keep tag creation in its
own ruleset with the existing `RepositoryRole` ID `5`, `always` bypass. Put tag
update/deletion in a separate ruleset with **no bypass actors**. At the observation
date, app ruleset `26505853` covered only `v*` and applied that bypass to creation,
update and deletion together. In the reviewed settings change, install the
unbypassable immutability rule first, then narrow the existing rule to creation
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

The app's v3 fixture
[`PIN.txt`](https://github.com/0cwa/collieim-android/blob/e52b15b48f0977ba19458916aab1e8a36450dc02/app/src/test/resources/colliesocket-contract-v3/PIN.txt)
records contract commit `9751c1effba7c94f49474febd941ca7fe3ba31cc` and source tree
`fc88c12cddc75f5f6a66191763da57a3f44dd7f8`. This is a contract reference, not a
tested release pair or proof that shipped clients implement v3. Preserve the
fixture ownership/manifest checks and the advisory scope of
[SECURITY-FUZZING.md](SECURITY-FUZZING.md). Record an actual tested pair before
making a release compatibility claim; do not infer it from similar version names
or passing schema fixtures.
