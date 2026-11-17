# Collie development cycle

Decided 2026-11-10. Where this conflicts with older docs here (`source/PUBLICATION.md`, `docs/PATCH-PIPELINE.md` contributor sections) or with app `docs/VERIFY.md`, this file wins until those are retired.

## Day-to-day work

- App `main` (`0cwa/collieim-android`) is the source. Change app code directly in ordinary PRs; do not re-import app changes as engineering layers.
- Every non-merge app commit carries one `Layer: <feature>` trailer (lowercase, hyphens), e.g. `Layer: collie-branding`. The app `pr.yml` check enforces it.
- Required app PR checks: fast lint and `Layer:` trailers. Unit tests and a debug APK build run on every PR but are not required.
- `main` in both repos: PRs only, required checks green, signed commits, no force pushes, no required human review. Agents merge with `gh pr merge`.

## Merging PRs

When an agent merges a PR, it needs required checks plus each line below that the PR's changes match, and nothing more. A job the PR itself breaks gets fixed first. Upgrade PRs follow "Upstream upgrades".

- CI, tooling and tests: the job the PR changes or that runs the changed tests, green on the PR head (if that job runs on PRs).
- App code, Gradle files and dependency versions: `Unit tests` green on the PR head.
- UI: the API 35 screenshot from app `AGENTS.md`.
- Keys, signing, permissions, updater, backup and database migrations, encrypted push, the proxy/network path, release or publishing workflows, workflow `permissions:`, secrets or `pull_request_target`: the PR's one review is the security review (`$sol-loop` in Codex).

Document PR implementation and review evidence in the GitHub PR description or comments, not under `docs/`. Do not commit screenshots, logs or acceptance transcripts as review records. Keep images only when they are actively used by the website, maintained documentation or Fastlane; keep active automated test baselines in test directories.

Then:

- Merge each PR as soon as it has its evidence, unless it depends on another PR.
- PRs need not be up to date with `main`. Update a branch only for a conflict, or when commits that landed since its last green run touch its files, Gradle files, dependency versions or shared test fixtures. When unsure, merge `main` locally and compile instead of re-running everything.
- A known out-of-memory or race failure gets one re-run. A failure with a shared cause (on more than one PR or on `main`) is fixed once in its own PR and merged first; then update only the PRs that failed on it.
- A landing task doesn't build new checks; note them for later. A check added during a task doesn't hold PRs that were already open, but a real bug it finds in a PR's own code gets fixed in that PR.
- Before each later merge in a task, look at `main`'s latest finished `PR checks` run (`gh run list --branch main --workflow pr.yml --limit 3`) without waiting for a running one. If a merged PR turned it red, fix or revert that in a PR before merging other app code.

## Upstream upgrades (Signal / Molly)

Take every Signal tag as one ordinary signed app PR, applying only what upstream changed since the last tag:

```
OLD=v<canonicalVersionName in app/build.gradle.kts>; NEW=<new Signal tag>
git fetch --no-tags https://github.com/signalapp/Signal-Android.git refs/tags/$OLD:refs/tags/signal-$OLD refs/tags/$NEW:refs/tags/signal-$NEW
git diff --binary -M signal-$OLD signal-$NEW | git apply --3way --index
```

- Never `git merge` an upstream tag: its commits are unsigned and `main` requires signatures. The diff gives the same tree (checked on 8.35.2), and it bumps `canonicalVersionName`, which records the base for next time.
- Resolve conflicts, then commit with `Layer: upgrade`, `Upstream: signalapp/Signal-Android $OLD..$NEW` and one `Co-authored-by:` per upstream author.
- Read the tag-to-tag diff for what applies cleanly but doesn't belong, and remove it or adapt it: network calls that skip the proxy code, Google or Firebase dependencies, new strings that say "Signal", donation or payment entry points. Donation UI stays out.
- If the diff changes the libsignal or ringrtc version in `gradle/libs.versions.toml`, the native libraries must be rebuilt (`tools/native`) and published first, or the app build fails.
- Molly: read Molly's own commits since the last one taken (`git log <last>..<new> --no-merges --not signal-<tag Molly merged>`; the last taken is `892d6264ba0`). Cherry-pick the ones Collie wants, keeping their authors, with `Upstream: mollyim/mollyim-android <sha>`. Skip Molly's Signal merges.
- Agents may merge upgrade PRs once required checks and `Unit tests` are green.

### Additional selected sources

The [upstream source registry](../upstreams/README.md) tracks selected changes from
other public forks, including `johanw666/Signal-Android`. Review custom release
snapshots against their own Signal base, then port individual features in app PRs
with `Layer:` and upstream attribution. Keep review decisions and merged app
references in the source ledger. The registry does not revive the historical
patch composer or make third-party changes automatic build inputs.

## Releases

Product and source policy: [DUAL-RELEASE-POLICY.md](DUAL-RELEASE-POLICY.md).
The steps below describe the Stable product. Its process is app
`docs/RELEASING.md`; a Stable verification prerelease and the separately installed
TrailBlazer Beta product have different purposes and publication routes.

- The release APK is built by this machine or GitHub, whichever is faster, with app `tooling/reproducible-builds/build-release.sh`.
- The maintainer signs it locally (APK key) and tags it locally (publication key, `WtFs…`, listed in app `.github/release-signers`).
- One `collie.apk` is uploaded. App CI checks it, uploads the profile copies (`collie-foss`, `molly-continued`, `molly-foss-continued`, `molly-minimal`) and publishes a prerelease within minutes.
- A full release is the same APK, promoted only after a rebuild on the other side (GitHub or this machine) matches it.
- Release notes: `distribution/release-notes/<version>.md` here, in the maintainer's style (see existing files). App CI refuses to publish without one. In-app notices (e.g. the later move to one `collie.apk`) ship as ordinary app PRs.
- The Stable F-Droid feed carries verified full Stable releases only. TrailBlazer
  Beta has a separate package, APK signer, feed and readiness/Pages approval path;
  its GitHub releases remain prereleases. Its source and feed activation gates
  are listed in the dual-release policy. Pages must preserve both approved feeds
  and native inputs when either feed changes.

## Security fuzzing pilot

The focused Socket/Android pilot and its evidence expectations are documented in [SECURITY-FUZZING.md](SECURITY-FUZZING.md). It is a development and release confidence measure; it does not change release approval or branch enforcement.

## To retire

Retire the historical app-source composer and generated publication commits on
app `main` (G/I). This does not retire the active Beta plan/receipt chain or its
preparation and verification helpers. Before removing any file under `source/`,
candidate plans/receipts, or `tools/patches`, identify its current workflow,
distribution and test consumers; remove only obsolete composition paths in a
reviewed change.

Keep `migration_receipts.py`, `runtime_receipts.py`, `release_artifacts.py`,
`preparation_verifier.py`, `stage_preparation.py`, and the active Beta collection,
signing and publication helpers in `tools/patches` while their consumers remain.
`distribution/beta_evidence.py` and `distribution/pages.py` still verify this
receipt graph. A future relocation must update imports, immutable verifier pins,
workflow consumers and tests together; the directory name is not a retirement
criterion.

Keep native-input delivery (`repo.collie.im/native-inputs`) and Pages/F-Droid
publishing; `pages-release.yml` stays until native files have another publishing
route. After the first diff-based upgrade ships as a full release, the historical
`patches/molly`, `patches/ports` and `patches/forks` forest may be archived under
`archive/patch-forest`. Apply the same consumer check before deleting any tools.
