# Collie development cycle

Decided 2026-11-10. Where this conflicts with older docs here (`source/PUBLICATION.md`, `docs/PATCH-PIPELINE.md` contributor sections) or with app `docs/VERIFY.md`, this file wins until those are retired.

## Day-to-day work

- App `main` (`0cwa/collieim-android`) is the source. Change app code directly in ordinary PRs; do not re-import app changes as engineering layers.
- Every non-merge app commit carries one `Layer: <feature>` trailer (lowercase, hyphens), e.g. `Layer: collie-branding`. The app `pr.yml` check enforces it.
- Required app PR checks: fast lint and `Layer:` trailers. Unit tests and a debug APK build run on every PR but are not required.
- `main` in both repos: PRs only, required checks green, signed commits, no force pushes, no required human review. Agents merge with `gh pr merge`.

## Merging PRs

When an agent merges a PR, this is the evidence it needs, in both repos: required checks, plus every line below that the PR's changes match. Docs need only required checks. Upgrade PRs follow "Upstream upgrades" below instead. Don't add evidence beyond this, but a job that fails on the PR head because of the PR's own change gets fixed before merging. Reviews still follow `full-send` and `orchestrate`.

- CI, tooling and tests: the job the PR changes, or the job that runs the changed tests, green on the PR head when that job runs on PRs.
- App code, Gradle files and dependency versions: a passing `Unit tests` job on the PR head.
- UI: the API 35 screenshot from app `AGENTS.md`.
- Keys, signing, permissions, updater, backup and database migrations, encrypted push, the proxy/network path, release or publishing workflows, and workflow `permissions:`, secrets or `pull_request_target`: the PR's one review is the security review from `full-send` step 3 (`$sol-loop` in Codex).

Then:

- Merge each PR as soon as it has its evidence, unless it depends on another PR. Don't hold it for another PR's order.
- PRs need not be up to date with `main` (the ruleset doesn't require it). Update a branch only for a conflict, or when commits that landed since its last green run touch its files, Gradle files, dependency versions or shared test fixtures. When unsure, merge `main` locally and run a compile instead of the whole suite.
- A known out-of-memory or race failure gets one re-run. When a failure has a shared cause (it shows up on more than one PR or on `main`), fix it once in its own PR, merge that first, then update only the PRs that failed on it. PRs that don't hit it keep merging.
- A check added during the current task doesn't hold PRs that were already open; land it in its own PR. When it flags a real bug in code a PR changed, fix that in the PR. During a landing task, don't build new checks: note them for a separate task.
- Keep merging after a merge. Before each later merge in the same task, look at `main`'s latest finished `PR checks` run (`gh run list --branch main --workflow pr.yml --limit 3`) without waiting for a running one. If it is red because of a merged PR (not a known out-of-memory or race failure, which gets one re-run), fix or revert in a PR before merging other app code.

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
- Agents may merge upgrade PRs once checks pass and CI runs the unit tests. Until CI runs them, upgrade PRs wait for the maintainer.

## Releases

Process: app `docs/RELEASING.md`.

- The release APK is built by this machine or GitHub, whichever is faster, with app `tooling/reproducible-builds/build-release.sh`.
- The maintainer signs it locally (APK key) and tags it locally (publication key, `WtFs…`, listed in app `.github/release-signers`).
- One `collie.apk` is uploaded. App CI checks it, uploads the profile copies (`collie-foss`, `molly-continued`, `molly-foss-continued`, `molly-minimal`) and publishes a prerelease within minutes.
- A full release is the same APK, promoted only after a rebuild on the other side (GitHub or this machine) matches it.
- Release notes: `distribution/release-notes/<version>.md` here, in the maintainer's style (see existing files). App CI refuses to publish without one. In-app notices (e.g. the later move to one `collie.apk`) ship as ordinary app PRs.
- F-Droid and Pages carry verified full releases only.

## Security fuzzing pilot

The focused Socket/Android pilot and its evidence expectations are documented in [SECURITY-FUZZING.md](SECURITY-FUZZING.md). It is a development and release confidence measure; it does not change release approval or branch enforcement.

## To retire

Now: generated publication commits on app `main` (G/I), the plan/receipt approval chain and the composer (`source/`, candidate plans/receipts). Keep native-input delivery (`repo.collie.im/native-inputs`) and Pages/F-Droid publishing; `pages-release.yml` stays until native files have another publishing route.

After the first diff-based upgrade ships as a full release: `patches/molly`, `patches/ports`, `patches/forks` and their tools in `tools/patches`, after tagging `archive/patch-forest`.
